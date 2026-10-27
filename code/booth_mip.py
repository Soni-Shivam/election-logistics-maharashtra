"""Capacitated booth-location MIP for district constituency maps.

For every constituency (seat) of an ``assigned_units.geojson`` written by
``sangli_recom.py``, this solves

    min  BOOTH_COST * sum_s y_s
       + VOTER_COST_PER_KM * sum_{i,s} d_is x_is
       + EVM_COST_PER_KM * sum_s d(depot, s) y_s

    s.t. sum_s x_is = e_i                 every populated unit i
         sum_i x_is <= CAPACITY * y_s     every candidate site s
         x_is <= min(e_i, CAPACITY) y_s   (valid tightening)
         sum_s y_s >= ceil(sum_i e_i / CAPACITY)   (valid tightening)
         x_is = 0 when d_is > MAX_KM (unit i may always use its own site)
         y_s integer >= 0, x_is >= 0

where candidate sites are the populated units of the seat, e_i is
population * ELECTOR_RATIO, and the depot is the elector-weighted road
1-median unit of the seat. Distances come from a cached road matrix
(``--distance-matrix``, npz with ``pts`` (P, 2) lon/lat and ``D`` km) or, if
that is absent, from straight-line km times ``--road-factor``.

ASSUMPTIONS (all overridable on the command line):
  * electors = population * 0.70. Census 2011 population is not the electoral
    roll; 0.70 is a placeholder adult-registration ratio, not a measured value.
  * a booth serves at most 1500 electors.
  * costs are rough INR per election, see DEFAULTS below.
  * a booth placed at a unit sits at that unit's demand point, so d_ii = 0.

Example:

    python booth_mip.py --assigned map_balanced/assigned_units.geojson \\
        map_locality/assigned_units.geojson map_taluka/assigned_units.geojson \\
        --max-km 2 3 5 --out booth_mip_results
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

# Defaults. Costs are rough INR figures per election and are ASSUMPTIONS.
ELECTOR_RATIO = 0.70  # electors / Census population (assumed, not measured)
CAPACITY = 1500  # max electors per booth
MAX_KM = 2.0  # max road km from a unit to its booth
BOOTH_COST = 25_000.0  # polling party, training, furniture, EVM share per booth
# Per elector per one-way road km: a 2 km round trip on foot is ~24 min,
# valued at ~Rs 50/h rural wage, times ~60% turnout -> ~Rs 12; rounded to 10.
VOTER_COST_PER_KM = 10.0
# Per booth per one-way road km from the depot: dispatch and return of the
# polling party and EVMs is two round trips (4 legs) at ~Rs 30/km.
EVM_COST_PER_KM = 120.0
ROAD_FACTOR = 1.33  # straight-line -> road km when no matrix is given
TIME_LIMIT = 300.0  # CBC seconds per seat
GAP_REL = 0.0  # requested relative MIP gap
FLOW_EPS = 1e-6  # flows below this many electors are treated as zero


def load_units(path: Path) -> pd.DataFrame:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [f["properties"] for f in data["features"]]
    units = pd.DataFrame(rows)
    need = {"id", "population", "constituency", "demand_lon", "demand_lat"}
    missing = need - set(units.columns)
    if missing:
        raise SystemExit(f"{path}: missing properties {sorted(missing)}")
    for col in ("name", "kind", "taluka"):
        if col not in units:
            units[col] = ""
    units["population"] = units["population"].fillna(0).astype(float)
    return units


def haversine_km(lon_a, lat_a, lon_b, lat_b) -> np.ndarray:
    lon_a, lat_a, lon_b, lat_b = map(np.radians, (lon_a, lat_a, lon_b, lat_b))
    h = (
        np.sin((lat_b - lat_a) / 2) ** 2
        + np.cos(lat_a) * np.cos(lat_b) * np.sin((lon_b - lon_a) / 2) ** 2
    )
    return 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


class Distances:
    """Road km between unit demand points, from an npz matrix or a fallback."""

    def __init__(self, matrix_path: Path | None, road_factor: float):
        self.road_factor = road_factor
        self.pts = self.D = None
        self.index: dict[tuple[float, float], int] = {}
        self.nonfinite_replaced = 0
        if matrix_path is None:
            self.source = f"straight-line km x {road_factor} (FALLBACK, no road matrix)"
            return
        z = np.load(matrix_path)
        self.pts = np.asarray(z["pts"], float)
        self.D = np.asarray(z["D"], float)
        if self.D.shape != (len(self.pts), len(self.pts)):
            raise SystemExit(f"{matrix_path}: D shape {self.D.shape} does not match pts")
        self.index = {(round(x, 6), round(y, 6)): k for k, (x, y) in enumerate(self.pts)}
        self.source = f"road matrix {matrix_path.name}"

    def _row(self, lon: float, lat: float) -> int:
        k = self.index.get((round(lon, 6), round(lat, 6)))
        if k is not None:
            return k
        gap = np.hypot(self.pts[:, 0] - lon, self.pts[:, 1] - lat)
        k = int(np.argmin(gap))
        if gap[k] > 1e-4:
            raise SystemExit(
                f"point ({lon:.6f}, {lat:.6f}) is not in the distance matrix; "
                "rebuild it for these maps or run without --distance-matrix"
            )
        return k

    def matrix(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Square km matrix among the given points, zero on the diagonal."""
        straight = haversine_km(lon[:, None], lat[:, None], lon[None, :], lat[None, :])
        if self.D is None:
            d = straight * self.road_factor
        else:
            rows = np.array([self._row(x, y) for x, y in zip(lon, lat)])
            d = self.D[np.ix_(rows, rows)].copy()
            bad = ~np.isfinite(d)
            self.nonfinite_replaced += int(bad.sum())
            d[bad] = straight[bad] * self.road_factor
        np.fill_diagonal(d, 0.0)
        return d


