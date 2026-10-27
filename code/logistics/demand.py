"""Where voters live: 2024 electors per settlement, split into hamlets, with the walk inside each.

1. **Electors 2024.** Each unit's Census 2011 population is scaled so that every official
   2008 AC adds up to its registered electors at the 2024 election. City ACs whose 2011
   rebuild is not trusted (audit: our rebuild and SHRUG's differ by more than 2%) share
   one pooled factor per district, which keeps the city's total right without trusting
   how people were split between its seats.
2. **Hamlets.** Each unit's people are placed on the WorldPop 2011 100 m grid (rescaled to the
   unit's electors). Populated cells within ``LINK_KM`` of each other form a settlement
   patch; patches under ``MIN_ELECTORS`` join the nearest bigger patch; a patch over
   ``SPLIT_ELECTORS`` is cut by k-means into pieces of about ``TARGET_ELECTORS``. Each
   piece becomes a demand point at its population-weighted medoid cell, which is also a
   candidate polling-station site.
3. **Walk inside the hamlet.** ``intra_km`` is the population-weighted mean straight-line
   distance from the piece's cells to its medoid, plus the mean distance inside a cell, times ``DETOUR``. A
   voter's walk to a station at site s is ``intra_km(i) + network_walk_km(i, s)``, so voting
   "in your own village" is no longer 0 km.

Usage:
    python -m logistics.demand --district sangli
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.windows import from_bounds
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely.geometry import shape
from sklearn.cluster import KMeans

from .common import CODE, DISTRICTS, ELECTORS, WORLDPOP_100M, boundary_geometry, local_xy_km, read_geojson, run_dir

LINK_KM = 0.25          # populated 100 m cells closer than this belong to one settlement patch
MIN_ELECTORS = 40       # smaller patches (single farmhouses) join the nearest patch
SPLIT_ELECTORS = 2400   # patches above this are split so stations can spread across them
TARGET_ELECTORS = 1200  # size of each piece of a split patch
DETOUR = 1.25           # lanes inside a settlement vs straight line
CENSUS_ELECTOR_RATIO = 0.70
# WorldPop 2011 smears people thinly over farmland (over half of Sangli's people sit in cells
# with < 5 per ha, where a village core holds 50-200). Within each unit only cells at least this
# share of the unit's densest cell count as settled; the unit's electors go to those cells.
DENSE_SHARE = 0.40


def official_assignment(district: str) -> pd.DataFrame:
    feats = read_geojson(DISTRICTS[district]["official"])["features"]
    return pd.DataFrame([{"id": f["properties"]["id"], "ac08_no": int(f["properties"]["ac08_no"]),
                          "ac08_name": f["properties"]["ac08_name"]} for f in feats])


def elector_factors(district: str, units: pd.DataFrame) -> pd.DataFrame:
    """Electors-2024 per 2011 person for each official AC, pooled over untrusted city ACs."""
    e = pd.read_csv(ELECTORS)
    seats = pd.read_csv(CODE / "analysis/official_audit/official_seats.csv")
    seats = seats[seats["district"] == DISTRICTS[district]["name"]]
    pop = units.groupby("ac08_no")["population"].sum()
    rows = []
    for ac, p in pop.items():
        el = int(e.loc[e["ac_no"] == ac, "electors_2024"].iloc[0])
        ver = seats.loc[seats["ac08_no"] == ac, "verified"]
        rows.append({"ac08_no": ac, "pop_2011": int(p), "electors_2024": el,
                     "verified": bool(ver.iloc[0]) if len(ver) else True})
    f = pd.DataFrame(rows)
    pooled = f.loc[~f["verified"], "electors_2024"].sum() / max(1, f.loc[~f["verified"], "pop_2011"].sum())
    f["factor"] = np.where(f["verified"], f["electors_2024"] / f["pop_2011"], pooled)
    f["pooled"] = ~f["verified"]
    return f


def load_units(district: str) -> tuple[pd.DataFrame, list]:
    feats = read_geojson(DISTRICTS[district]["units"])["features"]
    units = pd.DataFrame([f["properties"] for f in feats])
    geoms = [shape(f["geometry"]) for f in feats]
    units = units.merge(official_assignment(district), on="id", how="left")
    if units["ac08_no"].isna().any():
        raise SystemExit(f"{units['ac08_no'].isna().sum()} units have no official AC")
    units["ac08_no"] = units["ac08_no"].astype(int)
    return units, geoms


def read_grid(district: str, grid=WORLDPOP_100M):
    bound = boundary_geometry(DISTRICTS[district]["boundary"]).buffer(0.01)
    with rasterio.open(grid) as src:
        win = from_bounds(*bound.bounds, transform=src.transform).round_offsets().round_lengths()
        a = src.read(1, window=win).astype(float)
        tr = src.window_transform(win)
        nodata = src.nodata
    if nodata is not None:
        a[a == nodata] = 0
    a[~np.isfinite(a) | (a < 0)] = 0
    rows, cols = np.nonzero(a > 0)
    lon = tr.c + (cols + 0.5) * tr.a
    lat = tr.f + (rows + 0.5) * tr.e
    cell_km = abs(tr.a) * 111.32 * np.cos(np.radians(np.mean(lat)))
    return lon, lat, a[rows, cols], float(cell_km)


def medoid(xy: np.ndarray, w: np.ndarray) -> int:
    if len(xy) > 400:  # weighted medoid among the heaviest cells is plenty
        cand = np.argsort(w)[-400:]
    else:
        cand = np.arange(len(xy))
    d = np.linalg.norm(xy[cand][:, None, :] - xy[None, :, :], axis=2)
    return int(cand[np.argmin(d @ w)])


def pieces_for_unit(xy, w, electors, link_km=LINK_KM):
    """Split one unit's populated cells into demand pieces; returns list of index arrays."""
    if len(xy) == 1:
        return [np.array([0])]
    tree = cKDTree(xy)
    pairs = tree.query_pairs(link_km, output_type="ndarray")
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(xy), len(xy)))
    _, lab = connected_components(g, directed=False)
    share = np.bincount(lab, weights=w) / w.sum() * electors
    big = np.flatnonzero(share >= MIN_ELECTORS)
    if len(big) == 0:
        big = np.array([int(np.argmax(share))])
    # small patches join the nearest big patch (by nearest cell)
    big_cells = np.flatnonzero(np.isin(lab, big))
    bt = cKDTree(xy[big_cells])
    small_cells = np.flatnonzero(~np.isin(lab, big))
    if len(small_cells):
        _, k = bt.query(xy[small_cells])
        lab[small_cells] = lab[big_cells[k]]
    out = []
    for p in np.unique(lab):
        idx = np.flatnonzero(lab == p)
        el = w[idx].sum() / w.sum() * electors
        k = int(np.ceil(el / TARGET_ELECTORS)) if el > SPLIT_ELECTORS else 1
        k = min(k, len(idx))
        if k > 1:
            km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(xy[idx], sample_weight=w[idx])
            out += [idx[km.labels_ == j] for j in range(k) if (km.labels_ == j).any()]
        else:
            out.append(idx)
    return out


