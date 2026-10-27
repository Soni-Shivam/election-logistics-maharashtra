"""Group Assembly constituencies (ACs) into Lok Sabha seats (PCs).

Each PC is exactly ``--acs-per-pc`` (default 6) whole ACs that form one
connected piece of the AC adjacency graph. As in delimitation practice, a PC
may cross district lines; ACs never do. Maharashtra has 288 ACs and 48 PCs,
but any set of districts whose AC count is a multiple of 6 works.

Input is one ``constituencies.geojson`` per district, as written by
``sangli_recom.py``: one polygon feature per AC with ``constituency`` (the AC
label within the district) and ``population``. ACs are adjacent when they
share a land border of at least ``--min-border-km``, measured with a
``--snap-km`` tolerance so that borders between separately built districts
still match.

Method: a set-partitioning MIP. Every connected set of 6 ACs whose
population lies within +-``--balance`` of the PC average is enumerated as a
candidate column (so contiguity and balance hold by construction), and the
plan picks columns that cover each AC exactly once, minimizing

    sum over PCs of |population - average| / average * 100   (percentage points)
  + --cross-weight * (number of PCs that cross a district line)

Statewide the enumeration gives a few hundred thousand columns, and CBC
alone finds no plan at all in reasonable time. So a plan is first built
(LP diving, ReCom-style bisection, then ReCom-style annealing over pairs of
neighbouring PCs), and the LP bound and reduced costs then discard every
column that cannot improve on it; CBC solves what is left (see optimize()).
Small inputs are solved to proven optimality. Statewide, the result carries
its gap to the LP bound. The chosen plan is checked independently (cover,
sizes, contiguity, population bounds) before anything is written.

This is a research grouping, not the Delimitation Commission's procedure.

Example:

    python group_lok_sabha.py --district Sangli=runs/sangli --district Kolhapur=runs/kolhapur \\
        --map balanced --out lok_sabha
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import pulp
import shapely
from scipy.optimize import linprog
from scipy.sparse import csc_matrix
from shapely.geometry import mapping, shape
from shapely.strtree import STRtree


@dataclass
class AC:
    key: str  # "<district>-<constituency>", unique statewide
    district: str
    label: str
    population: int
    original_geometry: object  # lon/lat, as read
    geometry: object  # km


def _parse_district(text: str) -> tuple[str, Path]:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"expected NAME=PATH, got {text!r}")
    name, path = text.split("=", 1)
    if not name.strip():
        raise argparse.ArgumentTypeError(f"empty district name in {text!r}")
    return name.strip(), Path(path)


def resolve_map(path: Path, map_name: str) -> Path:
    """Accept a constituencies.geojson, a map folder, or a district result folder."""
    for candidate in (path, path / "constituencies.geojson",
                      path / f"map_{map_name}" / "constituencies.geojson"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"No constituencies.geojson at {path}, {path}/constituencies.geojson "
                     f"or {path}/map_{map_name}/constituencies.geojson")


def load_acs(districts: list[tuple[str, Path]], map_name: str) -> tuple[list[AC], dict]:
    names = [name for name, _ in districts]
    if len(set(n.lower() for n in names)) != len(names):
        raise SystemExit(f"District names must be unique: {names}")
    sources, raw = {}, []
    for name, path in districts:
        file = resolve_map(path, map_name)
        if file.resolve() in {p.resolve() for p in sources.values()}:
            raise SystemExit(f"{file} is given for two districts")
        sources[name] = file
        with file.open(encoding="utf-8") as fh:
            data = json.load(fh)
        features = data.get("features") if data.get("type") == "FeatureCollection" else None
        if not features:
            raise SystemExit(f"{file}: expected a non-empty FeatureCollection")
        labels = set()
        for feature in features:
            prop = feature.get("properties") or {}
            for field in ("constituency", "population"):
                if field not in prop:
                    raise SystemExit(f"{file}: a feature has no {field!r} property")
            label = str(prop["constituency"])
            if label in labels:
                raise SystemExit(f"{file}: AC {label} appears twice")
            labels.add(label)
            population = prop["population"]
            if population != int(population) or population < 0:
                raise SystemExit(f"{file}: AC {label} has invalid population {population!r}")
            geom = shape(feature["geometry"])
            if geom.geom_type not in {"Polygon", "MultiPolygon"} or geom.is_empty:
                raise SystemExit(f"{file}: AC {label} is not a polygon")
            if not geom.is_valid:
                geom = shapely.make_valid(geom)
            raw.append((name, label, int(population), geom))

    # One equirectangular km projection at the mean latitude: enough for
    # border lengths and areas (about 2% scale error across Maharashtra).
    lat0 = shapely.union_all([g.envelope for *_, g in raw]).centroid.y
    x_scale, y_scale = 111.32 * math.cos(math.radians(lat0)), 111.32

    def to_km(coords):
        out = coords.copy()
        out[:, 0] *= x_scale
        out[:, 1] *= y_scale
        return out

    def sort_key(item):
        label = item[1]
        return (names.index(item[0]), int(label) if label.isdigit() else math.inf, label)

    acs = [AC(key=f"{name}-{label}", district=name, label=label, population=pop,
              original_geometry=geom, geometry=shapely.transform(geom, to_km))
           for name, label, pop, geom in sorted(raw, key=sort_key)]
    return acs, {name: str(path) for name, path in sources.items()}


def build_adjacency(acs: list[AC], snap_km: float, min_border_km: float) -> nx.Graph:
    """AC graph; an edge is a shared land border of at least min_border_km."""
    geometries = [ac.geometry for ac in acs]
    boundaries = [g.boundary for g in geometries]
    near = [b.buffer(snap_km) if snap_km > 0 else b for b in boundaries]
    tree = STRtree(geometries)
    graph = nx.Graph()
    graph.add_nodes_from(range(len(acs)))
    for i, geom in enumerate(geometries):
        for j in tree.query(geom, predicate="dwithin", distance=max(snap_km, 1e-9)):
            j = int(j)
            if j <= i:
                continue
            overlap = geom.intersection(geometries[j]).area
            if overlap > 0.01 * min(geom.area, geometries[j].area):
                raise SystemExit(f"ACs {acs[i].key} and {acs[j].key} overlap by {overlap:.1f} km²; "
                                 "is the same area given under two districts?")
            if snap_km > 0:
                # Length of each boundary lying within snap_km of the other; the
                # smaller one ignores a sliver's far side.
                shared = min(boundaries[i].intersection(near[j]).length,
                             boundaries[j].intersection(near[i]).length)
            else:
                shared = boundaries[i].intersection(boundaries[j]).length
            if shared >= min_border_km:
                graph.add_edge(i, j, shared_km=float(shared),
                               cross_district=acs[i].district != acs[j].district)
    return graph


def check_components(acs: list[AC], graph: nx.Graph, size: int) -> None:
    bad = []
    for component in nx.connected_components(graph):
        if len(component) % size:
            members = sorted(acs[i].key for i in component)
            bad.append(f"{len(component)} ACs ({', '.join(members[:8])}"
                       f"{', ...' if len(members) > 8 else ''})")
    if bad:
        raise SystemExit(f"Each connected group of ACs must hold a multiple of {size} ACs; "
                         "these do not: " + "; ".join(bad) +
                         ". Add the missing neighbouring districts, or check --min-border-km.")


def enumerate_groups(graph: nx.Graph, populations: list[int], size: int,
                     lower: int, upper: int, max_columns: int) -> list[tuple[int, ...]]:
    """All connected size-node sets with population in [lower, upper].

    ESU enumeration (Wernicke 2006): each set is grown from its smallest node,
    adding only larger nodes from the exclusive neighbourhood, so every set
    is produced exactly once.
    """
    adjacency = {v: set(graph[v]) for v in graph}
    largest = max(populations)
    groups: list[tuple[int, ...]] = []

    def extend(members: list[int], pop: int, frontier: set[int], root: int, seen: set[int]):
        if len(members) == size:
            if pop >= lower:
                groups.append(tuple(sorted(members)))
                if len(groups) > max_columns:
                    raise SystemExit(f"More than {max_columns} candidate PCs; raise --max-columns "
                                     "or tighten --balance")
            return
        if pop + (size - len(members)) * largest < lower:
            return
        frontier = set(frontier)
        while frontier:
            w = frontier.pop()
            if pop + populations[w] > upper:
                continue
            new = frontier | {u for u in adjacency[w] if u > root and u not in seen}
            extend(members + [w], pop + populations[w], new, root, seen | adjacency[w])

    for v in sorted(graph):
        if populations[v] <= upper:
            extend([v], populations[v], {u for u in adjacency[v] if u > v}, v,
                   adjacency[v] | {v})
    return groups


def _random_spanning_tree(graph: nx.Graph, rng: random.Random) -> nx.Graph:
    weighted = nx.Graph()
    weighted.add_nodes_from(graph)
    weighted.add_edges_from((a, b, {"weight": rng.random()}) for a, b in graph.edges)
    return nx.minimum_spanning_tree(weighted, weight="weight")


def _bisect(graph: nx.Graph, nodes: frozenset[int], populations: list[int], size: int,
            lower: int, upper: int, rng: random.Random, budget: list[int]):
    """ReCom-style recursive bisection into connected, balanced groups of size."""
    budget[0] -= 1
    if budget[0] < 0:
        return None
    m = len(nodes) // size
    total = sum(populations[i] for i in nodes)
    if not m * lower <= total <= m * upper:
        return None
    if m == 1:
        return [tuple(sorted(nodes))]
    sub = graph.subgraph(nodes)
    for _ in range(20):
        tree = _random_spanning_tree(sub, rng)
        root = next(iter(nodes))
        parent, order = {root: None}, [root]
        for a in order:
            for b in tree[a]:
                if b not in parent:
                    parent[b] = a
                    order.append(b)
        count = {i: 1 for i in nodes}
        mass = {i: populations[i] for i in nodes}
        for child in reversed(order[1:]):
            count[parent[child]] += count[child]
            mass[parent[child]] += mass[child]
        cuts = []
        for child in order[1:]:
            k, rest = divmod(count[child], size)
            if rest == 0 and (k * lower <= mass[child] <= k * upper
                              and (m - k) * lower <= total - mass[child] <= (m - k) * upper):
                cuts.append((abs(k - m / 2) + rng.random(), child))
        for _, child in sorted(cuts)[:4]:
            side, stack = {child}, [child]
            while stack:
                a = stack.pop()
                for b in tree[a]:
                    if b != parent[a] and b not in side:
                        side.add(b)
                        stack.append(b)
            left = _bisect(graph, frozenset(side), populations, size, lower, upper, rng, budget)
            if left is None:
                continue
            right = _bisect(graph, nodes - side, populations, size, lower, upper, rng, budget)
            if right is not None:
                return left + right
    return None


class PairSplits:
    """All ways to re-split two neighbouring PCs into two candidate PCs.

    ``index`` holds every connected, in-bounds group, so a split is valid
    exactly when both halves are in it. Results are cached by AC set.
    """

    def __init__(self, graph: nx.Graph, populations: list[int], size: int, lower: int,
                 upper: int, index: dict, costs: np.ndarray):
        self.graph, self.populations, self.size = graph, populations, size
        self.lower, self.upper, self.index, self.costs = lower, upper, index, costs
        self.cache: dict[frozenset[int], list] = {}

    def __call__(self, union: set[int]) -> list[tuple[float, tuple, tuple]]:
        key = frozenset(union)
        if key not in self.cache:
            if len(self.cache) > 200_000:
                self.cache.clear()
            root = min(union)
            options = []
            for group in enumerate_groups(self.graph.subgraph(union), self.populations,
                                          self.size, self.lower, self.upper, math.inf):
                other = tuple(sorted(key.difference(group)))
                if root in group and other in self.index:
                    options.append((self.costs[self.index[group]] + self.costs[self.index[other]],
                                    group, other))
            self.cache[key] = options
        return self.cache[key]


def _improve_pairs(graph: nx.Graph, plan: list[tuple[int, ...]], splits: PairSplits,
                   cost) -> list[tuple[int, ...]]:
    """Re-split every pair of neighbouring PCs optimally until nothing improves."""
    plan = list(plan)
    improved = True
    while improved:
        improved = False
        pc_of = {i: p for p, members in enumerate(plan) for i in members}
        pairs = sorted({tuple(sorted((pc_of[a], pc_of[b]))) for a, b in graph.edges
                        if pc_of[a] != pc_of[b]})
        for p, q in pairs:
            best = min(splits(set(plan[p]) | set(plan[q])), key=lambda o: o[0])
            if best[0] < cost(plan[p]) + cost(plan[q]) - 1e-9:
                plan[p], plan[q] = best[1], best[2]
                improved = True
                break  # PC adjacency changed; rebuild the pair list
    return plan


# Annealing temperatures, in objective units (percentage points of deviation).
ANNEAL_T_START, ANNEAL_T_END = 3.0, 0.05


def _anneal(graph: nx.Graph, plan: list[tuple[int, ...]], splits: PairSplits, cost,
            seconds: float, max_moves: int, seed: int) -> tuple[list[tuple[int, ...]], int]:
    """ReCom-style annealing: pick a random border between two PCs, merge the
    pair, and draw a new split from all valid ones with weight exp(-cost / T)
    as T cools geometrically over the time budget. Returns the best plan seen."""
    plan = list(plan)
    edges = list(graph.edges)
    if len(plan) < 2 or not edges:
        return plan, 0
    rng = random.Random(seed)
    pc_of = {i: p for p, members in enumerate(plan) for i in members}
    current = best = sum(cost(g) for g in plan)
    best_plan = list(plan)
    t0 = time.perf_counter()
    moves = 0
    while moves < max_moves:
        elapsed = time.perf_counter() - t0
        if elapsed >= seconds:
            break
        moves += 1
        a, b = edges[rng.randrange(len(edges))]
        p, q = pc_of[a], pc_of[b]
        if p == q:
            continue
        temperature = ANNEAL_T_START * (ANNEAL_T_END / ANNEAL_T_START) ** (elapsed / seconds)
        options = splits(set(plan[p]) | set(plan[q]))
        low = min(o[0] for o in options)
        value, left, right = rng.choices(
            options, weights=[math.exp(-(o[0] - low) / temperature) for o in options])[0]
        current += value - cost(plan[p]) - cost(plan[q])
        plan[p], plan[q] = left, right
        for i in left:
            pc_of[i] = p
        for i in right:
            pc_of[i] = q
        if current < best - 1e-9:
            best, best_plan = current, list(plan)
    return best_plan, moves


def _cover_matrix(n_rows: int, groups: list[tuple[int, ...]]) -> csc_matrix:
    rows = np.fromiter((i for g in groups for i in g), dtype=np.int64)
    cols = np.repeat(np.arange(len(groups)), [len(g) for g in groups])
    return csc_matrix((np.ones(len(rows)), (rows, cols)), shape=(n_rows, len(groups)))


def _solve_lp(A: csc_matrix, costs: np.ndarray, rows: np.ndarray, cols: np.ndarray):
    """LP relaxation over the given rows and columns (HiGHS, in memory)."""
    return linprog(costs[cols], A_eq=A[rows][:, cols], b_eq=np.ones(len(rows)),
                   bounds=(0, None), method="highs")


def lp_dive(graph: nx.Graph, A: csc_matrix, costs: np.ndarray, groups: list[tuple[int, ...]],
            size: int) -> list[tuple[int, ...]] | None:
    """Fix LP columns at 1 and re-solve until every AC is covered.

    All columns at 1 are fixed together, otherwise the largest one. A fix that
    leaves a piece of the map whose AC count is not a multiple of size is
    undone and that column is banned.
    """
    covered = np.zeros(A.shape[0], dtype=bool)
    active = np.arange(A.shape[1])
    plan: list[tuple[int, ...]] = []
    while not covered.all():
        res = _solve_lp(A, costs, np.flatnonzero(~covered), active)
        if res.status != 0:
            return None
        order = np.argsort(-res.x)
        integral = [int(active[k]) for k in order if res.x[k] > 1 - 1e-6]
        fixed = None
        for attempt in ([integral] if len(integral) > 1 else []) + [[int(active[order[0]])]]:
            trial = covered.copy()
            for c in attempt:
                trial[list(groups[c])] = True
            rest = graph.subgraph(np.flatnonzero(~trial).tolist())
            if all(len(part) % size == 0 for part in nx.connected_components(rest)):
                fixed = attempt
                break
        if fixed is None:
            active = active[active != active[order[0]]]
            continue
        plan += [groups[c] for c in fixed]
        covered = trial
        clash = A.T @ covered.astype(float)
        active = active[clash[active] == 0]
    return plan


def _make_solver(time_limit: float, threads: int | None, warm_start: bool):
    # On Windows PuLP's CBC warm start needs keepFiles; _cbc runs CBC in a
    # temporary folder so the kept files do not land in the working directory.
    kw = dict(msg=False, timeLimit=time_limit, warmStart=warm_start, keepFiles=warm_start)
    if threads:
        kw["threads"] = threads
    for name in ("PULP_CBC_CMD", "COIN_CMD"):  # PuLP < 4 bundles CBC; 4.x needs cbcbox
        cls = getattr(pulp, name, None)
        if cls is not None:
            solver = cls(**kw)
            if solver.available():
                return solver
    raise SystemExit("CBC not found. Install PuLP < 4 (bundles CBC) or 'pulp[cbc]'")


def _new_binary(prob: pulp.LpProblem, name: str):
    if hasattr(prob, "add_variable"):  # PuLP 4 creates variables through the problem
        return prob.add_variable(name, 0, 1, "Integer")
    return pulp.LpVariable(name, cat="Binary")


def _cbc(n_rows: int, groups: list[tuple[int, ...]], costs: np.ndarray, cols: np.ndarray,
         start: set[int], time_limit: float, threads: int | None):
    """Set-partitioning MIP over cols; returns (chosen column ids, sol_status)."""
    prob = pulp.LpProblem("lok_sabha_grouping", pulp.LpMinimize)
    x = {int(c): _new_binary(prob, f"g{c}") for c in cols}
    covering: list[list[int]] = [[] for _ in range(n_rows)]
    for c in x:
        for i in groups[c]:
            covering[i].append(c)
    prob += pulp.lpSum(float(costs[c]) * var for c, var in x.items())
    for i, cs in enumerate(covering):
        prob += pulp.lpSum(x[c] for c in cs) == 1, f"cover_{i}"
    if start:
        for c, var in x.items():
            var.setInitialValue(1 if c in start else 0)
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="group_lok_sabha_") as tmp:
        os.chdir(tmp)
        try:
            prob.solve(_make_solver(time_limit, threads, warm_start=bool(start)))
        finally:
            os.chdir(cwd)
    chosen = [c for c, var in x.items() if (var.varValue or 0) > 0.5]
    return chosen, getattr(prob, "sol_status", None)


def optimize(graph: nx.Graph, groups: list[tuple[int, ...]], cost, populations: list[int],
             size: int, lower: int, upper: int, args) -> tuple[list[tuple[int, ...]], dict]:
    """Exact set-partitioning MIP over all candidate PCs.

    1. LP relaxation over all columns: lower bound sum(pi) and reduced costs
       rc_c = cost_c - sum_{i in c} pi_i (pi = duals of the cover rows).
    2. Incumbent Z: LP dive and ReCom-style bisection, each polished by
       re-splitting neighbouring PC pairs exactly; the best then seeds a
       ReCom-style annealing run (--search-seconds).
    3. Every plan x has cost(x) = sum(pi) + sum_c rc_c x_c, so with rc >= 0
       a plan cheaper than Z uses only columns with rc_c <= Z - sum(pi). CBC
       solves the MIP over those columns, warm-started from Z; its optimum
       is the optimum over all columns. If too many columns survive
       (--max-mip-columns), CBC is skipped and Z is returned with its gap.
    """
    t0 = time.perf_counter()
    n = len(populations)
    n_pcs = n // size
    costs = np.array([cost(g) for g in groups])
    A = _cover_matrix(n, groups)
    uncovered = np.flatnonzero(A.getnnz(axis=1) == 0)
    if len(uncovered):
        raise SystemExit(f"{len(uncovered)} ACs are in no connected, balanced group of {size}; "
                         "no plan exists at this --balance")
    root = _solve_lp(A, costs, np.arange(n), np.arange(len(groups)))
    if root.status == 2:
        raise SystemExit("Not even a fractional plan exists (LP infeasible); widen --balance")
    if root.status != 0:
        raise SystemExit(f"LP relaxation failed: {root.message}")
    pi = root.eqlin.marginals
    bound = float(pi.sum())
    rc = costs - A.T @ pi
    t_lp = time.perf_counter()

    index = {g: c for c, g in enumerate(groups)}
    splits = PairSplits(graph, populations, size, lower, upper, index, costs)
    starts = {}
    dive = lp_dive(graph, A, costs, groups, size)
    if dive is not None:
        starts["lp_dive"] = _improve_pairs(graph, dive, splits, cost)
    rng = random.Random(args.seed)
    for r in range(args.restarts):
        for _ in range(50):
            plan = _bisect(graph, frozenset(range(n)), populations, size, lower, upper, rng, [2000])
            if plan is not None:
                starts[f"bisection_{r + 1}"] = _improve_pairs(graph, plan, splits, cost)
                break
    start_costs = {k: float(sum(costs[index[g]] for g in p)) for k, p in starts.items()}
    moves = 0
    if starts and args.search_seconds > 0:
        best = min(start_costs, key=start_costs.get)
        plan, moves = _anneal(graph, starts[best], splits, cost, args.search_seconds,
                              20_000 * n_pcs ** 2, args.seed)
        starts["annealing"] = _improve_pairs(graph, plan, splits, cost)
        start_costs["annealing"] = float(sum(costs[index[g]] for g in starts["annealing"]))
    t_heuristic = time.perf_counter()

    info = {"lp_bound": bound, "heuristic_objectives": start_costs, "annealing_moves": moves}
    if starts:
        best = min(start_costs, key=start_costs.get)
        incumbent, z = starts[best], start_costs[best]
        # Negative reduced costs (LP tolerance) loosen the threshold.
        slack = (n_pcs - 1) * max(0.0, -float(rc.min()))
        cols = np.union1d(np.flatnonzero(rc <= z - bound + slack + 1e-7),
                          [index[g] for g in incumbent])
        start = {index[g] for g in incumbent}
    else:
        incumbent, cols, start = None, np.arange(len(groups)), set()
    info["mip_columns"] = len(cols)

    if incumbent is not None and z - bound <= 1e-6:
        chosen, status = incumbent, "optimal (heuristic plan meets the LP bound)"
    elif incumbent is not None and len(cols) > args.max_mip_columns:
        # A weak LP bound leaves most columns in; CBC then makes no progress
        # (statewide tests: no improvement in 300 s), so do not wait for it.
        chosen = incumbent
        status = f"heuristic (CBC skipped: {len(cols)} columns after filtering > --max-mip-columns)"
    else:
        ids, sol_status = _cbc(n, groups, costs, cols, start, args.time_limit, args.threads)
        chosen = [groups[c] for c in ids]
        status = {1: "optimal", 2: "feasible (time limit)"}.get(sol_status, "no solution")
        if sorted(i for g in chosen for i in g) != list(range(n)):
            if incumbent is None:
                raise SystemExit(f"No grouping found (CBC: {status}). "
                                 "Widen --balance or raise --time-limit.")
            chosen, status = incumbent, "heuristic (CBC returned no plan)"
        elif incumbent is not None and sum(costs[index[g]] for g in chosen) > z + 1e-9:
            chosen, status = incumbent, "heuristic (CBC found nothing better)"
    objective = float(sum(costs[index[g]] for g in chosen))
    info.update(status=status, objective=objective, lp_gap=objective - bound,
                lp_runtime_s=t_lp - t0, heuristic_runtime_s=t_heuristic - t_lp,
                mip_runtime_s=time.perf_counter() - t_heuristic)
    return chosen, info


def check_plan(acs: list[AC], graph: nx.Graph, plan: list[tuple[int, ...]],
               size: int, lower: int, upper: int) -> dict:
    """Independent checks of the chosen plan; stops on any failure."""
    covered = sorted(i for members in plan for i in members)
    problems = []
    if covered != list(range(len(acs))):
        problems.append("ACs are not each in exactly one PC")
    for pc, members in enumerate(plan, start=1):
        pop = sum(acs[i].population for i in members)
        if len(members) != size:
            problems.append(f"PC {pc} has {len(members)} ACs")
        if not nx.is_connected(graph.subgraph(members)):
            problems.append(f"PC {pc} is not contiguous")
        if not lower <= pop <= upper:
            problems.append(f"PC {pc} population {pop} outside [{lower}, {upper}]")
    if problems:
        raise SystemExit("Plan failed its checks: " + "; ".join(problems))
    return {"every_ac_once": True, "all_pcs_have_size": size, "all_contiguous": True,
            "all_within_bounds": True}


def write_outputs(out: Path, acs: list[AC], graph: nx.Graph, plan: list[tuple[int, ...]],
                  target: float, summary: dict, snap_km: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pcs = []
    for pc, members in enumerate(plan, start=1):
        pop = sum(acs[i].population for i in members)
        districts = list(dict.fromkeys(acs[i].district for i in members))
        km_union = shapely.union_all([acs[i].geometry for i in members])
        snapped = km_union.buffer(snap_km).buffer(-snap_km) if snap_km > 0 else km_union
        pcs.append({
            "pc": pc,
            "population": pop,
            "deviation_pct": 100 * (pop - target) / target,
            "ac_count": len(members),
            "acs": ";".join(acs[i].key for i in members),
            "districts": ";".join(districts),
            "district_count": len(districts),
            "crosses_district": len(districts) > 1,
            "area_km2": float(km_union.area),
            "geometry_parts": len(getattr(snapped, "geoms", [snapped])),
        })

    with (out / "pc_assignment.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ac_key", "district", "ac", "ac_population", "pc", "pc_population",
                         "pc_deviation_pct", "pc_districts"])
        for row, members in zip(pcs, plan):
            for i in members:
                ac = acs[i]
                writer.writerow([ac.key, ac.district, ac.label, ac.population, row["pc"],
                                 row["population"], f"{row['deviation_pct']:.3f}",
                                 row["districts"]])

    features = [{"type": "Feature", "properties": row,
                 "geometry": mapping(shapely.union_all([acs[i].original_geometry
                                                        for i in members]))}
                for row, members in zip(pcs, plan)]
    with (out / "pcs.geojson").open("w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh)

    with (out / "ac_adjacency.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ac_a", "ac_b", "shared_km", "cross_district"])
        for i, j, data in sorted(graph.edges(data=True)):
            writer.writerow([acs[i].key, acs[j].key, f"{data['shared_km']:.3f}",
                             data["cross_district"]])

    summary["pcs"] = pcs
    with (out / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--district", type=_parse_district, action="append", required=True,
                        metavar="NAME=PATH",
                        help="district name and its result folder, map folder or "
                             "constituencies.geojson; repeat for each district")
    parser.add_argument("--map", default="balanced",
                        help="map_<name> to read inside a district result folder (default balanced)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--acs-per-pc", type=int, default=6)
    parser.add_argument("--balance", type=float, default=0.10,
                        help="hard bound: each PC within this fraction of the average (default 0.10)")
    parser.add_argument("--cross-weight", type=float, default=5.0,
                        help="cost of one district-crossing PC, in percentage points of "
                             "population deviation (default 5)")
    parser.add_argument("--snap-km", type=float, default=0.05)
    parser.add_argument("--min-border-km", type=float, default=0.5)
    parser.add_argument("--search-seconds", type=float, default=180.0,
                        help="annealing time before the MIP (default 180; stops sooner on "
                             "small inputs)")
    parser.add_argument("--time-limit", type=float, default=300.0,
                        help="CBC seconds for the final MIP (default 300)")
    parser.add_argument("--restarts", type=int, default=5,
                        help="starts of the heuristic that seeds the MIP (default 5)")
    parser.add_argument("--seed", type=int, default=301)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--max-columns", type=int, default=3_000_000,
                        help="stop if there are more candidate PCs than this")
    parser.add_argument("--max-mip-columns", type=int, default=50_000,
                        help="skip CBC if more candidates than this survive the "
                             "reduced-cost filter (default 50000)")
    args = parser.parse_args()
    if args.acs_per_pc < 1 or not 0 < args.balance < 1 or args.cross_weight < 0:
        parser.error("Invalid --acs-per-pc, --balance or --cross-weight")
    if args.snap_km < 0 or args.min_border_km <= 0 or args.time_limit <= 0:
        parser.error("Invalid --snap-km, --min-border-km or --time-limit")
    if args.restarts < 0 or args.search_seconds < 0:
        parser.error("--restarts and --search-seconds must be nonnegative")

    t0 = time.perf_counter()
    acs, sources = load_acs(args.district, args.map)
    size = args.acs_per_pc
    if len(acs) % size:
        raise SystemExit(f"{len(acs)} ACs cannot be split into PCs of {size}; "
                         f"the districts given must hold a multiple of {size} ACs")
    n_pcs = len(acs) // size
    total = sum(ac.population for ac in acs)
    target = total / n_pcs
    lower = math.ceil(target * (1 - args.balance))
    upper = math.floor(target * (1 + args.balance))

    graph = build_adjacency(acs, args.snap_km, args.min_border_km)
    check_components(acs, graph, size)
    populations = [ac.population for ac in acs]
    groups = enumerate_groups(graph, populations, size, lower, upper, args.max_columns)
    if not groups:
        raise SystemExit("No connected group of ACs is within the population bounds")

    def cost(members) -> float:
        pop = sum(populations[i] for i in members)
        crosses = len({acs[i].district for i in members}) > 1
        return 100 * abs(pop - target) / target + args.cross_weight * crosses

    crossing_candidates = sum(len({acs[i].district for i in g}) > 1 for g in groups)
    t_build = time.perf_counter() - t0
    plan, solver = optimize(graph, groups, cost, populations, size, lower, upper, args)
    # Number PCs in input order: by district as given, then by AC label.
    plan = sorted(plan)
    checks = check_plan(acs, graph, plan, size, lower, upper)

    deviations = [100 * (sum(populations[i] for i in m) - target) / target for m in plan]
    crossing_pcs = sum(len({acs[i].district for i in m}) > 1 for m in plan)
    summary = {
        "inputs": sources,
        "map": args.map,
        "settings": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()
                     if k not in ("district", "map")},
        "acs": len(acs),
        "pcs_count": n_pcs,
        "districts": len(sources),
        "population": total,
        "population_target": target,
        "population_bounds": [lower, upper],
        "max_abs_deviation_pct": max(abs(d) for d in deviations),
        "mean_abs_deviation_pct": sum(abs(d) for d in deviations) / n_pcs,
        "crossing_pcs": crossing_pcs,
        "objective": solver["objective"],
        "solver": solver["status"],
        "lp_bound": solver["lp_bound"],
        "lp_gap": solver["lp_gap"],
        "heuristic_objectives": solver["heuristic_objectives"],
        "mip_columns_after_filter": solver["mip_columns"],
        "annealing_moves": solver["annealing_moves"],
        "runtime_s": {"build": t_build, "lp": solver["lp_runtime_s"],
                      "heuristic": solver["heuristic_runtime_s"], "mip": solver["mip_runtime_s"]},
        "adjacency_edges": graph.number_of_edges(),
        "cross_district_edges": sum(d["cross_district"] for *_, d in graph.edges(data=True)),
        "candidate_pcs": len(groups),
        "candidate_crossing_pcs": crossing_candidates,
        "checks": checks,
    }
    write_outputs(args.out, acs, graph, plan, target, summary, args.snap_km)
    print(json.dumps({k: summary[k] for k in ("acs", "pcs_count", "crossing_pcs",
                                              "max_abs_deviation_pct", "objective",
                                              "lp_bound", "solver")}
                     | {"output": str(args.out)}))


if __name__ == "__main__":
    sys.exit(main())
