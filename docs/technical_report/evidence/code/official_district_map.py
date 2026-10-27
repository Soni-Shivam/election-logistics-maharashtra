"""Rebuild a district's official 2008 Assembly constituencies on its assignment units.

Works for any district prepared by prepare_district_inputs.py:

  * villages and whole towns: SHRUG's shrid -> 2008 AC fragment key, using the
    fragment with the largest weight among this district's ACs;
  * city grid cells / wards, zero-population gap units and villages SHRUG has no row
    for: the official AC polygon (DataMeet India_AC, scraped from ECI) that covers
    most of the unit's area. DataMeet notes a small positional shift, so this is
    approximate along city seat edges.

Seats are numbered 1..K in AC-number order. The plan is scored with the same
scorer as the optimizer's maps (no validation: official seats were drawn on 2001
population), with K = the official seat count.

Example:
    python official_district_map.py --units pilots/pune/input/pune_units.geojson \
        --district pilots/pune/input/pune_district.geojson --dist-name PUNE \
        --distance-matrix pilots/pune/road_distances.npz --out pilots/pune/official_2008
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import shapely
import shapefile
from shapely.geometry import mapping, shape

import sangli_recom as sr

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", type=Path, required=True)
    ap.add_argument("--district", type=Path, required=True)
    ap.add_argument("--dist-name", required=True, help="DataMeet DIST_NAME, e.g. PUNE")
    ap.add_argument("--state-name", default="MAHARASHTRA")
    ap.add_argument("--keys", type=Path, default=ROOT.parent / "raw_data")
    ap.add_argument("--shapes", type=Path, default=ROOT.parent / "data_raw/datameet/India_AC")
    ap.add_argument("--distance-matrix", type=Path, default=None)
    ap.add_argument("--link-islands-km", type=float, default=0.0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    reader = shapefile.Reader(str(a.shapes))
    acs = {}
    for sr_, rec in zip(reader.shapes(), reader.records()):
        if rec["ST_NAME"].upper() == a.state_name and rec["DIST_NAME"].upper() == a.dist_name.upper():
            acs[int(rec["AC_NO"])] = {"name": rec["AC_NAME"].strip(), "pc": rec["PC_NAME"].strip(),
                                       "geom": shape(sr_.__geo_interface__).buffer(0)}
    if not acs:
        raise SystemExit(f"no ACs for {a.dist_name} in {a.shapes}")
    ac_nos = sorted(acs)
    ac_geoms = [acs[n]["geom"] for n in ac_nos]
    tree = shapely.STRtree(ac_geoms)

    feats = json.loads(a.units.read_text(encoding="utf-8"))["features"]
    meta = json.loads(a.district.read_text(encoding="utf-8"))["properties"]
    prefix = f"11-{meta['pc11_state_id']}-{meta['pc11_district_id']}-"
    frag = defaultdict(list)
    with (a.keys / "shrug-con-keys-csv/shrid_frag_con08_key.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["shrid2"].startswith(prefix):
                frag[r["shrid2"]].append((int(r["ac08_id"][-3:]), float(r["fragment_wt_con08_norm"])))

    assign, rule = {}, {}
    for i, f in enumerate(feats):
        p = f["properties"]
        rows = [(c, w) for c, w in frag.get(prefix + p["id"], []) if c in acs] if p["kind"] in ("VILLAGE", "TOWN") else []
        if rows:
            assign[i] = max(rows, key=lambda x: x[1])[0]
            rule[i] = "shrug_key"
            continue
        g = shape(f["geometry"])
        best, best_area = None, 0.0
        for j in tree.query(g, predicate="intersects"):
            ar = g.intersection(ac_geoms[int(j)]).area
            if ar > best_area:
                best, best_area = ac_nos[int(j)], ar
        if best is None:
            best = ac_nos[int(tree.nearest(g))]
        assign[i] = best
        rule[i] = f"shape_overlap_{p['kind'].lower()}"

    seat_of = {n: k for k, n in enumerate(ac_nos, start=1)}
    a.out.mkdir(parents=True, exist_ok=True)
    out = []
    for i, f in enumerate(feats):
        p = dict(f["properties"])
        p.update(constituency=seat_of[assign[i]], ac08_no=assign[i], ac08_name=acs[assign[i]]["name"],
                 official_mapping_rule=rule[i])
        out.append({"type": "Feature", "properties": p, "geometry": f["geometry"]})
    assigned = a.out / "assigned_units.geojson"
    assigned.write_text(json.dumps({"type": "FeatureCollection", "features": out}), encoding="utf-8")

    problem = sr.DistrictProblem(assigned, a.district, sr.Settings(seats=len(ac_nos), link_islands_km=a.link_islands_km), a.distance_matrix)
    groups = defaultdict(set)
    for i, u in enumerate(problem.units):
        groups[int(u.properties["constituency"])].add(i)
    plan = [frozenset(groups[k]) for k in sorted(groups)]
    report = problem.evaluate(plan, validate=False)
    checks = []
    for k, nodes in enumerate(plan, start=1):
        n = ac_nos[k - 1]
        pop = sum(problem.units[i].population for i in nodes)
        checks.append({"constituency": k, "ac08_no": n, "name": acs[n]["name"], "pc_name": acs[n]["pc"],
                       "population_2011": pop, "deviation_pct": round(100 * (pop / problem.target - 1), 2),
                       "within_10pct": problem.lower <= pop <= problem.upper,
                       "connected": bool(sr.nx.is_connected(problem.graph.subgraph(nodes))),
                       "single_polygon": problem._region_geometry(nodes).geom_type == "Polygon",
                       "passes_all_rules_incl_neck": bool(problem._valid_region(nodes))})
    report["official_checks"] = checks
    report["settings"] = vars(problem.settings)
    report["distance_source"] = problem.distance_source
    (a.out / "score.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    regions = []
    for k, nodes in enumerate(plan, start=1):
        g = shapely.union_all([problem.units[i].original_geometry for i in nodes])
        regions.append({"type": "Feature", "properties": {**checks[k - 1], **report["seats"][k - 1]},
                        "geometry": mapping(g)})
    (a.out / "constituencies.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": regions}),
                                                 encoding="utf-8")
    summary = {"district": a.dist_name, "seats": len(ac_nos), "rules": Counter(rule.values()),
               "score": report["score"], "taluka_excess": report["raw"]["taluka_excess_fragments"],
               "fail_band": [c["name"] for c in checks if not c["within_10pct"]],
               "not_contiguous": [c["name"] for c in checks if not c["connected"]],
               "checks": checks}
    (a.out / "mapping_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "checks"}, indent=2))


if __name__ == "__main__":
    main()
