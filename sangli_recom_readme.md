# Sangli constituency ReCom prototype

This is a research optimizer for eight contiguous Assembly-style regions across
Sangli district. It uses 2011 population and explicit settlement/ward polygons.
It does not model election equipment transport or booth logistics.

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
straight-line distance to the best constituent unit centre, taluka fragments
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

The official [Census Sangli PCA table](https://censusindia.gov.in/nada/index.php/catalog/6722)
provides population; [SHRUG PC11 village/town maps](https://docs.devdatalab.org/SHRUG-Construction-Details/shrug-open-source-polygons/)
provide open geometries; the [municipal final ward page](https://smkc.gov.in/SMKC_New_Election_Final.aspx)
provides the city ward map and populations. SHRUG reports position errors of
roughly 0–2 km and is released for noncommercial use under
[CC BY-NC-SA 4.0](https://docs.devdatalab.org/Getting-Started/license/).