def new_var(prob: pulp.LpProblem, name: str, cat: str = "Continuous"):
    """Nonnegative variable; PuLP 4 creates variables through the problem."""
    if hasattr(prob, "add_variable"):
        return prob.add_variable(name, 0, None, cat)
    return pulp.LpVariable(name, lowBound=0, cat=cat)


def make_solver(time_limit: float, gap_rel: float, log_path: Path, name: str = "cbc"):
    kw = dict(msg=False, timeLimit=time_limit, gapRel=gap_rel, logPath=str(log_path))
    if name == "gurobi":
        solver = pulp.GUROBI(**kw)
        if not solver.available():
            raise SystemExit("Gurobi (gurobipy) is not available; use --solver cbc")
        return solver
    for name in ("PULP_CBC_CMD", "COIN_CMD"):  # PuLP < 4 bundles CBC; 4.x needs cbcbox
        cls = getattr(pulp, name, None)
        if cls is not None:
            solver = cls(**kw)
            if solver.available():
                return solver
    raise SystemExit("CBC not found. Install PuLP < 4 (bundles CBC) or 'pulp[cbc]'")


def parse_cbc_log(text: str, objective: float | None) -> tuple[str, float | None]:
    """Return CBC's result line and the relative gap it proved."""
    m = re.search(r"Result - (.+)", text)
    status = m.group(1).strip() if m else "unknown"
    if status.startswith("Optimal solution found"):
        return status, 0.0
    m = re.search(r"Gap:\s+([-\d.eE+]+)", text)
    if m:
        return status, float(m.group(1))
    m = re.search(r"Lower bound:\s+([-\d.eE+]+)", text)
    if m and objective:
        return status, (objective - float(m.group(1))) / abs(objective)
    return status, None


