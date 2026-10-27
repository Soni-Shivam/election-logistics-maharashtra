"""Tests for group_lok_sabha.py. Run from code/:  python test_group_lok_sabha.py [--statewide]

Everything is written to a temporary folder; stored outputs (map_*,
booth_mip_results, sangli_input) are only read.

1. Sangli re-split by sangli_recom.py into 12 and 18 ACs -> 2 and 3 PCs.
2. The 18-AC map cut into two pseudo-districts (West, East) of 9 ACs, with
   East shifted 20 m so the borders between them no longer match exactly
   -> 3 PCs, exactly one of which must cross the district line.
3. Inputs that must be refused: the stored 8-AC map, the same area given as
   two districts.
4. --statewide: a synthetic 288-AC, 36-district "state" -> 48 PCs (timing).

Each grouping is checked from the written files alone: every AC in exactly
one PC, 6 ACs per PC, each PC's polygons forming one connected piece (with
a 50 m snap), populations recomputed from the inputs and within +-10%.
"""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box, mapping, shape

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "group_lok_sabha.py"


def run(args: list, expect_ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)
    if expect_ok and result.returncode:
        raise AssertionError(f"{args[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result


def load_input_acs(paths: dict[str, Path]) -> dict[str, tuple[int, object]]:
    acs = {}
    for district, path in paths.items():
        for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
            p = f["properties"]
            acs[f"{district}-{p['constituency']}"] = (int(p["population"]), shape(f["geometry"]))
    return acs


def verify(out: Path, inputs: dict[str, Path], balance: float = 0.10) -> dict:
    """Check a grouping from its output files; return the summary."""
    acs = load_input_acs(inputs)
    with (out / "pc_assignment.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    keys = [r["ac_key"] for r in rows]
    assert sorted(keys) == sorted(acs), "every AC must appear exactly once"
    members = defaultdict(list)
    for r in rows:
        members[int(r["pc"])].append(r["ac_key"])
    n_pcs = len(acs) // 6
    assert sorted(members) == list(range(1, n_pcs + 1)), f"expected PCs 1..{n_pcs}"
    target = sum(p for p, _ in acs.values()) / n_pcs
    lat = shapely.union_all([g for _, g in acs.values()]).centroid.y
    sx = 111.32 * math.cos(math.radians(lat))

    def km(g):
        return shapely.transform(g, lambda c: c * [sx, 111.32])

    features = {f["properties"]["pc"]: f
                for f in json.loads((out / "pcs.geojson").read_text(encoding="utf-8"))["features"]}
    for pc, keys in members.items():
        assert len(keys) == 6, f"PC {pc} has {len(keys)} ACs"
        pop = sum(acs[k][0] for k in keys)
        assert abs(pop - target) <= balance * target, f"PC {pc} population {pop} vs {target:.0f}"
        union = shapely.union_all([km(acs[k][1]).buffer(0.05) for k in keys])
        assert union.geom_type == "Polygon", f"PC {pc} is not one connected piece"
        props = features[pc]["properties"]
        assert props["population"] == pop and sorted(props["acs"].split(";")) == sorted(keys)
        districts = {k.rsplit("-", 1)[0] for k in keys}
        assert props["crosses_district"] == (len(districts) > 1)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["checks"]["all_contiguous"] and summary["pcs_count"] == n_pcs
    return summary


def write_fc(path: Path, features: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}),
                    encoding="utf-8")
    return path


def split_east_west(source: Path, tmp: Path, shift_km: float = 0.02) -> dict[str, Path]:
    features = json.loads(source.read_text(encoding="utf-8"))["features"]
    features.sort(key=lambda f: shape(f["geometry"]).centroid.x)
    half = len(features) // 2
    dx = shift_km / (111.32 * math.cos(math.radians(17)))
    east = []
    for f in features[half:]:
        g = shapely.transform(shape(f["geometry"]), lambda c: c + [dx, 0])
        east.append({**f, "geometry": mapping(g)})
    return {"West": write_fc(tmp / "west" / "constituencies.geojson", features[:half]),
            "East": write_fc(tmp / "east" / "constituencies.geojson", east)}


