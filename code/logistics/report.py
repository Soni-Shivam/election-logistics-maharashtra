"""Tidy result tables for the consulting dashboard, from every run under logistics_runs/.

Writes to ``logistics_runs/summary/report/``:
  scenarios.csv          one row per district x location scenario (stations, access, fleet, ₹/elector, queues)
  queues.csv             polling-day results by district, cap (1,200 / 1,500) and time per voter
  turnout_curve.csv      simulated share of the day's votes cast by each hour vs the reported 2024 curve
  reserves.csv           EVM failure downtime and waits by spare-machine policy
  cap_value.csv          what the 1,200 cap costs and buys, per district (₹ per voter-hour of waiting saved)
  validation.csv         model vs real 2024 figures (from validate.py)
  lok_sabha.csv          official vs balanced Lok Sabha seats on 2024 rolls
  reservation.csv        SC/ST share of reserved Assembly seats, official vs optimised maps

Usage:
    python -m logistics.report
"""

from __future__ import annotations

import json
import shutil

import pandas as pd

from .common import ELECTORS, RUNS
from .scenarios import collect

DISTRICTS = ["sangli", "satara", "nashik", "pune"]
HOURS = ["09", "11", "13", "15", "17"]
REPORTED = {"09": 0.1001, "11": 0.2746, "13": 0.4872, "15": 0.6893, "17": 0.8815}
VALUE_OF_TIME = 50.0  # ₹ per voter-hour, as in the location model's walking cost


def sims() -> pd.DataFrame:
    rows = []
    for d in DISTRICTS:
        for f in sorted((RUNS / d / "location").glob("*/simulation/summary_*.json")):
            j = json.loads(f.read_text())
            rows.append({"district": d, "scenario": f.parts[-3], **{k: v for k, v in j.items() if not isinstance(v, dict)},
                         **{f"cum_{k}": v for k, v in j["cum_votes_by"].items()}})
    return pd.DataFrame(rows)


def main() -> None:
    out = RUNS / "summary" / "report"
    out.mkdir(parents=True, exist_ok=True)
    sc = collect(DISTRICTS)
    sc["District"] = sc["district"].str.title()
    sc.to_csv(out / "scenarios.csv", index=False)

    s = sims()
    s["District"] = s["district"].str.title()
    s["cap"] = s["scenario"].map({"base": 1200, "cap1500": 1500})
    q = s[s["label"].isin(["svc40", "svc50", "svc60", "svc40_nobalk", "svc50_nobalk"])].copy()
    q["service_s"] = q["label"].str.extract(r"svc(\d+)").astype(int)
    q["voters_leave"] = ~q["label"].str.endswith("nobalk")
    q[["District", "cap", "service_s", "voters_leave", "mean_electors_per_station", "voter_mean_wait_min",
       "share_voters_wait_over_30", "share_voters_wait_over_60", "share_stations_queue_at_18", "votes_after_18_share",
       "p95_overrun_min", "balked_share"]].sort_values(["District", "service_s", "voters_leave", "cap"]).to_csv(out / "queues.csv", index=False)

    tc = []
    for _, r in s[(s["scenario"] == "base") & s["label"].isin(["svc40", "svc50"])].iterrows():
        for h in HOURS:
            tc.append({"District": r["District"], "hour": f"{h}:00", "service_s": int(r["label"][3:5]),
                       "simulated_pct": round(100 * r[f"cum_{h}"], 2), "reported_pct": round(100 * REPORTED[h], 2)})
    pd.DataFrame(tc).to_csv(out / "turnout_curve.csv", index=False)

    rv = s[(s["scenario"] == "base") & s["label"].isin(["nofail", "reserve10", "svc50", "reserve30"])].copy()
    rv["policy"] = rv["label"].map({"nofail": "No failures", "reserve10": "10% spares", "svc50": "20% spares", "reserve30": "30% spares"})
    rv[["District", "policy", "stations_with_failure", "mean_downtime_min_if_failed", "voter_mean_wait_min",
        "share_voters_wait_over_30", "balked_share"]].to_csv(out / "reserves.csv", index=False)

    e = pd.read_csv(ELECTORS)
    cv = []
    for d in DISTRICTS:
        a = sc[(sc["district"] == d) & (sc["scenario"] == "base")]
        b = sc[(sc["district"] == d) & (sc["scenario"] == "cap1500")]
        w12 = s[(s["district"] == d) & (s["scenario"] == "base") & (s["label"] == "svc40_nobalk")]
        w15 = s[(s["district"] == d) & (s["scenario"] == "cap1500") & (s["label"] == "svc40_nobalk")]
        if a.empty or b.empty or w12.empty or w15.empty or "total_cost_routed" not in a:
            continue
        g = e[e["district_2008"] == d.title()]
        voters = float(g["votes_2024"].sum())
        extra = float(a["total_cost_routed"].iloc[0] - b["total_cost_routed"].iloc[0])
        saved_h = voters * float(w15["voter_mean_wait_min"].iloc[0] - w12["voter_mean_wait_min"].iloc[0]) / 60
        cv.append({"District": d.title(), "voters_2024": int(voters),
                   "stations_1200": int(a["stations"].iloc[0]), "stations_1500": int(b["stations"].iloc[0]),
                   "extra_stations_pct": round(100 * (a["stations"].iloc[0] / b["stations"].iloc[0] - 1), 1),
                   "extra_cost_rs": round(extra), "extra_cost_per_elector": round(extra / float(a["electors"].iloc[0]), 2),
                   "wait_1200_min": float(w12["voter_mean_wait_min"].iloc[0]), "wait_1500_min": float(w15["voter_mean_wait_min"].iloc[0]),
                   "voter_hours_saved": round(saved_h), "rs_per_voter_hour_saved": round(extra / saved_h, 1),
                   "value_of_time_saved_rs": round(saved_h * VALUE_OF_TIME)})
    pd.DataFrame(cv).to_csv(out / "cap_value.csv", index=False)

    for src, name in ((RUNS / "summary/validation.csv", "validation.csv"),
                      (RUNS / "representation/lok_sabha_summary.csv", "lok_sabha.csv"),
                      (RUNS / "representation/assembly_reservation.csv", "reservation.csv")):
        if src.exists():
            shutil.copy(src, out / name)
    for f in sorted(out.glob("*.csv")):
        print(f.name, len(pd.read_csv(f)), "rows")
    print(pd.read_csv(out / "cap_value.csv").to_string(index=False))


if __name__ == "__main__":
    main()