def solve_seat(seat: pd.DataFrame, dist: Distances, args, log_dir: Path) -> dict:
    lon = seat["demand_lon"].to_numpy(float)
    lat = seat["demand_lat"].to_numpy(float)
    e = seat["population"].to_numpy(float) * args.elector_ratio
    n = len(seat)
    d = dist.matrix(lon, lat)

    depot = int(np.argmin(e @ d))  # elector-weighted 1-median
    d_depot = d[depot]

    allowed = [[s for s in range(n) if d[i, s] <= args.max_km or s == i] for i in range(n)]
    isolated = sum(len(a) == 1 for a in allowed)

    prob = pulp.LpProblem("booths", pulp.LpMinimize)
    y = [new_var(prob, f"y_{s}", "Integer") for s in range(n)]
    x = {(i, s): new_var(prob, f"x_{i}_{s}") for i in range(n) for s in allowed[i]}
    by_site: dict[int, list[int]] = {s: [] for s in range(n)}
    for i, s in x:
        by_site[s].append(i)

    prob += (
        args.booth_cost * pulp.lpSum(y)
        + args.voter_cost_per_km * pulp.lpSum(d[i, s] * v for (i, s), v in x.items())
        + args.evm_cost_per_km * pulp.lpSum(d_depot[s] * y[s] for s in range(n))
    )
    for i in range(n):
        prob += pulp.lpSum(x[i, s] for s in allowed[i]) == e[i]
    for s in range(n):
        prob += pulp.lpSum(x[i, s] for i in by_site[s]) <= args.capacity * y[s]
    for (i, s), v in x.items():
        prob += v <= min(e[i], args.capacity) * y[s]
    min_booths = math.ceil(e.sum() / args.capacity - 1e-9)
    prob += pulp.lpSum(y) >= min_booths

    log_path = log_dir / f"cbc_seat_{seat['constituency'].iloc[0]}.log"
    solver = make_solver(args.time_limit, args.gap, log_path, args.solver)
    t0 = time.perf_counter()
    prob.solve(solver)
    runtime = time.perf_counter() - t0

    objective = pulp.value(prob.objective)
    if args.solver == "gurobi":
        m = prob.solverModel
        status = "Optimal solution found" if m.Status == 2 else f"Gurobi status {m.Status} (9 = time limit)"
        gap = float(m.MIPGap) if m.SolCount else None
    else:
        log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
        status, gap = parse_cbc_log(log_text, objective)
        if not status.startswith("Optimal") and gap == 0.0:
            gap = None  # CBC prints Gap 0 on some time-limit stops; it is not a proof
    if pulp.LpStatus[prob.status] not in ("Optimal",) or objective is None:
        status = f"{pulp.LpStatus[prob.status]} / {status}"
    if objective is None:
        raise SystemExit(f"seat {seat['constituency'].iloc[0]}: no solution ({status})")

    booths = np.array([round(v.varValue or 0) for v in y], int)
    flows = [
        (i, s, v.varValue) for (i, s), v in x.items() if v.varValue and v.varValue > FLOW_EPS
    ]
    person_km = sum(f * d[i, s] for i, s, f in flows)
    max_km = max((d[i, s] for i, s, f in flows if f > 0.5), default=0.0)
    evm_km = float(booths @ d_depot)
    served = np.zeros(n)
    units_served = np.zeros(n, int)
    for i, s, f in flows:
        served[s] += f
        units_served[s] += 1

    ids = seat["id"].to_numpy()
    sites = pd.DataFrame(
        {
            "site_id": ids,
            "name": seat["name"].to_numpy(),
            "kind": seat["kind"].to_numpy(),
            "taluka": seat["taluka"].to_numpy(),
            "lon": lon,
            "lat": lat,
            "booths": booths,
            "electors_served": served.round(1),
            "units_served": units_served,
            "utilization": np.divide(served, args.capacity * booths, out=np.zeros(n), where=booths > 0).round(4),
            "depot_km": d_depot.round(3),
            "is_depot": np.arange(n) == depot,
        }
    )[lambda t: t["booths"] > 0]
    assign = pd.DataFrame(
        {
            "unit_id": [ids[i] for i, s, f in flows],
            "site_id": [ids[s] for i, s, f in flows],
            "electors": [round(f, 2) for i, s, f in flows],
            "km": [round(d[i, s], 3) for i, s, f in flows],
        }
    )
    electors = e.sum()
    summary = {
        "seat": seat["constituency"].iloc[0],
        "units": n,
        "electors": round(electors),
        "booths": int(booths.sum()),
        "min_booths": min_booths,
        "extra_booths": int(booths.sum()) - min_booths,
        "booth_sites": int((booths > 0).sum()),
        "capacity_util": round(electors / (args.capacity * booths.sum()), 4),
        "isolated_units": isolated,
        "voter_person_km": round(person_km, 1),
        "mean_voter_km": round(person_km / electors, 4),
        "max_voter_km": round(max_km, 3),
        "electors_travelling_pct": round(100 * sum(f for i, s, f in flows if i != s) / electors, 2),
        "evm_km": round(evm_km, 1),
        "depot_id": ids[depot],
        "depot_name": seat["name"].iloc[depot],
        "booth_cost": round(args.booth_cost * booths.sum()),
        "voter_cost": round(args.voter_cost_per_km * person_km),
        "evm_cost": round(args.evm_cost_per_km * evm_km),
        "objective": round(objective),
        "status": status,
        "mip_gap": None if gap is None else round(gap, 6),
        "runtime_s": round(runtime, 2),
    }
    return {"summary": summary, "sites": sites, "assign": assign}


