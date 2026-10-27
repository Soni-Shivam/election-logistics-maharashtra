#!/usr/bin/env python3
"""Build a standalone LaTeX report from the frozen, non-secret project evidence.

Run from any directory: python3 docs/technical_report/build_report.py
Only the report source and audit summary are written; no model is re-solved.
"""
from pathlib import Path
import csv
import hashlib
import json
import math
from collections import Counter

HERE = Path(__file__).resolve().parent
E = HERE / 'evidence'

def rows(path):
    with (E / path).open(newline='') as f:
        return list(csv.DictReader(f))

def obj(path):
    return json.loads((E / path).read_text())

def esc(v):
    s = str(v)
    for a, b in [('\\', r'\textbackslash{}'), ('&', r'\&'), ('%', r'\%'), ('_', r'\_'), ('#', r'\#'), ('$', r'\$')]:
        s = s.replace(a, b)
    return s

def num(v, dp=0):
    return f'{float(v):,.{dp}f}'

def table(headers, data, caption, widths=None, small=True):
    n = len(headers)
    spec = widths or ('l' + 'r' * (n-1))
    if len(data)<=8:
        return '\n'.join([r'\begin{table}[htbp]\centering',r'\small',
                          r'\setlength{\tabcolsep}{5pt}',r'\caption{'+caption+'}',
                          r'\begin{tabular}{'+spec+'}',r'\toprule',
                          ' & '.join(headers)+r'\\',r'\midrule']+
                         [' & '.join(str(x) for x in row)+r'\\' for row in data]+
                         [r'\bottomrule\end{tabular}',r'\end{table}'])
    out = [r'\begin{center}', r'\small' if small else '', r'\setlength{\tabcolsep}{5pt}',
           r'\begin{longtable}{' + spec + '}', r'\caption{' + caption + r'}\\',
           r'\toprule', ' & '.join(headers) + r'\\', r'\midrule\endfirsthead',
           r'\toprule', ' & '.join(headers) + r'\\', r'\midrule\endhead',
           r'\midrule\multicolumn{' + str(n) + r'}{r}{\footnotesize Continued on next page}\\\endfoot',
           r'\bottomrule\endlastfoot']
    out += [' & '.join(str(x) for x in row) + r'\\' for row in data]
    out += [r'\end{longtable}', r'\end{center}']
    return '\n'.join(out)

D = ['sangli', 'satara', 'nashik', 'pune']
sc = rows('code/logistics_runs/summary/scenarios.csv')
si = rows('code/logistics_runs/summary/simulations.csv')
base = {r['district']: r for r in sc if r['scenario'] == 'base'}
lookup = {(r['district'], r['scenario']): r for r in sc}
sims = {(r['district'], r['scenario'], r['label']): r for r in si}
audit = {}
for d in D:
    loc = rows(f'code/logistics_runs/{d}/location/base/seats.csv')
    rt = rows(f'code/logistics_runs/{d}/location/base/routing/seats.csv')
    fac = rows(f'code/logistics_runs/{d}/elector_factors.csv')
    audit[d] = {'location_seats': len(loc), 'status': dict(Counter(x['status'] for x in loc)),
                'max_gap': max(float(x['mip_gap']) for x in loc),
                'runtime_s': sum(float(x['runtime_s']) for x in loc),
                'dropped_stops': sum(int(x['dropped_stops']) for x in rt),
                'pooled_seats': sum(x['pooled'] == 'True' for x in fac)}
manifest = json.loads((HERE/'source_manifest.json').read_text())
for entry in manifest['sources']:
    p = E / entry['path']
    assert hashlib.sha256(p.read_bytes()).hexdigest() == entry['sha256'], p

T = {}
T['BASE'] = table(['District', 'ACs', 'Electors', 'Stations', 'Premises', 'km walk', 'Rs/elector'],
    [[d.title(), base[d]['seats'], num(base[d]['electors']), num(base[d]['stations']), num(base[d]['premises']), num(base[d]['mean_walk_km'],3), num(base[d]['rs_per_elector'],2)] for d in D],
    'Saved base scenario: 2024-derived demand, 1,200-elector capacity, 2 km planning limit, joint dispatch centre. Rs/elector includes imputed voter walking cost. Source: scenario summary [S9].')
T['DEMAND'] = table(['District', 'Populated units', 'Demand points', 'Inside walk (km)', 'Pooled ACs'],
    [[d.title(),num(obj(f'code/logistics_runs/{d}/demand_info.json')['units_populated']),num(obj(f'code/logistics_runs/{d}/demand_info.json')['demand_points']), num(obj(f'code/logistics_runs/{d}/demand_info.json')['intra_km_weighted_mean'],3),audit[d]['pooled_seats']] for d in D],
    'Demand construction and pooled calibration in the saved inputs [S7]. Inside walk is elector weighted; the reported hamlet p90 in metadata uses unweighted points.')
T['NETWORK'] = table(['District', 'Nodes stored', 'Walk edges', 'Drive edges', 'Walk LCC', 'Drive LCC'],
    [[d.title()]+[num(obj(f'code/logistics_runs/{d}/network_info.json')[k]) for k in ['nodes','walk_edges','drive_edges','walk_component_nodes','drive_component_nodes']] for d in D],
    'Separate OSM network sizes [S7]. LCC denotes the largest connected component. Stored node counts include nodes not retained in a graph component.')
dr = rows('code/analysis/official_audit/district_summary.csv')
T['DISTRICT'] = table(['District', 'ACs', 'Official MAD', 'Best MAD', 'Official outside', 'Best outside'],
    [[r['district'],r['seats'],num(r['official_mean_abs_dev_pct'],2)+r'\%',num(r['best_mean_abs_dev_pct'],2)+r'\%',r['official_outside_10pct'],r['optimized_outside_10pct']] for r in dr],
    'Census-2011 population balance on reconstructed units [S5]. Outside means outside the district mean by more than 10 percent. City allocation is approximate.')
T['TRADEOFF'] = table(['District', 'Extra cuts: off./range', 'PP: off./best', 'Centre km: off./best', 'Best map'],
    [[r['district'],r['official_taluka_excess']+' / '+r['optimized_taluka_excess_range'], num(r['official_polsby_popper'],3)+' / '+num(r['best_polsby_popper'],3),num(r['official_km_to_centre'],2)+' / '+num(r['best_km_to_centre'],2),esc(r['best_map'])] for r in dr],
    'Administrative and geometric trade-offs [S5]. The cuts column gives official value / range over all six optimized runs; PP and centre distance give official / selected best map. PP is Polsby--Popper compactness.')
es = rows('code/analysis/official_audit/electors_statewide.csv')
T['ELECTORS'] = table(['Year', 'Electors', 'Outside 10\%', 'Outside 20\%', 'MAD (\%)', 'Largest/smallest'],
    [[r['year'],num(r['electors']),r['outside_10pct_of_district_avg'],r['outside_20pct_of_district_avg'],num(r['mean_abs_dev_district_pct'],2),num(r['largest_over_smallest'],2)] for r in es],
    'Roll-based drift on all 288 official ACs, using the saved Wikipedia extraction [S6]. Deviations use district averages; the last column is the statewide maximum/minimum ratio.')
T['SOLVER'] = table(['District', 'ACs', 'Within tolerance', 'Time-limited', 'Max gap (\%)', 'Seat-solve seconds', 'Dropped stops'],
    [[d.title(),audit[d]['location_seats'],audit[d]['status'].get('optimal',0),audit[d]['status'].get('gurobi_9',0),num(audit[d]['max_gap']*100,3),num(audit[d]['runtime_s'],1),audit[d]['dropped_stops']] for d in D],
    'Saved base solve status and route omissions [S8, S10]. ``Within tolerance'' is the code-labelled ``optimal'' status, normally at a requested relative MIP gap of 0.5 percent; it does not imply a zero gap.')
T['ROUTES'] = table(['District', 'Buses', 'Jeeps', 'Vehicle-km', 'Transport (lakh Rs)', 'Sectors', 'Latest'],
    [[d.title(),num(base[d]['buses']),num(base[d]['jeeps']),num(base[d]['vehicle_km']),num(float(base[d]['transport_cost'])/1e5,2),base[d]['sectors'],base[d]['latest_arrival']] for d in D],
    'Saved base dispatch results [S9, S10]. Latest is the latest served-route arrival/completion measure reported by the code; omitted stops are excluded.')
T['CAP'] = table(['District', 'Extra stations', 'Extra cost (lakh Rs)', 'Wait: 1500/1200', r'$P(W>30)$: 1500/1200'],
    [[d.title(),num(float(base[d]['stations'])-float(lookup[d,'cap1500']['stations'])),num((float(base[d]['total_cost_routed'])-float(lookup[d,'cap1500']['total_cost_routed']))/1e5,2),num(lookup[d,'cap1500']['voter_mean_wait_min'],2)+' / '+num(base[d]['voter_mean_wait_min'],2),num(float(lookup[d,'cap1500']['share_voters_wait_over_30'])*100,2)+r'\% / '+num(float(base[d]['share_voters_wait_over_30'])*100,2)+r'\%'] for d in D],
    'Effect of reducing capacity from 1,500 to 1,200 [S9, S11]. Waits are minutes among voters who complete voting under the assumed balking model.')
T['SIM'] = table(['District', 'Wait (min)', r'$P(W>30)$', 'Balk (\%)', 'After close (\%)', 'p95 overrun (min)'],
    [[d.title(),num(base[d]['voter_mean_wait_min'],2),num(100*float(base[d]['share_voters_wait_over_30']),2)+r'\%',num(100*float(base[d]['balked_share']),2),num(100*float(base[d]['votes_after_18_share']),2),num(base[d]['p95_overrun_min'],1)] for d in D],
    'Base simulation, 50-second mean service, 20-percent sector reserve, failures enabled, 30 replications [S11]. After-close share is conditional on completed votes.')
T['NOBALK'] = table(['District', 'Wait: balk/no balk', 'Balked (\%)', 'After close: balk/no balk', 'p95 overrun: balk/no balk'],
    [[d.title(),num(sims[d,'base','svc50']['voter_mean_wait_min'],2)+' / '+num(sims[d,'base','svc50_nobalk']['voter_mean_wait_min'],2),num(100*float(sims[d,'base','svc50']['balked_share']),2),num(100*float(sims[d,'base','svc50']['votes_after_18_share']),2)+r'\% / '+num(100*float(sims[d,'base','svc50_nobalk']['votes_after_18_share']),2)+r'\%',num(sims[d,'base','svc50']['p95_overrun_min'],1)+' / '+num(sims[d,'base','svc50_nobalk']['p95_overrun_min'],1)] for d in D],
    'Behavioural sensitivity [S11]. Disabling balking exposes longer queues; lower waits with balking must be read alongside lost attempted votes.')
T['SERVICE'] = table(['District', '40 s mean: wait', '50 s mean: wait', '60 s mean: wait', '60 s balked (\%)'],
    [[d.title()]+[num(sims[d,'base',f'svc{s}']['voter_mean_wait_min'],2) for s in [40,50,60]]+[num(100*float(sims[d,'base','svc60']['balked_share']),2)] for d in D],
    'Service-speed sensitivity with the same location layout [S11]. Waits are minutes; all three runs include balking and failures.')
validation=rows('code/logistics_runs/summary/validation.csv')
T['VALIDATION'] = table(['Scope/check', 'Benchmark', 'Model', 'Error (\%)', '2011 model'],
    [[esc(r['scope'] if r['check']=='polling stations' else ('Urban Pune buses' if 'buses' in r['check'] else r['scope']+' electors/station')),num(r['actual_2024']),num(r['model_2024_rolls']),num(r['error_pct'],1),num(r['model_census_2011']) if r['model_census_2011'] else '--'] for r in validation if not r['check'].startswith('share of')],
    'Saved validation extract [S12]. The Hadapsar benchmark is labelled ``recent roll'', not a confirmed 2024 station inventory. These comparisons also use the 1,200-cap base against 2024 observations.',widths='p{5.2cm}rrrr')
T['TURNOUT']=table(['District', 'Hour', 'Reported (\%)', 'Simulated (\%)', 'Difference (pp)'],
    [[r['scope'].split(' ')[0],r['check'].split('by ')[-1],num(r['actual_2024'],1),num(r['model_2024_rolls'],1),num(float(r['model_2024_rolls'])-float(r['actual_2024']),1)] for r in validation if r['check'].startswith('share of')],
    'Saved 50-second base cumulative-vote comparisons [S12]. Values are shares of all completed votes. Difference is in percentage points, unlike the relative error in the original validation CSV.')
T['CAPVALUE']=table(['District', '40 s wait: 1200/1500', 'Hours saved', 'Extra cost/elector', 'Rs/hour saved'],
    [[r['District'],num(r['wait_1200_min'],2)+' / '+num(r['wait_1500_min'],2),num(r['voter_hours_saved']),num(r['extra_cost_per_elector'],2),num(r['rs_per_voter_hour_saved'],1)] for r in rows('code/logistics_runs/summary/report/cap_value.csv')],
    'Saved report-layer capacity valuation at 40-second service with no balking [S9, S11]. Extra cost is the generalized routed cost difference, not solely a cash-budget difference.')
pc = rows('code/logistics_runs/representation/lok_sabha_summary.csv')
T['PC'] = table(['Plan', 'PCs', 'Size ratio', 'Outside 10\%', 'Outside 20\%', 'MAD (\%)', 'SC/ST mean (\%)'],
    [[('Official' if r['plan']=='official' else 'Balanced 2024'),r['pcs'],num(r['size_ratio_2024'],3),r['outside_10pct_2024'],r['outside_20pct_2024'],num(r['mean_abs_dev_pct_2024'],2),num(r['sc_reserved_mean_sc_pct'],2)+' / '+num(r['st_reserved_mean_st_pct'],2)] for r in pc],
    'Statewide PC outcomes [S13]. Both use the 288 official ACs. The balanced run permits 25-percent deviation and is heuristic.')
