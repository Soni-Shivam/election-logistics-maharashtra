"""Walking and driving networks for one district, from the OpenStreetMap extract.

Two graphs share one node set (OSM nodes inside the district polygon buffered by
``--buffer-deg``):

* **walk**: every way a person can walk on (roads, footpaths, tracks, steps), weighted in
  km; voters' walking time is km / walking speed.
* **drive**: ways a bus or jeep can use, weighted in minutes from a speed per road class,
  slowed inside towns, which is how polling parties and EVMs move.

Points (villages, hamlets, booth sites, dispatch centres) are snapped to the nearest node
of each graph's largest connected component; the snap gap is covered at walking speed
on foot and at ``SNAP_KMH`` by vehicle.

Usage:
    python -m logistics.network --district sangli
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import osmium
import shapely
from scipy.sparse import coo_matrix, csr_matrix, load_npz, save_npz
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import shape

from .common import DISTRICTS, PBF, boundary_geometry, haversine_km, local_xy_km, read_geojson, run_dir

WALK_KMH = 4.5
SNAP_KMH = 15.0
URBAN_SPEED_FACTOR = 0.6  # congestion and narrow lanes inside towns
URBAN_KINDS = ("TOWN", "WARD", "APPROX_WARD", "GRID_CELL")

# Vehicle speeds by OSM highway class, km/h, for loaded buses and jeeps on Indian roads.
DRIVE_KMH = {
    "motorway": 60, "trunk": 50, "primary": 40, "secondary": 35, "tertiary": 30,
    "unclassified": 25, "residential": 20, "living_street": 10, "service": 15,
    "road": 20, "track": 12,
}
NOT_WALKABLE = {"motorway", "motorway_link", "construction", "proposed", "raceway",
                "bus_guideway", "planned", "abandoned", "razed", "elevator", "escalator", "no"}


def drive_speed(tags) -> float | None:
    hw = tags.get("highway")
    if hw is None:
        return None
    base = hw[:-5] if hw.endswith("_link") else hw
    if base not in DRIVE_KMH:
        return None
    if tags.get("access") in ("private", "no") or tags.get("motor_vehicle") == "no" or tags.get("motorcar") == "no":
        return None
    if base == "service" and tags.get("service") in ("parking_aisle", "private", "emergency_access"):
        return None
    return float(DRIVE_KMH[base])


def walkable(tags) -> bool:
    hw = tags.get("highway")
    if hw is None or hw in NOT_WALKABLE or tags.get("area") == "yes":
        return False
    return tags.get("foot") != "no" and tags.get("access") not in ("private", "no")


def read_ways(pbf, area):
    minx, miny, maxx, maxy = area.bounds
    ids, lons, lats = {}, [], []
    walk_e, drive_e, drive_kmh = [], [], []

    def idx(n):
        i = ids.get(n.ref)
        if i is None:
            i = ids[n.ref] = len(lons)
            lons.append(n.lon)
            lats.append(n.lat)
        return i

    fp = (osmium.FileProcessor(str(pbf)).with_locations()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.KeyFilter("highway")))
    for way in fp:
        w, v = walkable(way.tags), drive_speed(way.tags)
        if not w and v is None:
            continue
        prev = None
        for n in way.nodes:
            if not n.location.valid() or not (minx <= n.lon <= maxx and miny <= n.lat <= maxy):
                prev = None
                continue
            i = idx(n)
            if prev is not None and prev != i:
                if w:
                    walk_e.append((prev, i))
                if v is not None:
                    drive_e.append((prev, i))
                    drive_kmh.append(v)
            prev = i
    return np.asarray(lons), np.asarray(lats), np.asarray(walk_e).reshape(-1, 2), \
        np.asarray(drive_e).reshape(-1, 2), np.asarray(drive_kmh)


def symmetric_csr(src, dst, w, n) -> csr_matrix:
    # keep the cheapest of parallel edges (a plain sparse sum would add them)
    r, c, ww = np.r_[src, dst], np.r_[dst, src], np.r_[w, w]
    order = np.lexsort((ww, c, r))
    r, c, ww = r[order], c[order], ww[order]
    first = np.ones(len(r), bool)
    first[1:] = (r[1:] != r[:-1]) | (c[1:] != c[:-1])
    return coo_matrix((ww[first], (r[first], c[first])), shape=(n, n)).tocsr()


def largest_component_mask(g: csr_matrix) -> np.ndarray:
    _, labels = connected_components(g, directed=False)
    sizes = np.bincount(labels)
    sizes[labels[np.diff(g.indptr) == 0]] = 0  # isolated nodes never win
    return labels == sizes.argmax()


def build(district: str, buffer_deg: float = 0.03) -> dict:
    spec = DISTRICTS[district]
    t0 = time.time()
    area = boundary_geometry(spec["boundary"]).buffer(buffer_deg)
    shapely.prepare(area)
    lons, lats, we, de, dkmh = read_ways(PBF, area.envelope)
    inside = shapely.contains_xy(area, lons, lats)
    we = we[inside[we[:, 0]] & inside[we[:, 1]]]
    keep_d = inside[de[:, 0]] & inside[de[:, 1]]
    de, dkmh = de[keep_d], dkmh[keep_d]
    n = len(lons)

    # towns slow vehicles down: edges whose midpoint falls in a town or city-ward unit
    towns = [shape(f["geometry"]) for f in read_geojson(spec["units"])["features"]
             if f["properties"].get("kind") in URBAN_KINDS]
    urban = shapely.union_all(towns) if towns else None
    mid_lon = (lons[de[:, 0]] + lons[de[:, 1]]) / 2
    mid_lat = (lats[de[:, 0]] + lats[de[:, 1]]) / 2
    if urban is not None:
        shapely.prepare(urban)
        dkmh = np.where(shapely.contains_xy(urban, mid_lon, mid_lat), dkmh * URBAN_SPEED_FACTOR, dkmh)

    wk = haversine_km(lons[we[:, 0]], lats[we[:, 0]], lons[we[:, 1]], lats[we[:, 1]])
    dk = haversine_km(lons[de[:, 0]], lats[de[:, 0]], lons[de[:, 1]], lats[de[:, 1]])
    walk = symmetric_csr(we[:, 0], we[:, 1], wk, n)
    drive_min = symmetric_csr(de[:, 0], de[:, 1], dk / dkmh * 60, n)
    drive_km = symmetric_csr(de[:, 0], de[:, 1], dk, n)
    walk_ok = largest_component_mask(walk)
    drive_ok = largest_component_mask(drive_min)

    out = run_dir(district)
    np.savez(out / "network_nodes.npz", lon=lons, lat=lats, walk_ok=walk_ok, drive_ok=drive_ok)
    save_npz(out / "network_walk_km.npz", walk)
    save_npz(out / "network_drive_min.npz", drive_min)
    save_npz(out / "network_drive_km.npz", drive_km)
    info = {"district": district, "nodes": int(n), "walk_edges": int(walk.nnz // 2),
            "drive_edges": int(drive_min.nnz // 2), "walk_component_nodes": int(walk_ok.sum()),
            "drive_component_nodes": int(drive_ok.sum()), "seconds": round(time.time() - t0, 1),
            "walk_kmh": WALK_KMH, "drive_kmh": DRIVE_KMH, "urban_speed_factor": URBAN_SPEED_FACTOR}
    (out / "network_info.json").write_text(json.dumps(info, indent=2))
    return info


class Network:
    """Loaded graphs plus snapping and shortest-path helpers."""

    def __init__(self, district: str):
        d = run_dir(district)
        z = np.load(d / "network_nodes.npz")
        self.lon, self.lat = z["lon"], z["lat"]
        self.walk = load_npz(d / "network_walk_km.npz").tocsr()
        self.drive_min = load_npz(d / "network_drive_min.npz").tocsr()
        self.drive_km = load_npz(d / "network_drive_km.npz").tocsr()
        self.lat0 = float(np.mean(self.lat))
        xy = local_xy_km(self.lon, self.lat, self.lat0)
        self._walk_idx = np.flatnonzero(z["walk_ok"])
        self._drive_idx = np.flatnonzero(z["drive_ok"])
        self._walk_tree = cKDTree(xy[self._walk_idx])
        self._drive_tree = cKDTree(xy[self._drive_idx])

    def local(self, lon, lat, pad_deg: float = 0.06) -> "Network":
        """A copy restricted to the box around the given points plus ``pad_deg``: shortest
        paths inside one seat run on a fraction of the district graph."""
        x0, x1 = np.min(lon) - pad_deg, np.max(lon) + pad_deg
        y0, y1 = np.min(lat) - pad_deg, np.max(lat) + pad_deg
        keep = np.flatnonzero((self.lon >= x0) & (self.lon <= x1) & (self.lat >= y0) & (self.lat <= y1))
        sub = object.__new__(Network)
        sub.lon, sub.lat, sub.lat0 = self.lon[keep], self.lat[keep], self.lat0
        sub.walk = self.walk[keep][:, keep]
        sub.drive_min = self.drive_min[keep][:, keep]
        sub.drive_km = self.drive_km[keep][:, keep]
        xy = local_xy_km(sub.lon, sub.lat, sub.lat0)
        pos = -np.ones(len(self.lon), int)
        pos[keep] = np.arange(len(keep))
        sub._walk_idx = pos[np.intersect1d(self._walk_idx, keep)]
        sub._drive_idx = pos[np.intersect1d(self._drive_idx, keep)]
        sub._walk_tree = cKDTree(xy[sub._walk_idx])
        sub._drive_tree = cKDTree(xy[sub._drive_idx])
        return sub

    def snap(self, lon, lat, mode: str):
        xy = local_xy_km(lon, lat, self.lat0)
        tree, idx = (self._walk_tree, self._walk_idx) if mode == "walk" else (self._drive_tree, self._drive_idx)
        gap, k = tree.query(xy)
        return idx[k], gap

    def walk_km(self, src_lon, src_lat, dst_lon, dst_lat, limit_km: float | None = None) -> np.ndarray:
        """Walking km from each source to each destination (inf beyond ``limit_km``)."""
        s, sg = self.snap(src_lon, src_lat, "walk")
        t, tg = self.snap(dst_lon, dst_lat, "walk")
        out = np.empty((len(s), len(t)))
        lim = np.inf if limit_km is None else limit_km
        for a in range(0, len(s), 256):
            d = dijkstra(self.walk, directed=False, indices=s[a:a + 256], limit=lim)
            out[a:a + 256] = d[:, t]
        return out + sg[:, None] + tg[None, :]

    def drive(self, src_lon, src_lat, dst_lon, dst_lat):
        """(minutes, km) by vehicle from each source to each destination.

        Minutes follow the fastest path; km follow the shortest path, a close stand-in
        for the km a vehicle is paid for."""
        s, sg = self.snap(src_lon, src_lat, "drive")
        t, tg = self.snap(dst_lon, dst_lat, "drive")
        mins = np.empty((len(s), len(t)))
        kms = np.empty((len(s), len(t)))
        for a in range(0, len(s), 128):
            mins[a:a + 128] = dijkstra(self.drive_min, directed=False, indices=s[a:a + 128])[:, t]
            kms[a:a + 128] = dijkstra(self.drive_km, directed=False, indices=s[a:a + 128])[:, t]
        snap_min = (sg[:, None] + tg[None, :]) / SNAP_KMH * 60
        return mins + snap_min, kms + sg[:, None] + tg[None, :]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", required=True, choices=sorted(DISTRICTS))
    a = ap.parse_args()
    print(json.dumps(build(a.district), indent=2))


if __name__ == "__main__":
    main()
