"""Build the unit-to-unit road-distance matrix locally from an OSM .pbf file.

Same method and output format as the Colab logistics notebook, without osmnx or
pyrosm:
  * drivable roads (pyrosm "driving" filter) inside the district polygon buffered
    by 0.02 degrees and convex-hulled, so border villages can use roads just outside;
  * roads are treated as two-way and only the largest connected road component is kept;
  * every populated unit's demand point is snapped to its nearest road node, and the
    straight-line snap distance is added at both ends of each trip;
  * Dijkstra shortest paths in km between all snapped points.

Output: npz with ``pts`` (P x 2 lon/lat, rounded to 6 dp, sorted) and ``D`` (P x P km),
which sangli_recom.py --distance-matrix and booth_mip.py --distance-matrix read.

Example:
    python build_road_matrix.py --pbf ../data_raw/geofabrik/western-zone-261022.osm.pbf \
        --units sangli_input/sangli_units.geojson --district sangli_input/sangli_district.geojson \
        --out sangli_road/road_distances.npz
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import osmium
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import shape

# pyrosm's "driving" network filter, so results match the notebook's road graph.
EXCLUDED_HIGHWAY = {
    "cycleway", "footway", "path", "pedestrian", "steps", "track", "corridor",
    "elevator", "escalator", "proposed", "construction", "bridleway", "abandoned",
    "platform", "raceway", "bus_guideway", "planned", "no", "razed",
}
EXCLUDED_SERVICE = {"parking", "parking_aisle", "private", "emergency_access"}
EARTH_KM = 6371.0088


def drivable(tags) -> bool:
    hw = tags.get("highway")
    if hw is None or hw in EXCLUDED_HIGHWAY:
        return False
    if tags.get("area") == "yes":
        return False
    if tags.get("service") in EXCLUDED_SERVICE:
        return False
    if tags.get("access") == "private" or tags.get("motor_vehicle") == "no" or tags.get("motorcar") == "no":
        return False
    return True


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


def read_roads(pbf: Path, area):
    minx, miny, maxx, maxy = area.bounds
    node_ids, lons, lats = {}, [], []
    src, dst = [], []

    def node_index(n):
        k = n.ref
        i = node_ids.get(k)
        if i is None:
            i = node_ids[k] = len(lons)
            lons.append(n.lon)
            lats.append(n.lat)
        return i

    fp = (osmium.FileProcessor(str(pbf))
          .with_locations()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.KeyFilter("highway")))
    for way in fp:
        if not drivable(way.tags):
            continue
        prev = None
        for n in way.nodes:
            if not n.location.valid():
                prev = None
                continue
            lon, lat = n.lon, n.lat
            if not (minx <= lon <= maxx and miny <= lat <= maxy):
                prev = None
                continue
            i = node_index(n)
            if prev is not None and prev != i:
                src.append(prev)
                dst.append(i)
            prev = i
    lons, lats = np.asarray(lons), np.asarray(lats)
    src, dst = np.asarray(src), np.asarray(dst)
    # clip to the buffered hull (bbox was only a fast pre-filter)
    inside = shapely.contains_xy(area, lons, lats)
    keep = inside[src] & inside[dst]
    return lons, lats, src[keep], dst[keep]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", type=Path, required=True)
    ap.add_argument("--units", type=Path, required=True, nargs="+",
                    help="unit GeoJSON(s); populated units' demand points become matrix rows")
    ap.add_argument("--district", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--buffer-deg", type=float, default=0.02)
    a = ap.parse_args()
    t0 = time.time()

    district_json = json.loads(a.district.read_text(encoding="utf-8"))
    geom = district_json["geometry"] if district_json.get("type") == "Feature" else district_json
    area = shape(geom).buffer(a.buffer_deg).convex_hull
    shapely.prepare(area)

    pts = set()
    for path in a.units:
        for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
            p = f["properties"]
            if (p.get("population") or 0) > 0:
                pts.add((round(float(p["demand_lon"]), 6), round(float(p["demand_lat"]), 6)))
    pts = np.array(sorted(pts))
    print(f"{len(pts)} populated demand points")

    lons, lats, src, dst = read_roads(a.pbf, area)
    print(f"read {len(src):,} drivable road segments in {time.time() - t0:.0f}s")

    n = len(lons)
    w = haversine_km(lons[src], lats[src], lons[dst], lats[dst])
    # Make the graph two-way and keep the shortest of any parallel segments
    # (a plain sparse matrix would add them together).
    order = np.lexsort((np.r_[w, w], np.r_[dst, src], np.r_[src, dst]))
    rs, cs, ws = np.r_[src, dst][order], np.r_[dst, src][order], np.r_[w, w][order]
    first = np.ones(len(rs), bool)
    first[1:] = (rs[1:] != rs[:-1]) | (cs[1:] != cs[:-1])
    A = coo_matrix((ws[first], (rs[first], cs[first])), shape=(n, n)).tocsr()

    ncomp, labels = connected_components(A, directed=False)
    big = np.bincount(labels).argmax()
    keep_nodes = np.flatnonzero(labels == big)
    remap = -np.ones(n, int)
    remap[keep_nodes] = np.arange(len(keep_nodes))
    A = A[keep_nodes][:, keep_nodes]
    lons, lats = lons[keep_nodes], lats[keep_nodes]
    print(f"road graph: {len(keep_nodes):,} nodes, {A.nnz // 2:,} segments "
          f"(largest of {ncomp:,} components)")

    lat0 = float(np.mean(pts[:, 1]))
    kx = 111.32 * math.cos(math.radians(lat0))
    tree = cKDTree(np.c_[lons * kx, lats * 111.32])
    _, snap = tree.query(np.c_[pts[:, 0] * kx, pts[:, 1] * 111.32])
    snap_km = haversine_km(pts[:, 0], pts[:, 1], lons[snap], lats[snap])
    print(f"snap distance km: median {np.median(snap_km):.2f}, 95th pct {np.percentile(snap_km, 95):.2f}, "
          f"max {snap_km.max():.2f}")

    D = np.vstack([dijkstra(A, directed=False, indices=snap[i:i + 64])[:, snap]
                   for i in range(0, len(snap), 64)])
    D = D + snap_km[:, None] + snap_km[None, :]
    np.fill_diagonal(D, 0)
    if not np.isfinite(D).all():
        raise RuntimeError("some demand points are unreachable by road")

    straight = haversine_km(pts[:, None, 0], pts[:, None, 1], pts[None, :, 0], pts[None, :, 1])
    off = ~np.eye(len(pts), dtype=bool) & (straight > 2)
    print(f"road / straight-line ratio (pairs > 2 km apart): median {np.median(D[off] / straight[off]):.3f}")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(a.out, pts=pts, D=D, snap_km=snap_km)
    info = {"pbf": str(a.pbf), "points": int(len(pts)), "road_nodes": int(len(keep_nodes)),
            "road_segments": int(A.nnz // 2), "snap_km_median": float(np.median(snap_km)),
            "snap_km_max": float(snap_km.max()),
            "road_over_straight_median": float(np.median(D[off] / straight[off])),
            "seconds": round(time.time() - t0, 1)}
    a.out.with_suffix(".json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