def synthetic_state(tmp: Path, n_acs: int = 288, n_districts: int = 36,
                    seed: int = 7) -> dict[str, Path]:
    """Voronoi ACs on a Maharashtra-sized box, grouped into contiguous districts."""
    rng = np.random.default_rng(seed)
    x0, y0, w, h = 73.0, 16.0, 7.5, 5.5
    frame = box(x0, y0, x0 + w, y0 + h)
    pts = shapely.points(rng.uniform([x0, y0], [x0 + w, y0 + h], (n_acs, 2)))
    cells = shapely.voronoi_polygons(shapely.multipoints(pts), extend_to=frame)
    cells = [c.intersection(frame) for c in cells.geoms]
    # Districts: k-means on cell centroids, then sizes vary like real districts.
    xy = np.array([[c.centroid.x, c.centroid.y] for c in cells])
    centres = xy[rng.choice(n_acs, n_districts, replace=False)]
    for _ in range(20):
        label = np.argmin(((xy[:, None] - centres[None]) ** 2).sum(-1), axis=1)
        centres = np.array([xy[label == d].mean(0) if (label == d).any() else centres[d]
                            for d in range(n_districts)])
    # AC sizes differ by district (apportionment rounding) and within it (+-5%).
    factor = rng.uniform(0.92, 1.08, n_districts)
    paths = {}
    for d in range(n_districts):
        feats = [{"type": "Feature",
                  "properties": {"constituency": k, "population":
                                 int(390_000 * factor[d] * rng.uniform(0.95, 1.05))},
                  "geometry": mapping(cells[i])}
                 for k, i in enumerate(np.flatnonzero(label == d), start=1)]
        if feats:
            paths[f"D{d + 1:02d}"] = write_fc(tmp / f"d{d + 1:02d}" / "constituencies.geojson",
                                              feats)
    return paths


def district_args(paths: dict[str, Path]) -> list[str]:
    return [a for name, p in paths.items() for a in ("--district", f"{name}={p}")]


def main() -> None:
    statewide = "--statewide" in sys.argv
    with tempfile.TemporaryDirectory(prefix="group_ls_") as tmp_name:
        tmp = Path(tmp_name)
        maps = {}
        for seats in (12, 18):
            out = tmp / f"sangli{seats}" / "map_balanced"
            run([HERE / "sangli_recom.py", "optimize",
                 "--units", HERE / "sangli_input" / "sangli_units.geojson",
                 "--district", HERE / "sangli_input" / "sangli_district.geojson",
                 "--out", out, "--seats", seats, "--steps", 100, "--restarts", 1])
            maps[seats] = out / "constituencies.geojson"

        for seats, pcs in ((12, 2), (18, 3)):
            out = tmp / f"group{seats}"
            run([SCRIPT, "--district", f"Sangli={maps[seats].parent.parent}", "--out", out])
            s = verify(out, {"Sangli": maps[seats]})
            assert (s["pcs_count"] == pcs and s["crossing_pcs"] == 0
                    and s["solver"].startswith("optimal")), s["solver"]
            print(f"ok  Sangli {seats} ACs -> {pcs} PCs, max deviation "
                  f"{s['max_abs_deviation_pct']:.2f}%, {s['candidate_pcs']} candidates")

        halves = split_east_west(maps[18], tmp / "split")
        out = tmp / "group_split"
        run([SCRIPT, *district_args(halves), "--out", out])
        s = verify(out, halves)
        assert s["cross_district_edges"] > 0, "no West-East borders found after the 20 m shift"
        assert s["crossing_pcs"] == 1, f"expected 1 crossing PC, got {s['crossing_pcs']}"
        print(f"ok  West 9 + East 9 ACs (East shifted 20 m) -> 3 PCs, 1 crossing, "
              f"{s['cross_district_edges']} cross-district borders, max deviation "
              f"{s['max_abs_deviation_pct']:.2f}%, solver {s['solver']}")

        r = run([SCRIPT, "--district", f"Sangli={HERE}", "--out", tmp / "g8"], expect_ok=False)
        assert r.returncode and "multiple of 6" in r.stderr, r.stderr
        copy = write_fc(tmp / "copy" / "constituencies.geojson",
                        json.loads(maps[12].read_text(encoding="utf-8"))["features"])
        r = run([SCRIPT, "--district", f"A={maps[12]}", "--district", f"B={copy}",
                 "--out", tmp / "gdup"], expect_ok=False)
        assert r.returncode and "overlap" in r.stderr, r.stderr
        print("ok  refused: stored 8-AC map; overlapping districts")

        if statewide:
            state = synthetic_state(tmp / "state")
            out = tmp / "group_state"
            t0 = time.perf_counter()
            run([SCRIPT, *district_args(state), "--out", out])
            s = verify(out, state)
            assert s["pcs_count"] == 48
            print(f"ok  synthetic state {s['acs']} ACs / {s['districts']} districts -> 48 PCs in "
                  f"{time.perf_counter() - t0:.0f} s ({s['candidate_pcs']} candidates, "
                  f"solver {s['solver']}), {s['crossing_pcs']} crossing, max deviation "
                  f"{s['max_abs_deviation_pct']:.2f}%, objective {s['objective']:.2f} "
                  f"(LP bound {s['lp_bound']:.2f})")
    print("all tests passed")


if __name__ == "__main__":
    main()