rep = rows('code/logistics_runs/representation/assembly_reservation.csv')
bestmaps = {r['district']:r['best_map'] for r in dr}
T['RESERVATION'] = table(['District', 'Class', 'Seats', 'Official share (\%)', 'Selected-map share (\%)'],
    [[r['district'],r['kind'],r['seats'],num(r['reserved_mean_share_pct'],2),num(next(x['reserved_mean_share_pct'] for x in rep if x['district']==r['district'] and x['kind']==r['kind'] and x['map']==bestmaps[r['district']]),2)] for r in rep if r['map']=='official_2008'],
    'Mean SC or ST population share in reserved ACs [S13]. The selected map is the districting-score winner, not the map maximizing these shares.')
T['SCENARIOAPP'] = table(['District/scenario', 'Stations', 'Walk km', '>2 km (\%)', 'Cost (lakh Rs)', 'Dropped'],
    [[esc(r['district'].title()+'/'+r['scenario']),num(r['stations']),num(r['mean_walk_km'],3),num(r['pct_over_2km'],2),num(float(r['total_cost_routed'])/1e5,2),sum(int(x['dropped_stops']) for x in rows(f"code/logistics_runs/{r['district']}/location/{r['scenario']}/routing/seats.csv"))] for r in sc],
    'Complete saved location/routing scenario register [S8--S10]. Generalized cost includes walking valuation; route omissions qualify all cost comparisons.',widths='p{4.5cm}rrrrr')
T['SIMAPP'] = table(['District/variant', 'Wait min', '>30 min (\%)', 'Balk (\%)', 'After 18 (\%)', 'Overrun p95'],
    [[esc(r['district'].title()+'/'+('1500-' if r['scenario']=='cap1500' else '')+r['label']),num(r['voter_mean_wait_min'],2),num(100*float(r['share_voters_wait_over_30']),2),num(100*float(r['balked_share']),2),num(100*float(r['votes_after_18_share']),2),num(r['p95_overrun_min'],1)] for r in si],
    'Complete saved Monte Carlo sensitivity register [S11]. All have 30 replications; labels ending in nobalk disable balking.',widths='p{5cm}rrrrr')
acrows=[]
for d in D:
    for r in rows(f'code/logistics_runs/{d}/location/base/seats.csv'):
        acrows.append([d.title()+'/'+r['seat'],num(r['electors']),r['stations'],r['premises'],num(r['mean_walk_km'],3),num(100*float(r['mip_gap']),3),'Limit' if r['status']=='gurobi_9' else 'Tol.'])
T['ACAPP']=table(['District/AC', 'Electors', 'Stations', 'Sites', 'Walk km', 'Gap (\%)', 'Status'],acrows,
    'All 52 base Assembly seat solves [S8]. ``Tol.'' is optimal to the configured tolerance; ``Limit'' is a time-limited incumbent.')
alloc=rows('code/data/ac_allocation_2011.csv')
T['ALLOCATION']=table(['District', '2011 population', '2008 ACs', 'Hamilton ACs', 'Change'],
    [[esc(r['district']),num(r['population']),r['seats_reference'],r['seats'],f"{int(r['seats'])-int(r['seats_reference']):+d}"] for r in alloc],
    'Full saved 36-current-district apportionment experiment [S4]. This is separate from the fixed-seat pilot redistricting.')
maps=rows('code/analysis/official_audit/map_comparison.csv')
T['MAPAPP']=table(['District/map', 'Ratio', 'MAD (\%)', 'Max dev. (\%)', 'Cuts', 'Score'],
    [[esc(r['district']+'/'+r['map']),num(r['size_ratio'],3),num(r['mean_abs_dev_pct'],2),num(r['max_abs_dev_pct'],2),r['taluka_excess_cuts'],num(r['common_score'],4)] for r in maps],
    'All 28 road-stage official/optimized map evaluations [S5]. Common score is comparable within a district under common scoring settings.',widths='p{5.4cm}rrrrr')
legacy=[]
for d,p in [('Sangli','code/sangli_road/booth_mip_road/map_summary.csv'),('Satara','code/pilots/satara/booth_mip_gurobi/map_summary.csv'),('Nashik','code/pilots/nashik/booth_mip_gurobi/map_summary.csv'),('Pune','code/pilots/pune/booth_mip_gurobi/map_summary.csv')]:
    for r in rows(p):
        legacy.append([esc(d+'/'+r['map']),num(r['max_km'],0),num(r['booths']),num(r['mean_voter_km'],4),num(float(r['objective'])/1e5,2)])
T['LEGACYAPP']=table(['District/map', 'Limit km', 'Booths', 'Inter-unit km', 'Cost (lakh Rs)'],legacy,
    'Saved road-based first-generation booth-MIP comparisons [S3]. Demand is 0.70 times Census population, capacity 1,500, and within-unit walking is zero.',widths='p{6cm}rrrr')

def barplot(metric_a,metric_b,ylabel,caption,scale=1,legends='Base,Comparison'):
    c1=' '.join(f'({i},{float(metric_a[d])/scale:.5f})' for i,d in enumerate(D,1))
    c2=' '.join(f'({i},{float(metric_b[d])/scale:.5f})' for i,d in enumerate(D,1))
    return r'''\begin{figure}[htbp]\centering
\begin{tikzpicture}\begin{axis}[width=.94\textwidth,height=6.1cm,ybar,bar width=12pt,
xtick={1,2,3,4},xticklabels={Sangli,Satara,Nashik,Pune},enlarge x limits=.16,
ymin=0,ylabel={'''+ylabel+r'''},legend style={at={(.5,1.04)},anchor=south,legend columns=2,font=\small,/tikz/column sep=8pt},
grid=major,grid style={gray!15},tick label style={font=\small}]
\addplot[fill=navy!80,draw=navy] coordinates {'''+c1+r'''};
\addplot[fill=teal!65,draw=teal] coordinates {'''+c2+r'''};
\legend{'''+legends+r'''}\end{axis}\end{tikzpicture}
\caption{'''+caption+r'''}\end{figure}'''

T['FIGCAP']=barplot({d:base[d]['stations'] for d in D},{d:lookup[d,'cap1500']['stations'] for d in D},'Polling stations','Base (1,200 cap) versus comparison (1,500 cap), with all other scenario settings held fixed [S9].',legends='{1,200 cap},{1,500 cap}')
T['FIGWALK']=barplot({d:base[d]['mean_walk_km'] for d in D},{d:lookup[d,'village_rule']['mean_walk_km'] for d in D},'Mean one-way walk (km)','Base joint location versus village-centre rule, evaluated on the same settlement demand [S9]. The village rule has no enforced 2 km bound.',legends='Joint location,Village rule')
T['FIGBAL']=barplot({d:next(r['official_mean_abs_dev_pct'] for r in dr if r['district']==d.title()) for d in D},{d:next(r['best_mean_abs_dev_pct'] for r in dr if r['district']==d.title()) for d in D},'Mean absolute population deviation (percent)','Official reconstructed ACs versus the selected district-score winner [S5]. Both are evaluated on Census 2011 population.',legends='Official reconstruction,Selected map')
T['FIGWAIT']=barplot({d:sims[d,'base','svc50']['voter_mean_wait_min'] for d in D},{d:sims[d,'base','svc50_nobalk']['voter_mean_wait_min'] for d in D},'Mean wait among completed voters (min)','Base simulation with balking versus the comparison without balking [S11]. The difference shows why wait and abandonment must be interpreted together.',legends='Balking enabled,No balking')

# Simplify only the displayed outlines; computations and source geometries stay
# unchanged. Coordinates are embedded into the standalone TeX source.
def rdp(points, eps=0.15):
    if len(points) < 3:
        return points
    a,b=points[0],points[-1]
    dx,dy=b[0]-a[0],b[1]-a[1]
    den=dx*dx+dy*dy
    def distance(p):
        t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den)) if den else 0
        return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)
    k=max(range(1,len(points)-1),key=lambda i:distance(points[i]))
    if distance(points[k])<=eps:
        return [a,b]
    return rdp(points[:k+1],eps)[:-1]+rdp(points[k:],eps)

def outlines(path):
    feats=obj(path)['features']
    out=[]
    for f in feats:
        geom=f['geometry']
        parts=geom['coordinates'] if geom['type']=='MultiPolygon' else [geom['coordinates']]
        out.append((f['properties'],parts))
    return out

mapdefs=[('Sangli','code/official_2008/constituencies.geojson','code/sangli_road/runs/taluka_s303/constituencies.geojson'),
         ('Satara','code/pilots/satara/official_2008/constituencies.geojson','code/pilots/satara/runs/taluka_s303/constituencies.geojson'),
         ('Nashik','code/pilots/nashik/official_2008/constituencies.geojson','code/pilots/nashik/runs/taluka_s714/constituencies.geojson'),
         ('Pune','code/pilots/pune/official_2008/constituencies.geojson','code/pilots/pune/runs/balanced_s714/constituencies.geojson')]
palette=['80B1D3','FDB462','B3DE69','FB8072','BEBADA','8DD3C7','FCCDE5','D9D9D9','BC80BD','CCEBC5','FFED6F','A6CEE3','F781BF','CAB2D6','B2DF8A','FF7F00','A6D854','E5C494','66C2A5','E78AC3','FFD92F']
map_pages=[]
for name,p1,p2 in mapdefs:
    left,right=outlines(p1),outlines(p2)
    pts=[p for _,polys in left for poly in polys for p in poly[0]]
    lat0=sum(p[1] for p in pts)/len(pts)
    lon0=min(p[0] for p in pts); latmin=min(p[1] for p in pts)
    def xy(p):return ((p[0]-lon0)*111.32*math.cos(math.radians(lat0)),(p[1]-latmin)*111.32)
    pages=[r'\begin{figure}[htbp]\centering']
    for title,features in [('Reconstructed official map',left),('Selected redesigned map',right)]:
        draw=[r'\begin{minipage}{.48\textwidth}\centering\small\textbf{'+title+r'}\par\medskip',r'\resizebox{\linewidth}{!}{\begin{tikzpicture}[x=1mm,y=1mm]']
        for index,(props,parts) in enumerate(features):
            draw.append(r'\definecolor{seatfill}{HTML}{'+palette[index%len(palette)]+'}')
            paths=[]
            for poly in parts:
                if len(poly[0])<4:continue
                converted=[xy(p) for p in poly[0]]
                ring=rdp(converted)
                if len(ring)<4:continue
                paths.append(' -- '.join(f'({x:.3f},{y:.3f})' for x,y in ring)+r' -- cycle')
            if paths:draw.append(r'\path[fill=seatfill,draw=white,line width=.2mm] '+' '.join(paths)+';')
        draw.extend([r'\end{tikzpicture}}',r'\end{minipage}'])
        pages.append('\n'.join(draw))
        if title.startswith('Reconstructed'):pages.append(r'\hfill')
    pages.append(r'\caption{'+name+r': official-unit reconstruction and selected road-stage ReCom map [S5]. Colors distinguish seats within each panel; they do not match seat identities across panels. Outlines are simplified for display only; north is up. Urban subdivisions and official-unit allocation remain approximate.}')
    pages.append(r'\end{figure}')
    map_pages.append('\n'.join(pages))
T['MAPFIGURES']='\n'.join(map_pages)