def build(district: str, grid=WORLDPOP_100M, dense_share: float = DENSE_SHARE, write: bool = True) -> dict:
    units, geoms = load_units(district)
    fac = elector_factors(district, units)
    units = units.merge(fac[["ac08_no", "factor"]], on="ac08_no")
    units["electors_2024"] = units["population"] * units["factor"]
    units["electors_2011"] = units["population"] * CENSUS_ELECTOR_RATIO

    glon, glat, gpop, cell_km = read_grid(district, grid)
    link_km = max(LINK_KM, 1.5 * cell_km)
    half_cell = 0.383 * cell_km  # mean distance from a uniform point in a square cell to its centre
    lat0 = float(np.mean(glat))
    gxy = local_xy_km(glon, glat, lat0)
    tree = shapely.STRtree(geoms)
    # each populated cell belongs to the unit polygon containing its centre
    hit_cell, hit_unit = tree.query(shapely.points(glon, glat), predicate="within")
    cell_unit = np.full(len(glon), -1)
    cell_unit[hit_cell] = hit_unit

    rows = []
    for u, g in enumerate(geoms):
        r = units.iloc[u]
        if r["population"] <= 0:
            continue
        cells = np.flatnonzero(cell_unit == u)
        if len(cells) == 0 or gpop[cells].sum() <= 0:
            # no populated 100 m cell inside: one point; the walk inside is that of a uniform
            # spread over the polygon (0.38 x sqrt(area) for a square)
            area_km2 = float(shapely.area(shapely.transform(g, lambda c: local_xy_km(c[:, 0], c[:, 1], lat0))))
            rows.append({"unit_id": r["id"], "piece": 0, "lon": r["demand_lon"], "lat": r["demand_lat"],
                         "share": 1.0, "intra_km": round(0.38 * np.sqrt(area_km2) * DETOUR, 3), "cells": 0})
            continue
        settled = gpop[cells] >= dense_share * gpop[cells].max()
        cells = cells[settled]
        w = gpop[cells]
        for j, idx in enumerate(pieces_for_unit(gxy[cells], w, r["electors_2024"], link_km)):
            ww = w[idx]
            m = medoid(gxy[cells][idx], ww)
            d = np.linalg.norm(gxy[cells][idx] - gxy[cells][idx][m], axis=1)
            rows.append({"unit_id": r["id"], "piece": j, "lon": float(glon[cells][idx][m]), "lat": float(glat[cells][idx][m]),
                         "share": float(ww.sum() / w.sum()),
                         "intra_km": round((float(d @ ww / ww.sum()) + half_cell) * DETOUR, 3), "cells": int(len(idx))})
    pts = pd.DataFrame(rows)
    keep = ["id", "name", "kind", "taluka", "ac08_no", "ac08_name", "population", "electors_2024", "electors_2011"]
    pts = pts.merge(units[keep].rename(columns={"id": "unit_id", "population": "unit_population"}), on="unit_id")
    for col in ("electors_2024", "electors_2011"):
        pts[col] = pts[col] * pts["share"]
    pts.insert(0, "pid", [f"{a}#{b}" for a, b in zip(pts["unit_id"], pts["piece"])])
    out = run_dir(district)
    if write:
        pts.to_csv(out / "demand_points.csv", index=False)
        fac.to_csv(out / "elector_factors.csv", index=False)
    info = {"dense_share": dense_share,
        "district": district, "grid": str(grid), "cell_km": round(cell_km, 3), "units_populated": int((units["population"] > 0).sum()),
        "demand_points": len(pts), "electors_2024": round(pts["electors_2024"].sum()),
        "electors_2011_census_x0.70": round(pts["electors_2011"].sum()),
        "intra_km_weighted_mean": round(float(np.average(pts["intra_km"], weights=pts["electors_2024"])), 3),
        "intra_km_p90": round(float(pts["intra_km"].quantile(0.9)), 3),
        "points_per_unit_max": int(pts.groupby("unit_id").size().max()),
        "units_without_grid_cells": int((pts["cells"] == 0).sum()),
    }
    info["settlements_per_village_median"] = float(pts[pts["kind"] == "VILLAGE"].groupby("unit_id").size().median())
    info["intra_km_village_median"] = round(float(pts.loc[pts["kind"] == "VILLAGE", "intra_km"].median()), 3)
    if write:
        (out / "demand_info.json").write_text(json.dumps(info, indent=2))
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--district", required=True, choices=sorted(DISTRICTS))
    ap.add_argument("--grid", default=str(WORLDPOP_100M), help="population raster (default WorldPop 2011 100 m)")
    ap.add_argument("--dense-share", type=float, default=DENSE_SHARE)
    ap.add_argument("--dry-run", action="store_true", help="report statistics without writing")
    a = ap.parse_args()
    print(json.dumps(build(a.district, a.grid, a.dense_share, not a.dry_run), indent=2))


if __name__ == "__main__":
    main()
