# Booth placement as a capacitated facility-location MIP

`booth_mip.py` replaces the Stage 2 greedy heuristic, which places exactly
`ceil(pop/1500)` booths per seat. Here the number of booths is decided by the
model instead of being fixed in advance. Run it on any
`assigned_units.geojson` written by `sangli_recom.py`. It does not change the
maps.

## Model (per constituency)

Candidate sites are the populated units of the seat. Each unit's electors sit
at its demand point.

- `y_s` (integer ≥ 0): the number of booths at site `s`.
- `x_is` (≥ 0): the electors of unit `i` who vote at site `s`.

```
min  BOOTH_COST·Σ y_s  +  VOTER_COST_PER_KM·Σ d_is x_is  +  EVM_COST_PER_KM·Σ d(depot,s) y_s
s.t. Σ_s x_is = e_i                    (every elector served)
     Σ_i x_is ≤ 1500·y_s               (booth capacity)
     x_is = 0 if d_is > MAX_KM         (unit i may always use its own site, d_ii = 0)
     x_is ≤ min(e_i,1500)·y_s,  Σ y_s ≥ ceil(Σe/1500)   (valid tightening only)
```

The depot is the seat's 1-median unit by road distance, weighted by electors.
CBC solves the model through PuLP.

| Input | Default | Status |
|---|---|---|
| `--elector-ratio` | 0.70 electors per Census person | **Assumption.** No electoral roll has been loaded yet. |
| `--capacity` | 1500 electors per booth | ECI norm used in the project. ECI announced a lower cap of 1,200 in 2025, so check and rerun with `--capacity 1200` if needed. |
| `--max-km` | 2.0 road km | ECI's guideline of a booth within 2 km. Several values give a sensitivity run. |
| `--booth-cost` | ₹25,000 per booth | **Assumption:** polling party, training, furniture and an EVM share. |
| `--voter-cost-per-km` | ₹10 per elector per one-way km | **Assumption:** a 2 km round trip on foot (about 24 min) valued at about ₹50/h, times about 60% turnout. |
| `--evm-cost-per-km` | ₹120 per booth per one-way km | **Assumption:** dispatch and return take 4 legs at about ₹30/km. |
| `--distance-matrix` | none | An npz with `pts` (lon/lat, rounded to 6 decimals) and `D` (road km), the format of the notebook's `logistics_road/road_distances.npz`. **Without it the script uses straight-line km × 1.33 (`--road-factor`) and prints a FALLBACK label in its output.** |

## Run

```powershell
pip install "pulp>=2.8,<4"   # bundles CBC
python booth_mip.py --assigned map_balanced/assigned_units.geojson map_locality/assigned_units.geojson map_taluka/assigned_units.geojson --max-km 2 3 5 --out booth_mip_results
```

Pass `--distance-matrix logistics_road/road_distances.npz` to use road
distances. PuLP 4.x no longer bundles CBC and needs `pip install "pulp[cbc]"`.
The script works with either version. On this Windows machine, Windows Application
Control blocks the `cbcbox` binary that PuLP 4 uses, but it allows the CBC
bundled with PuLP 3.

The script writes these files to `booth_mip_results/`:

- `map_summary.csv`: one row per map and MAX_KM.
- `seat_summary_all.csv`: one row per seat, including the status, MIP gap, runtime, depot and cost split.
- `run_info.json`: every parameter and the distance source.
- `<map>/maxkm_<k>/`: the per-seat summary, `booth_locations.csv` (site, booths, electors served, utilisation, depot km) and `assignments.csv` (unit → site flows).

## Results: fallback distances (straight-line × 1.33), electors = 0.70 × population

There are 1,975,500 electors in total. The bare minimum is 1,320 to 1,322
booths, compared with about 1,882 for the notebook, which uses total
population. All 72 seat solves were proven optimal by CBC: Result "Optimal
solution found", gap 0.

