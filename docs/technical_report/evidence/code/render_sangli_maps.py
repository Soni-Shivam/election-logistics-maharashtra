"""Render a district's constituency GeoJSON as clear research PNGs.

The district name comes from --district-name or the district GeoJSON
(property district_name, else the words of name before " district"); the
seat count comes from the map. Taluka lines come from the SHRUG subdistrict
GeoPackage (--talukas with --district-id) or, if --talukas is not given, from
the union of each taluka's input units.
"""
import argparse
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import shapely
from scipy.optimize import linear_sum_assignment
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely import wkb

# The first eight are the Sangli pilot colours; the rest extend to 20 seats.
COLORS=['#72b7b2','#f4a261','#8da0cb','#e76f51',
        '#a7c957','#c89bc9','#e9c46a','#8ecae6',
        '#b5838d','#90be6d','#f28482','#84a59d','#cdb4db','#ffb703',
        '#a8dadc','#d4a373','#9d8189','#bde0fe','#e5989b','#b7b7a4']
WARD_KINDS={'APPROX_WARD','WARD'}
FONT_FILES={False:[r'C:\Windows\Fonts\arial.ttf','DejaVuSans.ttf',
                   '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'],
            True:[r'C:\Windows\Fonts\arialbd.ttf','DejaVuSans-Bold.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']}


def font(size,bold=False):
    for path in FONT_FILES[bold]:
        try:return ImageFont.truetype(path,size)
        except OSError:continue
    return ImageFont.load_default(size)


def load_geo(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))['features']


def gpkg_geoms(path,district_id):
    if not Path(path).is_file():raise SystemExit(f'Taluka GeoPackage not found: {path}')
    conn=sqlite3.connect(path)
    out=[]
    for (blob,) in conn.execute("SELECT geom FROM subdistrict WHERE pc11_district_id=?",
                                (str(district_id).zfill(3),)):
        flags=blob[3]
        n={0:0,1:32,2:48,3:48,4:64}[(flags>>1)&7]
        out.append(wkb.loads(blob[8+n:]))
    conn.close()
    if not out:raise SystemExit(f'No subdistricts for district {district_id} in {path}')
    return out


def unit_talukas(units_path):
    by_taluka={}
    for f in load_geo(units_path):
        by_taluka.setdefault(f['properties']['taluka'],[]).append(shape(f['geometry']))
    return [shapely.union_all(g) for _,g in sorted(by_taluka.items())]


def district_name(district_feature):
    p=district_feature.get('properties') or {}
    if p.get('district_name'):return p['district_name']
    name=p.get('name','')
    return name.split(' district')[0] if ' district' in name else (name or 'District')


def parts(g):
    if g.geom_type=='Polygon': return [g]
    if g.geom_type=='MultiPolygon': return list(g.geoms)
    return []


def coord(lon,lat,bounds,box,latitude):
    minx,miny,maxx,maxy=bounds
    left,top,right,bottom=box
    c=math.cos(math.radians(latitude))
    w=(maxx-minx)*c
    h=maxy-miny
    scale=min((right-left)/w,(bottom-top)/h)
    startx=left+((right-left)-w*scale)/2
    starty=top+((bottom-top)-h*scale)/2
    return (round(startx+(lon-minx)*c*scale),round(starty+(maxy-lat)*scale))


def poly(draw,g,bounds,box,latitude,fill,outline,width=2):
    for p in parts(g):
        xy=[coord(x,y,bounds,box,latitude) for x,y in p.exterior.coords]
        if len(xy)>=3: draw.polygon(xy,fill=fill)
        for ring in p.interiors:
            xy=[coord(x,y,bounds,box,latitude) for x,y in ring.coords]
            if len(xy)>=3: draw.polygon(xy,fill='#ffffff')
        xy=[coord(x,y,bounds,box,latitude) for x,y in p.exterior.coords]
        if len(xy)>=2: draw.line(xy,fill=outline,width=width,joint='curve')


def line(draw,g,bounds,box,latitude,color,width=1):
    for p in parts(g):
        xy=[coord(x,y,bounds,box,latitude) for x,y in p.exterior.coords]
        if len(xy)>=2: draw.line(xy,fill=color,width=width,joint='curve')


