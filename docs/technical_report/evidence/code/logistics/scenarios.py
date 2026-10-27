"""Run the full chain (location -> routing -> polling-day simulation) for every scenario.

Location scenarios (all on 2024 electors, ECI cap 1,200, 2 km walk, joint centre unless noted):

  base           joint dispatch centre + stations, official 2008 seats
  cap1500        station cap 1,500 (the 2009-2024 norm)
  walk3          3 km walk limit
  depot_hq       dispatch centre fixed at the seat's largest town (practice)
  depot_median   dispatch centre fixed at the electors' 1-median (old model)
  village_rule   ⌈electors/1,200⌉ stations at each village's main hamlet (rule of thumb)
  no_intra       planned as if the walk inside a hamlet were 0 (old model), measured truly
  census2011     demand = Census 2011 × 0.70 instead of 2024 rolls
  ac_calibrated  every official seat scaled to its own 2024 roll (city seats included)
  best_map       our best optimised Assembly map instead of the official seats

Simulation variants run on ``base`` (and ``cap1500`` for the queue comparison).

Usage:
    python -m logistics.scenarios --districts sangli satara --stage all
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

from .common import CODE, RUNS, run_dir

BEST_MAP = {"sangli": "taluka_s303", "pune": "balanced_s714", "nashik": "taluka_s714", "satara": "taluka_s303"}
BASE = {"map": "official_2008", "strategy": "joint", "cap": 1200, "walk": 2.0, "electors": "electors_2024", "plan_intra": True}
LOCATION = {
    "base": {},
    "cap1500": {"cap": 1500},
    "walk3": {"walk": 3.0},
    "depot_hq": {"strategy": "hq"},
    "depot_median": {"strategy": "median"},
    "village_rule": {"strategy": "village"},
    "no_intra": {"plan_intra": False},
    "census2011": {"electors": "electors_2011"},
    "ac_calibrated": {"electors": "electors_2024_ac"},
    "best_map": {"map": None},
}
SIMULATION = {
    "base": [("svc50", ["--service-s", "50"]), ("svc40", ["--service-s", "40"]), ("svc60", ["--service-s", "60"]),
             ("svc50_nobalk", ["--service-s", "50", "--no-balk"]), ("reserve10", ["--reserve", "0.10"]),
             ("reserve30", ["--reserve", "0.30"]), ("nofail", ["--p-fail", "0"])],
    "cap1500": [("svc50", ["--service-s", "50"]), ("svc60", ["--service-s", "60"])],
}
PY = sys.executable


def location_job(district: str, scen: str) -> str:
    from .location import run
    p = {**BASE, **LOCATION[scen]}
    if p["map"] is None:
        p["map"] = BEST_MAP[district]
    run(district, p["map"], p["strategy"], p["cap"], p["walk"], p["electors"], scen, time_limit=120, mip_gap=0.005,
        plan_intra=p["plan_intra"])
    return f"{district}/{scen}"


def cmd(args: list[str]) -> str:
    r = subprocess.run([PY, "-m", *args], cwd=CODE, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(args) + "\n" + r.stderr[-2000:])
    return " ".join(args[:5])


def run_location(districts, scens, workers):
    # the base run fills each district's distance cache before the others read it
    if "base" in scens:
        with ProcessPoolExecutor(workers) as ex:
            for f in as_completed([ex.submit(location_job, d, "base") for d in districts]):
                print("location done", f.result(), flush=True)
    rest = [s for s in scens if s != "base"]
    with ProcessPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(location_job, d, s) for d in districts for s in rest]):
            print("location done", f.result(), flush=True)


def run_routing(districts, scens, workers):
    jobs = [["logistics.routing", "--district", d, "--tag", s, "--time-limit", "30"] for d in districts for s in scens]
    with ProcessPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(cmd, j) for j in jobs]):
            print("routing done", f.result(), flush=True)


def run_simulation(districts, workers, reps):
    jobs = []
    for d in districts:
        for tag, variants in SIMULATION.items():
            for label, extra in variants:
                jobs.append(["logistics.simulate", "--district", d, "--tag", tag, "--reps", str(reps), "--label", label, *extra])
    with ProcessPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(cmd, j) for j in jobs]):
            print("simulation done", f.result(), flush=True)


def collect(districts) -> pd.DataFrame:
    rows = []
    for d in districts:
        for scen in LOCATION:
            loc = run_dir(d) / "location" / scen
            if not (loc / "seats.csv").exists():
                continue
            s = pd.read_csv(loc / "seats.csv")
            e = s["electors"]
            row = {"district": d, "scenario": scen, "seats": len(s), "electors": int(e.sum()),
                   "stations": int(s["stations"].sum()), "premises": int(s["premises"].sum()),
                   "electors_per_station": round(e.sum() / s["stations"].sum()),
                   "mean_walk_km": round(float((s["mean_walk_km"] * e).sum() / e.sum()), 3),
                   "p90_walk_km_seat_avg": round(float((s["p90_walk_km"] * e).sum() / e.sum()), 3),
                   "pct_over_2km": round(float((s["pct_over_2km"] * e).sum() / e.sum()), 2),
                   "star_trip_km": round(float(s["star_trip_km"].sum())),
                   "cost_stations": int(s["cost_stations"].sum()), "cost_walk": int(s["cost_walk"].sum()),
                   "cost_trip_star": int(s["cost_trip"].sum()), "location_cost": int(s["total_cost"].sum())}
            r = loc / "routing" / "seats.csv"
            if r.exists():
                rt = pd.read_csv(r)
                row.update({"buses": int(rt["buses"].sum()), "jeeps": int(rt["jeeps"].sum()),
                            "vehicle_km": round(float(rt["vehicle_km"].sum())), "transport_cost": int(rt["transport_cost"].sum()),
                            "latest_arrival": rt["latest_arrival"].max(), "sectors": int(rt["sectors"].sum()),
                            "p90_response_min": round(float(rt["p90_response_min"].median()), 1)})
                row["total_cost_routed"] = row["cost_stations"] + int(s["cost_premises"].sum()) + row["cost_walk"] + row["transport_cost"]
                row["rs_per_elector"] = round(row["total_cost_routed"] / row["electors"], 2)
            sim = loc / "simulation" / "summary_svc50.json"
            if sim.exists():
                j = json.loads(sim.read_text())
                row.update({k: j[k] for k in ("voter_mean_wait_min", "share_voters_wait_over_30", "share_stations_queue_at_18",
                                              "votes_after_18_share", "p95_overrun_min", "balked_share")})
            rows.append(row)
    df = pd.DataFrame(rows)
    out = RUNS / "summary"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "scenarios.csv", index=False)
    sims = []
    for d in districts:
        for tag in SIMULATION:
            for f in sorted((run_dir(d) / "location" / tag / "simulation").glob("summary_*.json")):
                j = json.loads(f.read_text())
                sims.append({"district": d, "scenario": tag, **{k: v for k, v in j.items() if not isinstance(v, dict)},
                             **{f"cum_{k}": v for k, v in j["cum_votes_by"].items()}})
    pd.DataFrame(sims).to_csv(out / "simulations.csv", index=False)
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--districts", nargs="+", default=["sangli", "satara", "nashik", "pune"])
    ap.add_argument("--scenarios", nargs="+", default=list(LOCATION))
    ap.add_argument("--stage", choices=["location", "routing", "simulation", "collect", "all"], default="all")
    ap.add_argument("--location-workers", type=int, default=2)
    ap.add_argument("--routing-workers", type=int, default=8)
    ap.add_argument("--reps", type=int, default=30)
    a = ap.parse_args()
    if a.stage in ("location", "all"):
        run_location(a.districts, a.scenarios, a.location_workers)
    if a.stage in ("routing", "all"):
        run_routing(a.districts, a.scenarios, a.routing_workers)
    if a.stage in ("simulation", "all"):
        run_simulation(a.districts, a.routing_workers, a.reps)
    df = collect(a.districts)
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
