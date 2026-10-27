"""Independently audit district map outputs and create a comparison table.

Works for any district written by sangli_recom.py: the seat count is read
from the maps (and must agree with --seats if given), and the population band
from each map's score.json settings. Seat labels are matched to the first
result by maximum shared population before counting changed units.
"""
import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import shapely
from scipy.optimize import linear_sum_assignment
from shapely.geometry import shape


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def assigned(result):
    return read(result/'assigned_units.geojson')['features']


def seat_labels(features):
    return sorted({int(f['properties']['constituency']) for f in features})


def match(weight,a_labels,b_labels):
    """Map each label of b to the label of a it overlaps most (one-to-one)."""
    m=np.array([[weight(i,j) for j in b_labels] for i in a_labels],dtype=float)
    rows,cols=linear_sum_assignment(m,maximize=True)
    return {b_labels[c]:a_labels[r] for r,c in zip(rows,cols)}


def align(base,current):
    a={};b={}
    pop={f['properties']['id']:int(f['properties']['population']) for f in base}
    for f in base:a.setdefault(int(f['properties']['constituency']),set()).add(f['properties']['id'])
    for f in current:b.setdefault(int(f['properties']['constituency']),set()).add(f['properties']['id'])
    return match(lambda i,j:sum(pop[u] for u in a[i]&b[j]),sorted(a),sorted(b))


def audit(result,input_features,district,seats=None):
    fs=assigned(result)
    regions=read(result/'constituencies.geojson')['features']
    score=read(result/'score.json')
    expected={f['properties']['id']:int(f['properties']['population']) for f in input_features}
    got={f['properties']['id']:int(f['properties']['population']) for f in fs}
    if len(got)!=len(fs) or got!=expected:raise ValueError(f'{result}: input unit/population mismatch')
    labels=seat_labels(fs)
    n=len(labels)
    if seats is not None and n!=seats:raise ValueError(f'{result}: {n} seats, expected {seats}')
    if len(regions)!=n or sorted(int(f['properties']['constituency']) for f in regions)!=labels:
        raise ValueError(f'{result}: constituencies.geojson does not match the {n} seats in assigned_units')
    balance=score.get('settings',{}).get('balance',0.10)
    target=sum(got.values())/n
    by_seat={i:[] for i in labels}
    for f in fs:by_seat[int(f['properties']['constituency'])].append(f)
    deviation=[]
    for i in labels:
        pop=sum(int(f['properties']['population']) for f in by_seat[i])
        deviation.append(abs(pop/target-1))
        if not math.ceil((1-balance)*target)<=pop<=math.floor((1+balance)*target):
            raise ValueError(f'{result}: seat {i} out of balance')
    geoms=[shape(f['geometry']) for f in regions]
    if not all(g.geom_type=='Polygon' and g.is_valid for g in geoms):
        raise ValueError(f'{result}: disconnected or invalid seat geometry')
    union=shapely.union_all(geoms)
    lat=district.centroid.y
    area_km2=district.symmetric_difference(union).area*111.32**2*math.cos(math.radians(lat))
    if area_km2>.1:raise ValueError(f'{result}: district coverage discrepancy {area_km2:.3f} km²')
    tree=shapely.STRtree(geoms)
    max_overlap=max((geoms[i].intersection(geoms[j]).area for i in range(n)
                     for j in tree.query(geoms[i]) if j>i),default=0)
    if max_overlap>1e-8:raise ValueError(f'{result}: overlapping seats')
    terms=score['terms']
    common=(.35*terms['population']+.30*terms['distance']+
            .20*terms['taluka_fragmentation']+.15*terms['shape'])
    search=score.get('search',{})
    return fs,score,dict(name=result.name,passed=True,units=len(fs),population=sum(got.values()),
        max_deviation_pct=round(100*max(deviation),2),
        mean_distance_km=round(score['raw']['population_weighted_mean_distance_km'],2),
        taluka_excess_fragments=score['raw']['taluka_excess_fragments'],
        mean_shape_penalty=round(score['terms']['shape'],3),
        own_weighted_score=round(score['score'],3),
        common_baseline_score=round(common,3),
        geographic_discrepancy_km2=round(area_km2,6),
        proposals=search.get('proposals',''),accepted=search.get('accepted',''))


def main():
    ap=argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--district',type=Path,required=True)
    ap.add_argument('--results',type=Path,nargs='+',required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--seats',type=int,default=None,help='expected seat count (default: read from the maps)')
    a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    inputs=read(a.input)['features']
    district=shape(read(a.district)['geometry'])
    results=[]
    for result in a.results:results.append(audit(result,inputs,district,a.seats))
    counts={len(seat_labels(r[0])) for r in results}
    if len(counts)!=1:raise ValueError(f'Maps have different seat counts: {sorted(counts)}')
    base_fs=results[0][0]
    base=a.results[0].name
    base=base[4:] if base.startswith('map_') else base
    for fs,score,row in results:
        colors=align(base_fs,fs)
        baseline={f['properties']['id']:int(f['properties']['constituency']) for f in base_fs}
        moved=[f for f in fs if colors[int(f['properties']['constituency'])]!=baseline[f['properties']['id']]]
        row[f'units_changed_vs_{base}']=len(moved)
        row[f'people_changed_vs_{base}']=sum(int(f['properties']['population']) for f in moved)
    rows=[r[2] for r in results]
    with (a.out/'map_comparison.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    (a.out/'validation_report.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    print(json.dumps(rows,indent=2))


if __name__=='__main__':main()
