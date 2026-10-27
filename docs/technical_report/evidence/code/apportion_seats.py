"""Allocate Assembly constituencies (ACs) to districts by largest remainder.

Each district gets seats in proportion to its population (Hamilton / largest
remainder method), with at least --min-seats per district. A district whose
quota falls below the minimum is fixed at the minimum and the remaining seats
are re-apportioned among the others. Ties in the remainder go to the larger
population, then to the district name.

Input CSV: one row per *current* district with columns ``district`` and
``population`` (other columns, such as ``pc11_district_id``, are kept). For
Maharashtra, data/maharashtra_district_population_2011.csv already lists the
36 current districts; Thane and Palghar are 2011 Census figures for the
talukas each district has held since 2014 (see
data/district_crosswalk_2011_to_current.csv).

With --reference (columns ``district`` and ``seats``), the allocation is
compared with that one, for example the 2008 Delimitation Order
(data/maharashtra_ac_2008.csv). The 2008 order used Census 2001 populations,
so a 2011-based allocation is expected to differ in a few districts.

Example:

    python apportion_seats.py --populations data/maharashtra_district_population_2011.csv \\
        --reference data/maharashtra_ac_2008.csv --out data/ac_allocation_2011.csv
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd


def largest_remainder(populations: dict[str, int], seats: int,
                      min_seats: int = 1) -> dict[str, int]:
    names = list(populations)
    if seats < min_seats * len(names):
        raise ValueError(f"{seats} seats cannot give {len(names)} districts "
                         f"at least {min_seats} each")
    fixed: dict[str, int] = {}
    while True:
        free = [n for n in names if n not in fixed]
        left = seats - sum(fixed.values())
        total = sum(populations[n] for n in free)
        quota = {n: left * populations[n] / total for n in free}
        below = [n for n in free if quota[n] < min_seats]
        if not below:
            break
        for n in below:
            fixed[n] = min_seats
    alloc = {n: math.floor(q) for n, q in quota.items()}
    order = sorted(free, key=lambda n: (-(quota[n] - alloc[n]), -populations[n], n))
    for n in order[:left - sum(alloc.values())]:
        alloc[n] += 1
    alloc.update(fixed)
    return {n: alloc[n] for n in names}


def _read(path: Path, need: set[str]) -> pd.DataFrame:
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")
    table = pd.read_csv(path)
    table.columns = [c.strip().lower() for c in table.columns]
    missing = need - set(table.columns)
    if missing:
        raise SystemExit(f"{path}: missing columns {sorted(missing)}; found {list(table.columns)}")
    table["district"] = table["district"].astype(str).str.strip()
    dupes = table["district"][table["district"].duplicated()].tolist()
    if dupes:
        raise SystemExit(f"{path}: duplicate districts {dupes}")
    return table


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--populations", type=Path, required=True,
                    help="CSV with district,population (one row per current district)")
    ap.add_argument("--seats", type=int, default=288, help="total ACs (default 288)")
    ap.add_argument("--min-seats", type=int, default=1, help="minimum ACs per district (default 1)")
    ap.add_argument("--reference", type=Path, default=None,
                    help="CSV with district,seats to compare against, e.g. the 2008 allocation")
    ap.add_argument("--out", type=Path, default=None, help="write the allocation CSV here")
    args = ap.parse_args(argv)

    table = _read(args.populations, {"district", "population"})
    if (table["population"] <= 0).any():
        raise SystemExit("Every district needs a positive population")
    pops = dict(zip(table["district"], table["population"].astype(int)))
    alloc = largest_remainder(pops, args.seats, args.min_seats)

    total = sum(pops.values())
    table["quota"] = table["population"] * args.seats / total
    table["seats"] = table["district"].map(alloc)
    table["pop_per_ac"] = (table["population"] / table["seats"]).round().astype(int)
    table["vs_state_avg_pct"] = (100 * (table["pop_per_ac"] / (total / args.seats) - 1)).round(1)
    cols = ["district", "population", "quota", "seats", "pop_per_ac", "vs_state_avg_pct"]

    if args.reference is not None:
        ref = _read(args.reference, {"district", "seats"})
        unknown = sorted(set(ref["district"]) ^ set(table["district"]))
        if unknown:
            raise SystemExit(f"District names differ between the files: {unknown}")
        table["seats_reference"] = table["district"].map(dict(zip(ref["district"], ref["seats"].astype(int))))
        table["diff"] = table["seats"] - table["seats_reference"]
        cols += ["seats_reference", "diff"]

    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(table[cols].to_string(index=False, formatters={"quota": "{:.3f}".format}))
    print(f"\n{len(table)} districts, population {total:,}, {args.seats} ACs, "
          f"state average {total / args.seats:,.0f} per AC")
    if args.reference is not None:
        changed = table[table["diff"] != 0]
        print(f"Reference total {int(table['seats_reference'].sum())} ACs; "
              f"{len(changed)} districts differ, {int(changed['diff'].abs().sum()) // 2} seats move")
        for _, r in changed.iterrows():
            print(f"  {r['district']}: {r['seats_reference']} -> {r['seats']} ({r['diff']:+d})")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        table.drop(columns=[c for c in ("diff",) if c in table]).to_csv(args.out, index=False)
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
