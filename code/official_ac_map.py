"""Rebuild Sangli's official 2008 Assembly constituencies on our 756 assignment units.

Source: SHRUG shrid -> 2008 AC fragment key (shrid_frag_con08_key.csv), which places
each Census 2011 village/town in the AC(s) it falls in. Rules, all reported in
official_2008/mapping_report.json:

  * villages/towns: the in-district AC with the largest fragment weight. Four border
    villages have a small fragment in a Kolhapur AC (SHRUG's 0-2 km position error);
    they go to their Sangli AC;
  * units SHRUG has no row for (8 villages merged into neighbouring shrids) and the 2 zero-
    population Shirala gap units: the AC of the neighbour sharing the longest border;
  * Sangli-Miraj-Kupwad city: SHRUG puts the whole city in Miraj (281), but at
    ~503k people it is split between Miraj (281) and Sangli (282). Our 20 city wards are
    approximate, so the split is a straight line across the Sangli -> Miraj axis
    (Sangli town is north-west, Miraj south-east), placed where 281 and 282 are most
    equal in 2011 population. That is the most favourable split for the official map,
    and it is approximate.

Output: official_2008/assigned_units.geojson (constituency = 1..8 for AC 281..288),
constituencies.geojson and score.json with the same scorer as the generated maps, plus
the rule checks each official seat passes or fails under the 2011 population.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import mapping, shape

import sangli_recom as sr

AC_NAMES = {281: "Miraj (SC)", 282: "Sangli", 283: "Islampur", 284: "Shirala",
            285: "Palus-Kadegaon", 286: "Khanapur", 287: "Tasgaon-Kavathe Mahankal", 288: "Jat"}
CITY_ID = "04302-802895"
# Approximate town centres (lon, lat) used only to orient the city split.
SANGLI_TOWN = (74.570, 16.860)
MIRAJ_TOWN = (74.645, 16.825)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", type=Path, default=Path("sangli_input/sangli_units.geojson"))
    ap.add_argument("--district", type=Path, default=Path("sangli_input/sangli_district.geojson"))
    ap.add_argument("--keys", type=Path, default=Path("../raw_data"))
    ap.add_argument("--distance-matrix", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("official_2008"))
    a = ap.parse_args()

    feats = json.loads(a.units.read_text(encoding="utf-8"))["features"]
    frag = defaultdict(list)
    with (a.keys / "shrug-con-keys-csv/shrid_frag_con08_key.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["shrid2"].startswith("11-27-531-"):
                frag[r["shrid2"]].append((int(r["ac08_id"][-3:]), float(r["fragment_wt_con08_norm"])))

    ac = {}
    rule = {}
    for i, f in enumerate(feats):
        p = f["properties"]
        if p["kind"] in ("VILLAGE", "TOWN"):
            rows = [(c, w) for c, w in frag.get("11-27-531-" + p["id"], []) if c in AC_NAMES]
            if rows:
                ac[i] = max(rows, key=lambda x: x[1])[0]
                all_rows = frag["11-27-531-" + p["id"]]
                rule[p["id"]] = "shrug_key" if len(all_rows) == len(rows) else "shrug_key_border_fragment_dropped"

    # City wards: split along the Sangli -> Miraj axis.
    wards = [i for i, f in enumerate(feats) if f["properties"]["settlement_id"] == CITY_ID]
    axis = np.subtract(MIRAJ_TOWN, SANGLI_TOWN)
    proj = {i: float(np.dot(np.subtract(shape(feats[i]["geometry"]).representative_point().coords[0],
                                        SANGLI_TOWN), axis)) for i in wards}
    order = sorted(wards, key=proj.get)

    # Fill the remaining units (no key row, gap units) from neighbours after the city split,
    # trying every split point and keeping the most equal 281/282 pair.
    geoms = [shape(f["geometry"]) for f in feats]
    tree = shapely.STRtree(geoms)
    pops = [int(f["properties"]["population"]) for f in feats]

    def fill(assign):
        assign = dict(assign)
        todo = [i for i in range(len(feats)) if i not in assign]
        for _ in range(10):
            left = []
            for i in todo:
                border = Counter()
                for j in tree.query(geoms[i], predicate="intersects"):
                    j = int(j)
                    if j != i and j in assign:
                        border[assign[j]] += geoms[i].boundary.intersection(geoms[j].boundary).length
                if border:
                    assign[i] = border.most_common(1)[0][0]
                else:
                    left.append(i)
            todo = left
            if not todo:
                break
        if todo:
            raise RuntimeError(f"{len(todo)} units could not be assigned from neighbours")
        return assign

    best = None
    for k in range(1, len(order)):
        trial = dict(ac)
        for i in order[:k]:
            trial[i] = 282
        for i in order[k:]:
            trial[i] = 281
        totals = Counter()
        for i, c in trial.items():
            totals[c] += pops[i]
        gap = abs(totals[281] - totals[282])
        if best is None or gap < best[0]:
            best = (gap, k, trial)
    _, k_best, ac_city = best
    assign = fill(ac_city)
    for i in range(len(feats)):
        pid = feats[i]["properties"]["id"]
        if pid not in rule:
            rule[pid] = ("city_ward_axis_split" if i in wards else
                         "zero_pop_gap_from_neighbour" if pops[i] == 0 and feats[i]["properties"]["kind"] == "ZERO_POP_GAP"
                         else "no_shrug_row_from_neighbour")

    a.out.mkdir(parents=True, exist_ok=True)
    seat_of = {c: n for n, c in enumerate(sorted(AC_NAMES), start=1)}
    out_feats = []
    for i, f in enumerate(feats):
        p = dict(f["properties"])
        p["constituency"] = seat_of[assign[i]]
        p["ac08_no"] = assign[i]
        p["ac08_name"] = AC_NAMES[assign[i]]
        p["official_mapping_rule"] = rule[p["id"]]
        out_feats.append({"type": "Feature", "properties": p, "geometry": f["geometry"]})
    assigned_path = a.out / "assigned_units.geojson"
    assigned_path.write_text(json.dumps({"type": "FeatureCollection", "features": out_feats}), encoding="utf-8")

    # Score with the same scorer as the generated maps (no validation: the official
    # map was drawn on 2001 population and need not pass our 2011 rules).
    problem = sr.DistrictProblem(assigned_path, a.district, sr.Settings(), a.distance_matrix)
    groups = defaultdict(set)
    for i, u in enumerate(problem.units):
        groups[int(u.properties["constituency"])].add(i)
    plan = [frozenset(groups[n]) for n in sorted(groups)]
    report = problem.evaluate(plan, validate=False)
    checks = []
    for n, nodes in enumerate(plan, start=1):
        pop = sum(problem.units[i].population for i in nodes)
        connected = sr.nx.is_connected(problem.graph.subgraph(nodes))
        single = problem._region_geometry(nodes).geom_type == "Polygon"
        checks.append({"constituency": n, "ac08_no": sorted(AC_NAMES)[n - 1],
                       "name": AC_NAMES[sorted(AC_NAMES)[n - 1]], "population_2011": pop,
                       "deviation_pct": round(100 * (pop / problem.target - 1), 2),
                       "within_10pct": problem.lower <= pop <= problem.upper,
                       "connected": bool(connected), "single_polygon": bool(single),
                       "passes_all_rules_incl_neck": bool(problem._valid_region(nodes))})
    report["official_checks"] = checks
    report["settings"] = vars(problem.settings)
    report["distance_source"] = problem.distance_source
    (a.out / "score.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    regions = []
    for n, nodes in enumerate(plan, start=1):
        g = shapely.union_all([problem.units[i].original_geometry for i in nodes])
        regions.append({"type": "Feature", "properties": {"constituency": n, **checks[n - 1],
                        **report["seats"][n - 1]}, "geometry": mapping(g)})
    (a.out / "constituencies.geojson").write_text(json.dumps({"type": "FeatureCollection",
                                                             "features": regions}), encoding="utf-8")
    summary = {"rules": Counter(rule.values()), "city_wards_to_sangli_282": k_best,
               "city_wards_to_miraj_281": len(order) - k_best,
               "score": report["score"], "checks": checks}
    (a.out / "mapping_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
