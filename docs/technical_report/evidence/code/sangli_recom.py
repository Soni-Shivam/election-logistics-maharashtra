"""Research constituency mapper using contiguous population units and ReCom.

Input is a GeoJSON FeatureCollection of villages, intact small towns, eligible
large-town wards, and any zero-population geographic gap units. Every feature
needs id, population, taluka, and settlement_id. Optional demand_lon/demand_lat
locate the inhabited part of the unit; otherwise a point inside the polygon is
used. A district GeoJSON polygon is required so missing or overlapping land
cannot be silently omitted.

This is a research optimizer, not a statutory delimitation procedure. The
default population band, split threshold, neck width, and weights are tunable
research choices. Distances are straight-line kilometres unless a road-distance
matrix is supplied with --distance-matrix; then each seat's centre is its road
1-median (the unit minimizing population-weighted road distance).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import shapely
from shapely.geometry import Polygon, mapping, shape
from shapely.strtree import STRtree

# People per EVM set, as in the logistics notebook; used only for the
# per-seat EVM-km estimate, not the score.
PEOPLE_PER_EVM = 1500


@dataclass(frozen=True)
class Settings:
    seats: int = 8
    balance: float = 0.10
    split_share: float = 0.25
    min_neck_km: float = 0.5
    neck_lobe_fraction: float = 0.10
    weight_population: float = 0.35
    weight_distance: float = 0.30
    weight_taluka: float = 0.20
    weight_shape: float = 0.15
    distance_reference_km: float = 20.0
    taluka_reference_fragments: float = 8.0
    link_islands_km: float = 0.0


@dataclass
class Unit:
    uid: str
    population: int
    taluka: str
    settlement_id: str
    point_xy: tuple[float, float]
    original_geometry: object
    geometry: object
    properties: dict


def _features(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("type") == "FeatureCollection":
        return data["features"]
    if data.get("type") == "Feature":
        return [data]
    if data.get("type") in {"Polygon", "MultiPolygon"}:
        return [{"type": "Feature", "geometry": data, "properties": {}}]
    raise ValueError(f"Expected GeoJSON geometry or FeatureCollection: {path}")


class DistrictProblem:
    def __init__(self, unit_path: Path, district_path: Path, settings: Settings,
                 distance_matrix_path: Path | None = None):
        self.settings = settings
        raw_units = _features(unit_path)
        district_features = _features(district_path)
        if len(district_features) != 1:
            raise ValueError("District GeoJSON must have exactly one geometry")
        self.district_original = shape(district_features[0]["geometry"])
        if not self.district_original.is_valid or self.district_original.is_empty:
            raise ValueError("District geometry is empty or invalid")
        latitude = self.district_original.centroid.y
        self.x_scale = 111.32 * math.cos(math.radians(latitude))
        self.y_scale = 111.32

        def to_km(coords):
            out = coords.copy()
            out[:, 0] *= self.x_scale
            out[:, 1] *= self.y_scale
            return out

        self.to_km = to_km
        self.district = shapely.transform(self.district_original, to_km)
        self.units: list[Unit] = []
        seen: set[str] = set()
        for feature in raw_units:
            prop = feature.get("properties") or {}
            for field in ("id", "population", "taluka", "settlement_id"):
                if field not in prop:
                    raise ValueError(f"Unit missing required property {field!r}")
            uid = str(prop["id"])
            if uid in seen:
                raise ValueError(f"Duplicate unit id: {uid}")
            seen.add(uid)
            population = int(prop["population"])
            if population < 0 or population != float(prop["population"]):
                raise ValueError(f"Invalid population for {uid}")
            original = shape(feature["geometry"])
            if original.is_empty or not original.is_valid or original.area <= 0:
                raise ValueError(f"Empty or invalid polygon for {uid}")
            if original.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError(f"Expected a polygon for {uid}")
            point = original.representative_point()
            if "demand_lon" in prop and "demand_lat" in prop:
                point = shapely.Point(float(prop["demand_lon"]), float(prop["demand_lat"]))
                if original.distance(point) * self.y_scale > 2.0:
                    raise ValueError(f"Demand point for {uid} is >2 km outside its polygon")
            self.units.append(Unit(
                uid=uid,
                population=population,
                taluka=str(prop["taluka"]),
                settlement_id=str(prop["settlement_id"]),
                point_xy=(point.x * self.x_scale, point.y * self.y_scale),
                original_geometry=original,
                geometry=shapely.transform(original, to_km),
                properties=dict(prop),
            ))
        if len(self.units) < settings.seats:
            raise ValueError("Fewer assignment units than constituencies")
        self.population = sum(u.population for u in self.units)
        self.fallback_location_units = sum(
            u.population > 0 and not {"demand_lon", "demand_lat"}.issubset(u.properties)
            for u in self.units
        )
        self.target = self.population / settings.seats
        self.lower = math.ceil(self.target * (1 - settings.balance))
        self.upper = math.floor(self.target * (1 + settings.balance))
        self._validate_units()
        self.road_km = None
        self.matrix_row: dict[int, int] = {}
        self.distance_source = {"type": "euclidean_km"}
        if distance_matrix_path is not None:
            self._load_distance_matrix(distance_matrix_path)
        self.graph = self._make_graph()
        self.region_cache: dict[frozenset[int], dict] = {}
        self.taluka_population = defaultdict(int)
        for unit in self.units:
            self.taluka_population[unit.taluka] += unit.population

    def _validate_units(self) -> None:
        by_settlement: dict[str, list[Unit]] = defaultdict(list)
        for unit in self.units:
            by_settlement[unit.settlement_id].append(unit)
            if unit.population > self.upper:
                raise ValueError(f"Unit {unit.uid} has population {unit.population}, above seat maximum {self.upper}; split its eligible settlement first")
        split_floor = self.target * self.settings.split_share
        for settlement, pieces in by_settlement.items():
            if len(pieces) > 1 and sum(u.population for u in pieces) < split_floor:
                raise ValueError(f"Settlement {settlement} was split below the {split_floor:.0f}-person threshold")

        geometries = [u.geometry for u in self.units]
        union = shapely.union_all(geometries)
        overlap = sum(g.area for g in geometries) - union.area
        outside = union.difference(self.district).area
        missing = self.district.difference(union).area
        tolerance = max(0.1, self.district.area * 0.00001)
        if overlap > tolerance or outside > tolerance or missing > tolerance:
            raise ValueError(
                "Unit polygons do not tile the district: "
                f"overlap={overlap:.2f}, outside={outside:.2f}, missing={missing:.2f} km². "
                "Repair the source map or add explicitly labelled zero-population gap units."
            )

    def _load_distance_matrix(self, path: Path) -> None:
        # Format written by the logistics notebook: pts (P x 2 lon/lat, rounded
        # to 6 dp) and D (P x P road km). Rows are matched on rounded demand points.
        with np.load(path) as z:
            if "pts" not in z or "D" not in z:
                raise ValueError(f"{path} must contain arrays 'pts' and 'D'")
            pts = np.asarray(z["pts"], dtype=float)
            road_km = np.asarray(z["D"], dtype=float)
        if pts.ndim != 2 or pts.shape[1] != 2 or road_km.shape != (len(pts), len(pts)):
            raise ValueError(f"{path}: expected pts (P, 2) and D (P, P); got {pts.shape} and {road_km.shape}")
        if not np.isfinite(road_km).all() or (road_km < 0).any():
            raise ValueError(f"{path}: D has non-finite or negative distances")
        row_of = {(float(lon), float(lat)): r
                  for r, (lon, lat) in enumerate(np.round(pts, 6))}
        missing = []
        for i, unit in enumerate(self.units):
            if unit.population == 0:
                continue
            prop = unit.properties
            if "demand_lon" not in prop or "demand_lat" not in prop:
                missing.append(f"{unit.uid} (no demand_lon/demand_lat)")
                continue
            key = (float(np.round(float(prop["demand_lon"]), 6)),
                   float(np.round(float(prop["demand_lat"]), 6)))
            if key not in row_of:
                missing.append(f"{unit.uid} {key}")
                continue
            self.matrix_row[i] = row_of[key]
        if missing:
            raise ValueError(
                f"{len(missing)} populated units have no row in {path}; first few: "
                + "; ".join(missing[:10])
                + ". Rebuild the matrix from the same assigned_units demand points.")
        self.road_km = road_km
        self.distance_source = {"type": "road_km_matrix", "path": str(path),
                                "matrix_points": int(len(pts)),
                                "matched_units": len(self.matrix_row)}

    def _make_graph(self) -> nx.Graph:
        geometries = [u.geometry for u in self.units]
        tree = STRtree(geometries)
        graph = nx.Graph()
        graph.add_nodes_from(range(len(geometries)))
        for i, geom in enumerate(geometries):
            for j in tree.query(geom, predicate="intersects"):
                j = int(j)
                if j <= i:
                    continue
                shared = geom.boundary.intersection(geometries[j].boundary).length
                if shared > 0.00001:  # a positive-length land border, not a corner
                    graph.add_edge(i, j, shared_km=float(shared))
        self.island_links = []
        if self.settings.link_islands_km > 0:
            # Join groups of units that touch the rest only at a corner (or across a
            # tiny mapping gap) with a zero-length link to their nearest outside unit.
            # shared_km = 0, so the neck rule still stops a big lobe hanging off it.
            while not nx.is_connected(graph):
                comps = sorted(nx.connected_components(graph), key=len)
                comp = comps[0]
                best = None
                for i in comp:
                    for j in tree.query(geometries[i], predicate="dwithin", distance=self.settings.link_islands_km):
                        j = int(j)
                        if j not in comp:
                            d = geometries[i].distance(geometries[j])
                            if best is None or d < best[0]:
                                best = (d, i, j)
                if best is None:
                    break
                graph.add_edge(best[1], best[2], shared_km=0.0)
                self.island_links.append({"from": self.units[best[1]].uid, "to": self.units[best[2]].uid,
                                          "gap_km": round(best[0], 4), "island_units": len(comp)})
        if not nx.is_connected(graph):
            sizes = sorted((len(c) for c in nx.connected_components(graph)), reverse=True)
            raise ValueError(f"Land-border adjacency is disconnected; component sizes: {sizes[:12]}. "
                             "If a group touches the rest only at a corner, pass --link-islands-km 0.2")
        return graph

    def _region_geometry(self, nodes: frozenset[int]):
        return shapely.union_all([self.units[i].geometry for i in nodes])

    def _valid_region(self, nodes: frozenset[int]) -> bool:
        if not nodes:
            return False
        pop = sum(self.units[i].population for i in nodes)
        if pop < self.lower or pop > self.upper:
            return False
        sub = self.graph.subgraph(nodes)
        if not nx.is_connected(sub):
            return False
        geom = self._region_geometry(nodes)
        if geom.geom_type != "Polygon" or not geom.is_valid:
            return False
        width = self.settings.min_neck_km
        if width <= 0:
            return True
        side_min = pop * self.settings.neck_lobe_fraction
        for a, b in nx.bridges(sub):
            if sub[a][b]["shared_km"] >= width:
                continue
            split = sub.copy()
            split.remove_edge(a, b)
            sides = [sum(self.units[i].population for i in part)
                     for part in nx.connected_components(split)]
            if len(sides) == 2 and min(sides) >= side_min:
                return False
        eroded = geom.buffer(-width / 2)
        parts = list(eroded.geoms) if eroded.geom_type == "MultiPolygon" else [eroded]
        substantial = [g for g in parts if g.geom_type == "Polygon"
                       and g.area >= geom.area * 0.05]
        return len(substantial) <= 1

    def _region_metrics(self, nodes: frozenset[int]) -> dict:
        if nodes in self.region_cache:
            return self.region_cache[nodes]
        population = sum(self.units[i].population for i in nodes)
        geom = self._region_geometry(nodes)
        compactness_loss = 1 - 4 * math.pi * geom.area / (geom.length ** 2)
        people = [i for i in nodes if self.units[i].population > 0]
        weights = np.asarray([self.units[i].population for i in people], dtype=float)
        if self.road_km is None:
            pts = np.asarray([self.units[i].point_xy for i in people], dtype=float)
            distances = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
        else:
            rows = [self.matrix_row[i] for i in people]
            # distances[c, u] = road km from unit u to candidate centre c
            distances = self.road_km[np.ix_(rows, rows)].T
        costs = distances @ weights
        best = int(np.argmin(costs))
        mean_distance = float(costs[best] / population)
        result = {
            "population": population,
            "mean_distance_km": mean_distance,
            "centre_unit_id": self.units[people[best]].uid,
            "compactness_loss": max(0.0, float(compactness_loss)),
            "area_km2": float(geom.area),
            "evm_km_estimate": float(costs[best] / PEOPLE_PER_EVM),
        }
        self.region_cache[nodes] = result
        return result

    def evaluate(self, plan: list[frozenset[int]], validate: bool = True) -> dict:
        if len(plan) != self.settings.seats:
            raise ValueError("Wrong number of constituencies")
        if validate:
            memberships = [i for nodes in plan for i in nodes]
            if sorted(memberships) != list(range(len(self.units))):
                raise ValueError("Each unit must appear exactly once")
            for seat, nodes in enumerate(plan, start=1):
                if not self._valid_region(nodes):
                    raise ValueError(f"Seat {seat} violates balance, connectivity, or neck rule")
        metrics = [self._region_metrics(nodes) for nodes in plan]
        population_loss = float(np.mean([abs(m["population"] / self.target - 1)
                                         for m in metrics]) / self.settings.balance)
        distance_km = sum(m["mean_distance_km"] * m["population"] for m in metrics) / self.population
        distance_loss = distance_km / self.settings.distance_reference_km
        compactness_loss = float(np.mean([m["compactness_loss"] for m in metrics]))
        pieces = defaultdict(int)
        for nodes in plan:
            by_taluka = defaultdict(set)
            for i in nodes:
                by_taluka[self.units[i].taluka].add(i)
            for taluka, members in by_taluka.items():
                pieces[taluka] += nx.number_connected_components(self.graph.subgraph(members))
        excess = sum(max(0, pieces[t] - math.ceil(p / self.upper))
                     for t, p in self.taluka_population.items())
        taluka_loss = excess / self.settings.taluka_reference_fragments
        s = self.settings
        score = (s.weight_population * population_loss
                 + s.weight_distance * distance_loss
                 + s.weight_taluka * taluka_loss
                 + s.weight_shape * compactness_loss)
        return {
            "score": float(score),
            "distance_source": self.distance_source,
            "terms": {
                "population": population_loss,
                "distance": distance_loss,
                "taluka_fragmentation": taluka_loss,
                "shape": compactness_loss,
            },
            "raw": {
                "population_target": self.target,
                "population_bounds": [self.lower, self.upper],
                "population_weighted_mean_distance_km": distance_km,
                "evm_km_estimate": float(sum(m["evm_km_estimate"] for m in metrics)),
                "taluka_excess_fragments": excess,
                "taluka_fragments": dict(sorted(pieces.items())),
                "units_using_polygon_interior_for_distance": self.fallback_location_units,
            },
            "seats": metrics,
        }


def _random_spanning_tree(subgraph: nx.Graph, rng: random.Random) -> nx.Graph:
    weighted = nx.Graph()
    weighted.add_nodes_from(subgraph)
    weighted.add_edges_from((a, b, {"weight": rng.random()}) for a, b in subgraph.edges)
    return nx.minimum_spanning_tree(weighted, weight="weight")


def _tree_cuts(problem: DistrictProblem, nodes: frozenset[int],
               low: int, high: int, target: float, rng: random.Random,
               trials: int):
    graph = problem.graph.subgraph(nodes)
    total = sum(problem.units[i].population for i in nodes)
    for _ in range(trials):
        tree = _random_spanning_tree(graph, rng)
        root = next(iter(nodes))
        parent = {root: None}
        order = [root]
        for a in order:
            for b in tree[a]:
                if b not in parent:
                    parent[b] = a
                    order.append(b)
        mass = {i: problem.units[i].population for i in nodes}
        descendants = {i: {i} for i in nodes}
        for child in reversed(order[1:]):
            p = parent[child]
            mass[p] += mass[child]
            descendants[p].update(descendants[child])
        candidates = []
        for child in order[1:]:
            m = mass[child]
            if low <= m <= high:
                candidates.append((abs(m - target) + rng.random() * problem.target * 0.05,
                                   frozenset(descendants[child])))
            elif low <= total - m <= high:
                complement = nodes.difference(descendants[child])
                candidates.append((abs(total - m - target) + rng.random() * problem.target * 0.05,
                                   frozenset(complement)))
        candidates.sort(key=lambda x: x[0])
        for _, left in candidates[:12]:
            yield left, nodes - left


def _initial_partition(problem: DistrictProblem, nodes: frozenset[int],
                       seats: int, rng: random.Random, depth: int = 0,
                       budget: list[int] | None = None,
                       deadline: float | None = None):
    # A difficult random tree can otherwise trigger unbounded recursive
    # backtracking. Fail this start cleanly and try another independent start.
    if budget is None:
        budget = [5000]
    budget[0] -= 1
    if budget[0] < 0 or (deadline is not None and time.monotonic() > deadline):
        return None
    total = sum(problem.units[i].population for i in nodes)
    if total < seats * problem.lower or total > seats * problem.upper:
        return None
    if seats == 1:
        return [nodes] if problem._valid_region(nodes) else None
    left_seats = seats // 2
    right_seats = seats - left_seats
    low = max(left_seats * problem.lower, total - right_seats * problem.upper)
    high = min(left_seats * problem.upper, total - right_seats * problem.lower)
    if low > high:
        return None
    for left, right in _tree_cuts(problem, nodes, low, high,
                                  left_seats * problem.target, rng, 40 if depth == 0 else 25):
        if deadline is not None and time.monotonic() > deadline:
            return None
        first = _initial_partition(problem, left, left_seats, rng, depth + 1, budget, deadline)
        if first is None:
            continue
        second = _initial_partition(problem, right, right_seats, rng, depth + 1, budget, deadline)
        if second is not None:
            return first + second
    return None


def _recom_candidate(problem: DistrictProblem, plan: list[frozenset[int]],
                     rng: random.Random):
    seat_of = {}
    for seat, nodes in enumerate(plan):
        for node in nodes:
            seat_of[node] = seat
    pairs = {(min(seat_of[a], seat_of[b]), max(seat_of[a], seat_of[b]))
             for a, b in problem.graph.edges if seat_of[a] != seat_of[b]}
    if not pairs:
        return None
    a, b = rng.choice(sorted(pairs))
    merged = plan[a] | plan[b]
    for left, right in _tree_cuts(problem, merged, problem.lower, problem.upper,
                                  problem.target, rng, 12):
        if not problem._valid_region(left) or not problem._valid_region(right):
            continue
        if len(left & plan[a]) < len(right & plan[a]):
            left, right = right, left
        proposal = list(plan)
        proposal[a], proposal[b] = left, right
        return proposal
    return None


def optimize(problem: DistrictProblem, steps: int, restarts: int, seed: int,
             initial_seconds: float = 30.0):
    rng = random.Random(seed)
    best_plan = None
    best_report = None
    accepted = 0
    proposed = 0
    successful_starts = 0
    for restart in range(restarts):
        start_budget = [5000]
        plan = _initial_partition(problem, frozenset(range(len(problem.units))),
                                  problem.settings.seats, rng, budget=start_budget,
                                  deadline=time.monotonic()+initial_seconds)
        if plan is None:
            continue
        successful_starts += 1
        report = problem.evaluate(plan)
        if best_report is None or report["score"] < best_report["score"]:
            best_plan, best_report = list(plan), report
        for step in range(steps):
            candidate = _recom_candidate(problem, plan, rng)
            if candidate is None:
                continue
            proposed += 1
            next_report = problem.evaluate(candidate, validate=False)
            delta = next_report["score"] - report["score"]
            temperature = 0.05 * (0.01 / 0.05) ** (step / max(1, steps - 1))
            if delta <= 0 or rng.random() < math.exp(-delta / temperature):
                plan, report = candidate, next_report
                accepted += 1
                if report["score"] < best_report["score"]:
                    best_plan, best_report = list(plan), report
    if best_plan is None:
        raise RuntimeError("No valid initial map found. Check the input graph and bounds; a failed random search does not prove infeasibility.")
    best_report["search"] = {
        "seed": seed, "steps_per_restart": steps, "restarts": restarts,
        "successful_starts": successful_starts,
        "initial_search_budget_per_start": 5000,
        "initial_seconds_per_start": initial_seconds,
        "proposals": proposed, "accepted": accepted,
    }
    return best_plan, best_report


def _load_existing(problem: DistrictProblem) -> list[frozenset[int]]:
    seats = defaultdict(set)
    for i, unit in enumerate(problem.units):
        if "constituency" not in unit.properties:
            raise ValueError(f"Unit {unit.uid} has no constituency property")
        seats[str(unit.properties["constituency"])].add(i)
    if len(seats) != problem.settings.seats:
        raise ValueError(f"Expected {problem.settings.seats} constituency labels, got {len(seats)}")
    return [frozenset(seats[name]) for name in sorted(seats)]


def save(problem: DistrictProblem, plan: list[frozenset[int]], report: dict,
         out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    seat_of = {i: seat for seat, nodes in enumerate(plan, start=1) for i in nodes}
    unit_features = []
    for i, unit in enumerate(problem.units):
        prop = dict(unit.properties)
        prop["constituency"] = seat_of[i]
        unit_features.append({"type": "Feature", "properties": prop,
                              "geometry": mapping(unit.original_geometry)})
    region_features = []
    for seat, nodes in enumerate(plan, start=1):
        original = shapely.union_all([problem.units[i].original_geometry for i in nodes])
        prop = {"constituency": seat, **report["seats"][seat - 1]}
        region_features.append({"type": "Feature", "properties": prop,
                                "geometry": mapping(original)})
    for name, features in (("assigned_units.geojson", unit_features),
                           ("constituencies.geojson", region_features)):
        with (out_dir / name).open("w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": features}, fh)
    report["settings"] = vars(problem.settings)
    if getattr(problem, "island_links", None):
        report["island_links"] = problem.island_links
    with (out_dir / "score.json").open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("optimize", "score"))
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--district", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seats", type=int, default=8)
    parser.add_argument("--balance", type=float, default=0.10)
    parser.add_argument("--split-share", type=float, default=0.25)
    parser.add_argument("--min-neck-km", type=float, default=0.5)
    parser.add_argument("--neck-lobe-fraction", type=float, default=0.10)
    parser.add_argument("--weight-population", type=float, default=0.35)
    parser.add_argument("--weight-distance", type=float, default=0.30)
    parser.add_argument("--weight-taluka", type=float, default=0.20)
    parser.add_argument("--weight-shape", type=float, default=0.15)
    parser.add_argument("--distance-reference-km", type=float, default=20.0)
    parser.add_argument("--taluka-reference-fragments", type=float, default=8.0)
    parser.add_argument("--link-islands-km", type=float, default=0.0,
                        help="join unit groups that touch the rest only at a corner (gap <= this many km) by a zero-length link")
    parser.add_argument("--steps", type=int, default=500)
    parser.add_argument("--restarts", type=int, default=5)
    parser.add_argument("--seed", type=int, default=303)
    parser.add_argument("--initial-seconds", type=float, default=30.0)
    parser.add_argument("--distance-matrix", type=Path, default=None,
                        help="road_distances.npz (pts, D) from the logistics notebook; "
                             "replaces straight-line km in the distance term")
    args = parser.parse_args()
    weights = (args.weight_population, args.weight_distance,
               args.weight_taluka, args.weight_shape)
    if args.seats < 1 or not 0 < args.balance < 1 or not 0 <= args.split_share <= 1:
        parser.error("Invalid seat count, balance, or split threshold")
    if args.min_neck_km < 0 or not 0 <= args.neck_lobe_fraction <= 0.5:
        parser.error("Invalid neck rule")
    if any(w < 0 for w in weights) or not math.isclose(sum(weights), 1.0):
        parser.error("The four score weights must be nonnegative and sum to one")
    if args.distance_reference_km <= 0 or args.taluka_reference_fragments <= 0:
        parser.error("Score reference scales must be positive")
    if args.steps < 0 or args.restarts < 1:
        parser.error("Steps must be nonnegative and restarts must be positive")
    if args.initial_seconds <= 0:
        parser.error("Initial search time limit must be positive")
    settings = Settings(seats=args.seats, balance=args.balance,
                        split_share=args.split_share, min_neck_km=args.min_neck_km,
                        neck_lobe_fraction=args.neck_lobe_fraction,
                        weight_population=args.weight_population,
                        weight_distance=args.weight_distance,
                        weight_taluka=args.weight_taluka,
                        weight_shape=args.weight_shape,
                        distance_reference_km=args.distance_reference_km,
                        taluka_reference_fragments=args.taluka_reference_fragments,
                        link_islands_km=args.link_islands_km)
    if args.distance_matrix is not None and not args.distance_matrix.is_file():
        parser.error(f"Distance matrix not found: {args.distance_matrix}")
    problem = DistrictProblem(args.units, args.district, settings, args.distance_matrix)
    if args.mode == "optimize":
        plan, report = optimize(problem, args.steps, args.restarts, args.seed,
                                args.initial_seconds)
    else:
        plan = _load_existing(problem)
        report = problem.evaluate(plan)
    save(problem, plan, report, args.out)
    print(json.dumps({"score": report["score"], "output": str(args.out),
                      "distance_source": problem.distance_source["type"],
                      "population_bounds": [problem.lower, problem.upper]}))


if __name__ == "__main__":
    main()
