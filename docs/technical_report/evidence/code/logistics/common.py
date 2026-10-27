"""Shared paths, district registry and small helpers for the election-logistics models.

Every layer (network, demand, location, routing, simulation) reads and writes under
``code/logistics_runs/<district>/``, so a scenario flows from one model to the next by files.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
RUNS = CODE / "logistics_runs"
PBF = ROOT / "data_raw/geofabrik/western-zone-261022.osm.pbf"
WORLDPOP_100M = ROOT / "data_raw/worldpop/ind_ppp_2011_UNadj.tif"
ELECTORS = CODE / "data/maharashtra_ac_electors_wikipedia.csv"
EARTH_KM = 6371.0088

DISTRICTS = {
    "sangli": {"name": "Sangli", "units": CODE / "sangli_input/sangli_units.geojson",
               "boundary": CODE / "sangli_input/sangli_district.geojson",
               "official": CODE / "official_2008/assigned_units.geojson"},
    **{k: {"name": k.title(), "units": CODE / f"pilots/{k}/input/{k}_units.geojson",
           "boundary": CODE / f"pilots/{k}/input/{k}_district.geojson",
           "official": CODE / f"pilots/{k}/official_2008/assigned_units.geojson"}
       for k in ("satara", "nashik", "pune")},
}


def run_dir(district: str) -> Path:
    d = RUNS / district
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_geojson(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def boundary_geometry(path: Path):
    from shapely.geometry import shape
    g = read_geojson(path)
    if g.get("type") == "FeatureCollection":
        g = g["features"][0]
    if g.get("type") == "Feature":
        g = g["geometry"]
    return shape(g)


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def local_xy_km(lon, lat, lat0: float):
    """Equirectangular km coordinates, good to ~0.1% over a district."""
    kx = 111.32 * math.cos(math.radians(lat0))
    return np.c_[np.asarray(lon) * kx, np.asarray(lat) * 111.32]
