"""Getting polling parties and EVMs out and back: dispatch routes and sectors for one seat.

Reads a location run (``location/<tag>/seats.csv`` and ``sites.csv``) and, per seat:

1. **Dispatch routing (CVRPTW, OR-Tools).** On the eve of the poll, parties collect their
   EVMs at the dispatch centre and leave by ``DEPART``; every party must be at its station by
   ``ARRIVE_BY``. A stop is one premises; a premises with more parties than a bus seats is
   split. Vehicles: buses (``BUS_PARTIES`` parties) and jeeps (one party), each with a
   two-day hire charge and a per-km rate; the solver picks the mix. Service time at a stop is
   ``STOP_MIN`` + ``PARTY_MIN`` per party. After the poll the same routes run in reverse
   to the receipt centre, so the fleet covers both trips and route km count twice.
2. **Sectors (capacitated p-median, Gurobi).** Premises are grouped into sectors of at most
   ``SECTOR_MAX`` (ECI handbook: 10–12 polling locations per sector officer); each sector
   officer is based at the premises that minimises driving time to the others. The
   response time base → premises feeds the polling-day failure simulation.

Usage:
    python -m logistics.routing --district sangli --tag official_2008__joint__cap1200__walk2__2024
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time

import numpy as np
import pandas as pd

from .common import DISTRICTS, run_dir

os.environ.setdefault("GRB_LICENSE_FILE", "/home/shivam/Downloads/gurobi.lic")

BUS_PARTIES = 6        # polling parties (4 staff + police + kit) per bus
DEPART_MIN = 13 * 60   # leave the dispatch centre by 13:00 on the eve of the poll
ARRIVE_BY_MIN = 18 * 60  # all parties in place by 18:00
STOP_MIN = 10
PARTY_MIN = 3
VEHICLES = {  # two-day hire (dispatch day + poll day/collection), per-km running cost
    "bus": {"capacity": BUS_PARTIES, "hire": 9_000, "per_km": 35.0},
    "jeep": {"capacity": 1, "hire": 3_500, "per_km": 14.0},
}
SECTOR_MAX = 12
SECTOR_TARGET = 11


def stops_for(sites: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, s in sites.iterrows():
        left = int(s["stations"])
        part = 0
        while left > 0:
            k = min(left, BUS_PARTIES)
            rows.append({"pid": s["pid"], "part": part, "parties": k, "lon": s["lon"], "lat": s["lat"], "name": s["name"]})
            left -= k
            part += 1
    return pd.DataFrame(rows)


def solve_routes(dmin: np.ndarray, dkm: np.ndarray, parties: np.ndarray, time_limit_s: int) -> dict:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    n = len(parties)  # node 0 is the depot
    total = int(parties.sum())
    n_bus = math.ceil(total / BUS_PARTIES) + 2
    n_jeep = min(n - 1, 40)
    types = ["bus"] * n_bus + ["jeep"] * n_jeep
    manager = pywrapcp.RoutingIndexManager(n, len(types), 0)
    routing = pywrapcp.RoutingModel(manager)
    tmin = np.rint(dmin).astype(int)
    kmx = np.rint(dkm * 10).astype(int)  # 0.1 km units
    service = np.where(np.arange(n) == 0, 0, STOP_MIN + PARTY_MIN * parties).astype(int)

    cost_cb = {}
    for t, spec in VEHICLES.items():
        rate = spec["per_km"] * 2 / 10  # ₹ per 0.1 km, out and back after the poll

        def cb(i, j, rate=rate):
            return int(kmx[manager.IndexToNode(i), manager.IndexToNode(j)] * rate)
        cost_cb[t] = routing.RegisterTransitCallback(cb)
    for v, t in enumerate(types):
        routing.SetArcCostEvaluatorOfVehicle(cost_cb[t], v)
        routing.SetFixedCostOfVehicle(VEHICLES[t]["hire"], v)

    def time_cb(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return int(tmin[a, b] + service[b])
    t_idx = routing.RegisterTransitCallback(time_cb)
    horizon = ARRIVE_BY_MIN - DEPART_MIN
    routing.AddDimension(t_idx, 0, horizon, True, "time")

    def dem_cb(i):
        return int(parties[manager.IndexToNode(i)])
    d_idx = routing.RegisterUnaryTransitCallback(dem_cb)
    routing.AddDimensionWithVehicleCapacity(d_idx, 0, [VEHICLES[t]["capacity"] for t in types], True, "load")
    # a stop that cannot be reached in time even directly is still served (jeep, late) at a penalty
    for node in range(1, n):
        routing.AddDisjunction([manager.NodeToIndex(node)], 10_000_000)

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds = time_limit_s
    sol = routing.SolveWithParameters(p)
    if sol is None:
        raise RuntimeError("routing found no solution")
    tdim = routing.GetDimensionOrDie("time")
    routes, dropped = [], []
    for node in range(1, n):
        if sol.Value(routing.NextVar(manager.NodeToIndex(node))) == manager.NodeToIndex(node):
            dropped.append(node)
    for v, t in enumerate(types):
        idx = routing.Start(v)
        if routing.IsEnd(sol.Value(routing.NextVar(idx))):
            continue
        seq, km = [], 0.0
        while not routing.IsEnd(idx):
            nxt = sol.Value(routing.NextVar(idx))
            a, b = manager.IndexToNode(idx), manager.IndexToNode(nxt)
            km += dkm[a, b]
            if b != 0:
                seq.append(b)
            idx = nxt
        last_arrival = sol.Value(tdim.CumulVar(routing.End(v))) - int(tmin[seq[-1], 0])
        routes.append({"vehicle": t, "stops": seq, "parties": int(parties[seq].sum()), "km_one_way_loop": round(km, 2),
                       "last_arrival_min": int(last_arrival)})
    return {"routes": routes, "dropped": dropped}


def sectors(dmin: np.ndarray, time_limit: float = 60) -> tuple[np.ndarray, np.ndarray]:
    """Assign premises to sector bases: returns (base index per premises, base set)."""
    import gurobipy as gp
    from gurobipy import GRB
    n = len(dmin)
    k = max(1, math.ceil(n / SECTOR_TARGET))
    if n <= SECTOR_MAX:
        c = int(np.argmin(dmin.sum(axis=1)))
        return np.full(n, c), np.array([c])
    reach = np.quantile(dmin[np.isfinite(dmin)], 0.35)
    m = gp.Model()
    m.Params.OutputFlag = 0
    m.Params.TimeLimit = time_limit
    m.Params.MIPGap = 0.01
    open_ = m.addVars(n, vtype=GRB.BINARY)
    pairs = [(i, j) for i in range(n) for j in range(n) if dmin[j, i] <= max(reach, 45) or i == j]
    x = m.addVars(pairs, vtype=GRB.BINARY)
    by_i, by_j = {}, {}
    for i, j in pairs:
        by_i.setdefault(i, []).append(j)
        by_j.setdefault(j, []).append(i)
    m.addConstrs(gp.quicksum(x[i, j] for j in by_i[i]) == 1 for i in range(n))
    m.addConstrs(gp.quicksum(x[i, j] for i in by_j.get(j, [])) <= SECTOR_MAX * open_[j] for j in range(n))
    m.addConstr(open_.sum() == k)
    m.setObjective(gp.quicksum(dmin[j, i] * x[i, j] for i, j in pairs), GRB.MINIMIZE)
    m.optimize()
    if m.SolCount == 0:  # too tight a reach: fall back to nearest of k spread bases
        bases = np.argsort(dmin.sum(axis=1))[:k]
        return bases[np.argmin(dmin[bases], axis=0)], bases
    base = np.array([next(j for j in by_i[i] if x[i, j].X > 0.5) for i in range(n)])
    return base, np.unique(base)


def run(district: str, tag: str, time_limit_s: int, seats_only: list[int] | None = None) -> pd.DataFrame:
    from .network import Network
    loc = run_dir(district) / "location" / tag
    seats = pd.read_csv(loc / "seats.csv")
    sites = pd.read_csv(loc / "sites.csv")
    net = Network(district)
    out = loc / "routing"
    out.mkdir(exist_ok=True)
    rows, route_rows, sector_rows = [], [], []
    for _, seat in seats.iterrows():
        sid = int(seat["seat"])
        if seats_only and sid not in seats_only:
            continue
        t0 = time.time()
        ss = sites[sites["seat"] == sid].reset_index(drop=True)
        st = stops_for(ss)
        lon = np.r_[seat["depot_lon"], st["lon"].to_numpy()]
        lat = np.r_[seat["depot_lat"], st["lat"].to_numpy()]
        local = net.local(lon, lat)
        dmin, dkm = local.drive(lon, lat, lon, lat)
        bad = ~np.isfinite(dmin)
        dmin[bad], dkm[bad] = 600, 300
        parties = np.r_[0, st["parties"].to_numpy()]
        R = solve_routes(dmin, dkm, parties, time_limit_s)
        # sectors on premises (not split stops)
        prem = np.r_[[0], [1 + int(st.index[(st["pid"] == p)][0]) for p in ss["pid"]]]
        pm = dmin[np.ix_(prem[1:], prem[1:])]
        base, bases = sectors(pm)
        resp = pm[base, np.arange(len(base))]
        used = pd.Series([r["vehicle"] for r in R["routes"]]).value_counts()
        km_total = sum(r["km_one_way_loop"] for r in R["routes"]) * 2
        hire = sum(VEHICLES[r["vehicle"]]["hire"] for r in R["routes"])
        run_cost = sum(VEHICLES[r["vehicle"]]["per_km"] * r["km_one_way_loop"] * 2 for r in R["routes"])
        star_km = float(seat["star_trip_km"]) * 4  # every station travelled alone, 4 legs
        rows.append({
            "seat": sid, "stations": int(ss["stations"].sum()), "premises": len(ss), "stops": len(st),
            "buses": int(used.get("bus", 0)), "jeeps": int(used.get("jeep", 0)),
            "vehicle_km": round(km_total, 1), "star_km_4legs": round(star_km, 1),
            "km_saving_vs_star_pct": round(100 * (1 - km_total / star_km), 1) if star_km else None,
            "hire_cost": hire, "running_cost": round(run_cost), "transport_cost": round(hire + run_cost),
            "latest_arrival": f"{(DEPART_MIN + max(r['last_arrival_min'] for r in R['routes'])) // 60:02d}:"
                              f"{(DEPART_MIN + max(r['last_arrival_min'] for r in R['routes'])) % 60:02d}",
            "mean_route_min": round(float(np.mean([r["last_arrival_min"] for r in R["routes"]])), 1),
            "dropped_stops": len(R["dropped"]),
            "sectors": len(bases), "mean_sector_size": round(len(ss) / len(bases), 1),
            "mean_response_min": round(float(resp.mean()), 1), "p90_response_min": round(float(np.quantile(resp, 0.9)), 1),
            "max_response_min": round(float(resp.max()), 1), "runtime_s": round(time.time() - t0, 1),
        })
        for r_i, r in enumerate(R["routes"]):
            route_rows.append({"seat": sid, "route": r_i, "vehicle": r["vehicle"], "parties": r["parties"],
                               "km_loop": r["km_one_way_loop"], "arrival_last_min": r["last_arrival_min"],
                               "stops": ";".join(f"{st['pid'].iloc[k - 1]}" for k in r["stops"])})
        for i, p in enumerate(ss["pid"]):
            sector_rows.append({"seat": sid, "pid": p, "sector_base": ss["pid"].iloc[base[i]],
                                "response_min": round(float(resp[i]), 1), "stations": int(ss["stations"].iloc[i])})
        x = rows[-1]
        print(f"  seat {sid}: {x['stations']} stations -> {x['buses']} buses + {x['jeeps']} jeeps, {x['vehicle_km']} km "
              f"(star {x['star_km_4legs']} km, {x['km_saving_vs_star_pct']}% less), last arrival {x['latest_arrival']}, "
              f"{x['sectors']} sectors, response p90 {x['p90_response_min']} min, {x['runtime_s']} s", flush=True)
    df = pd.DataFrame(rows)
    df.insert(0, "district", district)
    df["tag"] = tag
    df.to_csv(out / "seats.csv", index=False)
    pd.DataFrame(route_rows).to_csv(out / "routes.csv", index=False)
    pd.DataFrame(sector_rows).to_csv(out / "sectors.csv", index=False)
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", required=True, choices=sorted(DISTRICTS))
    ap.add_argument("--tag", required=True)
    ap.add_argument("--time-limit", type=int, default=30)
    ap.add_argument("--seats", type=int, nargs="*")
    a = ap.parse_args()
    df = run(a.district, a.tag, a.time_limit, a.seats)
    print(df[["stations", "buses", "jeeps", "vehicle_km", "star_km_4legs", "transport_cost"]].sum().to_string())


if __name__ == "__main__":
    main()
