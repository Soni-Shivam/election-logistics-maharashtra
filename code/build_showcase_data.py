"""Collect every Sangli result into one JSON file for the results dashboard.

Maps compared (all scored on the same road-distance matrix and common 35/30/20/15 weights):
  official 2008 delimitation, the three original maps (searched on straight-line distance),
  and the road-distance runs in sangli_road/runs (best seed per weight profile shown on the map,
  every seed listed for robustness).
Seat numbers of every map are aligned to the official seats by largest population overlap,
so "seat 1" is always the seat closest to Miraj, and so on.
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
ROAD = ROOT / "sangli_road"
OUT = ROOT.parent / "showcase" / "sangli_data.json"
W = {"population": 0.35, "distance": 0.30, "taluka_fragmentation": 0.20, "shape": 0.15}
PROFILES = {
    "balanced": ("Balanced", "35 / 30 / 20 / 15"),
    "locality": ("Locality", "30 / 45 / 10 / 15"),
    "taluka": ("Taluka", "30 / 20 / 35 / 15"),
}
SIMPLIFY = 0.0012  # degrees, about 130 m


def rnd(geom, nd=4):
    def r(c):
        return [round(c[0], nd), round(c[1], nd)]
    g = mapping(geom)
    if g["type"] == "Polygon":
        return [[[r(c) for c in ring] for ring in g["coordinates"]]]
    return [[[r(c) for c in ring] for ring in poly] for poly in g["coordinates"]]


def read_assigned(path):
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    return {f["properties"]["id"]: int(f["properties"]["constituency"]) for f in feats}


def common(terms):
    return sum(W[k] * terms[k] for k in W)


def booth_data(folder, max_km=2):
    p = folder / f"maxkm_{max_km}" / "booth_locations.csv"
    if not p.exists():
        return None
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    return [[round(float(r["lon"]), 4), round(float(r["lat"]), 4), int(r["booths"]), int(r["seat"]),
             r["is_depot"] == "True"] for r in rows]


def main():
    units = json.loads((ROOT / "sangli_input/sangli_units.geojson").read_text(encoding="utf-8"))["features"]
    uprops = {f["properties"]["id"]: f["properties"] for f in units}
    ugeom = {f["properties"]["id"]: shape(f["geometry"]) for f in units}
    pop = {k: int(p["population"]) for k, p in uprops.items()}
    district = shape(json.loads((ROOT / "sangli_input/sangli_district.geojson").read_text(encoding="utf-8"))["geometry"])

    official = read_assigned(ROOT / "official_2008/assigned_units.geojson")
    names = {int(f["properties"]["constituency"]): f["properties"]["ac08_name"]
             for f in json.loads((ROOT / "official_2008/assigned_units.geojson").read_text(encoding="utf-8"))["features"]}

    maps = [{"key": "official", "label": "Official 2008", "group": "official",
             "assigned": official, "score": ROOT / "official_2008/score.json",
             "booths": ROAD / "booth_mip_original/official_2008", "weights": "drawn in 2008 on 2001 Census"}]
    for k, (label, wts) in PROFILES.items():
        maps.append({"key": f"orig_{k}", "label": f"{label} (first run)", "group": "original",
                     "assigned": read_assigned(ROOT / f"map_{k}/assigned_units.geojson"),
                     "score": ROAD / f"rescored/map_{k}/score.json",
                     "booths": ROAD / f"booth_mip_original/map_{k}", "weights": wts})
    runs = []
    for k, (label, wts) in PROFILES.items():
        seeds = []
        for d in sorted((ROAD / "runs").glob(f"{k}_s*")):
            if (d / "score.json").exists():
                s = json.loads((d / "score.json").read_text(encoding="utf-8"))
                seeds.append((common(s["terms"]), d, s))
        for c, d, s in seeds:
            runs.append({"profile": label, "seed": s["search"]["seed"], "proposals": s["search"]["proposals"],
                         "accepted": s["search"]["accepted"], "own_score": s["score"], "common_score": c,
                         "max_dev_pct": max(abs(x["population"] / s["raw"]["population_target"] - 1) for x in s["seats"]) * 100,
                         "road_km": s["raw"]["population_weighted_mean_distance_km"],
                         "taluka_excess": s["raw"]["taluka_excess_fragments"], "shape": s["terms"]["shape"]})
        if seeds:
            own = min(seeds, key=lambda x: x[2]["score"])  # best under its own weights
            maps.append({"key": f"road_{k}", "label": f"{label} (road)", "group": "road",
                         "assigned": read_assigned(own[1] / "assigned_units.geojson"),
                         "score": own[1] / "score.json", "seed": own[2]["search"]["seed"],
                         "booths": ROAD / f"booth_mip_road/{own[1].name}", "weights": wts})

    seat_ids = sorted(set(official.values()))
    out_maps = []
    for m in maps:
        a = m["assigned"]
        # align seat labels to official seats by population overlap
        ov = np.zeros((8, 8))
        for uid, s in a.items():
            ov[s - 1, official[uid] - 1] += pop[uid]
        r, c = linear_sum_assignment(-ov)
        relabel = {int(i) + 1: int(j) + 1 for i, j in zip(r, c)}
        a2 = {uid: relabel[s] for uid, s in a.items()}
        moved = sum(pop[u] for u in a2 if a2[u] != official[u])
        sc = json.loads(m["score"].read_text(encoding="utf-8"))
        target = sc["raw"]["population_target"]
        seats = []
        for s_old in sorted(relabel):
            s = relabel[s_old]
            ids = [u for u in a2 if a2[u] == s]
            g = shapely.union_all([ugeom[u] for u in ids]).simplify(SIMPLIFY, preserve_topology=True)
            st = sc["seats"][s_old - 1]
            talukas = sorted({uprops[u]["taluka"] for u in ids if pop[u] > 0})
            seats.append({"seat": s, "population": st["population"],
                          "dev_pct": round(100 * (st["population"] / target - 1), 2),
                          "road_km": round(st["mean_distance_km"], 2),
                          "evm_km": round(st.get("evm_km_estimate", 0), 0),
                          "compact_loss": round(st["compactness_loss"], 3),
                          "area_km2": round(st["area_km2"], 0), "units": len(ids),
                          "centre": uprops[st["centre_unit_id"]]["name"],
                          "talukas": talukas, "geom": rnd(g),
                          "label": [round(v, 4) for v in g.representative_point().coords[0]]})
        seats.sort(key=lambda x: x["seat"])
        devs = [abs(x["dev_pct"]) for x in seats]
        out_maps.append({
            "key": m["key"], "label": m["label"], "group": m["group"], "weights": m["weights"],
            "seed": m.get("seed"),
            "common_score": round(common(sc["terms"]), 4), "terms": {k: round(v, 4) for k, v in sc["terms"].items()},
            "max_dev_pct": round(max(devs), 2), "passes_band": max(devs) <= 10.0,
            "road_km": round(sc["raw"]["population_weighted_mean_distance_km"], 2),
            "evm_km_est": round(sc["raw"].get("evm_km_estimate", 0)),
            "taluka_excess": sc["raw"]["taluka_excess_fragments"],
            "people_moved_vs_official": moved, "seats": seats,
            "booths": booth_data(m["booths"]) if m.get("booths") else None,
            "booth_key": m["booths"].name if m.get("booths") else None,
        })

    # booth model sensitivity table
    booth_rows = []
    for f in [ROAD / "booth_mip_original/map_summary.csv", ROAD / "booth_mip_road/map_summary.csv"]:
        if f.exists():
            for r in csv.DictReader(f.open(encoding="utf-8")):
                booth_rows.append({k: r[k] for k in ("map", "max_km", "booths", "min_booths", "extra_booths",
                                                    "capacity_util", "voter_person_km", "mean_voter_km",
                                                    "electors_travelling_pct", "evm_km", "objective")})

    # taluka boundaries and populated unit points
    con = sqlite3.connect(ROOT.parent / "raw_data/shrug-pc11subdist-poly-gpkg/subdistrict.gpkg")
    talukas = []
    for blob, name in con.execute("SELECT geom, subdistrict_name FROM subdistrict WHERE pc11_district_id='531'"):
        n = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 7]
        g = wkb.loads(blob[8 + n:]).simplify(SIMPLIFY, preserve_topology=True)
        talukas.append({"name": name, "geom": rnd(g), "label": [round(v, 4) for v in g.representative_point().coords[0]]})
    con.close()
    points = [[round(float(p["demand_lon"]), 4), round(float(p["demand_lat"]), 4), int(p["population"])]
              for p in uprops.values() if int(p["population"]) > 0]

    road_info = json.loads((ROAD / "road_distances.json").read_text(encoding="utf-8"))
    data = {"district": rnd(district.simplify(SIMPLIFY)), "official_names": names, "maps": out_maps,
            "runs": runs, "booth_rows": booth_rows, "talukas": talukas, "points": points,
            "road_info": road_info, "target": out_maps[0]["seats"] and json.loads(maps[0]["score"].read_text())["raw"]["population_target"],
            "bounds": json.loads(maps[0]["score"].read_text())["raw"]["population_bounds"]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, separators=(",", ":"))
    OUT.write_text(payload, encoding="utf-8")
    template = (OUT.parent / "template.html").read_text(encoding="utf-8")
    html = OUT.parent / "sangli_results.html"
    html.write_text(template.replace("/*DATA*/", payload.replace("</", "<\\/")), encoding="utf-8")
    print(f"wrote {html} ({html.stat().st_size / 1e3:.0f} kB)")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e3:.0f} kB): {len(out_maps)} maps, {len(runs)} runs, "
          f"{len(booth_rows)} booth rows")
    for m in out_maps:
        print(f"  {m['label']:24s} common {m['common_score']:.3f}  maxdev {m['max_dev_pct']:5.2f}%  "
              f"road {m['road_km']:.2f} km  taluka+{m['taluka_excess']:2d}  moved {m['people_moved_vs_official']:,}")


if __name__ == "__main__":
    main()
