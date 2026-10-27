"""Who the seats represent: SC/ST reservation for Assembly maps, and Lok Sabha seats statewide.

**Assembly (four districts).** Each unit's SC and ST population comes from the Census 2011 PCA
(SHRUG shrid table): villages and towns by their Census code, city wards and grid cells by
their town's share. For every map (official 2008 and each optimizer run) the district keeps
its official number of SC and ST seats, allotted by the Delimitation Act's rule: ST seats to
the ACs with the largest ST share, then SC seats to the remaining ACs with the largest SC share.
For the official map we also check whether the seats actually reserved in 2008 are the ones
this rule picks on 2011 data.

**Lok Sabha (statewide).** The 288 official ACs (DataMeet polygons) carry their official PC.
Official PCs are scored on 2024 electors; ``--group`` builds balanced PCs of 6 contiguous
ACs on 2024 electors with ``group_lok_sabha.py``; both get reservation by the same rule
(5 SC, 4 ST PCs in Maharashtra) on SC/ST population from SHRUG's AC table.

Usage:
    python -m logistics.representation assembly
    python -m logistics.representation lok-sabha [--group]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import numpy as np
import pandas as pd

from .common import CODE, DISTRICTS, ELECTORS, ROOT, RUNS, read_geojson

SHRUG = ROOT / "raw_data"
OUT = RUNS / "representation"
# Reserved Lok Sabha seats in Maharashtra under the 2008 order (DataMeet's names miss some tags)
OFFICIAL_PC_RESERVED = {"AMRAVATI": "SC", "RAMTEK": "SC", "SHIRDI": "SC", "LATUR": "SC", "SOLAPUR": "SC",
                        "NANDURBAR": "ST", "GADCHIROLI-CHIMUR": "ST", "DINDORI": "ST", "PALGHAR": "ST"}


def unit_scst(district: str) -> pd.DataFrame:
    """SC and ST population per unit, from the Census code in settlement_id."""
    feats = read_geojson(DISTRICTS[district]["units"])["features"]
    units = pd.DataFrame([f["properties"] for f in feats])[["id", "population", "kind", "settlement_id"]]
    keys = []
    for kind, col in (("r", "pc11_village_id"), ("u", "pc11_town_id")):
        k = pd.read_csv(SHRUG / f"shrug-pc-keys-csv/pc11{kind}_shrid_key.csv", dtype=str,
                        usecols=["shrid2", "pc11_state_id", "pc11_subdistrict_id", col])
        k = k[k["pc11_state_id"] == "27"]
        k["settlement_id"] = k["pc11_subdistrict_id"] + "-" + k[col]
        keys.append(k[["shrid2", "settlement_id"]])
    keys = pd.concat(keys).drop_duplicates("settlement_id")
    pca = pd.read_csv(SHRUG / "shrug-pca11-csv/pc11_pca_clean_shrid.csv", dtype={"shrid2": str},
                      usecols=["shrid2", "pc11_pca_tot_p", "pc11_pca_p_sc", "pc11_pca_p_st"])
    sh = keys.merge(pca, on="shrid2")
    sh["sc_share"] = sh["pc11_pca_p_sc"] / sh["pc11_pca_tot_p"]
    sh["st_share"] = sh["pc11_pca_p_st"] / sh["pc11_pca_tot_p"]
    units = units.merge(sh[["settlement_id", "sc_share", "st_share"]], on="settlement_id", how="left")
    units["matched"] = units["sc_share"].notna()
    units[["sc_share", "st_share"]] = units[["sc_share", "st_share"]].fillna(0)
    units["sc"] = units["population"] * units["sc_share"]
    units["st"] = units["population"] * units["st_share"]
    return units


def allot(df: pd.DataFrame, n_sc: int, n_st: int) -> pd.Series:
    """ST seats to the largest ST shares, then SC seats to the largest SC shares of the rest."""
    res = pd.Series("", index=df.index)
    st = df["st_pct"].sort_values(ascending=False).index[:n_st]
    res[st] = "ST"
    rest = df.drop(index=st)
    sc = rest["sc_pct"].sort_values(ascending=False).index[:n_sc]
    res[sc] = "SC"
    return res


def assembly() -> pd.DataFrame:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, checks = [], []
    for key, spec in DISTRICTS.items():
        u = unit_scst(key)
        print(f"{spec['name']}: {u.loc[u['population'] > 0, 'matched'].mean():.1%} of populated units matched to Census SC/ST")
        official = pd.DataFrame([f["properties"] for f in read_geojson(spec["official"])["features"]])[["id", "ac08_no", "ac08_name"]]
        names = official.drop_duplicates("ac08_no").set_index("ac08_no")["ac08_name"]
        n_sc = sum("(SC)" in n for n in names)
        n_st = sum("(ST)" in n for n in names)
        maps = {"official_2008": official.rename(columns={"ac08_no": "seat"})[["id", "seat"]]}
        runs = CODE / ("sangli_road/runs" if key == "sangli" else f"pilots/{key}/runs")
        for r in sorted(runs.glob("*_s*")):
            if (r / "assigned_units.geojson").exists():
                f = read_geojson(r / "assigned_units.geojson")["features"]
                maps[r.name] = pd.DataFrame([{"id": x["properties"]["id"], "seat": x["properties"]["constituency"]} for x in f])
        for name, m in maps.items():
            g = u.merge(m, on="id").groupby("seat")[["population", "sc", "st"]].sum()
            g["sc_pct"] = 100 * g["sc"] / g["population"]
            g["st_pct"] = 100 * g["st"] / g["population"]
            g["rule"] = allot(g, n_sc, n_st)
            if name == "official_2008":
                g["actual"] = [("SC" if "(SC)" in names[s] else "ST" if "(ST)" in names[s] else "") for s in g.index]
                g["name"] = [names[s] for s in g.index]
                for s, r in g.iterrows():
                    checks.append({"district": spec["name"], "ac08_no": s, "name": r["name"], "actual": r["actual"],
                                   "rule_2011": r["rule"], "sc_pct": round(r["sc_pct"], 1), "st_pct": round(r["st_pct"], 1),
                                   "sc_rank": int(g["sc_pct"].rank(ascending=False)[s]),
                                   "st_rank": int(g["st_pct"].rank(ascending=False)[s])})
            lab = g["actual"] if name == "official_2008" else g["rule"]
            for kind in ("SC", "ST"):
                sel = lab == kind
                if sel.any():
                    col = f"{kind.lower()}_pct"
                    rows.append({"district": spec["name"], "map": name, "kind": kind, "seats": int(sel.sum()),
                                 "reserved_mean_share_pct": round(float(g.loc[sel, col].mean()), 2),
                                 "best_possible_share_pct": round(float(g[col].sort_values(ascending=False)[:int(sel.sum())].mean()), 2),
                                 "district_share_pct": round(float(100 * g[kind.lower()].sum() / g["population"].sum()), 2),
                                 "max_seat_share_pct": round(float(g[col].max()), 2)})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "assembly_reservation.csv", index=False)
    pd.DataFrame(checks).to_csv(OUT / "official_reservation_check.csv", index=False)
    return df


def statewide_acs() -> "gpd.GeoDataFrame":
    import geopandas as gpd
    g = gpd.read_file(ROOT / "data_raw/datameet/India_AC.shp")
    g = g[g["ST_CODE"] == 27].dissolve(by="AC_NO", aggfunc="first").reset_index()
    e = pd.read_csv(ELECTORS)
    g = g.merge(e[["ac_no", "district_2008", "electors_2009", "electors_2024", "votes_2024"]], left_on="AC_NO", right_on="ac_no")
    pca = pd.read_csv(SHRUG / "shrug-pca11-csv/pc11_pca_clean_con08.csv", dtype={"ac08_id": str},
                      usecols=["ac08_id", "pc11_pca_tot_p", "pc11_pca_p_sc", "pc11_pca_p_st"])
    pca["AC_NO"] = pca["ac08_id"].str[-3:].astype(int)
    pca = pca[pca["ac08_id"].str.startswith("2008-27-")]
    g = g.merge(pca, on="AC_NO", how="left")
    return g


def lok_sabha(group: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    g = statewide_acs()
    missing = g["pc11_pca_tot_p"].isna().sum()
    g["pc_official"] = g["PC_NO"].astype(int)
    g["pc_name"] = g["PC_NAME"].str.strip()
    plans = {"official": g[["AC_NO", "pc_official"]].rename(columns={"pc_official": "pc"})}
    if group:
        # one constituencies.geojson per 2008 district, population = 2024 electors
        src = OUT / "pc_input"
        src.mkdir(exist_ok=True)
        args = []
        for d, part in g.groupby("district_2008"):
            p = part[["AC_NO", "electors_2024", "geometry"]].rename(columns={"AC_NO": "constituency", "electors_2024": "population"})
            f = src / f"{d.replace(' ', '_')}.geojson"
            p.to_file(f, driver="GeoJSON")
            args += ["--district", f"{d}={f}"]
        cmd = [sys.executable, str(CODE / "group_lok_sabha.py"), *args, "--out", str(OUT / "pc_balanced"),
               "--balance", "0.25", "--search-seconds", "600", "--time-limit", "900"]
        print("running", " ".join(cmd[:4]), "...")
        subprocess.run(cmd, check=True)
    bal = OUT / "pc_balanced" / "pc_assignment.csv"
    if bal.exists():
        b = pd.read_csv(bal)
        acol = next(c for c in b.columns if c.lower() in ("ac", "constituency", "ac_label"))
        pcol = next(c for c in b.columns if c.lower() in ("pc", "pc_id", "pc_number"))
        plans["balanced_2024"] = b.rename(columns={acol: "AC_NO", pcol: "pc"})[["AC_NO", "pc"]]
    summary, pcs = [], []
    for name, plan in plans.items():
        m = g.drop(columns=[c for c in ("pc",) if c in g]).merge(plan, on="AC_NO")
        agg = m.groupby("pc").agg(acs=("AC_NO", "count"), electors_2024=("electors_2024", "sum"),
                                  electors_2009=("electors_2009", "sum"), pop=("pc11_pca_tot_p", "sum"),
                                  sc=("pc11_pca_p_sc", "sum"), st=("pc11_pca_p_st", "sum"),
                                  name=("pc_name", lambda s: s.mode().iat[0]))
        agg["sc_pct"] = 100 * agg["sc"] / agg["pop"]
        agg["st_pct"] = 100 * agg["st"] / agg["pop"]
        agg["rule"] = allot(agg, 5, 4)
        if name == "official":
            base = agg["name"].str.replace(r"\s*\((SC|ST)\)", "", regex=True).str.strip().str.upper()
            agg["actual"] = base.map(OFFICIAL_PC_RESERVED).fillna("")
        avg = agg["electors_2024"].mean()
        dev = agg["electors_2024"] / avg - 1
        lab = agg["actual"] if name == "official" else agg["rule"]
        summary.append({
            "plan": name, "pcs": len(agg), "largest_2024": int(agg["electors_2024"].max()), "smallest_2024": int(agg["electors_2024"].min()),
            "size_ratio_2024": round(float(agg["electors_2024"].max() / agg["electors_2024"].min()), 3),
            "outside_10pct_2024": int((dev.abs() > 0.10).sum()), "outside_20pct_2024": int((dev.abs() > 0.20).sum()),
            "mean_abs_dev_pct_2024": round(float(100 * dev.abs().mean()), 2),
            "sc_reserved_mean_sc_pct": round(float(agg.loc[lab == "SC", "sc_pct"].mean()), 2),
            "st_reserved_mean_st_pct": round(float(agg.loc[lab == "ST", "st_pct"].mean()), 2),
            "best_sc_mean_pct": round(float(agg["sc_pct"].sort_values(ascending=False)[:5].mean()), 2),
            "best_st_mean_pct": round(float(agg["st_pct"].sort_values(ascending=False)[:4].mean()), 2),
        })
        agg["plan"] = name
        pcs.append(agg.reset_index())
    pd.DataFrame(summary).to_csv(OUT / "lok_sabha_summary.csv", index=False)
    pd.concat(pcs).to_csv(OUT / "lok_sabha_pcs.csv", index=False)
    print(pd.DataFrame(summary).T.to_string())
    if missing:
        print(f"note: {missing} ACs have no SHRUG SC/ST row")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("assembly")
    ls = sub.add_parser("lok-sabha")
    ls.add_argument("--group", action="store_true", help="also build balanced PCs on 2024 electors")
    a = ap.parse_args()
    if a.cmd == "assembly":
        print(assembly().to_string(index=False))
    else:
        lok_sabha(a.group)


if __name__ == "__main__":
    main()
