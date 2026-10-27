"""Build the Sangli 2011 constituency input map (kept for the old command line).

This is now a thin wrapper around prepare_district_inputs.py with
district_config/sangli.json (PC11 state 27, district 531, 8 seats). The
20 subdivisions of Sangli-Miraj-Kupwad are *approximate*: they use the 2025
official ward population list and rough ward-label positions read from the
official combined map, mapped onto the 2011 Census municipality polygon. They
are NOT official ward GIS lines. Output files and contents are unchanged.
"""

import argparse
import json
import tempfile
from pathlib import Path

import prepare_district_inputs

CONFIG = Path(__file__).with_name('district_config') / 'sangli.json'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--census', type=Path, required=True)
    ap.add_argument('--villages', type=Path, required=True)
    ap.add_argument('--district', type=Path, required=True)
    ap.add_argument('--wards', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    for town in config['towns'].values():
        town['ward_population_csv'] = str(args.wards.resolve())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'sangli.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        prepare_district_inputs.main([
            '--state-id', config['pc11_state_id'], '--district-id', config['pc11_district_id'],
            '--census', str(args.census), '--villages', str(args.villages),
            '--district', str(args.district), '--config', str(path), '--out', str(args.out)])


if __name__ == '__main__':
    main()
