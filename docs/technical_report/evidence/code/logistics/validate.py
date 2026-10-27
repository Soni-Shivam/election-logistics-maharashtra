"""Model vs the real 2024 Maharashtra election, on every figure we could find a public source for.

Reads the scenario outputs (``logistics_runs/<district>/location/<scenario>/...``) and
``data/validation_benchmarks.csv``; writes ``logistics_runs/summary/validation.csv``.

Checks:
  * polling stations, district and AC level (base on 2024 rolls; Census 2011 planning shown alongside);
  * electors per station;
  * buses for Pune's 11 urban ACs (PMC + PCMC) vs the 968 PMPML lent in Nov 2024;
  * simulated cumulative votes by hour vs the reported 2024 state curve.

Usage:
    python -m logistics.validate
"""

from __future__ import annotations

import json

import pandas as pd

from .common import CODE, RUNS, run_dir

PUNE_URBAN_ACS = list(range(205, 216))  # Chinchwad ... Kasba Peth: the PMC and PCMC seats


def seats(district: str, scenario: str) -> pd.DataFrame:
    f = run_dir(district) / "location" / scenario / "seats.csv"
    return pd.read_csv(f) if f.exists() else pd.DataFrame()


def routing(district: str, scenario: str) -> pd.DataFrame:
    f = run_dir(district) / "location" / scenario / "routing" / "seats.csv"
    return pd.read_csv(f) if f.exists() else pd.DataFrame()


def main() -> None:
    bench = pd.read_csv(CODE / "data/validation_benchmarks.csv")
    val = lambda m, scope: float(bench.loc[(bench["metric"] == m) & (bench["scope"] == scope), "value"].iloc[0])
    rows = []

    def add(check, scope, actual, model, census=None, note=""):
        rows.append({"check": check, "scope": scope, "actual_2024": actual, "model_2024_rolls": model,
                     "error_pct": round(100 * (model / actual - 1), 1) if actual and model is not None else None,
                     "model_census_2011": census,
                     "census_error_pct": round(100 * (census / actual - 1), 1) if actual and census else None,
                     "note": note})

    for d, scope in (("pune", "Pune district"), ("nashik", "Nashik district")):
        b, c = seats(d, "base"), seats(d, "census2011")
        if len(b):
            actual = val("polling_stations", scope)
            add("polling stations", scope, actual, int(b["stations"].sum()), int(c["stations"].sum()) if len(c) else None)
            add("electors per station", scope, round(b["electors"].sum() / actual), round(b["electors"].sum() / b["stations"].sum()),
                note="actual = 2024 electors / actual stations")
    for d, ac, scope, metric in (("nashik", 124, "Nashik East AC", "polling_stations"),
                                 ("pune", 213, "Hadapsar AC (roll parts)", "polling_stations")):
        b, c = seats(d, "base"), seats(d, "census2011")
        if len(b) and (b["seat"] == ac).any():
            add("polling stations", scope, val(metric, scope), int(b.loc[b["seat"] == ac, "stations"].iloc[0]),
                int(c.loc[c["seat"] == ac, "stations"].iloc[0]) if len(c) and (c["seat"] == ac).any() else None)
    r = routing("pune", "base")
    if len(r):
        u = r[r["seat"].isin(PUNE_URBAN_ACS)]
        add("buses, 11 urban Pune ACs", "PMPML lent for PMC + PCMC seats", val("buses_lent", "PMPML for 11 urban Pune ACs"),
            int(u["buses"].sum()), note=f"model also uses {int(u['jeeps'].sum())} jeeps; PMPML is one of several fleets")
    for d in ("sangli", "satara", "nashik", "pune"):
        f = run_dir(d) / "location" / "base" / "simulation" / "summary_svc50.json"
        if not f.exists():
            continue
        j = json.loads(f.read_text())
        for h, actual in j["reported_cum_by"].items():
            if h == "final":
                continue
            add(f"share of day's votes cast by {h}:00", f"{d.title()} (sim) vs Maharashtra (reported)", round(100 * actual, 1),
                round(100 * j["cum_votes_by"][h], 1))
    out = RUNS / "summary"
    out.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "validation.csv", index=False)
    with pd.option_context("display.width", 220, "display.max_colwidth", 60):
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