doc=r'''\documentclass[11pt,a4paper,oneside]{report}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{lmodern}
\usepackage[a4paper,margin=24mm,headheight=15pt]{geometry}
\usepackage{microtype,amsmath,amssymb,booktabs,longtable,array,graphicx}
\usepackage[dvipsnames]{xcolor}
\usepackage{tikz,pgfplots}
\pgfplotsset{compat=1.18}
\usetikzlibrary{arrows.meta,positioning}
\usepackage{fancyhdr,titlesec,enumitem}
\usepackage{url}
\usepackage[colorlinks=true,linkcolor=navy,citecolor=teal,urlcolor=teal,breaklinks=true]{hyperref}
\hypersetup{pdftitle={Election Logistics in Maharashtra: Technical Report},pdfauthor={IE 301 Group 4},pdfsubject={Project evolution, models, results and validation}}
\definecolor{navy}{HTML}{14334F}
\definecolor{teal}{HTML}{007F86}
\definecolor{paperblue}{HTML}{EEF4F7}
\titleformat{\chapter}[display]{\normalfont\huge\bfseries\color{navy}}{\large\chaptertitlename\ \thechapter}{8pt}{}
\titlespacing*{\chapter}{0pt}{-12pt}{22pt}
\titleformat{\section}{\Large\bfseries\color{navy}}{\thesection}{.7em}{}
\titleformat{\subsection}{\large\bfseries\color{teal}}{\thesubsection}{.7em}{}
\pagestyle{fancy}\fancyhf{}\fancyhead[L]{\small IE 301 | Election Logistics}\fancyhead[R]{\small Technical Report}\fancyfoot[C]{\thepage}
\renewcommand{\headrulewidth}{.3pt}
\setlength{\parindent}{0pt}\setlength{\parskip}{5pt}
\raggedbottom\clubpenalty=10000\widowpenalty=10000
\setlist{itemsep=3pt,topsep=4pt,leftmargin=*}
\setcounter{tocdepth}{2}\setcounter{secnumdepth}{2}
\setlength{\emergencystretch}{3em}
\newcommand{\source}[1]{\par\nopagebreak[4]{\footnotesize\color{teal}Evidence: #1.}\par}
\newcommand{\finding}[1]{\par\noindent\colorbox{paperblue}{\parbox{\dimexpr\linewidth-2\fboxsep}{\textbf{Interpretation.} #1}}\par}
\newcommand{\Rs}{\mathrm{Rs}}
\begin{document}
\begin{titlepage}
\color{navy}\rule{\textwidth}{2pt}
\vspace*{14mm}
{\Large IE 301 --- Operations Analysis\par}
\vspace{10mm}
{\Huge\bfseries Election Logistics\\[4pt]in Maharashtra\par}
\vspace{7mm}
{\LARGE Project evolution, mathematical models,\\computational results and validation\par}
\vspace{10mm}
{\large A technical reconstruction of the complete available project\par}
\vfill
{\large Group 4\par}
Pari Soni (24b4505)\quad Shivam Soni (24b4525)\\
Shreyaan Sharma (24b4529)\\
Asmaniwala Devanshu (26x0053)\quad S Ganesh (26x0052)\\[8pt]
Mentor: Prof. Ketan Savla\\[12pt]
Evidence snapshot: 27 October 2026\\
Repository revision: \texttt{a0dfaa0}, with saved scenario and report-layer results\\
Prepared from presentations, notebook outputs, source code and saved experiments
\vspace{7mm}\rule{\textwidth}{.6pt}
\end{titlepage}
\pagenumbering{roman}
\chapter*{Abstract}\addcontentsline{toc}{chapter}{Abstract}
This project began with the problem of dividing a geographic region into balanced, contiguous electoral constituencies while maintaining accessibility and administrative coherence. It developed into a layered operations-research system that links constituency design to polling-station location, dispatch-centre selection, vehicle routing, sector formation, polling-day queues, equipment failures and representation. The available evidence spans an initial conceptual and genetic-algorithm phase, a Sangli ReCom districting pilot, a capacitated booth-location model, road-network extensions, four district pilots, a historical electoral-roll audit, a settlement-level logistics chain, and statewide grouping of Assembly constituencies into Lok Sabha constituencies.

This report reconstructs that evolution from the actual workspace. The main operational experiments cover Sangli, Satara, Nashik and Pune: 52 Assembly constituencies and approximately 19.12 million registered electors. Thirty-six saved location/routing scenarios and 48 simulation variants are available. Under the base scenario, the model opens 20,196 polling stations at 14,532 premises. The selected redistricting plans improve Census-2011 population balance, but generally increase administrative fragmentation and sometimes worsen compactness. Operational costs change only modestly between official and selected redesigned Assembly maps. Reducing the assumed station capacity from 1,500 to 1,200 increases station requirements and generalized cost while reducing simulated queueing and abandonment. Modeling settlement-level walking materially changes the access conclusions compared with the early zero-within-village models.

The report also identifies the limits of these findings. Roll calibration is pooled across uncertain urban seats in the base case; the newer seat-specific calibration scenario is implemented but has no saved run. Routing allows stops to be dropped, with 82 such omissions in the base outputs. Some location solves terminate at their time limit. The statewide Lok Sabha plan is feasible on the model's graph, uses a 25-percent balance band, and has no global optimality certificate. Equipment-failure probabilities and costs are transferred or assumed parameters, not locally estimated quantities. Several detailed output files needed for exact replay are absent. Accordingly, the study supports comparative research conclusions, while operational deployment requires further geographical, behavioral and field validation.

\chapter*{How to read this report}\addcontentsline{toc}{chapter}{How to read this report}
\textbf{Scope.} ``Complete'' means all substantive stages supported by the supplied project directory, including superseded experiments and implemented but unexecuted extensions. The report does not claim to recover deleted conversations, unrecorded runs or unprovided team decisions.

\textbf{Evidence hierarchy.} Saved numerical outputs establish what a run reported; source code establishes what the current implementation does; notebooks and presentations establish earlier experiments; README files and the blueprint establish intent and historical interpretation. When these disagree, the distinction is stated rather than silently resolved. A program feature is not treated as an experimentally demonstrated result unless an output exists.

\textbf{Source references.} Identifiers [S1]--[S16] refer to the local source register at the end. They are local provenance references, not claims of independent external verification. External benchmark URLs are preserved from the repository, with their original source quality and year qualifications.

\textbf{Reproducibility.} The report is a standalone \LaTeX{} file: all tables and vector charts are embedded. Its companion builder, frozen evidence directory and SHA-256 manifest allow regeneration of this document without executing any optimization model. Model replay has separate requirements described in Chapter~\ref{ch:repro}.

\textbf{Snapshot caveat.} The evidence snapshot contains four repository commits, all dated 27 October 2026. Earlier evolution is reconstructed by artifact content, not assigned invented commit dates. The frozen source/result snapshot is at revision a0dfaa0; the working tree is otherwise clean apart from this report directory, as recorded in the manifest. The authors and mentor are transcribed from Presentation 3.

\tableofcontents\listoffigures\listoftables
\clearpage\pagenumbering{arabic}

\chapter{Project purpose and principal findings}
\section{The original problem and its expansion}
The starting question was how to divide a region into electoral constituencies while ensuring equitable representation and practical accessibility. Population, geography and demographics were primary design inputs; booth capacity, budget and travel time were operational constraints. Early presentations also framed gerrymandering as motivation: different boundaries can change electoral outcomes even for the same electorate. However, the saved final models do not optimize partisan outcomes or estimate party-specific seat advantage. They study representational balance, geometry, logistics and reservation.

The later blueprint poses two connected decision problems. A delimitation planner asks how unequal existing seats have become and what a fairer map costs in administrative coherence. A district election officer asks how many stations to open, where to place them, which dispatch centre to use, what vehicles and sectors are needed, and how capacity and reserve policies affect voter access and queueing. The final software addresses much of this chain, with important remaining gaps in realistic premises, return-night operations, resource accounting and validation.
\source{[S1], [S2], [S16]}

\section{Quantitative overview}
@@BASE@@
The four district elector counts sum to 19,123,489; premises and stations are different resources. A premises is a physical candidate location, and may hold multiple station-level polling parties and voting servers. Average load is therefore below the nominal 1,200 cap in many areas, especially where geographic coverage requires additional facilities.

Five findings persist across the evolution. First, population equality is achievable under the modeled contiguity constraints, but improved equality does not automatically improve shape or preserve taluka boundaries. Second, polling-site accessibility and capacity assumptions have much greater operational effects than the particular map among the saved finalists. Third, the early almost-zero walking distances were an aggregation artifact: most voters remained in their own unit, where the model assigned zero distance. Fourth, a tighter station cap reduces congestion at the cost of additional stations, staff and equipment. Fifth, a plausible district-total station count does not establish accurate seat-level geography, fleet requirements or queue predictions.

\section{What has actually been completed}
The project has working data-preparation, districting, scoring, mapping and booth-MIP scripts; four substantial district pilots plus a smaller Hingoli generalization test; a historical audit using all 288 official Assembly seats; separate walk/drive networks; raster-derived demand points; joint site/depot optimization; routing and sector models; Monte Carlo queue/failure simulation; Assembly reservation comparisons; and a statewide 48-PC grouping experiment. The showcase and final report-table dashboard provide browser-readable summaries, scenario comparisons and map comparisons.

These are linked sequential models. ``Joint'' in the current location layer means dispatch-centre selection and station placement are optimized together. It does not mean constituency boundaries, locations, routes and queues are solved in one global model. Districting uses an accessibility proxy; routes are optimized after locations; queue performance is evaluated after both. Feedback from the later stages into boundary search remains a proposed extension.
\source{[S2], [S7]--[S11], [S13]--[S15]}

\chapter{Evolution of the project}
\section{Phase 1: conceptual constituency design}
Presentation 1 introduces population equality, compactness, contiguity, administrative boundaries, public convenience and SC/ST reservation. Several slides retain placeholder text, so it establishes motivation rather than computational results. Presentation 2 surveys exact integer programming, local search/genetic methods, and graph/tree partitioning. It proposes synthetic-grid validation followed by a mid-sized state and discusses SHRUG geometry/population and Lok Dhaba voting data. The available final implementation uses SHRUG and electoral-roll extracts; no completed partisan analysis using Lok Dhaba is evidenced.
\source{[S1]}

\section{Phase 2: genetic-algorithm exploration}
Presentation 3 records an early GA experiment on 727 Census units. A candidate boundary assignment was evaluated together with booth placement. The presentation reports that the best score remained at 1.180 over generations 1--3, that crossover produced stringy or spread-out alternatives, and that repeatedly scoring booth placement was expensive. This motivates the switch to ReCom and the separation of districting from booth placement. The saved slides support that account, but the complete GA implementation, run configuration and optimization trace are not present as an independently replayable experiment.

The other approaches discussed---quadtree splitting, Kernighan--Lin/multilevel graph partitioning, and weighted k-means---should be described as explored methodological alternatives. The evidence does not establish a controlled numerical comparison among all of them. The method actually adopted in the saved districting code is ReCom with simulated annealing.
\source{[S1]}

\section{Phase 3: Sangli ReCom and greedy booth placement}
The Sangli pilot constructs 756 assignment units: 728 villages, six towns, 20 approximate city wards and two zero-population gaps, totaling 2,822,143 Census-2011 residents. It targets eight ACs, or 352,767.875 people per seat, with a 10-percent balance band. The presentation reports a 756-node adjacency graph with 1,988 edges.

Three score profiles produce ``Balanced'', ``Locality'' and ``Taluka'' maps. The original Euclidean outputs report maximum population deviations of 2.45, 4.61 and 1.24 percent, and common scores of 0.629, 0.687 and 0.602, respectively. The balanced and locality searches made 600 proposals each, accepting 314 and 378; taluka made 300 proposals, accepting 165. These are saved heuristic outcomes rather than global optima.

The notebook's downstream booth heuristic uses population directly with capacity 1,500. It reports 1,886, 1,886 and 1,885 booths; voter travel of 844,572.78, 876,203.99 and 945,532.06 person-km; and average travel of roughly 0.299, 0.310 and 0.335 km. Taluka wins on the common districting score, Balanced on voter travel; Locality is dominated in that two-objective comparison. The presentation's 27-percent break-even weight is specific to its normalization and original experiment. It is not a universal policy threshold or a result of the later road/hamlet models.
\source{[S1], [S2], [S3]}

\section{Phase 4: booth counts become optimization decisions}
The first booth MIP replaces fixed-count greedy placement by an integer facility-location model. It uses assumed electors equal to 0.70 times Census population, chooses booth multiplicities at populated-unit sites and imposes 2, 3 or 5 km assignment limits. Its initial Sangli results use straight-line distance multiplied by 1.33, explicitly labelled as fallback rather than road distance.

For Balanced, tightening the limit from 5 to 2 km raises booths from 1,433 to 1,649 and the objective from Rs~417.83 lakh to Rs~450.43 lakh. Mean modeled inter-unit distance falls from 0.1499 to 0.0145 km. These are valid results for that aggregation and assumed demand, but within-unit walking remains zero. Small distances therefore do not demonstrate that real voters walk only a few metres.
\source{[S3]}

\section{Phase 5: roads, generalization and official-map comparison}
An OSM shortest-path matrix replaces straight-line distance for districting medoids and booth assignments. The project runs six road-based districting variants per main pilot: three profiles at seeds 303 and 714. It reconstructs the official 2008 map on the same units so that official and generated maps can be scored comparably. It adds Pune, Nashik and Satara pilots, generic district wrappers, Hamilton apportionment, district crosswalks, and Lok Sabha grouping tools. Hingoli provides an additional small-district pipeline demonstration.

At 2 km, the road-stage Sangli booth finalists require 1,680, 1,681 and 1,679 booths. These differ from both the original population-based heuristic and the fallback MIP. Changing the distance metric can change which pairs are feasible, so it affects station requirements even before settlement-level demand is introduced.
\source{[S3]--[S5], [S14], [S15]}

\section{Phase 6: audit of population reconstruction and actual rolls}
The official-map audit identifies unreliable urban AC allocations when comparing reconstructed unit totals to SHRUG AC totals. Instead of taking implausible city population ratios literally, the project adds real registered-elector extracts for 2009, 2014, 2019 and 2024. This permits a roll-based assessment on all 288 seats without inferring those counts from reconstructed city geography.

This stage also changes the demand interpretation. A constant 0.70 times 2011 population cannot capture local growth and differences in enrollment. The final demand layer uses AC-specific 2024 scaling for trusted seats and a pooled district factor across untrusted seats. A subsequent code revision adds a fully AC-calibrated alternative, keeping the geographic uncertainty visible.
\source{[S5]--[S7]}

\section{Phase 7: settlement-level end-to-end logistics}
The current chain builds separate walk/drive networks, derives multiple demand points inside large or dispersed units using a nominal 100 m WorldPop raster, and accounts for within-hamlet walking. It optimizes stations and dispatch centre jointly, forms bus/jeep routes and officer sectors, then simulates voters, service, balking and equipment downtime. Thirty-six location/routing runs and 48 Monte Carlo variants are saved. Representation work adds reserved-seat comparisons and a statewide PC regrouping result.

The four visible Git commits record: the full modeling system at \texttt{7c2604a} (20:26 IST), a village-rule distance fallback and selective base re-solving at \texttt{ec0a947} (20:58 IST), and AC-specific calibration/validation work at \texttt{3645d96} (21:58 IST), all on 27 October 2026. The fourth commit, \texttt{a0dfaa0} (22:23 IST), saves scenario results, polling-day simulations, validation and report tables, including a safer turnout-weighting implementation and demand updates. Since individual output metadata lacks complete producing-code hashes, it is not possible to attribute every saved result uniquely to a producing commit.
\source{[S7]--[S16]}

\chapter{Data, spatial construction and demand}
\section{Input families and spatial units}
Census-2011 Primary Census Abstract totals are the population accounting baseline. SHRUG contributes village/town polygons, identifiers, fragment keys and PCA-derived attributes. DataMeet AC polygons support reconstruction of official constituency boundaries. WorldPop supplies gridded population weights. The OSM western-zone extract supplies transport topology. Saved electoral-roll data supplies registered electors and votes polled. Municipal ward material supports approximate Sangli urban subdivision.

The code distinguishes assignment units from demand points. Districting keeps an assignment unit whole. A large urban settlement may first be split into schematic wards or 1 km raster-based cells so districting is possible. Later, the demand layer splits each assignment unit again into hamlet-scale demand pieces. Thus the 1 km raster used in Pune/Nashik districting and the nominal 100 m raster used in logistics are two different spatial resolutions and purposes.

Sangli's 20 city wards use label locations and a Voronoi-style subdivision of the Census town polygon; they are research approximations. Pune uses 334 cells for Pune and 314 for Pimpri Chinchwad; Nashik uses 484 for Nashik and 47 for Malegaon. Satellite settlements are absorbed according to district configurations. Satara keeps its largest listed town whole. These choices preserve the district population total but do not provide official ward-boundary accuracy.
\source{[S4], [S7]}

\section{Population conservation and geographic gaps}
Preparation checks district totals against Census totals. Uncovered land may become zero-population gap units or be merged into neighboring units. Gap handling matters for topology, perimeter and apparent contiguity even when it does not affect total demand. Population conservation is necessary but insufficient: an internally exact district total can coexist with a badly allocated urban population across ACs.

For the main road-stage pilots, prepared totals are Sangli 2,822,143, Satara 3,003,741, Nashik 6,107,187 and Pune 9,429,408. Prepared assignment-unit counts are 756, 1,767, 2,475 and 2,555, respectively. Positive-demand unit counts are lower because zero-population units are retained for geometry. The generic input documentation identifies missing inhabited polygons in Buldhana and Beed as blockers for straightforward statewide district preparation; no claim is made here that all districts have been prepared.
\source{[S4]}

\section{Registered-elector calibration}
Let $p_u$ be unit $u$'s 2011 population, $a(u)$ its reconstructed official AC, $E_a^{2024}$ the saved 2024 electors, and $P_a=\sum_{u:a(u)=a}p_u$. For a trusted AC, the demand multiplier is
\begin{equation}
 f_a=\frac{E_a^{2024}}{P_a},\qquad e_u^{2024}=p_u f_{a(u)}.
\end{equation}
For the untrusted AC subset $U_d$ within district $d$, the base model instead uses
\begin{equation}
 f_{U_d}=\frac{\sum_{a\in U_d}E_a^{2024}}{\sum_{a\in U_d}P_a}.
\end{equation}
This preserves the combined total of the uncertain seats, while allowing individual AC demand totals to differ from the roll. ``Verified'' here is an internal reconstruction agreement flag, not certification by an election authority. Sangli pools two seats, Nashik four and Pune twelve; Satara pools none. A factor above one is possible because the numerator is 2024 electors and the denominator is 2011 population; it is not an estimate of the adult share of the same-year population.

The implemented alternative \texttt{electors\_2024\_ac} uses $E_a^{2024}/P_a$ for every AC. The \texttt{ac\_calibrated} scenario exposes it to the location pipeline. None of the four location directories contains a saved run for that scenario. Its definition is completed code; its comparative results remain unavailable.
\source{[S7], [S9]}
@@DEMAND@@

\section{WorldPop-derived hamlets}
The current demand implementation reads positive raster cells within each unit and retains cells with population at least 40 percent of that unit's highest cell population. It then redistributes the unit's electors over the retained cells. This density threshold counters diffuse gridded population over farmland, but it is a modeling choice that can remove lightly settled peripheral areas. Elector totals are conserved; the true spatial allocation is not independently validated.

Cells within 0.25 km form settlement patches (with the link threshold raised if necessary relative to raster cell size). Patches representing fewer than 40 electors are joined to the nearest sufficiently large patch. A patch above 2,400 electors is split by weighted k-means into approximately 1,200-elector pieces. K-means uses four initializations and a fixed random state. Each piece's candidate location is its population-weighted medoid among raster cells; when more than 400 cells are present, only the 400 heaviest cells are candidate medoids.

If unit $u$ has pieces $i$ with retained raster weights $w_i$, piece demand is $e_i=e_u w_i/\sum_{j\in u}w_j$. The within-piece walking allowance is
\begin{equation}
 h_i=1.25\left(\frac{\sum_{c\in i}w_c\|x_c-x_{m_i}\|}{\sum_{c\in i}w_c}+0.383\ell\right),
\end{equation}
where $m_i$ is the medoid and $\ell$ is the estimated raster-cell width in km. If no populated cells occur in a unit, the code substitutes one demand point and a polygon-area approximation. The saved four demand metadata files report zero such no-cell fallbacks.

\enlargethispage{3\baselineskip}
The saved files contain 2,650 Sangli, 3,372 Satara, 5,579 Nashik and 8,814 Pune demand points: 20,415 in total. The mean within-hamlet component alone is 0.352--0.516 km, making the early zero-within-unit assumption consequential. The model uses a piece-level mean, not each individual's address-to-booth path; a mean under 2 km does not guarantee every individual walk is under 2 km.
\source{[S7]}

\chapter{Constituency design and apportionment}
\section{District seat allocation}
The Hamilton/largest-remainder experiment assigns the fixed statewide total of 288 Assembly seats across 36 current districts in proportion to 2011 population. For district $d$, the quota is $q_d=288P_d/\sum_jP_j$. Floor quotas are allocated first, and remaining seats go in descending fractional-remainder order, breaking ties by larger population and then name. Districts below the minimum are fixed at that minimum and the rest are reapportioned.

The saved allocation moves 11 seats and changes 15 district allocations relative to the reference. Pune rises from 21 to 24; Thane from 18 to 21; Palghar from six to eight; Nashik from 15 to 16; Sangli falls from eight to seven. The pilot districting comparisons deliberately keep the existing counts of eight, eight, 15 and 21. Their results therefore isolate within-district boundary redesign and do not evaluate the full 2011 Hamilton allocation. The current-district crosswalk also differs from the 35 historical district groupings used in the statewide PC experiment.
\source{[S4]}

\section{Graph partitioning formulation}
Let $G=(V,A)$ be the assignment-unit adjacency graph. Each unit has population $p_i$, geometry $g_i$, taluka $t_i$ and demand point $r_i$. The decision is a partition $\mathcal{C}=\{C_1,\ldots,C_K\}$ of $V$. Every unit appears once; each seat's induced graph must be connected. With target $\bar P=\sum_i p_i/K$ and balance tolerance $\epsilon=0.10$,
\begin{equation}
 \lceil(1-\epsilon)\bar P\rceil\leq \sum_{i\in C_k}p_i\leq\lfloor(1+\epsilon)\bar P\rfloor.
\end{equation}
Units are adjacent when they share a positive-length land boundary. Optional links connect nearby islands or minor mapping gaps, with zero shared-border length recorded. The final seat must still pass a single-polygon validity check. Thus graph connectivity and geometric connectivity are both checked, rather than assumed interchangeable.

The neck rule tests graph bridges with less than 0.5 km shared border and rejects them if both sides hold at least 10 percent of the seat population. A geometric erosion check also rejects substantial separated lobes after shrinking the boundary by half the neck width. These are operationalized research constraints; they do not establish legal delimitation compliance.
\source{[S2]}

\section{Objective and normalization}
The score is a weighted sum
\begin{equation}\label{eq:score}
 J=w_P L_P+w_D L_D+w_T L_T+w_C L_C,
\end{equation}
with
\begin{align}
 L_P&=\frac{1}{K\epsilon}\sum_k\left|P_k/\bar P-1\right|,\\
 L_D&=\frac{1}{20}\frac{\sum_k\sum_{i\in C_k}p_i d(i,m_k)}{\sum_i p_i},\\
 L_C&=\frac1K\sum_k\left(1-\frac{4\pi A_k}{L_k^2}\right),\\
 L_T&=\frac18\sum_t\max\left(0,F_t-\left\lceil P_t/P_{\max}\right\rceil\right).
\end{align}
Here $m_k$ is the population-weighted discrete 1-median in seat $k$, $F_t$ is the number of connected taluka fragments across seats, $A_k$ and $L_k$ are seat area and perimeter, and $P_{\max}$ is the allowed seat maximum. The taluka penalty subtracts a lower bound on how many seats a taluka requires; it is more nuanced than counting every crossing as undesirable. Its reference value of eight and the 20 km distance reference are fixed scoring conventions, so absolute score values depend on their choice.

Balanced uses weights $(0.35,0.30,0.20,0.15)$; Locality $(0.30,0.45,0.10,0.15)$; Taluka $(0.30,0.20,0.35,0.15)$, ordered as population, distance, taluka and shape. A map's own weighted score is not comparable to another profile's own score. Common-baseline rescoring is used for cross-profile selection. The early EVM-distance estimate based on population divided by a per-machine scaling constant is a proxy; it is not the routed four-leg vehicle distance computed later.
\source{[S2], [S5]}

\section{ReCom and simulated annealing}
An initial legal partition is constructed using graph-tree splitting. A proposal selects adjacent seats, merges their units, constructs a random-edge-weight minimum spanning tree, and cuts an edge to form two legal seats. Random-weight spanning trees are not uniform spanning-tree samples. Consequently these runs are optimization searches and should not be presented as an unbiased ensemble of possible maps.

For a valid candidate with score change $\Delta J$, the implementation accepts improvement or accepts a worsening move with probability $\exp(-\Delta J/T)$. Temperature decreases geometrically from 0.05 to 0.01 over the search steps. The best state across the search and restarts is retained. Connectivity is structural in each tree split, while population and neck checks filter proposals. Cached region metrics reduce repeated geometry and medoid evaluation.

Two seeds per weight profile improve exploration, but six runs per district do not certify optimality or characterize all trade-offs. The selected ``best map'' means best common score among the saved candidates, not best possible legal map.
\source{[S2], [S5]}

\chapter{Official-map audit and districting results}
\section{Reconstruction of the official 2008 plan}
Whole villages and towns are assigned using SHRUG fragment keys, taking the largest matching weight among a district's official ACs. Urban cells, approximate wards, gap units and missing-key settlements use largest geometric overlap with DataMeet polygons, with nearest-polygon fallback if required. These operations create comparable assignment-unit maps, but introduce approximation near boundaries and in split towns.

The official map is scored without imposing the optimized plan's current population band or neck constraints: otherwise an existing map drawn for a different Census base could not be evaluated. The mapping report separately records which conditions fail. Nashik has five official seats noncontiguous on the reconstructed units. This is a warning about this representation and mapping pipeline; it is not sufficient evidence that the actual official constituencies are geographically disconnected.
\source{[S5]}

\section{Census-population balance and its trade-offs}
@@DISTRICT@@
@@FIGBAL@@
The saved selected maps reduce mean absolute population deviation substantially. Pune drops from 21.45 to 1.88 percent, while Sangli drops from 6.96 to 0.72 percent. Every saved optimized map is within the 10-percent population band. The official reconstructed population ratio reaches 3.021 in Pune, compared with 1.11--1.18 among its six generated runs. These statements concern the reconstructed Census-2011 populations; they do not establish equally balanced 2024 registered-elector counts.
@@TRADEOFF@@
In Sangli, Pune and Nashik, the selected map's Polsby--Popper compactness is lower than the official reconstructed map's; Satara improves slightly. The selected road distance to a constituency centre also rises slightly in all four district summaries. Improvements in common score can therefore be driven mainly by the population term despite deterioration elsewhere. The score combines policy preferences, rather than proving that one map dominates another on every criterion.

The number of fragmented talukas generally increases. For example, Pune's official three split talukas become 12--14 across the generated alternatives; its excess-cut metric rises from 18 to 29--38. This identifies an important substantive result: stronger population equality has an administrative-coherence cost in these saved solutions.
\source{[S5]}

\section{Historical roll-based drift}
@@ELECTORS@@
On the saved registered-elector series, the count outside 10 percent of the district mean grows from 73 seats in 2009 to 101 in 2024, while seats outside 20 percent grow from seven to 33. The statewide largest/smallest ratio grows from 2.10 to 3.23. In 2024, Chinchwad has 663,788 registered electors and Wadala 205,429 in the extracted dataset. These are differences in representation per registered elector; population-based and elector-based notions of equality must remain distinct.

The source is Wikipedia election-box extraction, not an ECI-certified dataset. It covers 288 seats, which is a stronger statewide comparison base than the SHRUG-only Census reconstruction, where the audit covers 285 ACs and city allocations are explicitly unreliable. The rural-only Census subset contains 176 seats and shows district-relative MAD rising from 8.70 percent on 2001 data to 10.17 percent on 2011 data. Implausibly large city ratios in the unrestricted Census table should not be quoted as literal real-world inequality.
\source{[S5], [S6]}

\chapter{Travel networks and distance conventions}
\section{First road-distance matrix}
The first road extension builds one symmetric drivable graph per district region from OSM, keeps the largest component, snaps populated-unit demand points to road nodes and adds off-network snap distances at both ends. The region is buffered and convex-hulled to allow trips just outside the district boundary. Dijkstra returns unit-to-unit road distances in km; the matrix includes the point coordinates to prevent accidental row mismatch. It feeds road-stage ReCom medoids and the first-generation booth MIP.

This replaces an approximate geometric detour factor with actual mapped topology, but treats roads as two-way and does not infer congestion or vehicle restrictions comprehensively. It also remains a unit-centroid model with no within-unit travel. These limits motivate the second network/demand generation.
\source{[S3], [S7]}

\section{Separate walking and driving graphs}
The final network layer uses a 0.03-degree district buffer, a shared OSM node inventory and separate edge sets. The walk graph accepts roads, footpaths, tracks and steps subject to access filters. The drive graph uses selected road classes and filters private/no-access ways. Edge lengths use great-circle geometry. Walking speed is 4.5 km/h; vehicle speeds range from 60 km/h on motorways to 10 km/h on living streets, with a factor of 0.6 on edges whose midpoint falls in a town or urban unit.
@@NETWORK@@
Both graphs are symmetric. The implementation therefore does not enforce one-way restrictions or directional congestion. Points are snapped to each graph's largest connected component; snap distance adds an off-network component, with a nominal 15 km/h connection speed for vehicles. The drive-time and drive-distance graphs are optimized separately. Their shortest paths can differ, so the time and km matrices do not necessarily describe the same route. That matters when an optimizer enforces a time constraint using one matrix but prices km using the other.

Walking matrices are truncated beyond 6 km for storage and computation. The village-rule benchmark substitutes straight-line distance times 1.3 plus within-hamlet distance when its requested pair is outside this range. That keeps the benchmark finite but mixes network and fallback distances. For logistics routing, unavailable drive paths are replaced by 600 minutes and 300 km before route search, a numerical penalty rather than a verified travel route.
\source{[S7], [S8], [S10]}

\section{Four distinct distance outputs}
The study reports (i) population-weighted distance to an AC centre during districting; (ii) elector-weighted walking distance to a polling site; (iii) one-way station-weighted depot distance in the location model; and (iv) vehicle-km from shared routes over two loops. These quantities answer different questions and have different units. The location summary's \texttt{star\_trip\_km} is a sum of one-way distances weighted by station count. Routing explicitly multiplies it by four for a solo-party four-leg benchmark. Comparing that column directly with vehicle-km would introduce a factor-of-four error.

Likewise, the scenario column \texttt{p90\_walk\_km\_seat\_avg} is an elector-weighted average of seat p90 values, not a district-wide 90th percentile. The scenario response statistic is the median of seat p90 response times, not a pooled 90th percentile over all premises. The report retains these definitions rather than relabelling them as district percentiles.
\source{[S8]--[S10]}

\chapter{Polling-station and dispatch-centre location}
\section{First-generation capacitated booth MIP}
For a fixed constituency, demand units $i$ have elector demand $e_i$, populated units $s$ are candidate sites, and a discrete 1-median is a fixed depot. Integer $y_s$ counts booths and continuous $x_{is}$ assigns electors. The objective prices booth count, voter distance and one-way depot distance times booth count, subject to complete demand assignment and $\sum_i x_{is}\leq K y_s$. Assignment pairs beyond the distance limit are removed; self-assignment is always allowed at zero distance.

The model improves on the notebook because booth count is endogenous. CBC/PuLP is used initially, with a Gurobi option in later pilot comparisons. Initial fallback Sangli results report zero MIP gaps; later Gurobi results can be labelled optimal within a small configured gap. The old results are preserved in Appendix~\ref{app:legacy}; they must not be pooled with the modern base results because demand, capacity, sites, walking and depot decisions all differ.
\source{[S3]}

\section{Current joint location model}
Each AC is solved independently. Demand pieces $i$ are also candidate premises $s$; candidate depots $d$ are selected from town main points and large hamlets, with a medoid added. At most roughly 14 initial depot candidates plus the added medoid are considered, so ``optimal depot'' means optimal within this restricted candidate set. Candidate premises are raster-derived points, not a verified inventory of schools, public buildings or existing polling premises.

Let $y_s\in\{0,\ldots,B\}$ count stations, $o_s\in\{0,1\}$ open a premises, $z_d\in\{0,1\}$ choose a depot, $x_{is}\geq0$ route elector demand, and $u_{ds}\geq0$ represent depot-to-site station flow. The implemented objective is
\begin{equation}\label{eq:location}
 \min C_{PS}\sum_s y_s+C_{site}\sum_s o_s+
 c_w\sum_{(i,s)\in A_w}w_{is}x_{is}+c_t\sum_{d,s}k_{ds}u_{ds}.
\end{equation}
The constraints are
\begin{align}
 \sum_{s:(i,s)\in A_w}x_{is}&=e_i &&\forall i,\\
 \sum_{i:(i,s)\in A_w}x_{is}&\leq K y_s &&\forall s,\\
 y_s&\leq B o_s &&\forall s,\\
 \sum_d z_d&=1,\\
 \sum_d u_{ds}&=y_s &&\forall s,\\
 u_{ds}&\leq Bz_d &&\forall d,s,\\
 x_{is}&\leq e_i o_s &&\forall(i,s)\in A_w,\\
 \sum_s y_s&\geq\left\lceil\frac{\sum_i e_i}{K}\right\rceil.
\end{align}
Here $B=12$, $K=1,200$ in base, $C_{PS}=25,000$ Rs/station, $C_{site}=5,000$ Rs/premises, $c_w=10$ Rs per elector per one-way walking km, and $c_t=120$ Rs per station per one-way depot km. The last term approximates four solo-party legs at Rs~30/km. These monetary coefficients are assumed valuations, not expenditure estimates fitted to invoices.

Walking cost uses $w_{is}=h_i+d^{walk}_{is}$, and the admissible set contains pairs within the planning limit plus every self-pair. This self exception guarantees a candidate even if $h_i$ itself exceeds 2 km; it explains the nonzero base share above the nominal bound. Flows are continuous, so one demand piece may split among premises, and station loads at a premises are later divided equally for simulation. The solution is not a voter-by-voter registration allocation.
\source{[S8]}

\section{Alternative strategies and computational control}
The joint strategy chooses the depot; the HQ strategy fixes it to the main point of the seat's largest urban unit if available, with a largest-point fallback; the median strategy fixes it to the cached elector-weighted walking medoid. ``HQ'' is therefore a proxy for common practice, not a verified taluka headquarters. The median strategy does not use a separately solved driving-time 1-median.

The village rule puts $\lceil e_u/K\rceil$ stations at each original unit's largest hamlet and sends all its hamlets there. It is not constrained by the optimized model's walk bound or 12-station-per-premises cap. It is a heuristic comparator, not evidence of actual ECI site choices. The no-intra scenario plans with network distance alone, then evaluates resulting assignments with the true $h_i+d^{walk}_{is}$ measure.

The scenario wrapper requests a 120-second time limit per seat, four solver threads and relative MIP gap 0.005. A feasible incumbent is retained on timeout. The code's label ``optimal'' also applies when the solver reaches its configured tolerance. It does not mean a zero-gap exact optimum.
@@SOLVER@@
Across the base location solves, 41 of 52 meet the configured optimality criterion and 11 hit the time limit. The worst saved gap is about 2.132 percent in Sangli and 0.771 percent in Pune. Small depot-strategy differences may be within optimization uncertainty, especially when the objective differences are smaller than reported gaps.
\source{[S8], [S9]}

\chapter{Dispatch routing, sectors and resource cost}
\section{Vehicle routing formulation}
The location layout determines the depot and sites. A site with more than six polling parties is split into multiple stop records, each with at most six. There are two vehicle types: a bus carrying six parties, with two-day hire Rs~9,000 and Rs~35/km; and a jeep carrying one party, with hire Rs~3,500 and Rs~14/km. Each party represents four staff plus police/kit in the motivating capacity assumption, rather than a calibrated passenger-capacity model.

A heterogeneous capacitated vehicle-routing model minimizes fixed hire plus twice the loop-distance running cost. Every used vehicle starts and ends at the depot. Load is bounded by vehicle type. The time callback adds network drive time and destination service time $10+3q_s$ minutes for a stop with $q_s$ parties. Vehicles start at a relative time of zero, interpreted as 13:00 on dispatch day; the 300-minute horizon bounds the full loop, including return to the depot, rather than merely arrival at the last site.

OR-Tools uses parallel cheapest insertion and guided local search with a 30-second routing budget per seat. The candidate fleet contains $\lceil\text{parties}/6\rceil+2$ buses and at most 40 jeeps per seat. That jeep bound limits the search space, so the solution is not an unrestricted optimal fleet mix. Each stop is optional with a large penalty of Rs~10 million. Contrary to an inline comment suggesting late jeep service, the implemented model can drop a stop; it does not add a separate guaranteed late-service route.
\source{[S10]}

\section{Collection and reported kilometers}
The same loops are assumed to run again in reverse for post-poll collection; saved vehicle-km are twice the dispatch-loop sum. There is no separate collection-night optimization with actual station closing times, sealing delays, receipt queues, a strong-room deadline, or minimization of the latest return. The blueprint's return-night problem is therefore only approximated, not fully implemented.

The reported latest arrival subtracts the final depot-return leg from the route end time. Because destination service is included in the time callback, it effectively includes handling at the last stop. It is not a direct observation of arrival at a real station. Missing stops are absent from this latest value, so a pre-18:00 maximum does not prove universal coverage.
@@ROUTES@@
The base outputs omit zero Sangli stops, 34 Satara stops, seven Nashik stops and 41 Pune stops. Their total is 82 split stop records; it is not necessarily 82 premises or 82 stations. Saved aggregate cost excludes a real operational completion plan for these stops. Appendices include omissions for every scenario so that apparently cheaper routes cannot be interpreted without a coverage qualification.
\source{[S10]}

\section{Sector formation}
For $n$ premises, the sector model opens $k=\lceil n/11\rceil$ bases, permits at most 12 premises per sector and minimizes the sum of base-to-premises driving times. Binary assignments choose an open base for every premises. Candidate pairs are screened using a time-reach threshold, with self assignment always allowed. Small instances with at most 12 premises use a single medoid directly.

The capacity is in premises, not station count. A premises with several stations still occupies one sector location, while simulation reserves are counted in station sets. If the MIP has no incumbent, the fallback picks $k$ centrally ranked bases and assigns premises to their nearest base; that fallback does not enforce the 12-premises cap. Saved sector files and summary outputs do not include a full solver-status certificate for distinguishing fallback use. Consequently sector capacities require independent checking before deployment.
\source{[S10]}

\section{Generalized cost and resource accounting}
The location objective is replaced after routing by
\begin{equation}
 C^{routed}=25,000N_{PS}+5,000N_{site}+10\sum_{is}w_{is}x_{is}+C^{vehicle}.
\end{equation}
It is incorrect to add the route transport cost on top of the full location objective: that would count both star transport and routed transport. The scenario collector correctly replaces the star term. The resulting Rs/elector is a generalized cost, since voter walking time is monetized alongside station and vehicle expenditures. It is not an audited government budget or cash cost per actual voter.

Staff and equipment are partly bundled into Rs~25,000/station. The blueprint proposes four staff per station plus 20-percent reserve, separate BU/CU/VVPAT reserves, and a full bill of materials. There is no distinct saved L5 bill-of-materials output. As an illustrative derived calculation, 20,196 base stations imply 80,784 direct polling-party staff and approximately 96,941 staff after multiplying by 1.2 and rounding up at the combined level. That is a formula-based implication, not a separately validated staffing run, and does not include all election personnel or administrative roles.
\source{[S8]--[S10], [S16]}

\chapter{Polling-day simulation}
\section{Demand, arrivals and service}
At each physical premises, the assigned load is divided equally among its station count. Each station's turnout parameter is an elector-weighted mean of the 2024 turnout of source official ACs. For replication $r$, potential voters are sampled as
\begin{equation}N_{sr}\sim\operatorname{Binomial}(\operatorname{round}(e_s),\tau_s).
\end{equation}
Conditional on this count, arrival times are independently sampled using an interpolated inverse CDF over 07:00--18:00. This is binomial-count sampling with an imposed within-day shape; the blueprint's nonhomogeneous Poisson wording is not the exact implemented generative process.

The cumulative turnout inputs at 09:00, 11:00, 13:00, 15:00, 17:00 and 18:00 are 6.61, 18.14, 32.18, 45.53, 58.22 and 66.05 percent of registered electors, normalized by 66.05 to obtain the day's arrival CDF. The 09:00 point is an additional code assumption/early-curve value, not listed as a separately sourced benchmark in the saved benchmark CSV. Identical curve shape is used across districts; AC-specific total turnout changes the count. The proposed urban/rural time-shape shifts are not implemented.

Service is FIFO with one server per station. Times are lognormal with mean 40, 50 or 60 seconds and coefficient of variation 0.45. In minutes, if $S\sim\operatorname{Lognormal}(\mu,\sigma^2)$,
\begin{equation}\sigma^2=\log(1+0.45^2),\qquad\mu=\log(\overline S/60)-\sigma^2/2.
\end{equation}
At 50 seconds, nominal mean capacity over 660 minutes is 792 voters before failures; at turnout 0.6605, a full 1,200-elector station expects 792.6 potential voters. The corresponding 1,500-elector station expects 990.75. This simple load calculation explains why the higher cap can create substantial queues even when the registered-elector capacity rule is satisfied.
\source{[S11]}

\section{Queue recursion, closing and balking}
For arrival $A_k$, previous completion $F_{k-1}$ and service $S_k$, tentative service starts at $B_k=\max(A_k,F_{k-1})$. If the service interval overlaps equipment downtime $[f_0,f_1)$, the model postpones the start to at least $f_1$. An overlapping service is effectively restarted after downtime rather than paused and resumed. Waiting time is $W_k=B_k-A_k$. Voters remaining in the queue at 18:00 continue to be served, implementing a token-style closing rule without generating new post-close arrivals.

With balking enabled, an arrival leaves before joining/being served with probability
\begin{equation}
 p_b(W)=1-\exp\left[-\frac{\max(0,W-20)}{60}\right],
\end{equation}
with minutes as the unit. The implementation evaluates the projected waiting time, including downtime, so it assumes the voter can respond to an estimate of future queue delay. It does not simulate reneging after a voter joins the queue, return visits, political mobilization or rescheduling. The tolerance and scale are assumed behavior parameters, not fitted local observations.

Wait metrics are calculated among voters who complete voting. As more long-wait voters leave, mean wait among those remaining can fall even while access worsens. Abandonment must therefore accompany every queue comparison. Total turnout is sampled before balking; it is not recalibrated to force the final completed vote total to equal the observed total after abandonment.
\source{[S11]}

\section{Equipment failures and sector reserves}
The model uses component replacement probabilities $p_{BU}=0.016$, $p_{CU}=0.033$, $p_{VVPAT}=0.055$ from a saved Assam 2024 phase-one benchmark. Assuming independence,
\begin{equation}
 p_{fail}=1-(1-0.016)(1-0.033)(1-0.055)=0.10080604.
\end{equation}
Each station has at most one failure per replication, at a uniform time during polling. The benchmark rates refer to reported replacements, not locally estimated Maharashtra failure hazards, and may combine reporting or operational circumstances that the model does not distinguish. Applying their union as a Maharashtra polling-day interruption probability is a sensitivity assumption.

Within each sector, failures are sorted chronologically. The officer handles replacements sequentially, adding travel to the premises plus 20 minutes setup and return-to-base travel before the next job. The sector initially has $\lceil r n_{sector}\rceil$ reserve sets, with $r$ equal to 0.10, 0.20 or 0.30. If the reserve is exhausted, the model uses a depot travel-time estimate based on 30 km/h plus setup. It does not model dispatch-centre vehicle/technician contention, replenishment trips or separate component inventories. The reserve variable counts complete sets, despite the blueprint discussing different BU/CU and VVPAT reserve shares.
\source{[S11], [S12]}

\section{Replication and output definitions}
The stored sensitivity suite uses 30 replications and seed one as the CLI default. Seven base variants cover no failures, reserve 10/30 percent, service 40/50/60 seconds, and 50-second service without balking. Two cap-1,500 variants use 50 and 60 seconds. The saved outputs also contain 40-second no-balking runs on both layouts and 40-second balking runs on cap1500, extending the current wrapper's default sweep. There are therefore 12 variants per district and 48 saved runs in total.

``Station p95 wait mean'' averages station-level 95th percentiles; it is not the global 95th-percentile voter wait. The p95 overrun is a percentile over station-replication outcomes, not the 95th percentile of district-wide last closure. The share of stations with votes after 18:00 can be large even if the share of votes completed after 18:00 is modest. Cumulative-vote output is normalized by all completed votes over the run. It is not a direct estimate of reported turnout as a fraction of registered electors.

No confidence intervals, independent holdout calibration or replication-level uncertainty summaries are saved in the summary CSV. Point estimates support scenario exploration, but the precision implied by two decimal places should not be confused with empirical accuracy. Reusing the same seed provides partial common random-number alignment; changes in station counts or random draws can still break matched comparisons.
\source{[S11]}

\chapter{Integrated scenario results}
\section{The scenario design}
Nine location scenarios have complete saved district summaries: base, cap1500, walk3, depot\_hq, depot\_median, village\_rule, no\_intra, census2011 and best\_map. The tenth current scenario, ac\_calibrated, has no saved location outputs. Queue variants are available only for base and cap1500. Blank simulation cells in other scenario rows mean not executed or not present, rather than zero queue or zero failure risk.

The experiments change one configured assumption at a time relative to base, except that best\_map imports a previously optimized boundary plan. They are comparative scenarios, not randomized field interventions. Solver gaps, heuristic routing, omitted stops and inherited spatial assumptions also affect differences.
\source{[S9]}

\section{Station capacity: 1,200 versus 1,500}
@@CAP@@
@@FIGCAP@@
Across four districts, the 1,200-cap base opens 20,196 stations versus 17,242 at cap 1,500, an increase of 2,954 or about 17.1 percent relative to the 1,500 scenario. The base generalized routed cost sums to approximately Rs~7,258.76 lakh, versus Rs~6,481.16 lakh at cap 1,500; the difference is about Rs~777.60 lakh (Rs~7.78 crore). These are model-valued totals under the assumed coefficients and saved incomplete routes.

The queue effect is substantial in all districts. Pune's completed-voter mean wait falls from 18.45 to 8.45 minutes and the share waiting over 30 minutes from 27.21 to 6.00 percent. In Sangli, the corresponding changes are 22.26 to 14.26 minutes and 40.14 to 16.93 percent. At the same time, modeled abandonment falls, so the lower cap improves both simulated retention and the waiting distribution under this behavioral model.

This is a policy sensitivity, not a causal estimate of a nationwide rule change. The cap itself is a project scenario parameter, described in the repository as a newer 1,200-elector norm; its present legal/administrative applicability is not independently established by this workspace reconstruction. Any deployment must use the applicable election's approved instructions and station list.
\source{[S8], [S9], [S11]}

\section{Walking limit and intra-hamlet access}
Relaxing the planning limit to 3 km reduces station count to 2,447 in Sangli, 2,825 in Satara, 5,052 in Nashik and 8,229 in Pune. Their shares above 2 km rise to 3.83, 7.17, 3.87 and 2.68 percent, compared with 0.46, 0.43, 0.35 and 0.36 percent in base. Satara experiences the strongest access trade-off among these four pilots.

The no-intra runs plan without within-hamlet distance but are evaluated with it included. They open 2,573, 3,056, 5,303 and 8,478 stations, respectively, and the shares over 2 km become 1.93, 3.84, 2.08 and 1.55 percent. This isolates the planning error from treating internal travel as zero. Since site choices can change, the final evaluated mean walk is not obtained simply by adding a constant to an old mean.
@@FIGWALK@@
The village rule uses fewer premises and concentrates many stations at the same settlement main point. Its mean walks are 1.478 km in Sangli, 1.241 in Satara, 1.153 in Nashik and 1.274 in Pune, versus 0.535, 0.624, 0.586 and 0.500 in base. The over-2-km shares reach 21.61, 15.70, 11.40 and 15.32 percent. The rule's generalized costs are higher despite comparable or smaller station counts, because of the monetized walking burden. That result relies on the walking valuation and the raster-based hamlet representation.
\source{[S8], [S9]}

\section{Depot selection}
Fixing the HQ proxy rather than choosing a depot jointly increases saved generalized routed cost by approximately Rs~4.61 lakh in Sangli, 2.06 lakh in Satara, 6.38 lakh in Nashik and 8.59 lakh in Pune. These changes are small relative to each district's total cost. The HQ proxy increases vehicle-km strongly in some districts: Pune rises from 116,577 to 138,319 and Sangli from 45,587 to 59,071.

The median-depot cost differences are smaller still. Nevertheless, the location objective is optimized using a star transport approximation, then evaluated with independently optimized routes. The joint location solution need not minimize final routed cost. Furthermore, some differences are comparable to location gaps, and routes have differing omission patterns. The evidence supports depot sensitivity, not an exact quantified guarantee of joint optimization savings.
\source{[S8]--[S10]}

\section{Planning with old population demand}
The census2011 scenario uses 0.70 times Census population, giving approximately 14.95 million electors over the pilots rather than 19.12 million in the 2024-derived base. It opens 16,932 stations, compared with 20,196 in base. Pune alone rises from 6,911 stations under old-demand planning to 8,712 in base. Ignoring enrollment growth can therefore dominate the effect of map choice.

This comparison changes both the magnitude and spatial allocation of demand, so it does not isolate a pure demographic growth effect. Also, per-elector cost can rise when total demand falls, because sparse geographic coverage still requires a minimum network of premises. Satara's saved Rs/elector is 50.17 under census2011 versus 44.95 in base.
\source{[S7], [S9]}

\section{Operational cost of the selected redesigned map}
Best-map station totals differ from base by +1 in Sangli, +14 in Satara, +2 in Nashik and +1 in Pune. Generalized routed cost changes by roughly +0.076, +0.599, +0.110 and -0.022 percent, respectively. These small differences reinforce the earlier finding that boundary choice among the finalists has modest logistics effects compared with demand resolution, capacity and access limits.

The near equality of costs is a finding about these pilots and selected maps, not a theorem about districting. Cross-seat assignment restrictions, depot candidates, genuine polling-premises availability and administrative/security rules could make other maps more operationally consequential. ``Best map'' is selected by the districting common score on Census population, not optimized for this routed cost or the current 2024 demand.
\source{[S5], [S9]}

\chapter{Queueing, failure and behavioral results}
@@SIM@@
\section{Capacity and service-time sensitivity}
@@SERVICE@@
At 40-second mean service, base completed-voter waits are about 2--5 minutes, with low modeled abandonment. At 60 seconds, waits and abandonment rise substantially. Sangli's wait reaches 22.56 minutes and abandonment 15.52 percent; Nashik's values are 19.53 minutes and 12.47 percent. Faster service is therefore a powerful modeled lever, but the project has not demonstrated that training, additional staff or equipment changes can deliver a given reduction in actual mean service time.

A station cap is expressed in registered electors, while queue load depends on turnout, service variability, time-of-day arrivals and failures. A single capacity threshold cannot ensure a uniform waiting-time outcome. Targeted additional stations in high-turnout or slow-service areas may be more efficient than a uniform cap, but that policy is not optimized in the saved code.
\source{[S11]}

\section{The effect of allowing abandonment}
@@NOBALK@@
@@FIGWAIT@@
Without balking, Sangli's mean wait increases to 30.61 minutes and p95 station overrun to 173.5 minutes. Satara's corresponding values are 25.80 and 160.4, Nashik's 22.49 and 149.8, and Pune's 14.52 and 116.9 minutes. These longer queues are not failures of demand conservation: they expose the demand that the balking model suppresses.

The base ``queue at 18:00'' measure is high, ranging from about 80 to 90 percent of station-replication observations. Yet only about 2.91--4.25 percent of completed votes occur after closing. A small number of final voters at many stations can produce that pattern. Neither statistic alone identifies a district's actual latest closure or the distribution of real election-day queue experiences.
\source{[S11]}

\section{Additional 40-second capacity valuation}
@@CAPVALUE@@
The final report-layer module, \texttt{logistics/report.py}, also packages scenarios, queues, turnout curves, reserve comparisons, validation, PC results and reservation into eight tidy CSVs and a local dashboard. Its capacity-valuation table uses 40-second mean service without balking on both layouts, multiplying the mean-wait difference by saved 2024 votes polled. It estimates approximately 3.22 million voter-hours saved by the lower cap across the pilots. Its cost-per-hour values range from Rs~14.8 in Sangli to Rs~38.5 in Pune, against an assumed time value of Rs~50/hour.

This is an accounting comparison derived from the simulation, not a separately optimized policy or measured welfare effect. It combines modeled waits with observed vote totals, uses the generalized location/routing cost increment, and inherits route omissions and demand assumptions. Since the numerator already includes walking valuation, it is not purely the fiscal cost of saving a queueing hour. These qualifications are essential before describing the lower cap as economically worthwhile.
\source{[S9], [S11], [S15]}

\section{Failures and reserve shares}
Disabling failures at 50-second service reduces mean wait only modestly: 14.26 to 14.02 minutes in Sangli, 11.83 to 11.58 in Satara, 11.36 to 11.10 in Nashik and 8.45 to 8.19 in Pune. This indicates that station load and service assumptions dominate aggregate mean wait in these scenarios. It does not imply that failures are unimportant at the affected station.

Increasing reserve from 10 to 30 percent shortens conditional downtime at failed stations: Sangli falls from 32.1 to 27.8 minutes, Satara from 35.4 to 30.7 and Nashik from 32.6 to 28.6. Mean waits change very little at district level. Reserve provision may still be valuable for resilience and tail risk, but there is no saved inventory-cost optimization or explicit voter-hours-lost-to-failures output. A defensible economic reserve recommendation requires the cost of spares, component-specific stocks and a validated downtime process.
\source{[S11]}

\chapter{Reservation and statewide Lok Sabha grouping}
\section{SC/ST attributes and allocation heuristic}
Village and town SC/ST counts are derived from SHRUG PCA shares using settlement identifiers. Urban wards and grid cells inherit their town's shares, so within-city demographic segregation is not represented. Missing matched shares are filled with zero in the implementation, which can bias concentrations if unmatched units carry population. The saved summary does not provide a full match-quality report by population.

For each pilot district, the model keeps the official count of reserved seats. It selects ST seats with the largest ST population shares first, then SC seats with the largest SC shares among the remainder. This is a simplified share-ranking operationalization motivated by the blueprint's reading of delimitation practice. It is not a legal determination of compliance, and the report does not independently verify the full legal process. The official map retains its actual reservation labels when calculating comparisons; generated maps receive the ranking-based labels.
@@RESERVATION@@
Sangli's selected map increases SC concentration in its one reserved seat from 14.67 to 15.28 percent, while Pune's two SC-reserved seats rise from 14.73 to 16.51 percent on average. Nashik's selected map increases its SC share but reduces mean ST share from 60.96 to 57.45 percent. Better population balance therefore does not uniformly improve protected-group concentration. Concentration is also not equivalent to electoral influence or substantive representation; no voting-behavior model is provided.
\source{[S13]}

\section{Grouping ACs into PCs}
The standalone grouping tool treats ACs as indivisible nodes. Adjacency is based on a shared border of at least 0.5 km with a 0.05 km snapping tolerance for independently prepared borders. A candidate PC is a connected set of exactly six ACs whose total weight lies within the specified balance band. For candidate set $\mathcal{Q}$, binary variable $v_q$ chooses candidates:
\begin{align}
 \min\;&\sum_{q\in\mathcal Q}\left(100\left|E_q/\bar E-1\right|+5I_q^{cross}\right)v_q,\\
 \text{s.t. }&\sum_{q:i\in q}v_q=1 &&\forall\text{ AC }i.
\end{align}
Here the actual statewide run weights nodes by 2024 electors, even though the generic grouping tool calls the field ``population''. Cross-district PCs pay a five-point penalty. The implementation enumerates candidates, solves an LP relaxation for a bound, builds/improves starts using bisection and pair recombination, applies annealing, filters columns by reduced cost and optionally warm-starts a reduced CBC set-partitioning MIP.

These stages separate exact ingredients from heuristic ones. Candidate validity enforces six-AC size, modeled contiguity and balance. The LP is a valid lower bound for that candidate model. The search itself is heuristic unless the MIP proves optimality. Connectivity is on the constructed adjacency graph, not a field-verified statement about every narrow corridor or administrative restriction.
\source{[S14]}

\section{Actual statewide result}
@@PC@@
The statewide run groups the 288 \emph{official} ACs into 48 PCs using 97,141,289 electors and 35 historical district groupings. It does not combine 288 redesigned Assembly seats: statewide redesigned ACs have not been generated. The target is 2,023,776.85 electors/PC. Unlike the grouping tool's 10-percent default described in older documentation, this saved run sets a 25-percent band, or 1,517,833--2,529,721 electors. Its largest absolute deviation is 24.997 percent.

The adjacency graph has 738 edges, including 250 cross-district edges. It enumerates 185,423 candidate PCs, of which 172,669 cross a district boundary. The selected plan has 26 crossing PCs, mean absolute deviation 6.2679 percent, and objective 430.8604. The LP bound is 388.6236, giving an absolute gap of 42.2368 objective points, approximately 9.80 percent of the incumbent objective (10.87 percent relative to the bound). The saved summary records 185,419 columns after filtering, above the 50,000-column MIP threshold, so CBC was skipped. There is no global optimality proof.

Independent stored checks confirm every AC appears once, every PC contains six ACs, all are contiguous on the graph, and all satisfy the configured bounds. The official PC largest/smallest ratio of 1.855 falls to 1.651, and PCs outside 10 percent decrease from 19 to ten. Population balance improves, but the result still includes substantial deviations and cross-district grouping. SC mean share rises from 16.47 to 18.93 percent in reserved PCs, whereas ST mean share falls from 41.97 to 38.77 percent. These demographic trade-offs are not optimized in the grouping objective.
\source{[S13], [S14]}

\chapter{Validation, uncertainty and unresolved issues}
\section{Saved benchmark comparisons}
@@VALIDATION@@
Pune's base station count is about 3.0 percent above the stored 8,462 benchmark, versus an 18.3-percent shortfall using the old-demand model. Nashik is 12.5 percent above its 4,922 benchmark. The old-demand Nashik model is closer numerically at 4,898 stations, but that alone does not validate outdated demand: offsetting assumptions can produce a correct aggregate for the wrong reasons.

At seat level the mismatch is larger: Nashik East is 14.2 percent low, and the Hadapsar comparison is 35.4 percent low. The Hadapsar source is a ``recent roll parts'' page without a fixed 2024 inventory date in the saved CSV, so it is a weak like-for-like benchmark. Pooled urban calibration further complicates both comparisons: district totals can be preserved while individual city AC elector loads are displaced.

The base cap of 1,200 is compared against observations from a 2024 election for which the project itself treats 1,500 as the earlier norm. Cap1500 is therefore a more natural capacity-matched retrospective comparison, but still differs in actual sites and rules. It gives Pune 7,291 stations (about 13.8 percent low against 8,462) and Nashik 4,776 (about 3.0 percent low against 4,922). The report preserves the saved base validation while showing why its interpretation needs qualification.
\source{[S9], [S12]}

\section{Fleet and turnout validation}
The saved comparison uses 636 modeled buses plus 354 jeeps for 11 urban Pune ACs against 968 buses reportedly supplied by PMPML. The latter is one supplier's fleet, while the model counts a synthetic bus/jeep mix. Parties-per-vehicle, actual reserve vehicles, staff movements, dispatch timing, omitted stops and provider coverage differ. The 34.3-percent bus shortfall is a diagnostic, not a standalone estimate of fleet-model accuracy.

The validation script now contains code to compare simulated completed votes by hour with reported state turnout fractions. The frozen validation CSV contains seven station/load/fleet checks and 20 cumulative-vote checks, for 27 comparisons in total. The simulation summary JSONs contain cumulative curves, but those curves are outputs of arrivals already shaped from the same reported curve. Agreement is partly built in. Moreover, reported turnout is an observed cumulative voting measure while the simulation uses it as an arrival distribution and then adds service delay. This is not an independent test of arrival or queue dynamics.
\source{[S11], [S12]}
@@TURNOUT@@

\section{Implementation gaps relative to the blueprint}
The implemented chain is substantial, but the following planned extensions have no demonstrated saved result: verified OSM school/public-building polling premises; a 30-minute walk scenario and slower walking-speed sensitivity; explicit district-hub depot alternatives; fleet-mix and dispatch-start sweeps; independently optimized collection-night routes; BHH continuum checks; queue-aware boundary optimization; urban/rural arrival-shape shifts; full component-specific equipment procurement and staffing/cost accounting; and distance--turnout analysis using Form 20 booth returns.

Reservation and PC grouping were marked ``new'' in the blueprint, but code and outputs now exist, so the blueprint's status column is stale. Conversely, merely finding an item in the blueprint is not enough to call it completed. The report's coverage register and appendices prioritize observed code/output evidence.
\source{[S7]--[S16]}

\section{Evidence and modeling risks}
\textbf{Spatial uncertainty.} Approximate city subdivisions, polygon-based official AC assignment, density thresholding, fixed 2011 settlement geography and snapping to the largest component can misplace demand or hide isolation. A district-level population check does not resolve these issues.

\textbf{Feasibility uncertainty.} Location's self-assignment exception can exceed the walk limit; the village benchmark has different premises constraints; sector fallback can exceed capacity; optional routing stops can remain unserved. Feasible within one submodel is therefore not equivalent to operationally complete across the entire chain.

\textbf{Optimization uncertainty.} ReCom and routing are heuristic. Eleven base location solves are time limited. The PC grouping has an LP certificate but no integer optimality proof. Scenario differences smaller than these uncertainties should not be presented as exact policy rankings.

\textbf{Behavioral uncertainty.} Service speed, variability, arrival shape, turnout transfer, balking, one-server structure and uniform failures are assumed. No observed queue dataset estimates their joint validity. Voters who leave reduce realized turnout, which changes the denominator of reported wait metrics.

\textbf{Economic uncertainty.} Walking cost is a social valuation, station cost is a bundled assumption, and route tariffs are assumed. Security, accessibility works, police deployment, terrain disruption, weather, actual driver duty limits and receipt-centre processing are not separately costed.

\textbf{Source uncertainty.} Rolls are from Wikipedia extraction; benchmarks combine media reports, a roll-parts site and a handbook URL. Values are preserved as project inputs, not independently authenticated facts. Data licensing also matters: the project README identifies SHRUG-derived material as CC BY-NC-SA 4.0; raw downloads are intentionally outside the repository.
\source{[S3]--[S14]}

\chapter{Software structure and reproducibility}\label{ch:repro}
\section{Pipeline and file contracts}
The intended data flow is raw geometry/population $\rightarrow$ assignment units $\rightarrow$ official/generated maps $\rightarrow$ network and hamlet demand $\rightarrow$ locations $\rightarrow$ routes/sectors $\rightarrow$ station simulation $\rightarrow$ scenario summaries. Files make the interfaces explicit, but they also create opportunities for stale caches and mismatched code versions.

\texttt{prepare\_district\_inputs.py} creates units, demand-point coordinates, district boundary and an input audit. \texttt{sangli\_recom.py} writes unit assignments, constituency polygons and score metadata. \texttt{official\_district\_map.py} writes reconstructed official assignments plus mapping checks. \texttt{build\_road\_matrix.py} writes legacy pairwise km. \texttt{audit\_official\_2008.py} consolidates district and statewide comparisons. \texttt{apportion\_seats.py} provides independent seat-count scenarios.

In the final chain, \texttt{network.py} writes sparse graphs and node masks; \texttt{demand.py} writes demand points, calibration factors and demand metadata; \texttt{location.py} expects to write seats, sites and assignments; \texttt{routing.py} consumes sites and writes routes, sector assignments and seat aggregates; \texttt{simulate.py} consumes sites, assignments and sectors, and writes simulation summaries plus station aggregates. \texttt{scenarios.py} orchestrates stages and collects comparative CSVs. \texttt{report.py} creates eight final presentation tables, including the 40-second no-balking capacity valuation. \texttt{validate.py} compares with the benchmark register. \texttt{representation.py} builds SC/ST and PC comparisons.
\source{[S2]--[S15]}

\section{Current replay limitations}
The saved location folders contain \texttt{seats.csv} and downstream routing/simulation summaries, but their \texttt{sites.csv} and \texttt{assignments.csv} are absent across the inspected scenario directories. These files are required by both routing and station-table construction. Thus aggregate results can be reviewed and the report regenerated, but downstream models cannot be replayed directly from the present location outputs without restoring or regenerating the missing detailed files.

The location matrix cache validates only the sequence of demand-point IDs. It does not validate coordinates, network hashes or elector weights, although cached medoid and candidate decisions can depend on those quantities. Changes to demand geography or calibration can therefore require cache invalidation even if IDs remain unchanged. This is especially relevant to demand regeneration and fully AC-calibrated scenarios. Exact replay also requires raw downloads, compatible solver packages and a valid Gurobi licence.

Output files generally do not embed the producing commit, dependency versions, input hashes, random-state trace, or a single run identifier. A timestamp or saved status cannot establish that every downstream output was generated from the current source. The committed source includes a safer turnout-averaging calculation for zero-rounded assignment weights, but output-to-code attribution remains incomplete. The frozen report evidence and manifest address document provenance only; they do not retroactively supply missing model-run provenance.
\source{[S7]--[S11], [S16]}

\section{Suggested replay commands}
The following commands describe the available interfaces and may regenerate large or licensed optimization runs. They were not executed as part of writing this report.
\begin{verbatim}
cd code
../.venv/bin/python -m logistics.network --district sangli
../.venv/bin/python -m logistics.demand --district sangli
../.venv/bin/python -m logistics.scenarios \
  --districts sangli --stage all
../.venv/bin/python -m logistics.scenarios \
  --districts sangli --scenarios ac_calibrated --stage location
../.venv/bin/python -m logistics.representation assembly
../.venv/bin/python -m logistics.representation lok-sabha --group
../.venv/bin/python -m logistics.validate
\end{verbatim}
The scenario wrapper's simulation stage always targets base and cap1500. Selecting another location scenario does not automatically produce its simulation. A specific scenario requires explicit routing and simulation calls after valid detailed location files exist. No licence content is copied into this report or its evidence package.

For document-only regeneration, run \texttt{python3 docs/technical\_report/build\_report.py}. The generated source uses embedded tables and pgfplots charts, requiring no external images or bibliography files. It can be opened in the Codex \LaTeX{} editor or compiled with a compatible \LaTeX{} distribution. The manifest hashes 267 frozen non-secret source/result files.

\section{Verification performed for this report}
The report builder verifies the SHA-256 digest of every frozen source. Its independent numerical checks verify the 52 base seat rows, all 36 location/routing summaries, complete scenario pairs for the four districts, and all 48 simulation variants. PC assignment coverage, six-AC counts and connectivity can be rechecked from the saved assignment and adjacency files without rerunning search. The final document is compiled and inspected for broken references, missing characters and layout issues. These are report/evidence checks, not a fresh execution of the original optimization pipeline.

\chapter{Conclusions and next research steps}
\section{What the project now establishes}
The work has moved well beyond its original Sangli boundary-design demonstration. It provides a modular research platform linking representation, physical accessibility, polling capacity, transport and service congestion. Its most convincing results are comparative: population balance can improve while administrative coherence worsens; replacing coarse point demand changes measured access; station capacity and service time materially influence queues; and the saved redesigned maps have relatively small logistics-cost differences compared with changes in demand and access assumptions.

The historical roll audit provides a distinct empirical contribution: it shows increasing imbalance in the project's 2009--2024 constituency-elector dataset without relying on questionable city population reconstruction. The PC model contributes a feasible statewide regrouping with explicit graph/balance checks and a bound on a heuristic objective. Neither contribution is a demonstration that the resulting boundaries or logistics plan are ready for official adoption.

\section{Highest-value next steps}
First, complete and compare the AC-calibrated scenario, particularly for urban Pune and Nashik, while auditing geographic placement against actual roll parts and approved station lists. Preserve both pooled and seat-specific alternatives as uncertainty scenarios rather than assuming one resolves the spatial problem.

Second, restore detailed locations/assignments and enforce complete routing. Every dropped stop needs an explicit feasible service plan, cost and timing consequence. Collection-night routes should be optimized using simulated or observed closing times, sealing delay and receipt-centre capacity. Network distance and time should describe a consistent selected path.

Third, validate queue behavior using observed station loads, arrival counts, service times and waits. Report waiting, abandonment and realized turnout together, with uncertainty intervals and replication-level diagnostics. Investigate targeted capacity or additional service resources only after that calibration.

Fourth, replace raster points with a feasible premises inventory including capacity, accessibility, power, security and staff constraints. Develop a component-specific equipment/staff bill of materials and separate fiscal cost from voter-time valuation. Check sector capacity and record fallback use explicitly.

Finally, make run provenance first class: input hashes, code revision, environment lock, full configuration, solver bound/status, seed and output coverage in every run folder. Use the existing platform to study trade-offs transparently, with the report's distinction between proposal, implementation, saved result and verified performance maintained throughout.

\appendix
\chapter{Complete map-comparison register}\label{app:maps}
This register covers the four main pilots, each with one reconstructed official map and six generated road-stage maps. It excludes the earlier Euclidean maps, which belong to a different experiment. Cuts is the saved audit's taluka-excess measure. Max deviation and MAD use reconstructed Census-2011 population.
@@MAPAPP@@

\chapter{Geographic map comparisons}
The following embedded vector maps show the actual saved constituency geometries for each official reconstruction and the selected district-score winner. They provide geographic context for the balance/fragmentation comparisons. The presentation of colored polygons is not a fresh boundary validation, and simplified outlines must not be used as operational GIS boundaries.
@@MAPFIGURES@@

\chapter{Complete location and routing scenario register}\label{app:scenarios}
Each row is one saved district/scenario output. All use official boundaries except best\_map. All use 2024-derived demand except census2011. All use the 1,200 cap except cap1500. The village rule has different feasibility restrictions from the optimized scenarios. Missing ac\_calibrated rows reflect missing runs, not omitted favorable results.
@@SCENARIOAPP@@

\chapter{Complete simulation sensitivity register}\label{app:simulation}
All stored variants use 30 replications. Unless encoded in the label, the run uses 50-second mean service, 20-percent reserve and the transferred failure probability. ``1500'' prefixes refer to the cap1500 station layout. Reserve10 and reserve30 mean 10- and 30-percent complete-set reserve. Overrun p95 is measured in minutes over station-replication observations.
@@SIMAPP@@

\chapter{All base Assembly-seat results}\label{app:acs}
Seat identifiers are official 2008 AC numbers. Elector counts in uncertain urban ACs reflect the pooled base calibration and therefore need not equal the seat's saved electoral-roll total. The gap is the saved relative objective bound gap. Site count means premises, not station count.
@@ACAPP@@

\chapter{First-generation road booth-MIP experiments}\label{app:legacy}
These results preserve the intermediate research stage rather than silently replacing it with the latest model. The first-generation formulation has one demand point per populated unit, zero within-unit travel, a fixed depot, capacity 1,500, and demand 0.70 times Census population. Sangli contains three finalist maps in the saved road-MIP file; the other pilots also compare the reconstructed official map. The extremely small average distances measure inter-unit travel only.
@@LEGACYAPP@@

\chapter{Full district apportionment experiment}\label{app:allocation}
The apportionment is a statewide hypothetical on current districts. Its population sum is 112,374,333 and its seat sum is 288. It must not be mistaken for the fixed-count four-pilot experiment or the 35-group historical district inventory in the PC run.
@@ALLOCATION@@

\chapter{Module coverage and research status}
\section{Implemented with saved results}
\begin{description}[style=nextline]
\item[Districting and preparation] Sangli, Satara, Nashik and Pune inputs and map families; Hingoli generic-pipeline test; population/geography validation; renderers and district wrappers.
\item[Legacy station optimization] Fallback and road-matrix booth MIPs; radius sensitivities; official-map comparisons; CBC/Gurobi run metadata.
\item[Audit and calibration] Reconstructed official maps, Census drift assessment, four election-year roll extraction and district/state metrics; pooled 2024 demand factors.
\item[Networks and final demand] Walk/drive sparse graphs, node masks, district metadata, nominal 100 m hamlet-demand construction and within-hamlet distance.
\item[Strategic/operational logistics] Nine saved location strategies per district, joint/fixed depot options, vehicle routes, sector results and generalized costs; detailed upstream site/assignment files currently missing.
\item[Polling day] Eight base and four cap1500 simulation variants per district, saved summary JSONs and combined sensitivity CSV.
\item[Representation] Pilot Assembly SC/ST comparisons; official/balanced PC summaries; 288-AC assignment to 48 contiguous six-AC groups under a 25-percent band.
\item[Communication] Original presentations/notebook, map renderers, local HTML showcase builders and JSON data; living blueprint and README documentation.
\end{description}

\section{Implemented without a saved comparative run}
The fully seat-specific \texttt{ac\_calibrated} demand scenario exists in source and input columns. It lacks location/routing/simulation results. The current validation code and saved 27-row validation CSV include turnout-curve checks as well as station/load/fleet comparisons. The grouping test script contains synthetic/small-input checks; historical README claims of prior test success are not represented here as fresh test execution.

\section{Proposed or partial}
\enlargethispage{3\baselineskip}
Verified building candidates, time-based access sensitivity, explicit collection-night optimization, full resource accounting, queue-aware districting, separate reserve-component inventories, BHH comparison, systematic fleet sweeps, Form 20 distance/turnout analysis, statewide redesigned ACs, legal compliance review and field deployment remain proposed or incomplete on the inspected evidence.

\chapter{Notation and metric glossary}
\begin{description}[style=nextline]
\item[AC / PC] Assembly constituency / Parliamentary (Lok Sabha) constituency.
\item[ReCom] Recombination: merge neighboring districts, draw a spanning tree and split into legal districts.
\item[MAD] Mean absolute percentage deviation from a specified target. The target may be a district mean, state mean or PC mean; the reference must be stated.
\item[Polsby--Popper] $4\pi A/L^2$; higher indicates a more compact polygon, subject to boundary-resolution effects.
\item[Premises / station] A physical site / an individual polling-party and voting-server unit. Multiple stations can share one premises.
\item[Hamlet / demand point] A raster-weighted spatial piece of an assignment unit, represented by a medoid and an internal mean walking component; not an observed administrative hamlet register.
\item[Star distance] One-way depot-to-site km multiplied by station count. The four-leg solo-party comparator is four times this value.
\item[Vehicle-km] Sum of routed loop km, multiplied by two for the assumed dispatch and collection loops.
\item[Generalized cost] Station, premises and transport assumptions plus monetized walking burden. Not identical to fiscal expenditure.
\item[Relative MIP gap] Difference between incumbent and bound normalized according to the solver; nonzero tolerance can still yield an ``optimal'' status in the code.
\item[Balking] A simulated arriving voter leaves based on prospective wait; distinct from reneging after joining a queue.
\item[After-close votes] Completed votes whose service finishes after 18:00, divided by all completed votes; distinct from the share of stations with any late vote.
\item[SC/ST share] Census-derived protected-group population share, not a direct measure of voting preference or influence.
\end{description}

\chapter{Local source and provenance register}
All relative paths below are rooted at the supplied project directory. The companion frozen evidence is under \path{docs/technical_report/evidence/}, preserving the same relative paths for selected non-secret source and result files. Original PDFs and the notebook were inspected directly and are included in the frozen evidence, along with eight map-geometry files. The 267-file manifest records selected code/result content hashes and the pre-existing repository state.

\begingroup\raggedright\small
\begin{description}[style=nextline]
\item[{[S1] Presentations}] \path{IE301 Presentation_1.pdf}; \path{IE301 Presentation_2.pdf}; \texttt{Election Logistics} (Presentation 3 PDF, root filename with an en-dash separator). Motivation, explored methods, GA account, team/mentor names, original Sangli results and future scope. Some first-presentation slides are placeholders.
\item[{[S2] Districting implementation and original results}] \path{code/sangli_recom.py}; \path{code/sangli_recom_readme.md}; \path{code/map_comparison.csv}; \path{code/map_balanced/score.json}, and analogous locality/taluka scores. Definitions of feasibility, objective, search and original-map comparison.
\item[{[S3] Early booth models and notebook}] \path{IE301_Election_logistics.ipynb}; \path{code/booth_mip.py}; \path{code/booth_mip_readme.md}; \path{code/booth_mip_results/}; \path{code/sangli_road/booth_mip_road/}; \path{code/pilots/*/booth_mip_gurobi/}. Historical demand, distance, capacity and solver results.
\item[{[S4] Preparation and apportionment}] \path{code/prepare_district_inputs.py}; \path{code/prepare_sangli_inputs.py}; \path{code/shrug_to_pca.py}; \path{code/district_config/}; \path{code/sangli_input/input_audit.json}; \path{code/pilots/*/input/input_audit.json}; \path{code/apportion_seats.py}; \path{code/data/ac_allocation_2011.csv}; district population and crosswalk CSVs.
\item[{[S5] Official-map reconstruction and audit}] \path{code/official_district_map.py}; \path{code/official_ac_map.py}; \path{code/audit_official_2008.py}; \path{code/analysis/official_audit/district_summary.csv}; \path{code/analysis/official_audit/map_comparison.csv}; \path{code/analysis/official_audit/official_seats.csv}; \path{code/analysis/official_audit/statewide_drift.csv}.
\item[{[S6] Electoral rolls}] \path{code/fetch_ac_electors.py}; \path{code/data/maharashtra_ac_electors_wikipedia.csv}; \path{code/analysis/official_audit/electors_statewide.csv}; \path{code/analysis/official_audit/electors_by_district_year.csv}. Secondary-source extraction across 2009--2024.
\item[{[S7] Networks and hamlet demand}] \path{code/build_road_matrix.py}; \path{code/logistics/common.py}; \path{code/logistics/network.py}; \path{code/logistics/demand.py}; \path{code/logistics_runs/*/network_info.json}; \path{code/logistics_runs/*/demand_info.json}; \path{code/logistics_runs/*/elector_factors.csv}. Large raw/network binaries are not copied into the report evidence.
\item[{[S8] Joint facility location}] \path{code/logistics/location.py}; \path{code/logistics_runs/*/location/*/seats.csv}. Saved per-seat costs, sites, capacity, depot, status and gap. Detailed site/assignment CSVs expected by the code are absent from the inspected original run folders.
\item[{[S9] Scenario definitions and aggregation}] \path{code/logistics/scenarios.py}; \path{code/logistics_runs/summary/scenarios.csv}. Nine saved scenarios per district and an implemented tenth without saved results.
\item[{[S10] Routing and sectors}] \path{code/logistics/routing.py}; \path{code/logistics_runs/*/location/*/routing/seats.csv}; corresponding route and sector CSVs in the original workspace. Fleet, omissions, timing and response metrics.
\item[{[S11] Simulation}] \path{code/logistics/simulate.py}; \path{code/logistics_runs/summary/simulations.csv}; \path{code/logistics_runs/*/location/*/simulation/summary_*.json}. The inspected simulation source includes the committed turnout-averaging robustness change.
\item[{[S12] Benchmarks and validation}] \path{code/data/validation_benchmarks.csv}; \path{code/logistics/validate.py}; \path{code/logistics_runs/summary/validation.csv}. Original stored URLs, values, scope, years and saved comparisons.
\item[{[S13] Representation outputs}] \path{code/logistics/representation.py}; \path{code/logistics_runs/representation/assembly_reservation.csv}; \path{code/logistics_runs/representation/official_reservation_check.csv}; \path{code/logistics_runs/representation/lok_sabha_summary.csv}; \path{code/logistics_runs/representation/lok_sabha_pcs.csv}.
\item[{[S14] PC grouping}] \path{code/group_lok_sabha.py}; \path{code/test_group_lok_sabha.py}; \path{code/logistics_runs/representation/pc_balanced/summary.json}; \path{code/logistics_runs/representation/pc_balanced/pc_assignment.csv}; \path{code/logistics_runs/representation/pc_balanced/ac_adjacency.csv}.
\item[{[S15] Wrappers, tests and showcase}] \path{code/run_district.sh}; \path{code/run_district.ps1}; \path{code/run_pilot.sh}; \path{code/runs/hingoli/map_comparison.csv}; \path{code/build_showcase.py}; \path{code/build_showcase_data.py}; \path{code/logistics/report.py}; \path{code/logistics_runs/summary/report/}; \path{showcase/}. Hingoli has 714 units and 1,177,345 population; saved maximum deviations are 0.53, 0.50 and 1.49 percent for its three maps.
\item[{[S16] Intent and repository state}] \path{README.md}; \path{docs/BLUEPRINT.md}; Git commits \texttt{7c2604a}, \texttt{ec0a947}, \texttt{3645d96}, \texttt{a0dfaa0}; \path{docs/technical_report/source_manifest.json}. The blueprint is a design document with partially stale status labels.
\end{description}

\endgroup
\section{Benchmark URLs preserved from the project}
The following examples identify the stored references without implying fresh authentication. The complete benchmark register remains in the frozen evidence.
\begin{itemize}
\item Pune district station arrangements: Deccan Herald, November 2024, stored benchmark 8,462; an alternative Punekar News value is 8,443.
\item Nashik district station count: PTI via The Week, 18 November 2024, stored benchmark 4,922, with 136 critical stations separately listed.
\item Hadapsar roll parts: \url{https://voterlist.co.in/hadapsar/}, labelled ``recent roll'' in the benchmark CSV, not a confirmed 2024 date.
\item Urban Pune buses: Punekar News reference \url{https://www.punekarnews.in/?p=190053}, stored value 968.
\item Failure rates: ETV Bharat's Assam phase-one 2024 component-replacement report; transferred BU/CU/VVPAT values are sensitivity inputs.
\item Sector locations: 2023 sector-officer handbook URL hosted by GHMC, with the stored 10--12 locations benchmark.
\end{itemize}

\section{What is intentionally not claimed}
No new election model was solved for this report. No claim is made of complete official-source verification, legal compliance, global optimality of heuristic runs, operational coverage of dropped routes, exact replay of missing detailed outputs, a statewide redesigned Assembly map, or empirical validation of the queue/balking model. These qualifications make the documented achievements reproducible and interpretable without erasing the project's substantial progress.
\end{document}
'''
for key,value in T.items():
    doc=doc.replace('@@'+key+'@@',value)
assert '@@' not in doc
# The source filename of Presentation 3 contains a Unicode en dash. Keep the
# standalone pdfLaTeX source portable by spelling the visible separator in TeX.
doc=doc.replace(' – ', ' -- ')
target=HERE/'election_logistics_technical_report.tex'
target.write_text(doc)
audit['snapshot']={'source_files':len(manifest['sources']),'scenarios':len(sc),'simulation_variants':len(si),'base_electors':sum(int(base[d]['electors']) for d in D),'base_stations':sum(int(base[d]['stations']) for d in D),'base_premises':sum(int(base[d]['premises']) for d in D)}
(HERE/'report_audit.json').write_text(json.dumps(audit,indent=2))
print(f'Wrote {target}; {len(doc.split())} source words; {len(T)} generated tables/figures')
print(json.dumps(audit['snapshot'],indent=2))
