#!/usr/bin/env bash
# Pilot one prepared district on road distances:
#   official 2008 map -> 3 weight profiles x 2 seeds (parallel) -> booth MIP on official + best seed per profile.
# Usage: bash run_pilot.sh <key> <DATAMEET_DIST_NAME> <steps> <restarts>
#   e.g. bash run_pilot.sh pune PUNE 2000 3
set -euo pipefail
cd "$(dirname "$0")"
key=$1; dist=$2; steps=${3:-2000}; restarts=${4:-3}; link=${5:-0}
base=pilots/$key
units=$base/input/${key}_units.geojson
bound=$base/input/${key}_district.geojson
mat=$base/road_distances.npz

python official_district_map.py --units "$units" --district "$bound" --dist-name "$dist" \
    --distance-matrix "$mat" --link-islands-km "$link" --out "$base/official_2008" > "$base/official.log" 2>&1
seats=$(python -c "import json;print(json.load(open('$base/official_2008/mapping_report.json'))['seats'])")
echo "$key: official map done, $seats seats"

mkdir -p "$base/runs"
run() {  # profile seed weights...
    python sangli_recom.py optimize --units "$units" --district "$bound" --distance-matrix "$mat" \
        --seats "$seats" --link-islands-km "$link" --steps "$steps" --restarts "$restarts" --seed "$2" --out "$base/runs/$1_s$2" "${@:3}" \
        > "$base/runs/$1_s$2.log" 2>&1 || echo "$key $1_s$2 FAILED (see log)"
}
for s in 303 714; do
    run balanced $s &
    run locality $s --weight-population 0.30 --weight-distance 0.45 --weight-taluka 0.10 --weight-shape 0.15 &
    run taluka $s --weight-population 0.30 --weight-distance 0.20 --weight-taluka 0.35 --weight-shape 0.15 &
done
wait
echo "$key: optimizer runs done"

best=$(python - "$base" <<'EOF'
import json, sys
from pathlib import Path
base = Path(sys.argv[1])
out = []
for prof in ("balanced", "locality", "taluka"):
    cands = [d for d in (base / "runs").glob(f"{prof}_s*") if (d / "score.json").exists()]
    if cands:
        out.append(min(cands, key=lambda d: json.loads((d / "score.json").read_text())["score"]))
print(" ".join(str(d / "assigned_units.geojson") for d in out))
EOF
)
python booth_mip.py --assigned "$base/official_2008/assigned_units.geojson" $best \
    --distance-matrix "$mat" --max-km 2 3 5 --out "$base/booth_mip" > "$base/booth_mip.log" 2>&1
echo "$key: booth model done"