def run_map(path: Path, max_km: float, dist: Distances, args, out_dir: Path) -> tuple[dict, pd.DataFrame]:
    units = load_units(path)
    map_name = path.parent.name
    pop = units[units["population"] > 0]
    out_dir.mkdir(parents=True, exist_ok=True)
    seat_args = argparse.Namespace(**{**vars(args), "max_km": max_km})
    rows, sites, assign = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for seat_id, seat in pop.groupby("constituency", sort=True):
            res = solve_seat(seat.reset_index(drop=True), dist, seat_args, Path(tmp))
            rows.append(res["summary"])
            sites.append(res["sites"].assign(seat=seat_id))
            assign.append(res["assign"].assign(seat=seat_id))
            s = res["summary"]
            print(
                f"  {map_name} max_km={max_km:g} seat {seat_id}: {s['booths']} booths "
                f"({s['extra_booths']:+d} vs min), mean {s['mean_voter_km']:.3f} km, "
                f"{s['status']}, gap {s['mip_gap']}, {s['runtime_s']} s",
                flush=True,
            )
    seats = pd.DataFrame(rows)
    seats.insert(0, "max_km", max_km)
    seats.insert(0, "map", map_name)
    seats.to_csv(out_dir / "seat_summary.csv", index=False)
    site_cols = ["seat", "site_id", "name", "kind", "taluka", "lon", "lat", "booths",
                 "electors_served", "units_served", "utilization", "depot_km", "is_depot"]
    pd.concat(sites)[site_cols].to_csv(out_dir / "booth_locations.csv", index=False)
    pd.concat(assign)[["seat", "unit_id", "site_id", "electors", "km"]].to_csv(
        out_dir / "assignments.csv", index=False
    )

    electors = seats["electors"].sum()
    person_km = seats["voter_person_km"].sum()
    gaps = seats["mip_gap"].dropna()
    summary = {
        "map": map_name,
        "max_km": max_km,
        "distance_source": dist.source,
        "electors": int(electors),
        "booths": int(seats["booths"].sum()),
        "min_booths": int(seats["min_booths"].sum()),
        "extra_booths": int(seats["extra_booths"].sum()),
        "capacity_util": round(electors / (args.capacity * seats["booths"].sum()), 4),
        "voter_person_km": round(person_km),
        "mean_voter_km": round(person_km / electors, 4),
        "max_voter_km": seats["max_voter_km"].max(),
        "electors_travelling_pct": round(
            (seats["electors_travelling_pct"] * seats["electors"]).sum() / electors, 2
        ),
        "evm_km": round(seats["evm_km"].sum(), 1),
        "booth_cost": int(seats["booth_cost"].sum()),
        "voter_cost": int(seats["voter_cost"].sum()),
        "evm_cost": int(seats["evm_cost"].sum()),
        "objective": int(seats["objective"].sum()),
        "seats_optimal": int(seats["status"].str.startswith("Optimal").sum()),
        "seats": len(seats),
        "max_mip_gap": None if gaps.empty else float(gaps.max()),
        "runtime_s": round(seats["runtime_s"].sum(), 2),
    }
    return summary, seats


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--assigned", type=Path, nargs="+", required=True,
                   help="one or more assigned_units.geojson files")
    p.add_argument("--distance-matrix", type=Path, default=None,
                   help="npz with pts (P,2 lon/lat) and D (P,P road km); "
                        "if absent, straight-line km x --road-factor is used")
    p.add_argument("--out", type=Path, default=Path("booth_mip_results"))
    p.add_argument("--elector-ratio", type=float, default=ELECTOR_RATIO,
                   help=f"ASSUMPTION: electors per Census person (default {ELECTOR_RATIO})")
    p.add_argument("--capacity", type=int, default=CAPACITY,
                   help=f"max electors per booth (default {CAPACITY})")
    p.add_argument("--max-km", type=float, nargs="+", default=[MAX_KM],
                   help=f"max road km to the booth; several values run a sensitivity (default {MAX_KM})")
    p.add_argument("--booth-cost", type=float, default=BOOTH_COST,
                   help=f"Rs per booth (default {BOOTH_COST:g})")
    p.add_argument("--voter-cost-per-km", type=float, default=VOTER_COST_PER_KM,
                   help=f"Rs per elector per one-way road km (default {VOTER_COST_PER_KM:g})")
    p.add_argument("--evm-cost-per-km", type=float, default=EVM_COST_PER_KM,
                   help=f"Rs per booth per one-way road km from the depot (default {EVM_COST_PER_KM:g})")
    p.add_argument("--road-factor", type=float, default=ROAD_FACTOR,
                   help=f"straight-line to road km factor for the fallback (default {ROAD_FACTOR})")
    p.add_argument("--solver", choices=("cbc", "gurobi"), default="cbc",
                   help="MIP solver (gurobi needs gurobipy and a licence)")
    p.add_argument("--time-limit", type=float, default=TIME_LIMIT,
                   help=f"CBC seconds per seat (default {TIME_LIMIT:g})")
    p.add_argument("--gap", type=float, default=GAP_REL,
                   help=f"relative MIP gap at which CBC may stop (default {GAP_REL:g})")
    args = p.parse_args(argv)

    if args.distance_matrix is not None and not args.distance_matrix.exists():
        print(f"WARNING: {args.distance_matrix} not found; using the straight-line fallback")
        args.distance_matrix = None
    dist = Distances(args.distance_matrix, args.road_factor)
    print(f"Distances: {dist.source}")
    print(f"Electors = population x {args.elector_ratio} (ASSUMPTION); capacity {args.capacity}/booth")

    summaries, seats = [], []
    for max_km in args.max_km:
        for path in args.assigned:
            out_dir = args.out / path.parent.name / f"maxkm_{max_km:g}"
            summary, seat_rows = run_map(path, max_km, dist, args, out_dir)
            summaries.append(summary)
            seats.append(seat_rows)
    if dist.nonfinite_replaced:
        print(f"WARNING: {dist.nonfinite_replaced} unreachable matrix entries replaced by "
              f"straight-line km x {args.road_factor}")

    args.out.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(summaries)
    table.to_csv(args.out / "map_summary.csv", index=False)
    pd.concat(seats).to_csv(args.out / "seat_summary_all.csv", index=False)
    info = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}
    info["assigned"] = [str(a) for a in args.assigned]
    info["distance_source"] = dist.source
    info["nonfinite_matrix_entries_replaced"] = dist.nonfinite_replaced
    info["solver"] = (f"Gurobi via PuLP {pulp.__version__}" if args.solver == "gurobi"
                      else f"CBC via PuLP {pulp.__version__}")
    (args.out / "run_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    cols = ["map", "max_km", "booths", "extra_booths", "capacity_util", "voter_person_km",
            "mean_voter_km", "max_voter_km", "evm_km", "objective", "seats_optimal",
            "max_mip_gap", "runtime_s"]
    print(f"\nDistances: {dist.source}")
    print(table[cols].to_string(index=False))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
