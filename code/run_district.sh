#!/usr/bin/env bash
# Run the district pipeline: prepare -> recom (3 maps) -> audit -> render -> booth_mip.
#
#   bash run_district.sh --district-id 531 --config district_config/sangli.json \
#       --census raw/DDW_PCA2735_2011_MDDS.xlsx --villages raw/shrug_pc11_village.gpkg \
#       --boundaries raw/shrug_pc11_district.gpkg --subdistricts raw/shrug_pc11_subdistrict.gpkg \
#       --out runs/sangli
#
# Reuse an existing input folder instead of preparing one:
#   bash run_district.sh --units-dir sangli_input --config district_config/sangli.json --out runs/sangli
#
# Options (defaults in brackets):
#   --state-id ID [27]           --district-id ID (needed for prepare and SHRUG taluka lines)
#   --config FILE                district config JSON (towns to split, seats, sources)
#   --seats N | --allocation CSV ACs in the district (else input_audit.json, then the config)
#   --current-district NAME      a current district inside a 2011 one (Palghar, Thane)
#   --census/--villages/--boundaries/--subdistricts FILE   raw Census/SHRUG files
#   --units-dir DIR              skip prepare and use DIR/*_units.geojson, DIR/*_district.geojson
#   --maps LIST [balanced,locality,taluka]   --steps N [300]
#   --restarts N                 [pilot values: balanced 2, locality 2, taluka 1]
#   --distance-matrix NPZ        road km for recom and booth_mip
#   --max-km "LIST" ["2 3 5"]    --booth-time-limit S [300]  --skip-booth
#   --python EXE [python]        --out DIR (required)
set -euo pipefail
cd "$(dirname "$0")"

PY=python; STATE=27; DISTRICT=""; CONFIG=""; SEATS=""; ALLOC=""; CURRENT=""
CENSUS=""; VILLAGES=""; BOUNDARIES=""; SUBDISTRICTS=""; UNITS_DIR=""; OUT=""
MAPS="balanced,locality,taluka"; STEPS=300; RESTARTS=""; MATRIX=""; MAXKM="2 3 5"
BOOTH_LIMIT=300; SKIP_BOOTH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --state-id) STATE="$2"; shift 2;;
    --district-id) DISTRICT="$2"; shift 2;;
    --config) CONFIG="$2"; shift 2;;
    --seats) SEATS="$2"; shift 2;;
    --allocation) ALLOC="$2"; shift 2;;
    --current-district) CURRENT="$2"; shift 2;;
    --census) CENSUS="$2"; shift 2;;
    --villages) VILLAGES="$2"; shift 2;;
    --boundaries) BOUNDARIES="$2"; shift 2;;
    --subdistricts) SUBDISTRICTS="$2"; shift 2;;
    --units-dir) UNITS_DIR="$2"; shift 2;;
    --maps) MAPS="$2"; shift 2;;
    --steps) STEPS="$2"; shift 2;;
    --restarts) RESTARTS="$2"; shift 2;;
    --distance-matrix) MATRIX="$2"; shift 2;;
    --max-km) MAXKM="$2"; shift 2;;
    --booth-time-limit) BOOTH_LIMIT="$2"; shift 2;;
    --skip-booth) SKIP_BOOTH=1; shift;;
    --python) PY="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    -h|--help) sed -n '2,23p' "$0"; exit 0;;
    *) echo "run_district.sh: unknown option $1" >&2; exit 2;;
  esac
done
die() { echo "run_district.sh: $*" >&2; exit 1; }
[ -n "$OUT" ] || die "--out is required"
step() { echo; echo "=== $* ==="; }

# recom weights population/distance/taluka/shape, seed and restarts per map
# (the Sangli pilot's settings, which reproduce its stored maps)
weights() { case "$1" in
  balanced) echo "0.35 0.30 0.20 0.15 303 2";;
  locality) echo "0.30 0.45 0.10 0.15 714 2";;
  taluka)   echo "0.30 0.20 0.35 0.15 303 1";;
  *) die "unknown map $1 (balanced, locality, taluka)";; esac; }
IFS=',' read -r -a map_list <<< "$MAPS"
for m in "${map_list[@]}"; do weights "$m" > /dev/null; done

