#!/usr/bin/env python3
"""Independently verify saved aggregates and PC feasibility; no solver needed."""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

HERE=Path(__file__).resolve().parent
E=HERE/'evidence'
def rows(p):
    with (E/p).open(newline='') as f:return list(csv.DictReader(f))
def close(a,b,tol=1.01):
    assert abs(float(a)-float(b))<=tol,(a,b,tol)

manifest=json.loads((HERE/'source_manifest.json').read_text())
for x in manifest['sources']:
    assert hashlib.sha256((E/x['path']).read_bytes()).hexdigest()==x['sha256'],x['path']
sc=rows('code/logistics_runs/summary/scenarios.csv')
assert len(sc)==36
assert len({(r['district'],r['scenario']) for r in sc})==36
all_dropped=0
missing_details=[]
for r in sc:
    prefix=f"code/logistics_runs/{r['district']}/location/{r['scenario']}"
    seats=rows(prefix+'/seats.csv')
    rt=rows(prefix+'/routing/seats.csv')
    assert len(seats)==int(r['seats'])==len(rt)
    assert {x['seat'] for x in seats}=={x['seat'] for x in rt}
    for k in ['electors','stations','premises','cost_stations','cost_walk']:
        close(sum(float(x[k]) for x in seats),r[k],0.01)
    for k in ['buses','jeeps','sectors','transport_cost']:
        close(sum(float(x[k]) for x in rt),r[k],0.01)
    close(sum(float(x['vehicle_km']) for x in rt),r['vehicle_km'])
    total=sum(float(x['cost_stations'])+float(x['cost_premises'])+float(x['cost_walk']) for x in seats)+sum(float(x['transport_cost']) for x in rt)
    close(total,r['total_cost_routed'],0.01)
    close(total/float(r['electors']),r['rs_per_elector'],0.006)
    close(sum(float(x['mean_walk_km'])*float(x['electors']) for x in seats)/float(r['electors']),r['mean_walk_km'],0.00051)
    if r['scenario']=='base':all_dropped+=sum(int(x['dropped_stops']) for x in rt)
    # Missing detailed files are assessed in the original workspace; their absence
    # is distinct from the deliberately selective frozen evidence collection.
    original=HERE.parents[1]/prefix
    for f in ['sites.csv','assignments.csv']:
        if not (original/f).exists():missing_details.append(str(Path(prefix)/f))

si=rows('code/logistics_runs/summary/simulations.csv')
assert len(si)==48
assert len({(r['district'],r['scenario'],r['label']) for r in si})==48
for r in si:
    p=f"code/logistics_runs/{r['district']}/location/{r['scenario']}/simulation/summary_{r['label']}.json"
    j=json.loads((E/p).read_text())
    assert int(r['reps'])==j['reps']==30
    for k in ['voter_mean_wait_min','balked_share','votes_after_18_share','p95_overrun_min','stations','share_voters_wait_over_30']:
        close(r[k],j[k],1e-8)

pc=rows('code/logistics_runs/representation/pc_balanced/pc_assignment.csv')
adj=rows('code/logistics_runs/representation/pc_balanced/ac_adjacency.csv')
summary=json.loads((E/'code/logistics_runs/representation/pc_balanced/summary.json').read_text())
assert len(pc)==288 and len({r['ac_key'] for r in pc})==288 and len({int(r['ac']) for r in pc})==288
graph=defaultdict(set)
for r in adj:
    graph[r['ac_a']].add(r['ac_b']);graph[r['ac_b']].add(r['ac_a'])
groups=defaultdict(list)
for r in pc:groups[r['pc']].append(r)
assert len(groups)==48
elector_total=0
for key,g in groups.items():
    assert len(g)==6
    members={r['ac_key'] for r in g}
    seen=set();todo=[next(iter(members))]
    while todo:
        n=todo.pop()
        if n in seen:continue
        seen.add(n);todo.extend((graph[n]&members)-seen)
    assert seen==members,key
    total=sum(int(r['ac_population']) for r in g)
    assert summary['population_bounds'][0]<=total<=summary['population_bounds'][1]
    assert all(int(r['pc_population'])==total for r in g)
    elector_total+=total
assert elector_total==summary['population']==97141289
assert all_dropped==82
base=[r for r in sc if r['scenario']=='base']
assert sum(int(r['electors']) for r in base)==19123489
assert sum(int(r['stations']) for r in base)==20196
assert sum(int(r['premises']) for r in base)==14532
assert len(missing_details)==72
result={'source_hashes_verified':len(manifest['sources']),'scenario_aggregates_verified':len(sc),'simulation_summaries_verified':len(si),'base_seats':sum(int(r['seats']) for r in base),'base_dropped_stops':all_dropped,'missing_detailed_files_in_original_workspace':len(missing_details),'pc_ac_coverage':288,'pcs':48,'pc_connectivity_and_bounds_verified':True}
(HERE/'verification_results.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
