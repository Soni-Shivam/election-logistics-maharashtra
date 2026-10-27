"""Build a Census-PCA-style table for one state from SHRUG 2.2 downloads.

The official Census 2011 PCA workbooks (censusindia.gov.in) are not always
reachable. SHRUG carries the same 2011 PCA total population for every
inhabited village (pc11r_shrid_key.csv) and town (pc11u_shrid_key.csv), plus
district and subdistrict totals. This script writes those as the rows
prepare_district_inputs.py reads from a PCA file: columns State, District,
Subdistt, Town/Village, Level, Name, TRU, TOT_P, with Level DISTRICT,
SUB-DISTRICT, VILLAGE and TOWN.

Villages that have a polygon in village_modified.gpkg but no row in the keys
are uninhabited in 2011 and get population 0 (the Census lists them with
TOT_P 0). Names come from the SHRUG polygon layers. Checked for Sangli:
every village and town population equals the official PCA workbook.

Example:

    python shrug_to_pca.py --state-id 27 --shrug ../raw_data --out ../raw_data/pca_from_shrug/pca_27.csv
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

FILES = {
    "villages": "shrug-pc-keys-csv/pc11r_shrid_key.csv",
    "towns": "shrug-pc-keys-csv/pc11u_shrid_key.csv",
    "subdistricts": "shrug-pca11-csv/pc11_pca_clean_pc11subdist.csv",
    "districts": "shrug-pca11-csv/pc11_pca_clean_pc11dist.csv",
    "village_poly": "shrug-pc11-village-poly-gpkg/village_modified.gpkg",
    "subdistrict_poly": "shrug-pc11subdist-poly-gpkg/subdistrict.gpkg",
    "district_poly": "shrug-pc11dist-poly-gpkg/district.gpkg",
}


def fail(message: str) -> None:
    raise SystemExit(f"shrug_to_pca: {message}")


def read_csv(path: Path, state: str) -> pd.DataFrame:
    table = pd.read_csv(path, dtype=str)
    return table[table["pc11_state_id"] == state]


def query(path: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state-id", required=True, help="PC11 state code, e.g. 27 (Maharashtra)")
    ap.add_argument("--shrug", type=Path, required=True,
                    help="folder holding the extracted SHRUG downloads, one sub-folder per zip")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    state = str(args.state_id).zfill(2)
    paths = {k: args.shrug / v for k, v in FILES.items()}
    missing = [f"  {p}" for p in paths.values() if not p.is_file()]
    if missing:
        fail("missing SHRUG files (download from https://www.devdatalab.org/shrug_download/ "
             "and unzip each into a folder named like the zip):\n" + "\n".join(missing))

    names = {(d, s, c): n for d, s, c, n in query(paths["village_poly"],
             "SELECT pc11_district_id, pc11_subdistrict_id, pc11_town_village_id, town_village_name "
             "FROM village_modified WHERE pc11_state_id=?", (state,))}
    sub_names = {(d, s): n for d, s, n in query(paths["subdistrict_poly"],
                 "SELECT pc11_district_id, pc11_subdistrict_id, subdistrict_name FROM subdistrict "
                 "WHERE pc11_state_id=?", (state,))}
    dist_names = dict(query(paths["district_poly"],
                      "SELECT pc11_district_id, district_name FROM district WHERE pc11_state_id=?", (state,)))

    rows = []
    def add(district, subdist, code, level, name, tru, pop):
        rows.append({"State": state, "District": district, "Subdistt": subdist, "Town/Village": code,
                     "Level": level, "Name": name, "TRU": tru, "TOT_P": int(round(float(pop)))})

    for _, r in read_csv(paths["districts"], state).iterrows():
        add(r.pc11_district_id, "00000", "000000", "DISTRICT",
            dist_names.get(r.pc11_district_id, r.pc11_district_id), "Total", r.pc11_pca_tot_p)
    for _, r in read_csv(paths["subdistricts"], state).iterrows():
        add(r.pc11_district_id, r.pc11_subdistrict_id, "000000", "SUB-DISTRICT",
            sub_names.get((r.pc11_district_id, r.pc11_subdistrict_id), r.pc11_subdistrict_id),
            "Total", r.pc11_pca_tot_p)
    seen = set()
    for kind, table, code_col, tru in (("VILLAGE", "villages", "pc11_village_id", "Rural"),
                                       ("TOWN", "towns", "pc11_town_id", "Urban")):
        keys = read_csv(paths[table], state)
        dupes = keys.duplicated(["pc11_district_id", "pc11_subdistrict_id", code_col])
        if dupes.any():
            fail(f"duplicate {kind.lower()} keys in {paths[table]}: "
                 f"{keys[dupes][[code_col]].values.ravel().tolist()[:5]}")
        for _, r in keys.iterrows():
            key = (r.pc11_district_id, r.pc11_subdistrict_id, r[code_col])
            seen.add(key)
            add(*key, kind, names.get(key, r[code_col]), tru, r.pc11_pca_tot_p)
    uninhabited = sorted(k for k in names if k not in seen)
    for key in uninhabited:
        add(*key, "VILLAGE", names[key], "Rural", 0)

    table = pd.DataFrame(rows)
    # Consistency: settlements add up to each district total.
    settlements = table[table.Level.isin(["VILLAGE", "TOWN"])].groupby("District").TOT_P.sum()
    totals = table[table.Level == "DISTRICT"].set_index("District").TOT_P
    bad = {d: (int(settlements.get(d, 0)), int(t)) for d, t in totals.items() if settlements.get(d, 0) != t}
    if bad:
        fail(f"village+town populations do not add up to district totals: {bad}")
    no_poly = sorted(seen - set(names))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    print(f"Wrote {args.out}: {len(totals)} districts, {int(totals.sum()):,} people, "
          f"{(table.Level == 'VILLAGE').sum():,} villages ({len(uninhabited):,} uninhabited), "
          f"{(table.Level == 'TOWN').sum():,} towns")
    if no_poly:
        print(f"Note: {len(no_poly)} settlements have population but no polygon: {no_poly[:10]}")


if __name__ == "__main__":
    sys.exit(main())
