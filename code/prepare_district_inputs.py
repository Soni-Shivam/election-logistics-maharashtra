"""Build the constituency-optimizer input map for one 2011 Census district.

Population and Census IDs are exact 2011 PCA values. Village/town land units
come from SHRUG's open PC11 polygons. Any settlement larger than
split_share x (district population / seats) must be listed in a district
config file (district_config/<name>.json) with a ward source, so that it can
be divided into wards; otherwise the script stops and names it. Two ward
sources are supported:

* ``approx_labels``: ward populations (CSV) plus rough ward-label positions
  read off an official ward map image. The labels are mapped onto the 2011
  town polygon and turned into Voronoi pieces. These are schematic research
  polygons, NOT official ward GIS lines (the Sangli-Miraj-Kupwad method).
* ``polygons``: a ward GeoJSON (with populations in a property or a CSV),
  clipped to the 2011 town polygon. Use this for big cities.

District land missing from the village layer becomes explicit zero-population
gap units; each gets the taluka of its largest neighbouring unit. The final
population must equal the district's Census total.

A current district that is part of a 2011 district (Palghar and today's Thane,
both from 2011 Thane) is built with --current-district and --crosswalk, which
restrict the units to the listed 2011 talukas.

Example (Sangli):

    python prepare_district_inputs.py --state-id 27 --district-id 531 \\
        --census DDW_PCA2735_2011_MDDS.xlsx --villages shrug_pc11_village.gpkg \\
        --district shrug_pc11_district.gpkg --config district_config/sangli.json \\
        --out sangli_input
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from shapely import wkb
from shapely.geometry import MultiPoint, Point, mapping, shape

from apportion_seats import largest_remainder

CENSUS_COLUMNS = {"Subdistt", "Town/Village", "Level", "Name", "TRU", "TOT_P"}
DATA_HELP = {
    "census": "Census 2011 Primary Census Abstract, district file (DDW_PCA<state><district>_2011_MDDS*.xlsx) "
              "from https://censusindia.gov.in/nada/index.php/catalog/ (PCA, Maharashtra)",
    "villages": "SHRUG open PC11 village/town polygons GeoPackage (table village_modified) from "
                "https://docs.devdatalab.org/SHRUG-Construction-Details/shrug-open-source-polygons/",
    "district": "SHRUG open PC11 district polygons GeoPackage (table district), same source",
    "subdistricts": "SHRUG open PC11 subdistrict polygons GeoPackage (table subdistrict), same source",
}


def fail(message: str) -> None:
    raise SystemExit(f"prepare_district_inputs: {message}")


# ---------------------------------------------------------------- readers

def gpkg_rows(path, table, columns, where="", params=()):
    conn = sqlite3.connect(path)
    try:
        column_sql = ',' + ','.join(columns) if columns else ''
        query = f"SELECT geom{column_sql} FROM {table} {where}"
        for row in conn.execute(query, params):
            # GeoPackage's 8-byte header plus an optional envelope precedes WKB.
            blob = row[0]
            flags = blob[3]
            envelope_bytes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[((flags >> 1) & 7)]
            geom = wkb.loads(blob[8 + envelope_bytes:])
            yield geom, row[1:]
    finally:
        conn.close()


def gpkg_where(path, table, state_id, district_id, extra=""):
    """WHERE clause for one district, using pc11_state_id only if present."""
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()
    if not cols:
        fail(f"{path} has no table {table!r}")
    if "pc11_district_id" not in cols:
        fail(f"{path}:{table} has no pc11_district_id column; columns: {sorted(cols)}")
    where, params = "WHERE pc11_district_id=?", [district_id]
    if "pc11_state_id" in cols:
        where += " AND pc11_state_id=?"
        params.append(state_id)
    return where + extra, params


def read_census(path: Path, sheet: str | None, state_id: str, district_id: str) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        table = pd.read_csv(path, dtype=str).fillna('')
    else:
        try:
            sheets = pd.read_excel(path, sheet_name=sheet if sheet else None, dtype=str)
        except ImportError as exc:
            fail(f"reading {path.name} needs an Excel reader ({exc}). Run: pip install openpyxl")
        except (ValueError, OSError) as exc:
            fail(f"cannot read {path} as a Census PCA workbook: {exc}")
        if sheet:
            table = sheets
        else:
            usable = {k: v for k, v in sheets.items() if CENSUS_COLUMNS <= set(v.columns)}
            if len(usable) != 1:
                fail(f"{path}: cannot tell which sheet holds the PCA table (candidates "
                     f"{list(usable) or list(sheets)}); pass --census-sheet")
            table = next(iter(usable.values()))
        table = table.fillna('')
    missing = CENSUS_COLUMNS - set(table.columns)
    if missing:
        fail(f"{path}: missing Census columns {sorted(missing)}")
    # A state-wide file holds every district; a district file holds one.
    if "District" in table.columns:
        table = table[table["District"].astype(str).str.zfill(3).isin({district_id, "000"})]
    if "State" in table.columns:
        table = table[table["State"].astype(str).str.zfill(2) == state_id]
    return table


def read_ward_populations(path: Path) -> dict[int, int]:
    if not path.is_file():
        fail(f"ward population file not found: {path}")
    out = {}
    with path.open(newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            out[int(r['ward_no'])] = int(r['population'])
    return out


# --------------------------------------------------------------- geometry

def polygon_parts(geom):
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return [g for g in geom.geoms if g.geom_type == "Polygon"]


def wards_from_labels(city, labels: dict[int, tuple[float, float]]):
    """Voronoi pieces of the town polygon around rescaled ward-label positions."""
    wards = sorted(labels)
    n = len(wards)
    minx, miny, maxx, maxy = city.bounds
    px = [x for x, y in labels.values()]
    py = [y for x, y in labels.values()]
    wanted = []
    for ward in wards:
        x, y = labels[ward]
        lon = minx + (x - min(px)) / (max(px) - min(px)) * (maxx - minx)
        lat = maxy - (y - min(py)) / (max(py) - min(py)) * (maxy - miny)
        wanted.append((lon, lat))
    # Assign each label to a distinct point on a fine interior grid. This
    # preserves approximate ordering even where the ward-map and 2011 town
    # shapes differ enough that several raw labels would land outside the town.
    candidates = [(float(x), float(y)) for x in np.linspace(minx, maxx, 100)
                  for y in np.linspace(miny, maxy, 60) if city.contains(Point(x, y))]
    if len(candidates) < n:
        fail(f"town polygon too small for {n} label points")
    candidate_xy = np.asarray(candidates)
    desired_xy = np.asarray(wanted)
    cost = ((desired_xy[:, None, :] - candidate_xy[None, :, :]) ** 2).sum(axis=2)
    points = [None] * n
    used = set()
    for r in sorted(range(n), key=lambda i: -float(cost[i].min())):
        c = next(int(j) for j in np.argsort(cost[r]) if int(j) not in used)
        used.add(c)
        points[r] = Point(*candidates[c])
    voronoi = shapely.voronoi_polygons(MultiPoint(points), extend_to=city.envelope)
    by_ward = {}
    for raw in voronoi.geoms:
        owner = [wards[k] for k in range(n) if raw.covers(points[k])]
        if len(owner) != 1:
            raise ValueError(f'Voronoi ownership ambiguity: {owner}')
        by_ward[owner[0]] = raw.intersection(city)
    return by_ward


def wards_from_polygons(city, path: Path, ward_field: str):
    """Ward polygons clipped to the 2011 town; leftover town land joins the
    ward it shares the longest border with."""
    if not path.is_file():
        fail(f"ward polygon file not found: {path}")
    data = json.loads(path.read_text(encoding='utf-8'))
    by_ward, populations = {}, {}
    for f in data["features"]:
        p = f.get("properties") or {}
        if ward_field not in p:
            fail(f"{path}: a ward feature has no {ward_field!r} property")
        ward = int(p[ward_field])
        by_ward[ward] = shape(f["geometry"]).buffer(0).intersection(city)
        if "population" in p:
            populations[ward] = int(p["population"])
    rest = city.difference(shapely.union_all(list(by_ward.values())))
    for part in polygon_parts(rest) if not rest.is_empty else []:
        if part.area <= 1e-12:
            continue
        ward = max(by_ward, key=lambda w: part.boundary.intersection(by_ward[w].boundary).length)
        by_ward[ward] = by_ward[ward].union(part)
    return by_ward, populations


_RASTERS: dict[str, tuple] = {}


def read_lonlat_raster(path: Path):
    """A north-up lon/lat GeoTIFF (e.g. WorldPop 30 arc-second population) as
    (array, west, north, pixel_deg). Nodata and negative values become 0."""
    key = str(path.resolve())
    if key not in _RASTERS:
        if not path.is_file():
            fail(f"population raster not found: {path}")
        try:
            import tifffile
        except ImportError:
            fail("raster_cells needs tifffile: pip install tifffile imagecodecs")
        with tifffile.TiffFile(path) as tif:
            page = tif.pages[0]
            tags = {t.name: t.value for t in page.tags}
            sx, sy, _ = tags["ModelPixelScaleTag"]
            tie = tags["ModelTiepointTag"]
            if abs(sx - sy) > 1e-12:
                fail(f"{path}: non-square pixels are not supported")
            arr = page.asarray().astype(np.float64)
        arr[~np.isfinite(arr) | (arr < 0)] = 0.0
        _RASTERS[key] = (arr, float(tie[3]), float(tie[4]), float(sx))
    return _RASTERS[key]


def wards_from_raster(city, path: Path, min_piece_share: float = 0.15):
    """Cut the town polygon along the raster grid. Each grid cell's population is
    shared among its pieces by area; pieces smaller than min_piece_share of a full
    cell (and extra parts of a cell split by the town edge) join the neighbouring
    piece they share the longest border with, so every piece is one polygon."""
    arr, west, north, d = read_lonlat_raster(path)
    minx, miny, maxx, maxy = city.bounds
    c0, c1 = int(np.floor((minx - west) / d)), int(np.ceil((maxx - west) / d))
    r0, r1 = int(np.floor((north - maxy) / d)), int(np.ceil((north - miny) / d))
    shapely.prepare(city)
    pieces = []  # [geom, population]
    for r in range(r0, r1):
        for c in range(c0, c1):
            cell = shapely.box(west + c * d, north - (r + 1) * d, west + (c + 1) * d, north - r * d)
            if not city.intersects(cell):
                continue
            part = city.intersection(cell)
            parts = [g for g in polygon_parts(part) if g.area > 1e-12]
            for g in parts:
                pieces.append([g, arr[r, c] * g.area / cell.area])
    if not pieces:
        fail("town polygon does not overlap the population raster")
    small = min_piece_share * d * d
    # Merge small pieces into neighbours, smallest first, until none remain.
    # A sliver with no shared border (corner contact only) stays as its own piece.
    stuck = set()
    while len(pieces) > 1:
        cand = [i for i, p in enumerate(pieces) if p[0].area < small and id(p) not in stuck]
        if not cand:
            break
        idx = min(cand, key=lambda i: pieces[i][0].area)
        g, pop = pieces[idx]
        tree = shapely.STRtree([p[0] for p in pieces])
        best, best_len = None, 0.0
        for j in tree.query(g, predicate="intersects"):
            j = int(j)
            if j != idx:
                length = g.boundary.intersection(pieces[j][0].boundary).length
                if length > best_len:
                    best, best_len = j, length
        if best is None:
            stuck.add(id(pieces[idx]))
            continue
        pieces[best] = [shapely.union_all([pieces[best][0], g]), pieces[best][1] + pop]
        pieces.pop(idx)
    by_ward, weights = {}, {}
    for n, (g, pop) in enumerate(sorted(pieces, key=lambda p: (-p[0].centroid.y, p[0].centroid.x)), start=1):
        by_ward[n], weights[n] = g, pop
    return by_ward, weights


def make_town_wards(key, city, census_pop, spec, config_dir: Path):
    method = spec.get("ward_method", "approx_labels")
    populations = {}
    if spec.get("ward_population_csv"):
        populations = read_ward_populations(config_dir / spec["ward_population_csv"])
    if method == "approx_labels":
        labels = {int(k): tuple(v) for k, v in spec.get("ward_labels", {}).items()}
        if not labels:
            fail(f"town {key}: ward_method approx_labels needs ward_labels")
        if set(labels) != set(populations):
            fail(f"town {key}: ward numbers differ between ward_labels and ward populations")
        by_ward = wards_from_labels(city, labels)
    elif method == "polygons":
        if not spec.get("ward_polygons"):
            fail(f"town {key}: ward_method polygons needs ward_polygons (GeoJSON)")
        by_ward, from_file = wards_from_polygons(city, config_dir / spec["ward_polygons"],
                                                 spec.get("ward_field", "ward_no"))
        populations = populations or from_file
        if set(by_ward) != set(populations):
            fail(f"town {key}: ward numbers differ between ward polygons and populations")
    elif method == "raster_cells":
        if not spec.get("raster"):
            fail(f"town {key}: ward_method raster_cells needs raster (a lon/lat population GeoTIFF)")
        by_ward, weights = wards_from_raster(city, config_dir / spec["raster"],
                                             float(spec.get("min_piece_share", 0.15)))
        total_w = sum(weights.values())
        if total_w <= 0:
            fail(f"town {key}: the raster has no population inside the town polygon")
        # Shares from the raster, total from the Census (rounded to whole people).
        populations = largest_remainder({w: max(v, 1e-9) for w, v in weights.items()}, census_pop, 0)
    else:
        fail(f"town {key}: unknown ward_method {method!r} (use approx_labels, polygons or raster_cells)")
    if any(g.is_empty for g in by_ward.values()):
        fail(f"town {key}: empty ward pieces {[w for w, g in by_ward.items() if g.is_empty]}")
    cover = shapely.union_all(list(by_ward.values()))
    if city.symmetric_difference(cover).area > 1e-8:
        raise ValueError(f"Wards of town {key} do not cover its 2011 polygon")
    total = sum(populations.values())
    if total != census_pop:
        if spec.get("population_check", "exact") != "rescale":
            fail(f"town {key}: ward populations sum to {total:,}, Census 2011 total is "
                 f"{census_pop:,}. Fix the ward file or set \"population_check\": \"rescale\"")
        populations = largest_remainder(populations, census_pop, 0)
    return by_ward, populations, method


def feature(geom, **properties):
    return {"type": "Feature", "geometry": mapping(geom), "properties": properties}


# ------------------------------------------------------------- crosswalk

def crosswalk_talukas(path: Path, current: str, district_id: str, talukas: dict[str, str]):
    """2011 subdistrict codes making up a current district, or None if the
    current district is the whole 2011 district."""
    if not path.is_file():
        fail(f"crosswalk not found: {path}")
    rows = [r for r in csv.DictReader(path.open(newline='', encoding='utf-8'))
            if r["current_district"].strip().casefold() == current.casefold()]
    if not rows:
        return None
    by_name = {re.sub(r"\s+", " ", n).strip().casefold(): code for code, n in talukas.items()}
    codes = []
    for r in rows:
        if str(r["pc11_district_id"]).zfill(3) != district_id:
            fail(f"crosswalk row for {current} names 2011 district {r['pc11_district_id']}, "
                 f"not --district-id {district_id}")
        code = (r.get("pc11_subdistrict_id") or "").strip()
        if code:
            code = code.zfill(5)
        else:
            names = [n.strip().casefold() for n in r["pc11_subdistrict_name"].split("|")]
            code = next((by_name[n] for n in names if n in by_name), "")
        if code not in talukas:
            fail(f"crosswalk: 2011 taluka {r['pc11_subdistrict_name']!r} "
                 f"{r.get('pc11_subdistrict_id') or ''} not in the Census file. Talukas present: "
                 + ", ".join(f"{c} {n}" for c, n in sorted(talukas.items())))
        codes.append(code)
    return sorted(set(codes))


# ------------------------------------------------------------------ main

def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument('--state-id', required=True, help="PC11 state code, e.g. 27 (Maharashtra)")
    ap.add_argument('--district-id', required=True, help="PC11 (Census 2011) district code, e.g. 531 (Sangli)")
    ap.add_argument('--census', type=Path, default=None, help=DATA_HELP["census"])
    ap.add_argument('--census-sheet', default=None, help="Excel sheet name (default: config, then auto-detect)")
    ap.add_argument('--villages', type=Path, default=None, help=DATA_HELP["villages"])
    ap.add_argument('--district', type=Path, default=None, help=DATA_HELP["district"])
    ap.add_argument('--subdistricts', type=Path, default=None,
                    help=DATA_HELP["subdistricts"] + "; needed with --current-district")
    ap.add_argument('--config', type=Path, default=None, help="district config JSON (towns to split, seats, name)")
    ap.add_argument('--seats', type=int, default=None, help="ACs in this district (default: --allocation, then the config)")
    ap.add_argument('--allocation', type=Path, default=None,
                    help="allocation CSV written by apportion_seats.py (district,seats)")
    ap.add_argument('--current-district', default=None,
                    help="current district name when it differs from the 2011 district (e.g. Palghar)")
    ap.add_argument('--crosswalk', type=Path, default=Path(__file__).with_name('data') / 'district_crosswalk_2011_to_current.csv')
    ap.add_argument('--split-share', type=float, default=None,
                    help="settlements above split_share x target must be split (default 0.25)")
    ap.add_argument('--balance', type=float, default=0.10, help="seat population band, as in sangli_recom.py")
    ap.add_argument('--name', default=None, help="district name for file names and maps")
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args(argv)

    state_id = str(args.state_id).zfill(2)
    district_id = str(args.district_id).zfill(3)
    config, config_dir = {}, Path.cwd()
    if args.config is not None:
        if not args.config.is_file():
            fail(f"config not found: {args.config}")
        config = json.loads(args.config.read_text(encoding='utf-8'))
        config_dir = args.config.resolve().parent
        for k, v in (("pc11_state_id", state_id), ("pc11_district_id", district_id)):
            if k in config and str(config[k]).zfill(len(v)) != v:
                fail(f"{args.config} is for {k}={config[k]}, not {v}")
    needed = {"census": args.census, "villages": args.villages, "district": args.district}
    if args.current_district:
        needed["subdistricts"] = args.subdistricts
    missing = [k for k, p in needed.items() if p is None or not Path(p).is_file()]
    if missing:
        fail("missing input data:\n" + "\n".join(
            f"  --{k} {needed[k] or '(not given)'}: {DATA_HELP[k]}" for k in missing))

    census = read_census(args.census, args.census_sheet or config.get("census_sheet"),
                         state_id, district_id)
    records, talukas = {}, {}
    census_total, census_name = None, None
    taluka_total = {}
    for _, r in census.iterrows():
        key = (str(r['Subdistt']).zfill(5), str(r['Town/Village']).zfill(6))
        if r['Level'] in ('VILLAGE', 'TOWN'):
            records[key] = (str(r['Name']), int(r['TOT_P']), str(r['Level']))
        if r['Level'] == 'SUB-DISTRICT' and r['TRU'] == 'Total':
            talukas[key[0]] = str(r['Name'])
            taluka_total[key[0]] = int(r['TOT_P'])
        if r['Level'] == 'DISTRICT' and r['TRU'] == 'Total':
            census_total, census_name = int(r['TOT_P']), str(r['Name']).strip()
    if not records:
        fail(f"no VILLAGE/TOWN rows for state {state_id} district {district_id} in {args.census}")
    if census_total is None:
        fail(f"{args.census} has no DISTRICT/Total row to check the population against")

    keep = None
    if args.current_district:
        keep = crosswalk_talukas(args.crosswalk, args.current_district, district_id, talukas)
        if keep is None:
            print(f"{args.current_district}: not in {args.crosswalk}; using the whole 2011 district")
    name = args.name or config.get("name") or args.current_district or census_name.title()
    if keep is not None:
        records = {k: v for k, v in records.items() if k[0] in keep}
        talukas = {k: v for k, v in talukas.items() if k in keep}
        census_total = sum(taluka_total[k] for k in keep)
    if sum(p for _, p, _ in records.values()) != census_total:
        fail(f"village+town rows sum to {sum(p for _, p, _ in records.values()):,}, "
             f"Census total is {census_total:,}")

    seats = args.seats
    if seats is None and args.allocation is not None:
        alloc = pd.read_csv(args.allocation)
        row = alloc[alloc["district"].str.strip().str.casefold() == name.casefold()]
        if len(row) != 1:
            fail(f"{args.allocation}: no single row for district {name!r}")
        seats = int(row["seats"].iloc[0])
    seats = seats or config.get("seats")
    if not seats:
        fail("number of ACs unknown: pass --seats, --allocation, or set seats in the config")
    split_share = args.split_share if args.split_share is not None else config.get("split_share", 0.25)
    target = census_total / seats
    split_floor = split_share * target
    upper = int(target * (1 + args.balance))
    towns = config.get("towns", {})

    large = {f"{k[0]}-{k[1]}": (n, p, kind) for k, (n, p, kind) in records.items() if p > split_floor}
    unlisted = sorted(set(large) - set(towns), key=lambda k: -large[k][1])
    if unlisted:
        fail("these settlements exceed split_share x target = "
             f"{split_share:g} x {target:,.0f} = {split_floor:,.0f} people and must be listed under "
             f"\"towns\" in a district config with a ward source (see district_config/sangli.json):\n"
             + "\n".join(f"  {k} {large[k][0]} ({large[k][2]}, population {large[k][1]:,})" for k in unlisted))
    for k, spec in towns.items():
        if tuple(k.split("-")) not in records:
            fail(f"config town {k} is not in the Census file for this district")
        town_name, pop, _ = records[tuple(k.split("-"))]
        if spec.get("keep_whole"):
            if pop > upper:
                fail(f"town {k} {town_name} has {pop:,} people, above the seat maximum "
                     f"{upper:,}; it cannot be kept whole")
        elif pop < split_floor:
            fail(f"config town {k} {town_name} has {pop:,} people, below the {split_floor:,.0f} "
                 "split threshold; sangli_recom.py would reject its wards. Remove it from the config")
    split_keys = {tuple(k.split("-")): spec for k, spec in towns.items() if not spec.get("keep_whole")}
    # "absorb": small towns inside or against a split city (cantonments) whose polygons
    # touch dozens of city pieces. As separate units they become hubs that random
    # spanning trees cannot cut around, so they are cut into the city's pieces instead.
    absorbed = {}
    for k, spec in split_keys.items():
        for a in spec.get("absorb", []):
            ak = tuple(a.split("-"))
            if ak not in records:
                fail(f"town {'-'.join(k)}: absorb lists {a}, which is not in the Census file")
            if ak in split_keys or ak in absorbed:
                fail(f"town {a} is absorbed twice or is itself split")
            absorbed[ak] = k
    absorb_geoms = {k: [] for k in split_keys}
    absorb_pop = {k: 0 for k in split_keys}

    where, params = gpkg_where(args.district, 'district', state_id, district_id)
    district_rows = list(gpkg_rows(args.district, 'district', [], where, params))
    if len(district_rows) != 1:
        fail(f"{args.district}: expected one district polygon for {district_id}, got {len(district_rows)}")
    district = district_rows[0][0]
    if keep is not None:
        where, params = gpkg_where(args.subdistricts, 'subdistrict', state_id, district_id)
        parts = [g for g, (code,) in gpkg_rows(args.subdistricts, 'subdistrict',
                 ['pc11_subdistrict_id'], where, params) if str(code).zfill(5) in keep]
        if len(parts) != len(keep):
            fail(f"{args.subdistricts}: found {len(parts)} of {len(keep)} subdistrict polygons")
        district = shapely.union_all(parts)

    where, params = gpkg_where(args.villages, 'village_modified', state_id, district_id)
    village_rows = [row for row in gpkg_rows(args.villages, 'village_modified',
                    ['pc11_subdistrict_id', 'pc11_town_village_id'], where, params)
                    if keep is None or str(row[1][0]).zfill(5) in keep]
    features = []
    seen = set()
    split_geoms = {}
    for geom, (subdist, code) in village_rows:
        key = (str(subdist).zfill(5), str(code).zfill(6))
        if key not in records:
            raise ValueError(f'No Census row for {key}')
        if key in seen:
            raise ValueError(f'Duplicate map row {key}')
        seen.add(key)
        name_, pop, kind = records[key]
        if key in split_keys:
            split_geoms[key] = geom
            continue
        if key in absorbed:
            absorb_geoms[absorbed[key]].append(geom)
            absorb_pop[absorbed[key]] += pop
            continue
        pos = geom.representative_point()
        features.append(feature(geom, id=f'{key[0]}-{key[1]}', population=pop,
            name=name_, kind=kind, taluka=talukas[key[0]], settlement_id=f'{key[0]}-{key[1]}',
            demand_lon=pos.x, demand_lat=pos.y, location_method='polygon_interior'))
    if seen != set(records):
        absent = sorted(set(records) - seen)
        raise ValueError(f'Missing {len(absent)} Census settlements in the village layer, e.g. {absent[:5]}')

    ward_counts = {}
    for key, spec in split_keys.items():
        town_name, town_pop, _ = records[key]
        sid = f'{key[0]}-{key[1]}'
        city_geom = shapely.union_all([split_geoms[key], *absorb_geoms[key]]) if absorb_geoms[key] else split_geoms[key]
        town_pop += absorb_pop[key]
        by_ward, populations, method = make_town_wards(sid, city_geom, town_pop, spec, config_dir)
        ward_counts[sid] = len(by_ward)
        approx = method == "approx_labels"
        cells = method == "raster_cells"
        prefix = spec.get("id_prefix", f"{sid}-{'C' if cells else 'W'}")
        default_label = (f"{spec.get('name', town_name)} grid cell {{ward}}" if cells else
                         f"{spec.get('name', town_name)} {'approximate ' if approx else ''}ward {{ward}}")
        label = spec.get("ward_name", default_label)
        kind = 'GRID_CELL' if cells else 'APPROX_WARD' if approx else 'WARD'
        note = ("WorldPop-style raster cell clipped to the 2011 town polygon; population = raster share "
                "x Census town total" if cells else
                "Approximate ward pieces on the 2011 town polygon; not official GIS" if approx else
                "Ward polygons clipped to the 2011 town polygon")
        width = max(2, len(str(len(by_ward))))
        for ward in sorted(by_ward):
            geom = by_ward[ward]
            pos = geom.representative_point()
            features.append(feature(geom, id=f'{prefix}{ward:0{width}d}', population=populations[ward],
                name=label.format(ward=ward), kind=kind,
                taluka=talukas[key[0]], settlement_id=sid,
                demand_lon=pos.x, demand_lat=pos.y,
                location_method=('grid_cell_interior' if cells else
                                 'approx_ward_polygon_interior' if approx else 'ward_polygon_interior'),
                geometry_note=spec.get("geometry_note", note)))

    geoms = [shape(f['geometry']) for f in features]
    covered = shapely.union_all(geoms)
    gap = district.difference(covered)
    # Ignore floating-point overlay dust (far below the optimizer's 0.1 km²
    # tiling tolerance); retain real unmapped land parcels.
    gap_parts = [g for g in polygon_parts(gap) if g.area > 1e-9]
    tree = shapely.STRtree(geoms)
    merged_gaps = 0
    kept_gaps = []
    for geom in gap_parts:
        # "merge_gap_units": zero-population slivers join the neighbour they share the
        # longest border with (populations unchanged). Long slivers touch many units and
        # act as hubs, like cantonments; isolated ones stay as their own unit.
        if config.get("merge_gap_units"):
            border = {int(j): geom.boundary.intersection(geoms[int(j)].boundary).length
                      for j in tree.query(geom, predicate="intersects")}
            border = {j: v for j, v in border.items() if v > 0}
            if border:
                j = max(border, key=border.get)
                geoms[j] = shapely.union_all([geoms[j], geom])
                features[j]['geometry'] = mapping(geoms[j])
                merged_gaps += 1
                continue
        kept_gaps.append(geom)
    gap_parts = kept_gaps
    for idx, geom in enumerate(gap_parts, 1):
        taluka = config.get("gap_taluka")
        if taluka is None:
            touching = [int(j) for j in tree.query(geom, predicate="intersects")
                        if geom.boundary.intersection(geoms[int(j)].boundary).length > 0]
            if not touching:
                touching = [int(tree.nearest(geom))]
            taluka = features[max(touching, key=lambda j: geoms[j].area)]['properties']['taluka']
        pos = geom.representative_point()
        features.append(feature(geom, id=f'GAP-{idx:02d}', population=0,
            name='Unmapped land in source village layer', kind='ZERO_POP_GAP',
            taluka=taluka, settlement_id=f'GAP-{idx:02d}',
            demand_lon=pos.x, demand_lat=pos.y,
            location_method='not_applicable',
            geometry_note='District land absent from SHRUG village/town layer; zero population assumed'))
    total = sum(f['properties']['population'] for f in features)
    if total != census_total:
        raise ValueError(f'Population {total} != Census district total {census_total}')

    stem = slug(name)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f'{stem}_units.geojson').write_text(json.dumps({
        'type': 'FeatureCollection', 'features': features}), encoding='utf-8')
    with (args.out / f'{stem}_locations.csv').open('w', newline='', encoding='utf-8') as f:
        columns = ['id', 'name', 'kind', 'taluka', 'population', 'settlement_id',
                   'demand_lon', 'demand_lat', 'location_method']
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for item in features:
            p = item['properties']
            writer.writerow({k: p.get(k, '') for k in columns})
    (args.out / f'{stem}_district.geojson').write_text(json.dumps(feature(district,
        name=f'{name} district 2011 PC11 boundary', district_name=name,
        pc11_state_id=state_id, pc11_district_id=district_id, seats=int(seats),
        sources=config.get("sources", ""))), encoding='utf-8')
    approx_wards = sum(f['properties']['kind'] == 'APPROX_WARD' for f in features)
    summary = {'population': total, 'unit_count': len(features),
        'village_town_count': len(village_rows), 'approx_city_wards': approx_wards,
        'zero_population_gap_parts': len(gap_parts), 'gap_area_degree2': gap.area,
        'location_method': 'polygon interior, not verified habitation centroid'}
    if approx_wards:
        summary['city_ward_geometry'] = config.get('ward_geometry_note',
            'approximate; ward populations and rough map positions on 2011 Census town extent')
    summary.update({'district_name': name, 'pc11_state_id': state_id, 'pc11_district_id': district_id,
        'subdistricts': sorted(talukas), 'seats': int(seats), 'census_total': census_total,
        'split_share': split_share, 'split_threshold': round(split_floor),
        'split_towns': ward_counts,
        'absorbed_into_split_towns': {'-'.join(k): '-'.join(v) for k, v in absorbed.items()},
        'merged_gap_parts': merged_gaps,
        'kept_whole_large_towns': sorted(k for k, s in towns.items() if s.get("keep_whole"))})
    summary['gap_talukas'] = [f['properties']['taluka'] for f in features if f['properties']['kind'] == 'ZERO_POP_GAP']
    summary['outputs'] = {k: f'{stem}_{k}' for k in ('units.geojson', 'locations.csv', 'district.geojson')}
    (args.out / 'input_audit.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    sys.exit(main())
