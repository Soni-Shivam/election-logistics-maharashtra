"""Collect results for every district into one JSON payload and render showcase/index.html.

Per district: the official 2008 map, the optimizer's plans (straight-line first runs where
they exist, and road-distance runs: best seed per weight profile shown, every seed listed),
booth-model results for 2/3/5 km walking limits, and per-village booth assignments for the
booth lab. Every plan is scored on the district's road-distance matrix with the common
35/30/20/15 weights, and its seat numbers are aligned to the official seats by population overlap.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from scipy.optimize import linear_sum_assignment
from shapely import wkb
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent
SHOW = ROOT.parent / "showcase"
W = {"population": 0.35, "distance": 0.30, "taluka_fragmentation": 0.20, "shape": 0.15}
PROFILES = {"balanced": ("Balanced", "35 / 30 / 20 / 15"), "locality": ("Locality", "30 / 45 / 10 / 15"),
            "taluka": ("Taluka", "30 / 20 / 35 / 15")}
SUBDIST = ROOT.parent / "raw_data/shrug-pc11subdist-poly-gpkg/subdistrict.gpkg"

DISTRICTS = [
    {"key": "sangli", "name": "Sangli", "pc11": "531", "units": ROOT / "sangli_input/sangli_units.geojson",
     "district": ROOT / "sangli_input/sangli_district.geojson", "official": ROOT / "official_2008",
     "road": ROOT / "sangli_road/road_distances.npz", "runs": ROOT / "sangli_road/runs",
     "booth_official": ROOT / "sangli_road/booth_mip_original/official_2008",
     "booth_runs": ROOT / "sangli_road/booth_mip_road",
     "first_runs": {k: (ROOT / f"map_{k}", ROOT / f"sangli_road/rescored/map_{k}",
                        ROOT / f"sangli_road/booth_mip_original/map_{k}") for k in PROFILES},
     "city_note": "Sangli-Miraj-Kupwad split into 20 approximate wards from the 2025 ward map."},
]
for key, name, pc11, note in [
        ("satara", "Satara", "527", "No city needs splitting; Satara town (120k) stays whole."),
        ("nashik", "Nashik", "516", "Nashik city and Malegaon are cut into ~1 km WorldPop grid cells."),
        ("pune", "Pune", "521", "Pune and Pimpri-Chinchwad are cut into ~1 km WorldPop grid cells.")]:
    base = ROOT / "pilots" / key
    booth = base / ("booth_mip_gurobi" if (base / "booth_mip_gurobi/map_summary.csv").exists() else "booth_mip")
    DISTRICTS.append({"key": key, "name": name, "pc11": pc11, "units": base / f"input/{key}_units.geojson",
                      "district": base / f"input/{key}_district.geojson", "official": base / "official_2008",
                      "road": base / "road_distances.npz", "runs": base / "runs",
                      "booth_official": booth / "official_2008", "booth_runs": booth,
                      "first_runs": {}, "city_note": note})


def rnd(geom, nd=4):
    g = mapping(geom)
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    return [[[[round(x, nd), round(y, nd)] for x, y in ring] for ring in poly] for poly in polys]


def common(terms):
    return sum(W[k] * terms[k] for k in W)


def read_assigned(path):
    return {f["properties"]["id"]: int(f["properties"]["constituency"])
            for f in json.loads(path.read_text(encoding="utf-8"))["features"]}


def booth_sites(folder, km):
    p = folder / f"maxkm_{km}" / "booth_locations.csv"
    if not p.exists():
        return None
    return [[round(float(r["lon"]), 4), round(float(r["lat"]), 4), int(r["booths"]), int(r["seat"]),
             r["is_depot"] == "True", r["site_id"]] for r in csv.DictReader(p.open(encoding="utf-8"))]


def booth_lab(folder, uidx, relabel):
    """Per walking limit: assignment rows [unit, site, electors, km] and per-seat summaries."""
    out = {}
    for km in (2, 3, 5):
        d = folder / f"maxkm_{km}"
        if not (d / "assignments.csv").exists():
            return None
        rows = [[uidx[r["unit_id"]], uidx[r["site_id"]], round(float(r["electors"]), 1), round(float(r["km"]), 2)]
                for r in csv.DictReader((d / "assignments.csv").open(encoding="utf-8"))]
        seats = {}
        for r in csv.DictReader((d / "seat_summary.csv").open(encoding="utf-8")):
            s = relabel[int(r["seat"])]
            seats[s] = {"booths": int(r["booths"]), "min_booths": int(r["min_booths"]),
                        "isolated": int(r["isolated_units"]), "units": int(r["units"]),
                        "util": float(r["capacity_util"]), "evm_km": float(r["evm_km"]),
                        "voter_km": float(r["voter_person_km"]), "depot": uidx.get(r["depot_id"]),
                        "depot_name": r["depot_name"], "electors": float(r["electors"])}
        sites = {}
        for r in csv.DictReader((d / "booth_locations.csv").open(encoding="utf-8")):
            sites[uidx[r["site_id"]]] = int(r["booths"])
        out[str(km)] = {"rows": rows, "seats": seats, "sites": [[k, v] for k, v in sites.items()]}
    return out


def booth_summary(folder):
    p = folder.parent / "map_summary.csv" if (folder.parent / "map_summary.csv").exists() else None
    rows = {}
    if p:
        for r in csv.DictReader(p.open(encoding="utf-8")):
            if r["map"] == folder.name:
                rows[str(int(float(r["max_km"])))] = {k: float(r[k]) for k in (
                    "booths", "min_booths", "extra_booths", "capacity_util", "voter_person_km", "mean_voter_km",
                    "electors_travelling_pct", "evm_km", "objective", "seats_optimal", "seats")}
    return rows or None


def build_district(spec):
    feats = json.loads(spec["units"].read_text(encoding="utf-8"))["features"]
    uprops = {f["properties"]["id"]: f["properties"] for f in feats}
    ugeom = {f["properties"]["id"]: shape(f["geometry"]) for f in feats}
    pop = {k: int(p["population"]) for k, p in uprops.items()}
    district = shape(json.loads(spec["district"].read_text(encoding="utf-8"))["geometry"])
    populated = [k for k in uprops if pop[k] > 0]
    uidx = {k: i for i, k in enumerate(populated)}
    points = [[round(float(uprops[k]["demand_lon"]), 4), round(float(uprops[k]["demand_lat"]), 4), pop[k],
               uprops[k]["name"], uprops[k]["kind"]] for k in populated]

    official = read_assigned(spec["official"] / "assigned_units.geojson")
    off_feats = json.loads((spec["official"] / "assigned_units.geojson").read_text(encoding="utf-8"))["features"]
    names = {int(f["properties"]["constituency"]): f["properties"]["ac08_name"] for f in off_feats}
    k_seats = len(names)

    plans = [{"key": "official", "label": "Official 2008", "group": "official",
              "assigned": official, "score": spec["official"] / "score.json",
              "booths": spec["booth_official"], "weights": "drawn in 2008 on 2001 Census"}]
    for k, (folder, rescored, booth) in spec["first_runs"].items():
        plans.append({"key": f"orig_{k}", "label": f"{PROFILES[k][0]} (first run)", "group": "original",
                      "assigned": read_assigned(folder / "assigned_units.geojson"), "score": rescored / "score.json",
                      "booths": booth, "weights": PROFILES[k][1]})
    runs = []
    for k, (label, wts) in PROFILES.items():
        seeds = []
        for d in sorted(spec["runs"].glob(f"{k}_s*")):
            if (d / "score.json").exists():
                s = json.loads((d / "score.json").read_text(encoding="utf-8"))
                seeds.append((d, s))
                runs.append({"profile": label, "seed": s["search"]["seed"], "proposals": s["search"]["proposals"],
                             "accepted": s["search"]["accepted"], "own_score": s["score"],
                             "common_score": common(s["terms"]),
                             "max_dev_pct": max(abs(x["population"] / s["raw"]["population_target"] - 1)
                                                for x in s["seats"]) * 100,
                             "road_km": s["raw"]["population_weighted_mean_distance_km"],
                             "taluka_excess": s["raw"]["taluka_excess_fragments"], "shape": s["terms"]["shape"]})
        if seeds:
            d, s = min(seeds, key=lambda x: x[1]["score"])
            plans.append({"key": f"road_{k}", "label": f"{label} (road)", "group": "road",
                          "assigned": read_assigned(d / "assigned_units.geojson"), "score": d / "score.json",
                          "seed": s["search"]["seed"], "booths": spec["booth_runs"] / d.name, "weights": wts})

    out_plans = []
    for m in plans:
        a = m["assigned"]
        ov = np.zeros((k_seats, k_seats))
        for uid, s in a.items():
            ov[s - 1, official[uid] - 1] += pop[uid]
        r, c = linear_sum_assignment(-ov)
        relabel = {int(i) + 1: int(j) + 1 for i, j in zip(r, c)}
        a2 = {uid: relabel[s] for uid, s in a.items()}
        sc = json.loads(m["score"].read_text(encoding="utf-8"))
        target = sc["raw"]["population_target"]
        seats = []
        for s_old in sorted(relabel):
            s = relabel[s_old]
            ids = [u for u in a2 if a2[u] == s]
            full = shapely.union_all([ugeom[u] for u in ids])
            tol = 0.0012 if len(ids) < 400 else 0.0015
            g = full.simplify(tol, preserve_topology=True)
            st = sc["seats"][s_old - 1]
            seats.append({"seat": s, "population": st["population"],
                          "dev_pct": round(100 * (st["population"] / target - 1), 2),
                          "road_km": round(st["mean_distance_km"], 2), "evm_km": round(st.get("evm_km_estimate", 0)),
                          "compact_loss": round(st["compactness_loss"], 3), "area_km2": round(st["area_km2"]),
                          "units": len(ids), "centre": uprops[st["centre_unit_id"]]["name"],
                          "cell_share": round(sum(pop[u] for u in ids if uprops[u]["kind"] == "GRID_CELL")
                                              / max(1, st["population"]), 3),
                          "talukas": sorted({uprops[u]["taluka"] for u in ids if pop[u] > 0}),
                          "geom": rnd(g), "label": [round(v, 4) for v in full.representative_point().coords[0]]})
        seats.sort(key=lambda x: x["seat"])
        devs = [abs(x["dev_pct"]) for x in seats]
        folder = m["booths"]
        out_plans.append({
            "key": m["key"], "label": m["label"], "group": m["group"], "weights": m["weights"], "seed": m.get("seed"),
            "common_score": round(common(sc["terms"]), 4), "terms": {k: round(v, 4) for k, v in sc["terms"].items()},
            "max_dev_pct": round(max(devs), 2), "passes_band": max(devs) <= 10.0,
            "road_km": round(sc["raw"]["population_weighted_mean_distance_km"], 2),
            "evm_km_est": round(sc["raw"].get("evm_km_estimate", 0)), "taluka_excess": sc["raw"]["taluka_excess_fragments"],
            "people_moved_vs_official": sum(pop[u] for u in a2 if a2[u] != official[u]),
            "unit_seat": [a2[u] for u in populated],
            "seats": seats,
            "booths": booth_sites(folder, 2) if folder.exists() else None,
            "booth_summary": booth_summary(folder) if folder.exists() else None,
            "_folder": folder, "_relabel": relabel,
        })

    # Booth lab: the official map and the best road plan by common score.
    road_plans = [p for p in out_plans if p["group"] == "road"]
    lab_keys = ["official"] + ([min(road_plans, key=lambda p: p["common_score"])["key"]] if road_plans else [])
    for p in out_plans:
        p["lab"] = booth_lab(p["_folder"], uidx, p["_relabel"]) if p["key"] in lab_keys and p["_folder"].exists() else None
        del p["_folder"], p["_relabel"]

    # Why the walking limit decides the booth count: nearest other village by road.
    z = np.load(spec["road"])
    rows = {(round(float(a), 6), round(float(b), 6)): i for i, (a, b) in enumerate(z["pts"])}
    ridx = [rows[(round(float(uprops[k]["demand_lon"]), 6), round(float(uprops[k]["demand_lat"]), 6))] for k in populated]
    Dm = z["D"][np.ix_(ridx, ridx)].copy()
    np.fill_diagonal(Dm, np.inf)
    nearest = Dm.min(axis=1)
    electors = np.array([pop[k] * 0.7 for k in populated])
    road_info = json.loads(spec["road"].with_suffix(".json").read_text(encoding="utf-8"))
    sc0 = json.loads(plans[0]["score"].read_text(encoding="utf-8"))

    con = sqlite3.connect(SUBDIST)
    talukas = []
    for blob, nm in con.execute("SELECT geom, subdistrict_name FROM subdistrict WHERE pc11_district_id=?", (spec["pc11"],)):
        n = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 7]
        g = wkb.loads(blob[8 + n:]).simplify(0.0015, preserve_topology=True)
        talukas.append({"name": nm, "geom": rnd(g), "label": [round(v, 4) for v in g.representative_point().coords[0]]})
    con.close()
    return {"key": spec["key"], "name": spec["name"], "seats": k_seats, "official_names": names,
            "population": sum(pop.values()), "units": len(feats), "populated_units": len(populated),
            "city_units": sum(1 for p in uprops.values() if p["kind"] in ("GRID_CELL", "APPROX_WARD", "WARD")),
            "city_note": spec["city_note"], "target": sc0["raw"]["population_target"],
            "bounds": sc0["raw"]["population_bounds"], "district": rnd(district.simplify(0.0015)),
            "plans": out_plans, "runs": runs, "talukas": talukas, "points": points, "road_info": road_info,
            "nearest_km_hist": {"edges": [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 10],
                                "counts": np.histogram(np.minimum(nearest, 9.99),
                                                       [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 10])[0].tolist()},
            "share_neighbour_within": {str(k): round(float((nearest <= k).mean()), 4) for k in (2, 3, 5)},
            "share_over_capacity": round(float((electors > 1500).mean()), 4),
            "elector_quantiles": [round(float(q)) for q in np.quantile(electors, [0.1, 0.25, 0.5, 0.75, 0.9])]}


def main():
    data = {"districts": []}
    for spec in DISTRICTS:
        if not (spec["official"] / "score.json").exists() or not spec["road"].exists():
            print(f"skip {spec['name']}: official map or road matrix missing")
            continue
        d = build_district(spec)
        data["districts"].append(d)
        print(f"{d['name']}: {len(d['plans'])} plans, {len(d['runs'])} runs")
        for p in d["plans"]:
            bs = p["booth_summary"] or {}
            print(f"   {p['label']:22s} common {p['common_score']:.3f} maxdev {p['max_dev_pct']:5.2f}% road {p['road_km']:.2f} "
                  f"taluka+{p['taluka_excess']:2d} booths@2 {bs.get('2', {}).get('booths', '-')}")
    payload = json.dumps(data, separators=(",", ":"))
    (SHOW / "showcase_data.json").write_text(payload, encoding="utf-8")
    tpl = (SHOW / "index_template.html").read_text(encoding="utf-8")
    html = SHOW / "index.html"
    html.write_text(tpl.replace("/*DATA*/", payload.replace("</", "<\\/")), encoding="utf-8")
    print(f"wrote {html} ({html.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
