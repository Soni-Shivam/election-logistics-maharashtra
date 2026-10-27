"""Polling day at every station: queues, the 18:00 backlog and EVM failures (Monte Carlo).

For each polling station of a location run:

* **Voters** = Binomial(electors, turnout), turnout from the 2024 result of the official AC(s)
  its electors belong to. Arrival times follow the 2024 Maharashtra cumulative-turnout curve
  (09:00 ≈10%, 11:00 27.5%, 13:00 48.7%, 15:00 68.9%, 17:00 88.2%, 18:00 100% of the day's voters),
  sampled by inverse CDF.
* **Service**: one voting compartment, FIFO; time per voter lognormal (mean ``--service-s``,
  CV ``SERVICE_CV``). Everyone in line at 18:00 still votes (ECI token rule).
* **Balking** (optional): an arriving voter who sees a wait W leaves with probability
  1 − exp(−max(0, W − ``BALK_TOLERANCE``)/``BALK_SCALE``).
* **EVM failure**: with probability ``P_FAIL`` (any of BU, CU or VVPAT; Assam 2024 phase-1
  replacement rates 1.6%, 3.3%, 5.5%) a failure strikes at a uniform time. The sector officer
  handles failures in the sector one after another, driving base → station (routing.py) and
  taking ``SETUP_MIN`` to replace and re-seal; with the sector's reserve sets used up
  (``--reserve`` share of its stations), the replacement comes from the dispatch centre.
  Voting stops until it is done.

Usage:
    python -m logistics.simulate --district sangli --tag <location tag> --reps 50
"""

from __future__ import annotations

import argparse
import json
import math

import numpy as np
import pandas as pd
from numba import njit

from .common import ELECTORS, run_dir

POLL_MIN = 660                       # 07:00-18:00
CURVE_T = np.array([0, 120, 240, 360, 480, 600, 660], float)
CURVE_F = np.array([0, 6.61, 18.14, 32.18, 45.53, 58.22, 66.05]) / 66.05
SERVICE_CV = 0.45
BALK_TOLERANCE = 20.0
BALK_SCALE = 60.0
P_UNIT = {"BU": 0.016, "CU": 0.033, "VVPAT": 0.055}
P_FAIL = 1 - np.prod([1 - p for p in P_UNIT.values()])
SETUP_MIN = 20.0
DEPOT_KMH = 30.0


@njit(cache=True)
def run_queue(arr, serv, f0, f1, balk, tol, scale, u):
    """Single-server FIFO with one blocked window [f0, f1) and optional balking.

    Returns waits (−1 for a voter who left), finish time of the last vote."""
    n = arr.shape[0]
    waits = np.empty(n)
    free = 0.0
    for k in range(n):
        a = arr[k]
        start = max(a, free)
        if start < f1 and start + serv[k] > f0:
            start = max(start, f1)
        w = start - a
        if balk and w > tol and u[k] < 1.0 - math.exp(-(w - tol) / scale):
            waits[k] = -1.0
            continue
        waits[k] = w
        free = start + serv[k]
    return waits, free


def station_table(district: str, tag: str) -> pd.DataFrame:
    loc = run_dir(district) / "location" / tag
    sites = pd.read_csv(loc / "sites.csv")
    assign = pd.read_csv(loc / "assignments.csv")
    pts = pd.read_csv(run_dir(district) / "demand_points.csv")[["pid", "ac08_no"]]
    e = pd.read_csv(ELECTORS)
    e["turnout_2024"] = e["votes_2024"] / e["electors_2024"]
    assign = assign.merge(pts.rename(columns={"pid": "from"}), on="from").merge(
        e[["ac_no", "turnout_2024"]].rename(columns={"ac_no": "ac08_no"}), on="ac08_no")
    # electors-weighted turnout of the seats each station's voters come from (plain mean if all
    # its assigned flows rounded to 0 electors)
    assign["w"] = assign["turnout_2024"] * assign["electors"]
    g = assign.groupby("to")
    num, den, plain = g["w"].sum(), g["electors"].sum(), g["turnout_2024"].mean()
    t = (num / den.where(den > 0)).fillna(plain)
    sites["turnout"] = sites["pid"].map(t).fillna(float(assign["turnout_2024"].mean()))
    sec = pd.read_csv(loc / "routing" / "sectors.csv")[["pid", "sector_base", "response_min"]]
    sites = sites.merge(sec, on="pid", how="left")
    rows = []
    for _, s in sites.iterrows():
        for k in range(int(s["stations"])):
            rows.append({"seat": int(s["seat"]), "pid": s["pid"], "station": k, "electors": s["load"] / s["stations"],
                         "turnout": s["turnout"], "sector": f"{s['seat']}|{s['sector_base']}",
                         "response_min": s["response_min"], "depot_min": s["drive_km_from_depot"] / DEPOT_KMH * 60})
    return pd.DataFrame(rows)


def failure_windows(st: pd.DataFrame, rng, reserve: float, p_fail: float) -> tuple[np.ndarray, np.ndarray]:
    n = len(st)
    f0 = np.full(n, np.inf)
    f1 = np.full(n, np.inf)
    fails = rng.random(n) < p_fail
    t_fail = rng.uniform(0, POLL_MIN, n)
    for sector, g in st[fails].groupby("sector"):
        idx = g.index.to_numpy()
        idx = idx[np.argsort(t_fail[idx])]
        spares = math.ceil(reserve * (st["sector"] == sector).sum())
        officer_free = 0.0
        for j, i in enumerate(idx):
            if j < spares:
                go = max(t_fail[i], officer_free)
                done = go + st.at[i, "response_min"] + SETUP_MIN
                officer_free = done + st.at[i, "response_min"]  # drives back to base
            else:  # no spare left in the sector: fetched from the dispatch centre
                done = t_fail[i] + st.at[i, "depot_min"] + SETUP_MIN
            f0[i], f1[i] = t_fail[i], done
    return f0, f1