def align_colors(reference,current):
    """Renumber current seats to the reference seat each overlaps most."""
    labels=sorted(int(f['properties']['constituency']) for f in current)
    if reference is None:return {i:i for i in labels}
    a={int(f['properties']['constituency']):shape(f['geometry']) for f in reference}
    b={int(f['properties']['constituency']):shape(f['geometry']) for f in current}
    if sorted(a)!=labels:raise SystemExit('Reference map has different seat labels')
    m=np.array([[a[i].intersection(b[j]).area for j in labels] for i in labels])
    rows,cols=linear_sum_assignment(m,maximize=True)
    return {labels[c]:labels[r] for r,c in zip(rows,cols)}


def seat_fills(seats,color_id):
    """Fill per seat: the palette by aligned number, or, with more seats than
    colours, a greedy colouring in which neighbouring seats differ."""
    labels=[int(f['properties']['constituency']) for f in seats]
    if len(labels)<=len(COLORS):
        return {s:COLORS[color_id[s]-1] for s in labels}
    geoms={int(f['properties']['constituency']):shape(f['geometry']) for f in seats}
    fill={}
    for s in sorted(geoms,key=lambda s:color_id[s]):
        used={fill[t] for t in fill if geoms[s].intersection(geoms[t]).length>0}
        fill[s]=next((c for c in COLORS if c not in used),COLORS[color_id[s]%len(COLORS)])
    return fill


