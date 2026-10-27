# Sangli constituency ReCom prototype

This is a research optimizer for eight contiguous Assembly-style regions across
Sangli district. It uses 2011 population and explicit settlement/ward polygons.
It does not model booth placement. By default it uses straight-line distance;
with `--distance-matrix` it uses road kilometres from the logistics notebook,
which makes its distance term the notebook's EVM transport cost (see
[Road distances](#road-distances-optional)).

## Method

Each village and small town is an indivisible geographic unit. A settlement may
have several input units only when its total population is at least 25% of the
district's per-seat target. For Sangli's 2011 population, that is about 88,192
people; only Sangli Miraj Kupwad city needs subdivision. The 20-city-ward
population values come from the municipal 2025 final map. Their approximate
spatial ordering was read from that map, then converted into a Voronoi partition
of the *2011 Census city polygon*. These are schematic research polygons, **not
traced official ward boundaries**. This choice keeps exact district population,
complete spatial coverage, and executable logic while avoiding false precision.

The program connects units only when they share a border with positive length.
It builds an eight-seat starting map by balanced spanning-tree cuts, then uses
ReCom: choose adjacent seats, merge them, draw a new spanning tree, and cut it
into two population-balanced seats. Simulated annealing allows some temporary
score increases; the best valid map is retained. Multiple restarts reduce
dependence on the first map. The search is heuristic and does not prove a global
optimum. Random starting-map backtracking is limited to 5,000 recursive calls
and 30 seconds per start; a failed start is retried at the next restart.

Hard checks: every unit is assigned once; each seat is within ±10% of target;
each seat is a single connected polygon; corner-only touching does not connect
units; a short shared-border bridge between two substantial lobes is rejected;
and a geometric erosion check rejects substantial lobes connected by a neck
under the configured width. The 0.5 km default neck width is provisional. The
open village map reports boundary error up to 2 km, so any narrow-neck finding
needs map review before it is treated as a real geographic fact.

The four score terms are population deviation, population-weighted mean
distance (straight-line, or road km with `--distance-matrix`) to the best
constituent unit centre, taluka fragments
beyond the population-implied minimum, and shape compactness. The starting
weights are 35%, 30%, 20%, and 15%; all normalization scales are fixed so two
maps' raw terms are directly comparable. Scores using different weights are
*not* directly comparable; `map_comparison.csv` also reports every map under
the common 35/30/20/15 weights. These weights should be varied in a sensitivity
check before selecting a final map.

## Delivered Sangli pilot input

`sangli_input/sangli_units.geojson` contains 728 complete villages, 6 complete
smaller towns, 20 approximate wards of Sangli-Miraj-Kupwad city, and 2 explicit
zero-population Shirala land units missing from the source village map: 756
assignment units in total. The population sum is exactly 2,822,143, matching
the 2011 PCA district total. `sangli_input/sangli_district.geojson` is the
2011 district boundary; `sangli_input/sangli_locations.csv` lists every unit's
population and input point; and `sangli_input/input_audit.json` summarizes the join.
Every village/town code was checked against the Census table; none were missing
or duplicated. Each unit has an input location, but for rural settlements this
is a point inside its polygon, **not a verified inhabited-centre coordinate**.
Thus the distance term is geographically meaningful at coarse scale, not a
travel-time or precise household-access estimate.

The zero-population Shirala polygons fill about 105.5 km² of mapped land absent
from the village/town layer. Assigning zero there follows the settlement table,
not an independent population-surface measurement. The 2011 district/village
polygons and the 2025 municipal ward scheme are from different dates and do
not line up exactly; the city subdivision is intentionally approximate.

The `map_balanced`, `map_locality`, and `map_taluka` directories contain editable
assignment-unit and constituency GeoJSON plus metric JSON. Matching `.png`
images visualize them, and `map_comparison.csv` / `validation_report.json`
compare their raw metrics and independent checks. These are alternatives,
**not a recommendation or a claim of global optimality**. No official current
constituency line is held fixed in this research pilot.

## Input contract

`--units` is a GeoJSON FeatureCollection in longitude/latitude (EPSG:4326).
Every feature must have:

| Property | Meaning |
| --- | --- |
| `id` | Unique assignment-unit identifier |
| `population` | Nonnegative integer 2011 population |
| `taluka` | 2011 taluka identifier or name |
| `settlement_id` | Same identifier on all wards of a divided city; otherwise unique per village or town |
| `demand_lon`, `demand_lat` | Optional inhabited-location point; a polygon interior point is used if absent |

The polygons must be valid, nonoverlapping, and tile the district geometry.
`--district` is a GeoJSON Polygon or one-feature FeatureCollection in the same
coordinates. Any geographic areas in the district map that lack a village/town
polygon must be added as explicit zero-population units, with an appropriate
taluka and a unique `settlement_id`. The program reports overlap, outside area,
or missing area and stops rather than silently changing source boundaries.

The raw open village/town polygons join all 735 2011 Census settlement IDs,
but leave about 105.5 km² of the district polygon uncovered in Shirala. The
delivered pilot input fills the two real missing land pieces explicitly and
uses approximate city subdivisions. The optimizer refuses an incomplete map.

## Run

Install packages from `sangli_recom_requirements.txt`, then run from the
`outputs` directory:

```powershell
python sangli_recom.py optimize --units sangli_input/sangli_units.geojson --district sangli_input/sangli_district.geojson --out result --steps 300 --restarts 2
```

This writes `assigned_units.geojson`, `constituencies.geojson`, and
`score.json` to the output directory. Seat numbers are arbitrary labels.

To score an existing map with the same data, add a `constituency` property to
every unit and run:

```powershell
python sangli_recom.py score --units existing_assigned_units.geojson --district sangli_input/sangli_district.geojson --out comparison
```

## Road distances (optional)

`--distance-matrix PATH.npz` replaces straight-line km with road km in the
distance term. The file is the cache written by the logistics notebook,
`logistics_road/road_distances.npz`: array `pts` (P × 2 longitude/latitude of
the populated units' demand points, rounded to 6 dp) and `D` (P × P shortest
road km, snap distances included, diagonal 0). Every populated unit is matched
to a row by its `demand_lon`/`demand_lat` rounded to 6 dp; if any populated unit
has no row, the program stops and names it. Zero-population units need no row.

Each seat's centre then becomes its road 1-median: the unit with the least
population-weighted road distance to everyone in the seat, i.e. the best EVM
depot. Each seat in `score.json` (and `constituencies.geojson`) carries
`evm_km_estimate` = Σ (population / 1500) × distance from the unit to the
centre, and `raw.evm_km_estimate` is the district total. Without the flag this
uses straight-line km. This is the same quantity the notebook reports as EVM km
(up to its rounding of EVM counts), so a road-distance run optimizes districting
and EVM transport together. `score.json` records the source under
`distance_source` (`euclidean_km`, or `road_km_matrix` with the file path and
the number of matched units).

Road km run about a third longer than straight-line km, while
`--distance-reference-km` stays 20, so the distance term carries more weight
than in a straight-line run. Compare road scores only with other road scores,
or raise the reference (for example to 26) to keep the old balance between
terms.

In Colab, after the logistics notebook has written the cache, upload
`sangli_recom.py` to the same working folder (the one holding `sangli_input/`
and `logistics_road/`), then run:

```python
!python sangli_recom.py optimize --units sangli_input/sangli_units.geojson --district sangli_input/sangli_district.geojson --out map_road --steps 300 --restarts 2 --distance-matrix logistics_road/road_distances.npz
```

The new `map_road/` folder matches the notebook's `map_*` pattern, so the next
run of the logistics cell compares it with the other maps. All maps share the
same demand points, so the cached matrix stays valid and is not recomputed. To
rescore an existing map on roads, run `score` mode with the same flag and that
map's weights.

The official [Census Sangli PCA table](https://censusindia.gov.in/nada/index.php/catalog/6722)
provides population; [SHRUG PC11 village/town maps](https://docs.devdatalab.org/SHRUG-Construction-Details/shrug-open-source-polygons/)
provide open geometries; the [municipal final ward page](https://smkc.gov.in/SMKC_New_Election_Final.aspx)
provides the city ward map and populations. SHRUG reports position errors of
roughly 0–2 km and is released for noncommercial use under
[CC BY-NC-SA 4.0](https://docs.devdatalab.org/Getting-Started/license/).

## Running another district

The Sangli scripts now form a pipeline that works for any PC11 district.
The script names keep the `sangli_` prefix so that existing commands and the
notebook still work. Sangli itself is set up in `district_config/sangli.json`.

| Step | Script | What it does |
| --- | --- | --- |
| 0 | `apportion_seats.py` | Allocates the 288 ACs to the 36 current districts by largest remainder, with at least 1 per district, and compares the result with the 2008 allocation |
| 1 | `prepare_district_inputs.py` | Builds `<name>_units.geojson`, `<name>_district.geojson`, `<name>_locations.csv` and `input_audit.json` for one district (`prepare_sangli_inputs.py` is now a wrapper around it) |
| 2 | `sangli_recom.py` | Builds the ReCom maps; `--seats` is the district's AC count |
| 3 | `audit_sangli_maps.py` | Runs independent checks; the seat count is read from the maps |
| 4 | `render_sangli_maps.py` | Draws the PNGs; the district name comes from the district GeoJSON |
| 5 | `booth_mip.py` | Places booths in each seat |

`run_district.sh` (Git Bash) and `run_district.ps1` (PowerShell) run steps
1–5 for one district. They build the three maps `balanced`, `locality` and
`taluka`, with the pilot's weights and seeds:

```powershell
python apportion_seats.py --populations data/maharashtra_district_population_2011.csv --reference data/maharashtra_ac_2008.csv --out data/ac_allocation_2011.csv
.\run_district.ps1 -DistrictId 530 -Allocation data/ac_allocation_2011.csv -Config district_config/kolhapur.json `
    -Census raw/DDW_PCA2734_2011_MDDS.xlsx -Villages raw/village.gpkg -Boundaries raw/district.gpkg `
    -Subdistricts raw/subdistrict.gpkg -Out runs/kolhapur
```

```bash
bash run_district.sh --district-id 530 --allocation data/ac_allocation_2011.csv --config district_config/kolhapur.json \
    --census raw/DDW_PCA2734_2011_MDDS.xlsx --villages raw/village.gpkg --boundaries raw/district.gpkg \
    --subdistricts raw/subdistrict.gpkg --out runs/kolhapur
```

`district_config/kolhapur.json` is not in the repo yet. Kolhapur city is
above the split threshold, so you must write that config (see
[District config](#district-config)) before the run gets past prepare.

Pass `--units-dir sangli_input` (or `-UnitsDir`) to skip prepare and reuse an
existing input folder. Other options:

- `--maps balanced,locality`
- `--steps` (default 300) and `--restarts` (by default the pilot's value for each map: balanced 2, locality 2, taluka 1)
- `--distance-matrix road.npz` (used for both recom and booth_mip)
- `--max-km "2 3 5"`
- `--skip-booth`

The outputs go under `--out`: `input/`, `map_<name>/`, `map_<name>.png`,
`map_comparison.csv`, `validation_report.json` and `booth_mip_results/`.

### Data files needed

The raw data is in `../raw_data/` (about 2.4 GB unzipped, not part of the
code). It is SHRUG 2.2, downloaded on 2026-10-24 from
<https://www.devdatalab.org/shrug_download/> under CC BY-NC-SA 4.0. There is
one folder per zip:

| Folder | Use | Flag |
| --- | --- | --- |
| `shrug-pc11-village-poly-gpkg/village_modified.gpkg` | village/town polygons | `--villages` |
| `shrug-pc11dist-poly-gpkg/district.gpkg` | district polygons | `--boundaries` (prepare: `--district`) |
| `shrug-pc11subdist-poly-gpkg/subdistrict.gpkg` | taluka polygons; needed with `--current-district` | `--subdistricts` |
| `shrug-pca11-csv/` | 2011 PCA district and subdistrict totals | input to `shrug_to_pca.py` |
| `shrug-pc-keys-csv/` | 2011 population of every inhabited village and town | input to `shrug_to_pca.py` |
| `shrug-shrid-keys-csv/` | SHRUG location names | not used yet |
| `shrug-con-keys-csv/` | 2008 AC to SHRUG location keys and AC names | for comparing with the real ACs later |

The official Census PCA workbooks (censusindia.gov.in) could not be reached
from this machine. `shrug_to_pca.py` builds the same table from the SHRUG
files instead, so pass it to `--census`:

```powershell
python shrug_to_pca.py --state-id 27 --shrug ../raw_data --out ../raw_data/pca_from_shrug/pca_27.csv
```

I checked the converted table against the Sangli inputs, which were built
from the official workbook:

- The Maharashtra total (112,374,333) and the Sangli total (2,822,143) match.
- Every Sangli village and town population matches.
- Rebuilding Sangli from SHRUG alone gives the same 756 units and polygons.
  Only some name spellings differ.

Two known gaps:

- Villages with no SHRUG key row are uninhabited and get population 0.
- Three inhabited villages have no polygon: two in Buldhana (500) and one in
  Beed (523). Prepare stops for those two districts until this is handled.

The PCA workbook route (`DDW_PCA27xx_2011_MDDS*.xlsx`, needs `openpyxl`)
still works if you can download the workbooks from an Indian network.

Large towns also need ward populations and ward positions or polygons, which
come from the municipal corporation. Neither SHRUG nor this folder has them.

These reference files are already in `data/`:

- `maharashtra_district_population_2011.csv`: Census 2011 population of the 36 current districts.
- `maharashtra_ac_2008.csv`: ACs per district under the 2008 Delimitation Order.
- `district_crosswalk_2011_to_current.csv`: the crosswalk from 2011 to current districts.

The figures in these files were checked against Wikipedia's district and AC
lists. Check them against the official sources before quoting them.

### District config

A district is identified by its PC11 (Census 2011) code. Maharashtra's state
code is `27`, and its district codes run from `497` (Nandurbar) to `531`
(Sangli). Prepare reads the district total, the talukas and the district
name from the Census file, and it stops unless the final population equals
the Census `DISTRICT` total. Each zero-population gap unit gets the taluka of
its largest neighbouring unit. Set `"gap_taluka"` to override this.

A district needs a config file if any settlement has more than
`split_share × population / seats` people (25% of a seat by default).
Without one, prepare stops and lists those settlements with their codes.
List each one under `"towns"`, keyed by `"<subdistrict>-<town code>"`, with
one of these ward sources:

- `"ward_method": "approx_labels"` (the Sangli method; see
  `district_config/sangli.json`). It needs a ward population CSV
  (`ward_no,population`) and `"ward_labels"`, the rough pixel positions of
  the ward labels on an official ward map. These become Voronoi pieces of the
  2011 town polygon.
- `"ward_method": "polygons"`. It needs `"ward_polygons"`, a ward GeoJSON
  with a `ward_no` property (or the field named in `"ward_field"`) and
  populations, either as a `population` property or in a CSV. The wards are
  clipped to the 2011 town polygon, and any leftover town land joins the ward
  with the longest shared border. Use this for Mumbai, Pune, Thane, Nagpur
  and other big cities.
- `"keep_whole": true`. This is allowed only while the town is below the
  seat maximum.

Ward populations must add up to the town's Census 2011 total. Set
`"population_check": "rescale"` to scale them to that total by largest
remainder instead. Paths in the config are relative to the config file.
Optional keys are `name`, `seats`, `split_share`, `census_sheet`, `sources`
(the PNG footer) and `gap_taluka`. Command-line flags override the config.
The seat count is taken from `--seats`, then `--allocation`, then the config.

### Palghar and Thane

Palghar was carved out of Thane in 2014, so neither current district matches
a 2011 district code. Both are built from 2011 district `517` with
`--current-district`, which keeps only the talukas the crosswalk lists for
that district:

```bash
bash run_district.sh --district-id 517 --current-district Palghar --allocation data/ac_allocation_2011.csv \
    --config district_config/palghar.json --census ... --villages ... --boundaries ... --subdistricts ... --out runs/palghar
```

The crosswalk lists each taluka's 2011 code, taken from SHRUG/Census. The
talukas add up to Thane 8,070,032 and Palghar 2,990,116. If a code is blank,
talukas are matched by name; alternative spellings are separated by `|`.
Thane's cities all need ward entries in a config: Thane, Kalyan-Dombivli,
Navi Mumbai, Bhiwandi, Ulhasnagar, Mira-Bhayandar and Vasai-Virar.

### Things to know

- **Seat counts.** On 2011 populations, largest remainder gives Sangli 7 ACs
  instead of its 2008 count of 8. 15 districts differ from 2008, and 11 seats
  move in total. Pune and Thane each gain 3. Mumbai Suburban and Mumbai City
  each lose 2. The 2008 order used Census 2001 populations. The Sangli pilot
  keeps `"seats": 8` in its config; pass
  `--allocation data/ac_allocation_2011.csv` to use the 2011 count.
- **Big cities.** Mumbai City and Mumbai Suburban are almost entirely one
  Census town each. Each needs a ward-polygon config before prepare will run
  for it. The approximate-label method is too coarse there.
- **Colours.** For up to 20 seats, render uses a 20-colour palette whose
  first 8 colours are the pilot's. With more than 20 seats, it colours
  greedily so that neighbouring seats differ. Seats are matched to the first
  map by an optimal assignment (`scipy`) instead of by trying every
  permutation.

### Lok Sabha seats (statewide step)

`group_lok_sabha.py` combines the district maps into Lok Sabha seats (PCs).
Each PC is exactly 6 whole ACs that form one connected piece, and each PC's
population is within ±10% of the average (both are hard constraints). As in
delimitation practice, PCs may cross district lines. ACs never do.
Maharashtra's 288 ACs make 48 PCs, but any set of districts works if the AC
count of each connected piece of the map is a multiple of 6.

```powershell
python group_lok_sabha.py --district Sangli=runs/sangli --district Kolhapur=runs/kolhapur `
    --district Satara=runs/satara --map balanced --out lok_sabha
```

`NAME=PATH` points to a district's `--out` folder (its `map_<--map>/` is
read), a `map_*` folder, or a `constituencies.geojson` file. The AC
polygons and their `population` come from that file. ACs are adjacent if
they share at least `--min-border-km` (0.5) of border, measured with a
`--snap-km` (0.05) tolerance. The tolerance lets borders between
separately built districts match. Overlapping ACs (for example, the same
district given twice) stop the run.

Method: an exact set-partitioning MIP (PuLP + CBC). Every connected set of 6
ACs within the population bounds is a candidate PC, so contiguity and
balance hold by construction. Statewide this gives a few hundred thousand
candidates. The objective is

  Σ |PC population − average| / average × 100 + `--cross-weight` × (PCs crossing a district line)

with `--cross-weight 5` by default: one crossing PC costs as much as 5
percentage points of deviation. On a statewide instance, CBC (and HiGHS)
given the whole model find no plan at all in several minutes, so the
script works in four steps:

1. Solve the LP relaxation (scipy HiGHS) for a lower bound and reduced costs.
2. Build starting plans by LP diving and by ReCom-style tree bisection
   (`--restarts`, default 5). Improve each start by re-splitting every pair
   of neighbouring PCs optimally.
3. Run ReCom-style annealing from the best start for `--search-seconds`
   (default 180; it stops much sooner on small inputs). Each move merges
   two neighbouring PCs and draws a new split from all valid splits. The
   best plan seen is kept.
4. Drop every candidate whose reduced cost exceeds (best plan − LP bound).
   No cheaper plan can use those candidates, so dropping them is exact. CBC
   then solves the reduced MIP, warm-started from the best plan, for up to
   `--time-limit` seconds (default 300). If more than `--max-mip-columns`
   (50,000) candidates are left, CBC is skipped, because it made no
   progress on such models in testing.

`solver` in the summary says `optimal` when the plan is proven optimal over
all candidates. It says `feasible (time limit)` or `heuristic (...)` when
it is not proven. `lp_gap` (plan − LP bound) then bounds how far the plan
can be from optimal. The LP bound is loose statewide, so the real gap is
smaller; see the statewide test below. Annealing runs on a time budget,
so statewide plans can differ slightly from run to run. Every plan is checked independently before anything is written: each
AC is used once, each PC has 6 ACs, each PC is contiguous and within bounds.

Outputs in `--out`:

- `pc_assignment.csv`: one row per AC, giving its PC, populations, deviation
  and the districts the PC spans.
- `pcs.geojson`: one polygon per PC with `population`, `deviation_pct`,
  `acs`, `districts`, `crosses_district` and `area_km2`.
- `ac_adjacency.csv`: AC borders used, with shared km and a cross-district flag.
- `summary.json`: settings, bounds, objective, LP bound, solver status,
  runtimes and the per-PC table.

PC numbers are arbitrary labels. Only Sangli outputs exist so far, and
Sangli's 8 ACs are not a multiple of 6, so the script refuses them alone.
`python test_group_lok_sabha.py` covers the following cases. It writes
only to a temporary folder:

- Sangli re-split by `sangli_recom.py` into 12 and 18 ACs, grouped into 2
  and 3 PCs;
- the 18-AC map cut into two pseudo-districts, one shifted 20 m so their
  borders no longer match exactly, grouped into 3 PCs with exactly one
  crossing PC;
- inputs that must be refused (8 ACs; the same area given twice).

Add `--statewide` to also group a synthetic 288-AC, 36-district state into
48 PCs. The state is made of Voronoi ACs, with AC populations that vary by
district and within it. That run takes about 4 minutes (180 s of it is
annealing). It gives about 202 against an LP bound of 183, with 22–24
crossing PCs and every PC within ±7%. As a check, Gurobi given the same
model for 600 s found 201.1 and proved a bound of 195.4. The plan is
therefore within about 3.5% of optimal, but that is not proven. CBC with
no starting plan found no plan in 600 s. All tests passed on 2026-10-24.

The test's synthetic districts hold about 8 ACs each, so almost every
candidate crosses a district line. Real districts hold 3 to 26
ACs, so expect fewer crossings and possibly a tighter bound. Raise
`--search-seconds` for a better statewide plan.

### Sangli regression (checked 2026-10-24)

`run_district.sh` and `run_district.ps1` were each run with
`--units-dir sangli_input --config district_config/sangli.json` and default
settings. Both reproduce the stored pilot outputs:

- all three `map_*` assignments, scores and seat geometries match exactly;
- `map_comparison.csv` and `validation_report.json` are byte-identical;
- the booth MIP summaries match except for runtime, and `booth_locations.csv` and `assignments.csv` match for every map and MAX_KM;
- the PNGs are pixel-identical to the original render script, given the same taluka layer.

The prepare step was tested on stand-in raw files rebuilt from `sangli_input`.
On that input, the original prepare script, the new one and the
`prepare_sangli_inputs.py` wrapper write byte-identical units and locations
files, and the gap units get `Shirala` from the data.