def simulate(st: pd.DataFrame, reps: int, service_s: float, balk: bool, reserve: float, p_fail: float, seed: int):
    rng = np.random.default_rng(seed)
    sigma = math.sqrt(math.log(1 + SERVICE_CV ** 2))
    mu = math.log(service_s / 60) - sigma ** 2 / 2
    grid = np.linspace(0, 1, 2001)
    inv_t = np.interp(grid, CURVE_F, CURVE_T)
    hours = np.array([120, 240, 360, 480, 600, 660, 720, 780], float)
    per_station, curve = [], np.zeros(len(hours))
    total_votes = 0
    for r in range(reps):
        f0, f1 = failure_windows(st, rng, reserve, p_fail)
        for i, s in enumerate(st.itertuples()):
            nv = rng.binomial(int(round(s.electors)), min(max(s.turnout, 0), 1))
            arr = np.sort(np.interp(rng.random(nv), grid, inv_t))
            serv = rng.lognormal(mu, sigma, nv)
            waits, last = run_queue(arr, serv, f0[i], f1[i], balk, BALK_TOLERANCE, BALK_SCALE, rng.random(nv))
            ok = waits >= 0
            done_t = arr[ok] + waits[ok] + serv[ok]
            curve += np.searchsorted(np.sort(done_t), hours)
            total_votes += ok.sum()
            w = waits[ok]
            per_station.append((r, i, nv, int(ok.sum()), float(w.mean()) if ok.any() else 0.0,
                                float(np.quantile(w, 0.95)) if ok.any() else 0.0, float(w.max()) if ok.any() else 0.0,
                                float((w > 30).mean()) if ok.any() else 0.0, float((w > 60).sum()), int((done_t > POLL_MIN).sum()),
                                max(0.0, last - POLL_MIN), float(f1[i] - f0[i]) if np.isfinite(f0[i]) else 0.0))
    cols = ["rep", "i", "voters", "voted", "mean_wait", "p95_wait", "max_wait", "share_wait_30", "n_wait_60",
            "votes_after_18", "overrun_min", "downtime_min"]
    df = pd.DataFrame(per_station, columns=cols)
    return df, curve / max(1, total_votes)


def summarise(st: pd.DataFrame, df: pd.DataFrame, curve: np.ndarray) -> dict:
    v = df["voted"].clip(lower=1)
    return {
        "stations": len(st), "reps": int(df["rep"].nunique()),
        "mean_electors_per_station": round(float(st["electors"].mean())),
        "voter_mean_wait_min": round(float(np.average(df["mean_wait"], weights=v)), 2),
        "share_voters_wait_over_30": round(float(np.average(df["share_wait_30"], weights=v)), 4),
        "share_voters_wait_over_60": round(float(df["n_wait_60"].sum() / df["voted"].sum()), 4),
        "station_p95_wait_mean": round(float(df["p95_wait"].mean()), 1),
        "share_stations_queue_at_18": round(float((df["votes_after_18"] > 0).mean()), 4),
        "votes_after_18_share": round(float(df["votes_after_18"].sum() / df["voted"].sum()), 4),
        "mean_overrun_min": round(float(df["overrun_min"].mean()), 1),
        "p95_overrun_min": round(float(df["overrun_min"].quantile(0.95)), 1),
        "balked_share": round(float(1 - df["voted"].sum() / df["voters"].sum()), 4),
        "stations_with_failure": round(float((df["downtime_min"] > 0).mean()), 4),
        "mean_downtime_min_if_failed": round(float(df.loc[df["downtime_min"] > 0, "downtime_min"].mean()), 1)
        if (df["downtime_min"] > 0).any() else 0.0,
        "cum_votes_by": {h: round(float(c), 4) for h, c in zip(["09", "11", "13", "15", "17", "18", "19", "20"], curve)},
        "reported_cum_by": {"09": 0.1001, "11": 0.2746, "13": 0.4872, "15": 0.6893, "17": 0.8815, "final": 1.0},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--service-s", type=float, default=50)
    ap.add_argument("--no-balk", action="store_true")
    ap.add_argument("--reserve", type=float, default=0.20)
    ap.add_argument("--p-fail", type=float, default=float(P_FAIL))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--label", default=None)
    a = ap.parse_args()
    st = station_table(a.district, a.tag)
    df, curve = simulate(st, a.reps, a.service_s, not a.no_balk, a.reserve, a.p_fail, a.seed)
    label = a.label or f"svc{a.service_s:g}_{'nobalk' if a.no_balk else 'balk'}_res{a.reserve:g}_pf{a.p_fail:.3f}"
    out = run_dir(a.district) / "location" / a.tag / "simulation"
    out.mkdir(exist_ok=True)
    summary = {"label": label, "service_s": a.service_s, "balk": not a.no_balk, "reserve": a.reserve, "p_fail": a.p_fail,
               **summarise(st, df, curve)}
    agg = df.groupby("i").mean(numeric_only=True).drop(columns="rep")
    pd.concat([st.reset_index(drop=True), agg.reset_index(drop=True)], axis=1).to_csv(out / f"stations_{label}.csv", index=False)
    (out / f"summary_{label}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
