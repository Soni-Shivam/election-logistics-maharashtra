"""Audit the official 2008 Assembly map against our optimized maps.

For Sangli, Pune, Nashik and Satara this compares the official 2008 constituencies
(rebuilt on our units by official_ac_map.py / official_district_map.py) with every
road-distance optimizer run, on:

  * seat-size spread: smallest/largest seat, largest/smallest ratio, mean and max
    deviation from the district target, seats outside +-10%, share of people living
    in those seats;
  * vote weight: target / seat population (1.0 = a fair vote, 0.5 = worth half);
  * taluka splits: talukas cut at all, and cuts beyond the population-implied minimum;
  * compactness (mean Polsby-Popper), road km to the seat centre, EVM km;
  * the common 35/30/20/15 score, recomputed from each map's normalized terms.

It also rebuilds every Maharashtra 2008 AC's population for 2001 (the base the
Delimitation Commission used) and 2011 from SHRUG's shrid -> AC fragment key, to show
whether the map was balanced when drawn and how far it drifted. SHRUG splits large towns
across ACs by intersection area, so city ACs carry an area-weighting error in this
statewide table; the four-district seat tables use our population-aware rebuild.

Example:
    python audit_official_2008.py --out analysis/official_audit
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SHRUG = ROOT.parent / "raw_data"
COMMON_WEIGHTS = {"population": 0.35, "distance": 0.30, "taluka_fragmentation": 0.20, "shape": 0.15}
BAND = 0.10

DISTRICTS = [
    {"key": "sangli", "name": "Sangli", "official": ROOT / "official_2008", "runs": ROOT / "sangli_road/runs",
     "units": ROOT / "sangli_input/sangli_units.geojson"},
    *({"key": k, "name": k.title(), "official": ROOT / f"pilots/{k}/official_2008", "runs": ROOT / f"pilots/{k}/runs",
       "units": ROOT / f"pilots/{k}/input/{k}_units.geojson"} for k in ("pune", "nashik", "satara")),
]


def taluka_populations(units_path: Path) -> dict[str, int]:
    pops: dict[str, int] = defaultdict(int)
    for f in json.loads(units_path.read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        pops[str(p["taluka"])] += int(p["population"])
    return pops


def map_metrics(score: dict, taluka_pop: dict[str, int]) -> dict:
    raw, seats = score["raw"], score["seats"]
    target = raw["population_target"]
    upper = raw["population_bounds"][1]
    pops = np.array([s["population"] for s in seats], float)
    dev = pops / target - 1
    out = np.abs(dev) > BAND + 1e-12
    weight = target / pops
    pieces = raw["taluka_fragments"]
    split = sum(n > 1 for n in pieces.values())
    needless = sum(max(0, pieces[t] - math.ceil(p / upper)) for t, p in taluka_pop.items() if t in pieces)
    common = sum(w * score["terms"][k] for k, w in COMMON_WEIGHTS.items())
    return {
        "seats": len(seats),
        "target": round(target),
        "smallest": int(pops.min()),
        "largest": int(pops.max()),
        "size_ratio": round(pops.max() / pops.min(), 3),
        "mean_abs_dev_pct": round(100 * np.abs(dev).mean(), 2),
        "max_abs_dev_pct": round(100 * np.abs(dev).max(), 2),
        "seats_outside_10pct": int(out.sum()),
        "pop_in_outside_seats_pct": round(100 * pops[out].sum() / pops.sum(), 1),
        "min_vote_weight": round(weight.min(), 3),
        "max_vote_weight": round(weight.max(), 3),
        "talukas": len(taluka_pop),
        "talukas_split": int(split),
        "taluka_excess_cuts": int(needless),
        "mean_polsby_popper": round(float(np.mean([1 - s["compactness_loss"] for s in seats])), 3),
        "mean_km_to_centre": round(raw["population_weighted_mean_distance_km"], 2),
        "evm_km": round(raw["evm_km_estimate"]),
        "common_score": round(common, 4),
    }


def shrug_ac_populations() -> pd.DataFrame:
    """2001 and 2011 population per Maharashtra 2008 AC, via SHRUG fragment weights."""
    frag = pd.read_csv(SHRUG / "shrug-con-keys-csv/shrid_frag_con08_key.csv", dtype={"shrid2": str, "ac08_id": str},
                       usecols=["shrid2", "ac08_id", "fragment_wt_con08_norm", "match_source"])
    frag = frag[frag["ac08_id"].str.startswith("2008-27-")].copy()
    frag["split_town"] = frag["match_source"].str.contains("split to 2", na=False)
    pops = {}
    for year in ("01", "11"):
        parts = []
        for kind in ("r", "u"):
            k = pd.read_csv(SHRUG / f"shrug-pc-keys-csv/pc{year}{kind}_shrid_key.csv", dtype={"shrid2": str},
                            usecols=["shrid2", f"pc{year}_pca_tot_p"])
            parts.append(k)
        pops[year] = pd.concat(parts).groupby("shrid2")[f"pc{year}_pca_tot_p"].sum()
    dist = pd.read_csv(SHRUG / "shrug-pc-keys-csv/shrid_pc11dist_key.csv", dtype=str)
    # one Maharashtra shrid is listed under two districts; keep the first
    dist = dist[dist["pc11_state_id"] == "27"].drop_duplicates("shrid2").set_index("shrid2")["pc11_district_id"]
    frag["pop01"] = frag["shrid2"].map(pops["01"]).fillna(0) * frag["fragment_wt_con08_norm"]
    frag["pop11"] = frag["shrid2"].map(pops["11"]).fillna(0) * frag["fragment_wt_con08_norm"]
    frag["pc11_district_id"] = frag["shrid2"].map(dist)
    # each AC sits in the 2011 district holding most of its 2011 population
    home = (frag.groupby(["ac08_id", "pc11_district_id"])["pop11"].sum().reset_index()
            .sort_values("pop11").drop_duplicates("ac08_id", keep="last").set_index("ac08_id")["pc11_district_id"])
    ac = frag.groupby("ac08_id").agg(pop01=("pop01", "sum"), pop11=("pop11", "sum"),
                                     split_town_share=("split_town", "mean")).reset_index()
    ac["pc11_district_id"] = ac["ac08_id"].map(home)
    names = pd.read_csv(SHRUG / "shrug-con-keys-csv/ac08_name_key.csv", dtype=str).set_index("ac08_id")["ac08_name"]
    dnames = pd.read_csv(SHRUG / "shrug-pc-keys-csv/shrid_pc11dist_key.csv", dtype=str)
    ac["ac08_name"] = ac["ac08_id"].map(names)
    pca = pd.read_csv(SHRUG / "shrug-pca11-csv/pc11_pca_clean_con08.csv", dtype={"ac08_id": str},
                      usecols=["ac08_id", "pc11_pca_tot_p", "pc11_pca_p_sc", "pc11_pca_p_st"]).set_index("ac08_id")
    ac["sc_pct"] = (100 * ac["ac08_id"].map(pca["pc11_pca_p_sc"]) / ac["ac08_id"].map(pca["pc11_pca_tot_p"])).round(1)
    ac["st_pct"] = (100 * ac["ac08_id"].map(pca["pc11_pca_p_st"]) / ac["ac08_id"].map(pca["pc11_pca_tot_p"])).round(1)
    for year in ("01", "11"):
        n = ac.groupby("pc11_district_id")["ac08_id"].transform("count")
        target = ac.groupby("pc11_district_id")[f"pop{year}"].transform("sum") / n
        ac[f"dev{year}_pct"] = (100 * (ac[f"pop{year}"] / target - 1)).round(2)
        state_target = ac[f"pop{year}"].sum() / len(ac)
        ac[f"state_dev{year}_pct"] = (100 * (ac[f"pop{year}"] / state_target - 1)).round(2)
    ac["growth_pct"] = (100 * (ac["pop11"] / ac["pop01"] - 1)).round(1)
    ac["pop01"] = ac["pop01"].round().astype(int)
    ac["pop11"] = ac["pop11"].round().astype(int)
    del dnames
    return ac.sort_values("ac08_id").reset_index(drop=True)


def statewide_summary(ac: pd.DataFrame) -> list[dict]:
    # SHRUG splits big towns across ACs by area, so per-AC totals are only trusted
    # where no fragment comes from a split town ("rural ACs").
    rows = []
    for subset, sub in (("all ACs (city ACs unreliable)", ac), ("rural ACs only", ac[ac["split_town_share"] == 0])):
      for year, label in (("01", "2001 (base used in 2008)"), ("11", "2011")):
        d = sub[f"dev{year}_pct"].abs()
        s = sub[f"state_dev{year}_pct"].abs()
        p = sub[f"pop{year}"]
        rows.append({
            "subset": subset,
            "census": label,
            "acs": len(sub),
            "outside_10pct_of_district_avg": int((d > 10).sum()),
            "outside_20pct_of_district_avg": int((d > 20).sum()),
            "outside_10pct_of_state_avg": int((s > 10).sum()),
            "mean_abs_dev_district_pct": round(d.mean(), 2),
            "largest_over_smallest": round(p.max() / p.min(), 2),
        })
    return rows


def spread(sizes: pd.Series) -> dict:
    """Seat-size spread against the group's own average seat."""
    dev = sizes / sizes.mean() - 1
    return {
        "seats": len(sizes),
        "avg_seat": round(sizes.mean()),
        "smallest": int(sizes.min()),
        "largest": int(sizes.max()),
        "size_ratio": round(sizes.max() / sizes.min(), 3),
        "mean_abs_dev_pct": round(100 * dev.abs().mean(), 2),
        "max_abs_dev_pct": round(100 * dev.abs().max(), 2),
        "seats_outside_10pct": int((dev.abs() > BAND).sum()),
        "seats_outside_20pct": int((dev.abs() > 2 * BAND).sum()),
    }


