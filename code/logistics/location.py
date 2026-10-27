"""Polling stations and the dispatch centre of each Assembly seat, chosen together.

For every AC of a map, a two-echelon capacitated facility-location MIP (Gurobi):

    min  C_PS Σ y_s + C_SITE Σ o_s + C_WALK Σ w_is x_is + C_TRIP Σ k_ds u_ds
    s.t. Σ_s x_is = e_i                    every elector served
         Σ_i x_is ≤ CAP y_s                stations of CAP electors (1,200 since 2025)
         y_s ≤ B o_s                       at most B stations in one premises
         x_is = 0 if w_is > MAX_WALK       walk limit (own hamlet always allowed)
         Σ_d z_d = 1                       one dispatch centre for the seat
         Σ_d u_ds = y_s,  u_ds ≤ B z_d     each station is supplied from the open centre
         y integer; o, z binary; x, u ≥ 0

w_is = walk inside hamlet i (demand.py) + walking-network km from i to site s. k_ds = road
km from centre d to site s. C_TRIP prices the four legs a polling party and its EVMs travel
(out and back on the eve, out and back after the poll); routing.py replaces this star
estimate with real routes.

Strategies, all on the same demand and distances:
  * ``joint``      — the MIP above; the dispatch centre is a decision.
  * ``hq``         — the MIP with the centre fixed at the AC's largest town (the usual practice).
  * ``median``     — the MIP with the centre fixed at the electors' 1-median (the old model).
  * ``village``    — no optimisation: ⌈electors/CAP⌉ stations at each village's main hamlet,
                     every hamlet of the village votes there (the rule of thumb in practice).

Usage:
    python -m logistics.location --district sangli --strategy joint --cap 1200 --max-walk-km 2
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .common import CODE, DISTRICTS, read_geojson, run_dir

os.environ.setdefault("GRB_LICENSE_FILE", "/home/shivam/Downloads/gurobi.lic")

C_PS = 25_000.0     # ₹ per polling station: party of 4, training, furniture, EVM share
C_SITE = 5_000.0    # ₹ per premises used: assured minimum facilities, signage, ramp
C_WALK = 10.0       # ₹ per elector per one-way walking km (time value × turnout)
C_TRIP = 120.0      # ₹ per station per road km from the centre (4 legs × ₹30/km)
B_MAX = 12          # stations in one premises
NET_LIMIT_KM = 6.0  # walking matrix is kept up to this many km


def map_assignment(district: str, map_name: str) -> pd.DataFrame:
    """unit_id -> seat label for the official map or an optimizer run."""
    if map_name == "official_2008":
        return None
    base = CODE / ("sangli_road/runs" if district == "sangli" else f"pilots/{district}/runs") / map_name
    feats = read_geojson(base / "assigned_units.geojson")["features"]
    return pd.DataFrame([{"unit_id": f["properties"]["id"], "seat": int(f["properties"]["constituency"])} for f in feats])


def load_demand(district: str, map_name: str) -> pd.DataFrame:
    pts = pd.read_csv(run_dir(district) / "demand_points.csv")
    a = map_assignment(district, map_name)
    if a is None:
        pts["seat"] = pts["ac08_no"]
    else:
        pts = pts.drop(columns=[c for c in ("seat",) if c in pts]).merge(a, on="unit_id", how="left")
        if pts["seat"].isna().any():
            raise SystemExit(f"{pts['seat'].isna().sum()} demand points have no seat in {map_name}")
        pts["seat"] = pts["seat"].astype(int)
    return pts


def depot_candidates(seat: pd.DataFrame, max_n: int = 14) -> np.ndarray:
    """Row positions of plausible dispatch centres: the main point of every town, the largest
    hamlets, and the electors' 1-median is added by the caller."""
    s = seat.reset_index(drop=True)
    towns = s[s["kind"].isin(["TOWN", "WARD", "APPROX_WARD", "GRID_CELL"])].sort_values("electors_2024", ascending=False)
    town_main = towns.drop_duplicates("unit_id").index.tolist()
    big = s.sort_values("electors_2024", ascending=False).index.tolist()
    out = []
    for k in town_main[: max_n // 2] + big:
        if k not in out:
            out.append(k)
        if len(out) >= max_n:
            break
    return np.array(out, int)


def matrices(district: str, map_name: str, seat_id: int, seat: pd.DataFrame, net) -> dict:
    """Walking km among the seat's points and road km/min from depot candidates; cached."""
    cache = run_dir(district) / "matrices" / map_name
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"seat_{seat_id}.npz"
    pids = seat["pid"].to_numpy()
    if f.exists():
        z = np.load(f, allow_pickle=True)
        if list(z["pids"]) == list(pids):
            return {k: z[k] for k in z.files}
    lon, lat = seat["lon"].to_numpy(), seat["lat"].to_numpy()
    walk = net.walk_km(lon, lat, lon, lat, limit_km=NET_LIMIT_KM)
    np.fill_diagonal(walk, 0.0)
    e = seat["electors_2024"].to_numpy()
    walk_fin = np.where(np.isfinite(walk), walk, 1e3)
    median = int(np.argmin(e @ walk_fin))
    cand = depot_candidates(seat)
    if median not in cand:
        cand = np.r_[cand, median]
    # road from each candidate centre to every point
    dmin, dkm = net.drive(lon[cand], lat[cand], lon, lat)
    out = {"pids": pids, "walk": walk.astype(np.float32), "cand": cand, "median": median,
           "drive_min": dmin.astype(np.float32), "drive_km": dkm.astype(np.float32)}
    np.savez_compressed(f, **out)
    return out


def solve_seat(seat: pd.DataFrame, M: dict, strategy: str, cap: int, max_walk: float, electors_col: str,
               time_limit: float, mip_gap: float, plan_intra: bool = True) -> dict:
    import gurobipy as gp
    from gurobipy import GRB

    s = seat.reset_index(drop=True)
    n = len(s)
    e = s[electors_col].to_numpy(float)
    intra = s["intra_km"].to_numpy(float)
    walk = M["walk"].astype(float)
    w = intra[:, None] + walk                      # home -> hamlet core -> site
    w[np.arange(n), np.arange(n)] = intra
    # plan_intra=False plans as the old model did (the walk inside a hamlet counts as 0) but
    # every result below is still measured on the true walk w
    wp = w if plan_intra else np.where(np.isfinite(walk), walk, np.inf)
    cand = M["cand"]
    dkm = M["drive_km"].astype(float)
    dkm[~np.isfinite(dkm)] = 1e3

    if strategy == "hq":
        towns = s[s["kind"].isin(["TOWN", "WARD", "APPROX_WARD", "GRID_CELL"])]
        if len(towns):
            biggest_unit = towns.groupby("unit_id")[electors_col].sum().idxmax()
            hq = int(s[s["unit_id"] == biggest_unit][electors_col].idxmax())
        else:
            hq = int(s[electors_col].idxmax())
        fixed = int(np.flatnonzero(cand == hq)[0]) if hq in cand else None
        if fixed is None:  # not among cached candidates: fall back to the largest point
            fixed = int(np.argmax(e[cand]))
    elif strategy == "median":
        fixed = int(np.flatnonzero(cand == int(M["median"]))[0])
    else:
        fixed = None

    t0 = time.time()
    if strategy == "village":
        return village_rule(s, e, w, dkm, cand, int(np.flatnonzero(cand == int(M["median"]))[0]), cap)

    allowed = [np.flatnonzero((wp[i] <= max_walk) | (np.arange(n) == i)) for i in range(n)]
    m = gp.Model()
    m.Params.OutputFlag = 0
    m.Params.TimeLimit = time_limit
    m.Params.MIPGap = mip_gap
    m.Params.Threads = 4
    y = m.addVars(n, vtype=GRB.INTEGER, lb=0, ub=B_MAX, name="y")
    o = m.addVars(n, vtype=GRB.BINARY, name="o")
    pairs = [(i, int(j)) for i in range(n) for j in allowed[i]]
    x = m.addVars(pairs, lb=0.0, name="x")
    D = range(len(cand))
    if fixed is None:
        z = m.addVars(D, vtype=GRB.BINARY, name="z")
        u = m.addVars(D, range(n), lb=0.0, name="u")
        m.addConstr(z.sum() == 1)
        m.addConstrs(u.sum("*", j) == y[j] for j in range(n))
        m.addConstrs(u[d, j] <= B_MAX * z[d] for d in D for j in range(n))
        trip = gp.quicksum(C_TRIP * dkm[d, j] * u[d, j] for d in D for j in range(n))
    else:
        trip = gp.quicksum(C_TRIP * dkm[fixed, j] * y[j] for j in range(n))
    by_site = {}
    for i, j in pairs:
        by_site.setdefault(j, []).append(i)
    m.addConstrs(gp.quicksum(x[i, int(j)] for j in allowed[i]) == e[i] for i in range(n))
    m.addConstrs(gp.quicksum(x[i, j] for i in by_site.get(j, [])) <= cap * y[j] for j in range(n))
    m.addConstrs(y[j] <= B_MAX * o[j] for j in range(n))
    m.addConstrs(x[i, j] <= e[i] * o[j] for i, j in pairs)  # valid tightening: no flow to a closed site
    m.addConstr(y.sum() >= math.ceil(e.sum() / cap - 1e-9))
    m.setObjective(C_PS * y.sum() + C_SITE * o.sum()
                   + C_WALK * gp.quicksum(wp[i, j] * x[i, j] for i, j in pairs) + trip, GRB.MINIMIZE)
    m.optimize()
    if m.SolCount == 0:
        raise RuntimeError(f"no solution for seat {s['seat'].iloc[0]} (status {m.Status})")
    Y = np.array([round(y[j].X) for j in range(n)])
    X = {(i, j): x[i, j].X for i, j in pairs if x[i, j].X > 1e-6}
    if fixed is None:
        d_open = int(max(D, key=lambda d: z[d].X))
    else:
        d_open = fixed
    res = summarise(s, e, w, dkm, cand, d_open, Y, X, cap)
    res.update({"status": "optimal" if m.Status == GRB.OPTIMAL else f"gurobi_{m.Status}",
                "mip_gap": float(m.MIPGap), "runtime_s": round(time.time() - t0, 2), "objective": float(m.ObjVal)})
    return res


def village_rule(s, e, w, dkm, cand, d_open, cap) -> dict:
    """⌈electors/cap⌉ stations at each village's largest hamlet; all its hamlets vote there."""
    n = len(s)
    Y = np.zeros(n, int)
    X = {}
    w = w.copy()
    far = ~np.isfinite(w)
    if far.any():  # hamlets beyond the walking matrix's range: straight line x 1.3 + walk inside
        from .common import haversine_km
        lon, lat = s["lon"].to_numpy(), s["lat"].to_numpy()
        ii, jj = np.nonzero(far)
        w[ii, jj] = haversine_km(lon[ii], lat[ii], lon[jj], lat[jj]) * 1.3 + s["intra_km"].to_numpy()[ii]
    for unit, g in s.groupby("unit_id"):
        main = int(g["electors_2024"].idxmax())
        total = float(e[g.index].sum())
        Y[main] += math.ceil(total / cap - 1e-9) if total > 0 else 0
        for i in g.index:
            if e[i] > 0:
                X[(int(i), main)] = e[i]
    res = summarise(s, e, w, dkm, cand, d_open, Y, X, cap)
    res.update({"status": "rule", "mip_gap": 0.0, "runtime_s": 0.0, "objective": res["total_cost"]})
    return res


def summarise(s, e, w, dkm, cand, d_open, Y, X, cap) -> dict:
    walk = np.array([w[i, j] for (i, j) in X])
    flow = np.array(list(X.values()))
    site_load = np.zeros(len(s))
    for (i, j), f in X.items():
        site_load[j] += f
    stations = int(Y.sum())
    trip_km = float((dkm[d_open] * Y).sum())
    order = np.argsort(walk)
    cum = np.cumsum(flow[order]) / flow.sum()
    p = lambda q: float(walk[order][np.searchsorted(cum, q)])
    cost_ps, cost_site = C_PS * stations, C_SITE * int((Y > 0).sum())
    cost_walk, cost_trip = C_WALK * float(walk @ flow), C_TRIP * trip_km
    return {
        "seat": int(s["seat"].iloc[0]), "points": len(s), "electors": round(float(e.sum())),
        "stations": stations, "min_stations": math.ceil(e.sum() / cap - 1e-9), "premises": int((Y > 0).sum()),
        "mean_electors_per_station": round(float(e.sum() / max(1, stations))),
        "util": round(float(e.sum() / (cap * max(1, stations))), 4),
        "mean_walk_km": round(float(walk @ flow / flow.sum()), 3), "p50_walk_km": round(p(0.5), 3),
        "p90_walk_km": round(p(0.9), 3), "max_walk_km": round(float(walk.max()), 3),
        "pct_over_2km": round(100 * float(flow[walk > 2.0].sum() / flow.sum()), 2),
        "pct_over_3km": round(100 * float(flow[walk > 3.0].sum() / flow.sum()), 2),
        "depot_pid": s["pid"].iloc[cand[d_open]], "depot_name": s["name"].iloc[cand[d_open]],
        "depot_lon": float(s["lon"].iloc[cand[d_open]]), "depot_lat": float(s["lat"].iloc[cand[d_open]]),
        "star_trip_km": round(trip_km, 1), "mean_depot_km": round(trip_km / max(1, stations), 2),
        "cost_stations": cost_ps, "cost_premises": cost_site, "cost_walk": round(cost_walk), "cost_trip": round(cost_trip),
        "total_cost": round(cost_ps + cost_site + cost_walk + cost_trip),
        "_sites": [{"pid": s["pid"].iloc[j], "stations": int(Y[j]), "load": round(float(site_load[j]), 1),
                    "lon": float(s["lon"].iloc[j]), "lat": float(s["lat"].iloc[j]), "name": s["name"].iloc[j],
                    "kind": s["kind"].iloc[j], "drive_km_from_depot": round(float(dkm[d_open, j]), 2)}
                   for j in np.flatnonzero(Y > 0)],
        "_assign": [{"from": s["pid"].iloc[i], "to": s["pid"].iloc[j], "electors": round(f, 1), "walk_km": round(float(w[i, j]), 3)}
                    for (i, j), f in X.items()],
    }


def run(district: str, map_name: str, strategy: str, cap: int, max_walk: float, electors_col: str,
        tag: str | None, time_limit: float, mip_gap: float, seats: list[int] | None = None,
        plan_intra: bool = True) -> pd.DataFrame:
    from .network import Network
    pts = load_demand(district, map_name)
    net = Network(district)
    tag = tag or f"{map_name}__{strategy}__cap{cap}__walk{max_walk:g}__{electors_col.split('_')[-1]}"
    out = run_dir(district) / "location" / tag
    out.mkdir(parents=True, exist_ok=True)
    rows, sites, assign = [], [], []
    for seat_id, seat in pts.groupby("seat", sort=True):
        if seats and seat_id not in seats:
            continue
        seat = seat[seat[electors_col] > 0].reset_index(drop=True)
        M = matrices(district, map_name, int(seat_id), seat, net)
        r = solve_seat(seat, M, strategy, cap, max_walk, electors_col, time_limit, mip_gap, plan_intra)
        sites += [{**x, "seat": int(seat_id)} for x in r.pop("_sites")]
        assign += [{**x, "seat": int(seat_id)} for x in r.pop("_assign")]
        rows.append(r)
        print(f"  {district} {tag} seat {seat_id}: {r['stations']} stations at {r['premises']} premises, "
              f"mean walk {r['mean_walk_km']:.2f} km, {r['pct_over_2km']}% > 2 km, centre {r['depot_name']}, "
              f"{r['status']} gap {r['mip_gap']:.4f} {r['runtime_s']} s", flush=True)
    df = pd.DataFrame(rows)
    df.insert(0, "district", district)
    for k, v in {"map": map_name, "strategy": strategy, "cap": cap, "max_walk_km": max_walk, "electors_basis": electors_col,
                 "plan_intra": plan_intra}.items():
        df[k] = v
    df.to_csv(out / "seats.csv", index=False)
    pd.DataFrame(sites).to_csv(out / "sites.csv", index=False)
    pd.DataFrame(assign).to_csv(out / "assignments.csv", index=False)
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", required=True, choices=sorted(DISTRICTS))
    ap.add_argument("--map", default="official_2008")
    ap.add_argument("--strategy", default="joint", choices=["joint", "hq", "median", "village"])
    ap.add_argument("--cap", type=int, default=1200)
    ap.add_argument("--max-walk-km", type=float, default=2.0)
    ap.add_argument("--electors", default="electors_2024", choices=["electors_2024", "electors_2011"])
    ap.add_argument("--tag")
    ap.add_argument("--time-limit", type=float, default=120)
    ap.add_argument("--gap", type=float, default=0.005)
    ap.add_argument("--seats", type=int, nargs="*")
    a = ap.parse_args()
    df = run(a.district, a.map, a.strategy, a.cap, a.max_walk_km, a.electors, a.tag, a.time_limit, a.gap, a.seats)
    cols = ["stations", "premises", "electors", "star_trip_km", "total_cost"]
    print(df[cols].sum().to_string())
    print("mean walk km", round(float(np.average(df["mean_walk_km"], weights=df["electors"])), 3),
          "| % over 2 km", round(float(np.average(df["pct_over_2km"], weights=df["electors"])), 2))


if __name__ == "__main__":
    main()
