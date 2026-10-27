# Election logistics in Maharashtra — project blueprint

A consulting-style study of how India runs an Assembly election on the ground,
built as a chain of operations-research models that hand results to each other,
each validated against what the Election Commission (ECI) and district officers
actually did in 2009–2024.

**The client question.** *For a district election officer: how many polling
stations, where, served from which dispatch centre, with what fleet, reserve
machines and staff, and what does each ECI rule (1,200 electors per station,
2 km walk, one dispatch centre per seat) cost and buy in voter access?*
And for the Delimitation Commission: *how unequal are the 2008 seats now, and
what does a fairer map cost in administrative coherence and logistics?*

---

## 1. System: seven layers

| Layer | Question | Model | Status |
|---|---|---|---|
| L0 Data & demand | Who votes where, and how far is it on foot or by road? | Census 2011 units, calibrated to 2024 rolls; sub-village demand from WorldPop; drive and walk networks with travel time | partly built |
| L1 Representation | Assembly seats (ACs), Lok Sabha seats (PCs), SC/ST reservation | ReCom districting; PC grouping of 6 ACs; reservation by the Delimitation Act's share rule | ACs built; PCs and reservation new |
| L2 Strategic logistics | Polling stations and dispatch centre per AC | Two-echelon capacitated facility location (joint depot + booth MIP) | new |
| L3 Operational logistics | Moving polling parties and EVMs out and back | Capacitated VRP with time windows (dispatch day, collection night); sector formation | new |
| L4 Polling day | Queues, closing-time backlog, EVM failures and replacement | Discrete-event simulation with Monte Carlo | new |
| L5 Resources & cost | Staff, EVMs and reserves, vehicles, ₹ per elector | Bill of materials from L2–L4 | new |
| L6 Validation | Does the model reproduce what happened? | Comparison with 2009–2024 rolls, station counts, fleets, turnout curves, failure reports | started |

Each layer reads the previous layer's files, so any scenario (a new map, a new
cap, a new rule) flows through to cost, access and risk.

---

## 2. Models

### L0 — demand
* **Electors 2024.** Census 2011 population per unit × (AC's 2024 electors ÷ AC's 2011
  population), so every official AC matches its real 2024 roll. Census-to-roll ratio
  0.62–0.79 in 2009–14 (audit), so a flat 0.70 is replaced.
* **Inside the village.** Voting "in your own village" is not 0 km. Each village's people are spread
  over its WorldPop 1 km cells (rescaled to the Census total); a voter's walk is cell →
  booth plus the expected distance inside a 1 km cell (0.38 km × road factor). Large
  villages and wards get several demand points, so the model can place two booths at
  opposite ends of a big village.
* **Travel time.** Walk 4.5 km/h (3.0 km/h sensitivity for elderly/PwD voters) on roads plus footpaths
  and tracks; vehicles by road class (e.g. trunk 50, primary 40, secondary 35, tertiary 30, village
  20 km/h), loading time at stops.

### L1 — representation
* **ACs:** ReCom + simulated annealing (existing), ±10% population band.
* **Reservation:** per district, the number of SC and ST seats in the 2008 order; ST
  seats go to the ACs with the highest ST share, then SC seats to the highest SC share among the rest
  (Delimitation Act 2002, s.9(c)–(d)). Compare reserved seats' shares, official vs ours.
* **PCs:** 48 groups of 6 contiguous ACs (statewide AC adjacency), balanced on 2024
  electors; PC reservation by the same share rule; compare with the official 48.

### L2 — joint dispatch centre + polling station location (per AC)
Sets: demand points *i*, candidate premises *s* (OSM schools/public buildings, else village
centres), candidate dispatch centres *d* (towns in the AC).