Costs are in ₹ lakh. "Travel %" is the share of electors who vote outside
their own unit.

| Map | MAX_KM | Booths | vs min | Cap. use | Voter person-km | Mean km | Max km | Travel % | EVM km | Booth cost | Voter cost | EVM cost | Objective | Optimal | Gap | Time (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| map_balanced | 2 | 1649 | +329 | 0.799 | 28,616 | 0.015 | 2.00 | 0.9 | 29,433 | 412.2 | 2.9 | 35.3 | 450.4 | 8/8 | 0 | 0.6 |
| map_locality | 2 | 1649 | +327 | 0.799 | 28,592 | 0.015 | 2.00 | 0.9 | 29,275 | 412.2 | 2.9 | 35.1 | 450.2 | 8/8 | 0 | 0.7 |
| map_taluka | 2 | 1651 | +330 | 0.798 | 28,508 | 0.014 | 2.00 | 0.9 | 29,367 | 412.8 | 2.9 | 35.2 | 450.8 | 8/8 | 0 | 0.7 |
| map_balanced | 3 | 1564 | +244 | 0.842 | 112,746 | 0.057 | 2.98 | 2.6 | 27,880 | 391.0 | 11.3 | 33.5 | 435.7 | 8/8 | 0 | 2.0 |
| map_locality | 3 | 1569 | +247 | 0.839 | 107,369 | 0.054 | 2.98 | 2.5 | 27,879 | 392.2 | 10.7 | 33.5 | 436.4 | 8/8 | 0 | 2.6 |
| map_taluka | 3 | 1564 | +243 | 0.842 | 118,347 | 0.060 | 2.98 | 2.7 | 27,837 | 391.0 | 11.8 | 33.4 | 436.2 | 8/8 | 0 | 2.3 |
| map_balanced | 5 | 1433 | +113 | 0.919 | 296,099 | 0.150 | 4.99 | 5.0 | 24,978 | 358.2 | 29.6 | 30.0 | 417.8 | 8/8 | 0 | 6.1 |
| map_locality | 5 | 1435 | +113 | 0.918 | 295,321 | 0.149 | 4.99 | 5.0 | 24,862 | 358.8 | 29.5 | 29.8 | 418.1 | 8/8 | 0 | 16.9 |
| map_taluka | 5 | 1436 | +115 | 0.917 | 294,240 | 0.149 | 4.99 | 5.0 | 24,909 | 359.0 | 29.4 | 29.9 | 418.3 | 8/8 | 0 | 15.4 |

What the table shows:

- **The 2 km rule decides how many booths open, not the map.** At 2 km
  (1.5 km straight-line), 630 of the 751 populated units in
  map_balanced have no other unit in reach. Each of those units must get its
  own booth, so the model opens about 330 booths (25%) above the bare minimum
  and capacity use falls to 0.80. Relaxing the limit to 3 km and then 5 km
  removes 85 and then about 215 booths. It saves ₹15 lakh and then ₹33 lakh, but voter
  person-km rises 4× and then 10×.
- **The three maps cost almost the same.** The objectives differ by less than
  0.2% at every MAX_KM. Booth logistics depend on how the villages are spaced,
  not on where the seat boundaries fall. Booth cost should not be a reason to
  choose one map over another.
- **Booth cost dominates the objective**, at 86 to 92% of it. The voter
  person-km result is close to zero because most electors vote in their own
  unit.

## Limitations

- **Travel inside a unit is not modelled.** Each unit is a single demand
  point and `d_ii = 0`. City wards with about 20,000 people each therefore
  show almost zero voter km. Mean and maximum km measure travel between units
  only.
- The fallback distances are not road distances. Rerun with the road matrix
  before quoting any km figures.
- The elector ratio and all costs are assumptions, listed in the table under
  Model. The booth count depends mainly on MAX_KM. The costs mainly decide
  whether a small village shares a neighbour's booth when one is within reach.