# 1. prepare
if [ -z "$UNITS_DIR" ]; then
  [ -n "$DISTRICT" ] || die "--district-id is required to prepare inputs (or pass --units-dir)"
  UNITS_DIR="$OUT/input"
  args=(--state-id "$STATE" --district-id "$DISTRICT" --out "$UNITS_DIR")
  [ -n "$CENSUS" ] && args+=(--census "$CENSUS")
  [ -n "$VILLAGES" ] && args+=(--villages "$VILLAGES")
  [ -n "$BOUNDARIES" ] && args+=(--district "$BOUNDARIES")
  [ -n "$SUBDISTRICTS" ] && args+=(--subdistricts "$SUBDISTRICTS")
  [ -n "$CONFIG" ] && args+=(--config "$CONFIG")
  [ -n "$SEATS" ] && args+=(--seats "$SEATS")
  [ -n "$ALLOC" ] && args+=(--allocation "$ALLOC")
  [ -n "$CURRENT" ] && args+=(--current-district "$CURRENT")
  step "prepare"
  "$PY" prepare_district_inputs.py "${args[@]}"
fi
shopt -s nullglob
units=("$UNITS_DIR"/*_units.geojson); bounds=("$UNITS_DIR"/*_district.geojson)
[ ${#units[@]} -eq 1 ] || die "expected one *_units.geojson in $UNITS_DIR, found ${#units[@]}"
[ ${#bounds[@]} -eq 1 ] || die "expected one *_district.geojson in $UNITS_DIR, found ${#bounds[@]}"
UNITS="${units[0]}"; BOUNDARY="${bounds[0]}"

# Seats and footer: --seats, then input_audit.json, then the config.
read_json() { "$PY" -c "import json,sys
try: print(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2],''))
except FileNotFoundError: print('')" "$1" "$2"; }
[ -z "$SEATS" ] && SEATS=$(read_json "$UNITS_DIR/input_audit.json" seats)
[ -z "$SEATS" ] && [ -n "$CONFIG" ] && SEATS=$(read_json "$CONFIG" seats)
[ -n "$SEATS" ] || die "seat count unknown: pass --seats or a --config with \"seats\""
SOURCES=""; [ -n "$CONFIG" ] && SOURCES=$(read_json "$CONFIG" sources)
echo "units: $UNITS"; echo "district: $BOUNDARY"; echo "seats: $SEATS"

# 2. recom
dirs=()
for m in "${map_list[@]}"; do
  w=$(weights "$m")
  read -r wp wd wt ws seed restarts <<< "$w"
  [ -n "$RESTARTS" ] && restarts="$RESTARTS"
  step "recom map_$m (weights $wp/$wd/$wt/$ws, seed $seed)"
  args=(optimize --units "$UNITS" --district "$BOUNDARY" --out "$OUT/map_$m" --seats "$SEATS"
        --weight-population "$wp" --weight-distance "$wd" --weight-taluka "$wt" --weight-shape "$ws"
        --steps "$STEPS" --restarts "$restarts" --seed "$seed")
  [ -n "$MATRIX" ] && args+=(--distance-matrix "$MATRIX")
  "$PY" sangli_recom.py "${args[@]}"
  dirs+=("$OUT/map_$m")
done

# 3. audit
step "audit"
"$PY" audit_sangli_maps.py --input "$UNITS" --district "$BOUNDARY" --results "${dirs[@]}" \
  --out "$OUT" --seats "$SEATS"

# 4. render (colours aligned to the first map)
for d in "${dirs[@]}"; do
  step "render $(basename "$d")"
  args=(--result "$d" --units "$UNITS" --district "$BOUNDARY" --out "$d.png")
  [ "$d" != "${dirs[0]}" ] && args+=(--reference "${dirs[0]}")
  [ -n "$SUBDISTRICTS" ] && args+=(--talukas "$SUBDISTRICTS" --district-id "$DISTRICT")
  [ -n "$SOURCES" ] && args+=(--sources "$SOURCES")
  "$PY" render_sangli_maps.py "${args[@]}"
done

# 5. booth MIP
if [ "$SKIP_BOOTH" -eq 0 ]; then
  step "booth_mip"
  assigned=(); for d in "${dirs[@]}"; do assigned+=("$d/assigned_units.geojson"); done
  read -r -a km <<< "$MAXKM"
  args=(--assigned "${assigned[@]}" --max-km "${km[@]}" --time-limit "$BOOTH_LIMIT"
        --out "$OUT/booth_mip_results")
  [ -n "$MATRIX" ] && args+=(--distance-matrix "$MATRIX")
  "$PY" booth_mip.py "${args[@]}"
fi
step "done: $OUT"