```
min  C_ps Σ y_s + C_site Σ o_s + c_walk Σ t_is x_is + c_trip Σ τ_ds u_ds + F_d Σ z_d
s.t. Σ_s x_is = e_i                       every elector served
     Σ_i x_is ≤ K y_s                     K = 1,200 (ECI 2025) or 1,500
     y_s ≤ B o_s                          at most B stations per premises
     x_is = 0 if t_is > T_max             walk limit (2 km, or 30 min)
     Σ_d z_d = 1                          one dispatch centre per AC
     Σ_d u_ds = y_s,  u_ds ≤ B z_d        every station is served from the open centre
     y integer, o, z binary, x, u ≥ 0
```
Benchmarks: (a) ECI-style rule — stations per village = ⌈electors/K⌉ at the village
centre; (b) depot fixed at the 1-median (old model); (c) depot at the taluka HQ town
(common practice). Transport here is a star approximation; L3 replaces it with routes
and checks it with the Beardwood–Halton–Hammersley continuum estimate.

### L3 — dispatch and collection routing
* Per AC: CVRPTW from the dispatch centre. Vehicles: bus (6 parties), jeep (1 party).
  Dispatch day: leave 12:00–14:00, all parties in place by 18:00. Collection: after the 18:00
  close + about 1 h of sealing, back to the receipt centre; minimise latest arrival.
  Service time 10 min per stop + 3 min per party. OR-Tools routing.
* Sectors: capacitated p-median clustering of stations into sectors of 10–12 (ECI handbook);
  the sector officer's base feeds L4.
* Outputs: vehicles by type, vehicle-km, route hours, latest arrival, ₹.

### L4 — polling-day simulation
* Arrivals: non-homogeneous Poisson from the 2024 state turnout curve (11:00 18.1%,
  13:00 32.2%, 15:00 45.5%, 17:00 58.2%, final 66.05%), urban/rural shifted.
* Service: one voting compartment; time per voter lognormal, mean 45–60 s (sweep).
* Closing rule: everyone in line at 18:00 votes (token system) → backlog after close.
* EVM failures: replacement rates per unit from 2024 reports (VVPAT 5.5%, CU 3.3%, BU 1.6%
  in Assam phase 1); downtime = sector officer's travel + 20 min setup; reserve
  policy (20% BU/CU, 30% VVPAT) and sector size as levers.
* Outputs: wait distribution, P(wait > 30/60 min), votes cast after 18:00, voter-hours lost
  to failures, by station size and scenario.

### L5 — resources
Staff = 4 per station + 20% reserve; EVMs BU/CU/VVPAT + reserve; vehicles from L3; cost
per elector, split by station, transport, staff and voter time.

---

## 3. Validation against the real election

| Check | Real data | Model output |
|---|---|---|
| Seat sizes | Registered electors per AC, 2009–2024 (all 288) | L0/L1 — done |
| Census → roll ratio | District electors ÷ 2011 population | L0 calibration |
| Number of polling stations | Pune district 8,443–8,462 (2024); Hadapsar 525 parts; per-AC counts where found | L2 station count per AC |
| Fleet | PMPML lent 968 buses for 11 urban Pune ACs (Nov 2024) | L3 buses for the same ACs |
| Turnout curve and closing surge | Hourly turnout; 5 pm 58.2% → final 66.05% | L4 cumulative votes and post-18:00 backlog |
| EVM replacements | Assam 2024 phase 1 component rates; Goa 49 replacements | L4 replacement counts |
| Distance vs turnout (optional) | Form 20 booth results (Pune district site) | L2 access vs turnout |

## 4. Scenarios for the consulting report
1. Cap 1,500 → 1,200: stations, staff, EVMs, ₹, wait times saved.
2. Walk rule: 2 km vs 3 km vs 30 min walking.
3. Dispatch centre: taluka HQ (practice) vs optimised vs one district hub.
4. Fleet mix: buses vs jeeps; dispatch start time.
5. Reserve policy and sector size vs voter-hours lost to failures.
6. Demand year: 2011 vs 2024 rolls.
7. Map: official 2008 ACs vs optimised ACs — logistics cost of redistricting.
8. Lok Sabha: official PCs vs balanced PCs on 2024 rolls, with reservation.

## 5. Compute
Gurobi (WLS licence) locally for L2; OR-Tools for L3; NumPy DES for L4. Google Cloud VMs
for statewide or many-scenario runs; Kaggle CPU notebooks for Monte Carlo batches (no
licence file goes to Kaggle).

## 6. Pilot districts
Sangli (8 ACs), Satara (8), Nashik (15), Pune (21): rural, mixed and metropolitan cases.
