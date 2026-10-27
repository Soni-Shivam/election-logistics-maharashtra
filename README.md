# Election logistics in Maharashtra

IE 301 (Operations Analysis) project: an operations-research model of how an Indian Assembly
election is delimited and run on the ground, validated against Maharashtra's 2009–2024 elections.
The design and every model's formulation are in [docs/BLUEPRINT.md](docs/BLUEPRINT.md).

| Layer | Code |
|---|---|
| Constituencies (ReCom districting) | `code/sangli_recom.py`, `code/prepare_district_inputs.py`, `code/official_district_map.py` |
| Audit of the official 2008 map on real rolls | `code/audit_official_2008.py`, `code/fetch_ac_electors.py` |
| Walking and driving networks (OSM) | `code/logistics/network.py` |
| Demand: 2024 electors, hamlets, walk inside villages | `code/logistics/demand.py` |
| Joint dispatch centre + polling-station location (Gurobi) | `code/logistics/location.py` |
| Dispatch routing (OR-Tools CVRPTW) and sectors | `code/logistics/routing.py` |
| Polling-day queue and EVM-failure simulation | `code/logistics/simulate.py` |
| SC/ST reservation and Lok Sabha seats | `code/logistics/representation.py`, `code/group_lok_sabha.py` |
| Scenario sweep | `code/logistics/scenarios.py` |

Raw inputs (OSM extract, WorldPop, SHRUG, DataMeet) are not in the repository; the scripts name
their sources. SHRUG-derived data is CC BY-NC-SA 4.0.

```bash
python -m venv .venv && .venv/bin/pip install numpy pandas scipy pulp highspy ortools osmium rasterio shapely networkx geopandas pyproj scikit-learn numba gurobipy
cd code
../.venv/bin/python -m logistics.network --district sangli
../.venv/bin/python -m logistics.demand --district sangli
../.venv/bin/python -m logistics.scenarios --districts sangli --stage all
```