def render(result_dir,units,district,talukas,out,reference=None,name=None,sources=None):
    result_dir=Path(result_dir)
    seats=load_geo(result_dir/'constituencies.geojson')
    assigned=load_geo(result_dir/'assigned_units.geojson')
    score=json.loads((result_dir/'score.json').read_text())
    ref=load_geo(Path(reference)/'constituencies.geojson') if reference else None
    color_id=align_colors(ref,seats)
    fills=seat_fills(seats,color_id)
    district_feature=json.loads(Path(district).read_text())
    name=name or district_name(district_feature)
    sources=(sources or (district_feature.get('properties') or {}).get('sources')
             or 'Sources: Census 2011 PCA, SHRUG PC11 polygons. Research map only.')
    city=[f for f in assigned if f['properties'].get('kind') in WARD_KINDS]
    approx=any(f['properties']['kind']=='APPROX_WARD' for f in city)
    road=score.get('distance_source',{}).get('type')=='road_km_matrix'
    aligned_to=Path(reference).name if reference else result_dir.name
    aligned_to=aligned_to[4:] if aligned_to.startswith('map_') else aligned_to
    D=shape(district_feature['geometry'])
    lat=D.centroid.y
    bounds=D.bounds
    img=Image.new('RGB',(2400,1550),'#ffffff')
    d=ImageDraw.Draw(img)
    title=result_dir.name.replace('map_','').replace('_',' ').title()
    d.text((75,45),f'{name} district | {title}',font=font(46,True),fill='#17324d')
    subtitle=f'{len(seats)} research constituencies • 2011 population'
    if city:subtitle+=' • approximate urban subdivisions' if approx else ' • urban ward subdivisions'
    d.text((78,105),subtitle,font=font(24),fill='#52616b')
    mainbox=(70,185,1740,1400)
    for feat in seats:
        seat=int(feat['properties']['constituency'])
        poly(d,shape(feat['geometry']),bounds,mainbox,lat,fills[seat],'#273849',4)
    for geom in talukas:
        line(d,geom,bounds,mainbox,lat,'#ffffff',3)
        line(d,geom,bounds,mainbox,lat,'#555f68',1)
    # Village/town demand locations show the population lattice actually used.
    for feat in assigned:
        p=feat['properties']
        if p['population']<=0 or p.get('kind') in WARD_KINDS:continue
        x,y=coord(p['demand_lon'],p['demand_lat'],bounds,mainbox,lat)
        r=max(1,min(5,int(math.sqrt(p['population'])/40)))
        d.ellipse((x-r,y-r,x+r,y+r),fill='#273849')
    for feat in seats:
        g=shape(feat['geometry'])
        pos=g.representative_point()
        x,y=coord(pos.x,pos.y,bounds,mainbox,lat)
        seat=int(feat['properties']['constituency'])
        pop=int(feat['properties']['population'])
        label=f'{color_id[seat]}  {pop:,}'
        bb=d.textbbox((0,0),label,font=font(23,True))
        d.rounded_rectangle((x-10,y-8,x+bb[2]+10,y+bb[3]+10),radius=8,
                            fill='#ffffff',outline='#30404a',width=2)
        d.text((x,y),label,font=font(23,True),fill='#14202b')
    d.rectangle((1775,165,2360,1405),outline='#bac6ce',width=2)
    d.text((1810,195),'Comparison',font=font(30,True),fill='#17324d')
    terms=score['terms'];raw=score['raw']
    details=[f"Population: {score['raw']['population_target']:,.0f} target / seat",
             f"Allowed range: {score['raw']['population_bounds'][0]:,}–{score['raw']['population_bounds'][1]:,}",
             f"Mean distance: {raw['population_weighted_mean_distance_km']:.1f} km",
             f"Taluka excess fragments: {raw['taluka_excess_fragments']}",
             f"Shape penalty: {terms['shape']:.3f}",
             f"Score (own weights): {score['score']:.3f}"]
    for i,t in enumerate(details):d.text((1810,255+i*46),t,font=font(21),fill='#263b4a')
    if city:
        # Inset of the most populous divided town.
        towns={}
        for f in city:towns.setdefault(f['properties']['settlement_id'],[]).append(f)
        city=max(towns.values(),key=lambda fs:sum(int(f['properties']['population']) for f in fs))
        d.text((1810,565),'City inset (schematic)',font=font(27,True),fill='#17324d')
        city_geom=shapely.union_all([shape(f['geometry']) for f in city])
        cb=city_geom.bounds
        inset=(1800,620,2330,1030)
        for feat in city:
            seat=int(feat['properties']['constituency'])
            poly(d,shape(feat['geometry']),cb,inset,city_geom.centroid.y,
                 fills[seat],'#ffffff',2)
        for feat in city:
            pos=shape(feat['geometry']).representative_point()
            x,y=coord(pos.x,pos.y,cb,inset,city_geom.centroid.y)
            label=feat['properties']['id'][-2:].lstrip('0')
            d.text((x-6,y-8),label,font=font(15,True),fill='#25313c',
                   stroke_width=2,stroke_fill='#ffffff')
    d.text((1810,1050),'Dots: village/small-town input locations',font=font(19),fill='#52616b')
    if city:
        d.text((1810,1082),'City wards: approximate, not official GIS' if approx else
               'City wards: clipped to 2011 town extent',font=font(19),fill='#52616b')
    d.text((1810,1130),f'Seat numbers/colors aligned to {aligned_to} map',font=font(18),fill='#52616b')
    d.text((1810,1170),'Road km (OSM matrix), not travel time' if road else
           'Straight-line distance, not travel time',font=font(18),fill='#52616b')
    d.text((75,1450),sources,font=font(19),fill='#52616b')
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    img.save(out,optimize=True)
    print(out)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--result',required=True)
    ap.add_argument('--units',required=True)
    ap.add_argument('--district',required=True)
    ap.add_argument('--talukas',help='SHRUG subdistrict GeoPackage; default: taluka outlines from --units')
    ap.add_argument('--district-id',help='PC11 district code for --talukas, e.g. 531')
    ap.add_argument('--district-name',help='default: from the district GeoJSON')
    ap.add_argument('--sources',help='footer text; default: from the district GeoJSON')
    ap.add_argument('--out',required=True)
    ap.add_argument('--reference')
    a=ap.parse_args()
    if a.talukas and not a.district_id:ap.error('--talukas needs --district-id')
    talukas=gpkg_geoms(a.talukas,a.district_id) if a.talukas else unit_talukas(a.units)
    render(a.result,a.units,a.district,talukas,a.out,a.reference,a.district_name,a.sources)