def elector_drift(path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Real registered electors per AC, 2009-2024: district-level spread, statewide, per seat."""
    e = pd.read_csv(path)
    years = [2009, 2014, 2019, 2024]
    by_district = []
    for (district, g) in e.groupby("district_2008", sort=False):
        for y in years:
            by_district.append({"district": district, "year": y, "electors": int(g[f"electors_{y}"].sum()),
                                **spread(g[f"electors_{y}"])})
    statewide = []
    for y in years:
        col = f"electors_{y}"
        dev = e[col] / e.groupby("district_2008")[col].transform("mean") - 1
        sdev = e[col] / e[col].mean() - 1
        statewide.append({
            "year": y, "electors": int(e[col].sum()),
            "outside_10pct_of_district_avg": int((dev.abs() > BAND).sum()),
            "outside_20pct_of_district_avg": int((dev.abs() > 2 * BAND).sum()),
            "mean_abs_dev_district_pct": round(100 * dev.abs().mean(), 2),
            "outside_10pct_of_state_avg": int((sdev.abs() > BAND).sum()),
            "largest_seat": e.loc[e[col].idxmax(), "name"], "largest": int(e[col].max()),
            "smallest_seat": e.loc[e[col].idxmin(), "name"], "smallest": int(e[col].min()),
            "largest_over_smallest": round(e[col].max() / e[col].min(), 2),
        })
    seats = []
    for _, r in e.iterrows():
        row = {k: r[k] for k in ("ac_no", "name", "district_2008", "reserved")}
        for y in years:
            avg = e.loc[e["district_2008"] == r["district_2008"], f"electors_{y}"].mean()
            row[f"electors_{y}"] = int(r[f"electors_{y}"])
            row[f"dev_{y}_pct"] = round(100 * (r[f"electors_{y}"] / avg - 1), 2)
        row["growth_2009_2024_pct"] = round(100 * (r["electors_2024"] / r["electors_2009"] - 1), 1)
        seats.append(row)
    return by_district, statewide, seats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "analysis/official_audit")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    ac = shrug_ac_populations()
    ac.to_csv(a.out / "maharashtra_ac08_2001_2011.csv", index=False)
    statewide = statewide_summary(ac)
    pd.DataFrame(statewide).to_csv(a.out / "statewide_drift.csv", index=False)

    map_rows, seat_rows, scatter = [], [], []
    for spec in DISTRICTS:
        tpop = taluka_populations(spec["units"])
        official = json.loads((spec["official"] / "score.json").read_text(encoding="utf-8"))
        report = json.loads((spec["official"] / "mapping_report.json").read_text(encoding="utf-8"))
        map_rows.append({"district": spec["name"], "map": "official_2008", "kind": "official",
                         **map_metrics(official, tpop)})
        target = official["raw"]["population_target"]
        for check, seat in zip(report["checks"], official["seats"]):
            assert check["population_2011"] == seat["population"], (spec["key"], check["name"])
            ac_id = f"2008-27-{int(check['ac08_no']):03d}"
            sh = ac[ac["ac08_id"] == ac_id].iloc[0]
            name = check["name"]
            seat_rows.append({
                "district": spec["name"], "ac08_no": check["ac08_no"], "name": name,
                "reserved": "SC" if "(SC)" in name else "ST" if "(ST)" in name else "",
                "pc_name": check.get("pc_name", ""),
                "pop_2011": seat["population"],
                "dev_2011_pct": round(100 * (seat["population"] / target - 1), 2),
                "vote_weight_2011": round(target / seat["population"], 3),
                "within_10pct_2011": abs(seat["population"] / target - 1) <= BAND,
                "shrug_pop_2001": int(sh["pop01"]), "shrug_dev_2001_pct": float(sh["dev01_pct"]),
                "shrug_pop_2011": int(sh["pop11"]), "shrug_growth_pct": float(sh["growth_pct"]),
                "rebuild_vs_shrug_2011_pct": round(100 * (seat["population"] / sh["pop11"] - 1), 2),
                # our rebuild and SHRUG's are independent; where they agree the seat's size is trusted
                "verified": bool(abs(seat["population"] / sh["pop11"] - 1) <= 0.02),
                "city_share": round(float(sh["split_town_share"]), 3),
                "sc_pct": float(sh["sc_pct"]), "st_pct": float(sh["st_pct"]),
                "polsby_popper": round(1 - seat["compactness_loss"], 3),
                "mean_km_to_centre": round(seat["mean_distance_km"], 2),
            })
            scatter.append({"district": spec["name"], "map": "official_2008", "seat": name,
                            "dev_pct": seat_rows[-1]["dev_2011_pct"], "verified": seat_rows[-1]["verified"]})
        for run in sorted(spec["runs"].glob("*_s*")):
            f = run / "score.json"
            if not run.is_dir() or not f.exists():
                continue
            s = json.loads(f.read_text(encoding="utf-8"))
            map_rows.append({"district": spec["name"], "map": run.name, "kind": run.name.split("_")[0],
                             **map_metrics(s, tpop)})
            t = s["raw"]["population_target"]
            scatter += [{"district": spec["name"], "map": run.name, "seat": f"{run.name} #{k}",
                         "dev_pct": round(100 * (x["population"] / t - 1), 2), "verified": True}
                        for k, x in enumerate(s["seats"], start=1)]

    maps = pd.DataFrame(map_rows)
    seats = pd.DataFrame(seat_rows)
    maps.to_csv(a.out / "map_comparison.csv", index=False)
    seats.to_csv(a.out / "official_seats.csv", index=False)
    pd.DataFrame(scatter).to_csv(a.out / "seat_deviations_all_maps.csv", index=False)

    # one line per district: official vs the range and best (common score) of the optimized runs
    summary = []
    for name, g in maps.groupby("district", sort=False):
        off = g[g["kind"] == "official"].iloc[0]
        opt = g[g["kind"] != "official"]
        best = opt.sort_values("common_score").iloc[0]
        summary.append({
            "district": name, "seats": int(off["seats"]),
            "official_size_ratio": off["size_ratio"], "optimized_size_ratio_range": f"{opt['size_ratio'].min():.2f}-{opt['size_ratio'].max():.2f}",
            "official_outside_10pct": int(off["seats_outside_10pct"]), "optimized_outside_10pct": int(opt["seats_outside_10pct"].max()),
            "official_mean_abs_dev_pct": off["mean_abs_dev_pct"], "best_mean_abs_dev_pct": best["mean_abs_dev_pct"],
            "official_talukas_split": int(off["talukas_split"]), "optimized_talukas_split_range": f"{opt['talukas_split'].min()}-{opt['talukas_split'].max()}",
            "official_taluka_excess": int(off["taluka_excess_cuts"]), "optimized_taluka_excess_range": f"{opt['taluka_excess_cuts'].min()}-{opt['taluka_excess_cuts'].max()}",
            "official_polsby_popper": off["mean_polsby_popper"], "best_polsby_popper": best["mean_polsby_popper"],
            "official_km_to_centre": off["mean_km_to_centre"], "best_km_to_centre": best["mean_km_to_centre"],
            "official_common_score": off["common_score"], "best_map": best["map"], "best_common_score": best["common_score"],
            "official_not_contiguous_on_units": len(json.loads((next(d for d in DISTRICTS if d["name"] == name)["official"] / "mapping_report.json").read_text(encoding="utf-8")).get("not_contiguous", [])),
        })
    pd.DataFrame(summary).to_csv(a.out / "district_summary.csv", index=False)

    electors_csv = ROOT / "data/maharashtra_ac_electors_wikipedia.csv"
    e_district, e_state, e_seats = elector_drift(electors_csv)
    pd.DataFrame(e_district).to_csv(a.out / "electors_by_district_year.csv", index=False)
    pd.DataFrame(e_state).to_csv(a.out / "electors_statewide.csv", index=False)
    pd.DataFrame(e_seats).to_csv(a.out / "electors_by_seat.csv", index=False)

    # electors per Census 2011 person, by district (booth_mip.py assumes 0.70)
    census = pd.read_csv(ROOT / "data/maharashtra_district_population_2011.csv")
    census["district"] = census["district"].str.strip().replace({"Palghar": "Thane"})
    census = census.groupby("district")["population"].sum()
    ratio = []
    for r in e_district:
        if r["district"] in census.index and r["year"] in (2009, 2014):
            ratio.append({"district": r["district"], "year": r["year"],
                          "electors_per_2011_person": round(r["electors"] / census[r["district"]], 3)})
    pd.DataFrame(ratio).to_csv(a.out / "elector_ratio_check.csv", index=False)

    payload = {"districts": summary, "maps": map_rows, "seats": seat_rows, "deviations": scatter,
               "electors_by_district": e_district, "electors_statewide": e_state, "electors_seats": e_seats,
               "elector_ratio": ratio,
               "statewide": statewide,
               "statewide_acs": ac[["ac08_id", "ac08_name", "pc11_district_id", "pop01", "pop11", "dev01_pct", "dev11_pct",
                                    "growth_pct", "split_town_share"]].to_dict("records")}
    (a.out / "audit.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    pd.set_option("display.width", 250)
    print(pd.DataFrame(summary).T.to_string())
    print()
    print(pd.DataFrame(statewide).to_string(index=False))
    print()
    print(pd.DataFrame(e_state).to_string(index=False))
    four = {d["name"] for d in DISTRICTS}
    print(pd.DataFrame([r for r in e_district if r["district"] in four]).to_string(index=False))
    print(pd.DataFrame([r for r in ratio if r["district"] in four]).to_string(index=False))


if __name__ == "__main__":
    main()
