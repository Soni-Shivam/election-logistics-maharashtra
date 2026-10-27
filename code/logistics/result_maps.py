"""Result maps: what the models actually produced, drawn on the map.

Constituencies (official vs ours), polling stations and dispatch centres, vehicle routes on
the road network, who votes where, sector-officer sectors, polling-day waits by station, and
Lok Sabha seats. Same look as figures.py; images go to ``figures/results/``.

Usage:
    python -m logistics.result_maps                    # all
    python -m logistics.result_maps seats stations     # some
"""

from __future__ import annotations

import subprocess
import sys

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from . import figures as F
from .common import CODE, ROOT, RUNS, run_dir

OUT = ROOT / "figures" / "results"
NAMES = {"sangli": "Sangli", "satara": "Satara", "nashik": "Nashik", "pune": "Pune"}
BEST = {"sangli": ("sangli_road/runs", "taluka_s303"), "satara": ("pilots/satara/runs", "taluka_s303"),
        "nashik": ("pilots/nashik/runs", "taluka_s714"), "pune": ("pilots/pune/runs", "balanced_s714")}
# soft, distinct fills for neighbouring seats (graph-coloured so neighbours never share one)
SEAT_COLOURS = ["#A8C5F0", "#F6C28B", "#B5DDA4", "#E7A9C4", "#C9B6E8", "#9ED9D5", "#F3E08A", "#D7B49E"]


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png")
    plt.close(fig)
    print("wrote", name)


def colour_seats(gdf, key):
    """Greedy graph colouring of seat polygons so touching seats differ."""
    import networkx as nx
    g = nx.Graph()
    geoms = gdf.geometry.buffer(0.0005).values
    ids = gdf[key].tolist()
    g.add_nodes_from(ids)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if geoms[i].intersects(geoms[j]):
                g.add_edge(ids[i], ids[j])
    col = nx.greedy_color(g, strategy="largest_first")
    return gdf[key].map(lambda k: SEAT_COLOURS[col[k] % len(SEAT_COLOURS)])


def official_seats(district):
    g = F.statewide()
    s = g[g["district_2008"] == NAMES[district]].copy()
    s["label"] = s["AC_NAME"].str.replace(r"\s*\((SC|ST)\)", lambda m: f" ({m.group(1)})", regex=True)
    return s


def our_seats(district):
    geo = F.gpd()
    base, run = BEST[district]
    a = geo.read_file(CODE / base / run / "assigned_units.geojson")
    s = a.dissolve(by="constituency", aggfunc={"population": "sum"}).reset_index()
    s["geometry"] = s.geometry.buffer(0.0008).buffer(-0.0008)  # close slivers between village pieces
    target = s["population"].sum() / len(s)
    s["dev"] = 100 * (s["population"] / target - 1)
    return s


def talukas(district):
    return F.units_gdf(district).dissolve(by="taluka").reset_index()


def label_seats(ax, gdf, text_col, size=8.5, min_area=0.0):
    for _, r in gdf.iterrows():
        if r.geometry.area < min_area:  # tiny city seats: shown in the city close-up instead
            continue
        p = r.geometry.representative_point()
        ax.annotate(str(r[text_col]), (p.x, p.y), ha="center", va="center", fontsize=size, color=F.INK,
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.6))


# ---------------------------------------------------------------- constituencies
def seats_map(district):
    off, ours, tal = official_seats(district), our_seats(district), talukas(district)
    off["fill"] = colour_seats(off, "AC_NO")
    ours["fill"] = colour_seats(ours, "constituency")
    ours["label"] = [f"{i}\n{d:+.0f}%".replace("-", "−") for i, d in zip(ours["constituency"], ours["dev"])]
    mc = pd.read_csv(CODE / "analysis/official_audit/map_comparison.csv")
    mc = mc[mc["district"] == NAMES[district]].set_index("map")
    o, b = mc.loc["official_2008"], mc.loc[BEST[district][1]]
    fig, axes = plt.subplots(1, 2, figsize=(F.W, F.H))
    for ax, s, title in ((axes[0], off, f"Official 2008 seats ({len(off)})"), (axes[1], ours, f"Our optimised seats ({len(ours)})")):
        s.plot(ax=ax, color=s["fill"], edgecolor="white", linewidth=1.2)
        tal.boundary.plot(ax=ax, color=F.INK, linewidth=0.6, linestyle=(0, (3, 2)), alpha=0.6)
        label_seats(ax, s, "label", size=8 if len(s) > 10 else 9.5, min_area=0.012 if len(s) > 10 else 0.0)
        ax.set_title(title, fontsize=13)
        F.map_axis(ax, s)
    small = district in ("pune", "nashik")
    F.suptitle(fig, f"{NAMES[district]}: the official seats and our redrawn seats",
               f"Official: {int(o['seats_outside_10pct'])} seats off ±10%, largest ÷ smallest {o['size_ratio']:.2f}, "
               f"{int(o['talukas_split'])} talukas split · Ours: {int(b['seats_outside_10pct'])} off ±10%, {b['size_ratio']:.2f}, "
               f"{int(b['talukas_split'])} talukas split · our labels: seat number and population vs target (Census 2011)")
    F.source(fig, "Official boundaries: DataMeet 2008 AC polygons. Ours: ReCom + annealing on Census 2011 villages and wards"
                  f" ({BEST[district][1].split('_')[0]} weights). Dashed = taluka boundaries."
                  + (" Small city seats are unlabelled here; see the Pune city close-up." if district == "pune" else
                     " Small Nashik city seats are unlabelled at this scale." if small else ""))
    F.map_layout(fig, top=0.82, bottom=0.06)
    save(fig, f"R1_seats_{district}")


def seats_city(district="pune"):
    """Close-up of the city seats, where the district map is too small to read."""
    off, ours = official_seats(district), our_seats(district)
    off["fill"] = colour_seats(off, "AC_NO")
    ours["fill"] = colour_seats(ours, "constituency")
    ours["label"] = [f"{i}" for i in ours["constituency"]]
    box = (73.70, 18.40, 74.05, 18.72)
    fig, axes = plt.subplots(1, 2, figsize=(F.W, F.H))
    for ax, s, title in ((axes[0], off, "Official 2008 seats"), (axes[1], ours, "Our optimised seats")):
        s.plot(ax=ax, color=s["fill"], edgecolor="white", linewidth=1.2)
        for _, r in s.iterrows():
            p = r.geometry.intersection(F.gpd().GeoSeries([__import__("shapely").box(*box)]).iloc[0])
            if p.is_empty or p.area < 1e-5:
                continue
            q = p.representative_point()
            ax.annotate(str(r["label"]), (q.x, q.y), ha="center", va="center", fontsize=9, color=F.INK,
                        bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.65))
        ax.set_xlim(box[0], box[2])
        ax.set_ylim(box[1], box[3])
        ax.set_aspect(1 / np.cos(np.radians(18.55)))
        ax.set_axis_off()
        ax.set_anchor("N")
        ax.set_title(title, fontsize=13)
    F.suptitle(fig, "Pune and Pimpri-Chinchwad: city seats, official and redrawn",
               "Official seats follow ward lines; ours are built from 1 km population cells, so city edges are blockier")
    F.source(fig, "Official: DataMeet 2008 AC polygons. Ours: ReCom on Census 2011 villages and 1 km city cells (balanced weights).")
    F.map_layout(fig, top=0.84, bottom=0.05)
    save(fig, "R1_seats_pune_city")


# ---------------------------------------------------------------- polling stations and dispatch centres
def stations_map(district):
    loc = run_dir(district) / "location"
    sites = pd.read_csv(loc / "base/sites.csv")
    seats = pd.read_csv(loc / "base/seats.csv")
    hq = pd.read_csv(loc / "depot_hq/seats.csv")
    off = official_seats(district)
    fig, ax = plt.subplots(figsize=(F.W, F.H))
    off.plot(ax=ax, color="#F7F8FA", edgecolor=F.GREY, linewidth=0.9)
    sc = ax.scatter(sites["lon"], sites["lat"], s=np.clip(sites["stations"] * 3.5, 3, 80), c=sites["stations"].clip(upper=8),
                    cmap=mpl.colors.LinearSegmentedColormap.from_list("st", ["#7BA7E8", "#0B3D91"]), alpha=0.85,
                    edgecolor="none", zorder=3)
    moved = hq.merge(seats[["seat", "depot_lon", "depot_lat"]], on="seat", suffixes=("_hq", ""))
    moved = moved[(abs(moved["depot_lon_hq"] - moved["depot_lon"]) + abs(moved["depot_lat_hq"] - moved["depot_lat"])) > 0.005]
    ax.scatter(moved["depot_lon_hq"], moved["depot_lat_hq"], s=170, marker="*", facecolor="none", edgecolor=F.MUTED, linewidth=1.2, zorder=4)
    ax.scatter(seats["depot_lon"], seats["depot_lat"], s=260, marker="*", color=F.RED, edgecolor="white", linewidth=1.2, zorder=5)
    handles = [Line2D([], [], marker="o", ls="", color="#3B6FC9", markersize=6, label="polling premises (size and shade = stations in it)"),
               Line2D([], [], marker="*", ls="", color=F.RED, markersize=15, label="optimised dispatch centre (one per seat)"),
               Line2D([], [], marker="*", ls="", markerfacecolor="none", markeredgecolor=F.MUTED, markersize=13,
                      label=f"largest-town centre, where it differs ({len(moved)} seats)")]
    ax.legend(handles=handles, loc="lower left", frameon=True, facecolor="white", edgecolor="none", framealpha=0.9, fontsize=10.5)
    F.map_axis(ax, off, pad=0.02)
    F.map_layout(fig, top=0.86, bottom=0.04)
    F.suptitle(fig, f"{NAMES[district]}: {int(seats['stations'].sum()):,} polling stations in {len(sites):,} premises, "
                    f"from {len(seats)} dispatch centres",
               f"Optimised plan on 2024 rolls · 1,200 electors per station · 2 km walk · "
               f"{(seats['pct_over_2km'] * seats['electors']).sum() / seats['electors'].sum():.2f}% of electors walk over 2 km")
    F.source(fig, "Gurobi facility-location model per seat; premises at settlement centres from the 100 m population grid. Grey lines = official seats.")
    save(fig, f"R2_stations_{district}")


# ---------------------------------------------------------------- routes
def district_paths(district):
    """Road paths of every route in the district (cached)."""
    import pickle
    from scipy.sparse.csgraph import dijkstra
    from .network import Network
    cache = OUT / f"_paths_{district}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    loc = run_dir(district) / "location/base"
    sites = pd.read_csv(loc / "sites.csv")
    seats = pd.read_csv(loc / "seats.csv").set_index("seat")
    routes = pd.read_csv(loc / "routing/routes.csv")
    net = Network(district)
    lines = []
    for seat, rr in routes.groupby("seat"):
        ss = sites[sites["seat"] == seat].set_index("pid")
        lon = np.r_[seats.loc[seat, "depot_lon"], ss["lon"].to_numpy()]
        lat = np.r_[seats.loc[seat, "depot_lat"], ss["lat"].to_numpy()]
        local = net.local(lon, lat, pad_deg=0.05)
        nodes, _ = local.snap(lon, lat, "drive")
        pos = {p: i + 1 for i, p in enumerate(ss.index)}
        _, pred = dijkstra(local.drive_min, directed=False, indices=nodes, return_predecessors=True)
        for _, r in rr.iterrows():
            seq = [0] + [pos[p] for p in r["stops"].split(";") if p in pos]
            xs, ys = [], []
            for a, b in zip(seq[:-1], seq[1:]):
                out, v = [], nodes[b]
                while v >= 0 and v != nodes[a]:
                    out.append(v)
                    v = pred[a][v]
                out.append(nodes[a])
                out = out[::-1]
                xs += list(local.lon[out])
                ys += list(local.lat[out])
            lines.append((r["vehicle"], np.array(xs), np.array(ys)))
        print(f"  {district} seat {seat}: {len(rr)} routes", flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(lines))
    return lines


def routes_map(district):
    lines = district_paths(district)
    loc = run_dir(district) / "location/base"
    seats = pd.read_csv(loc / "seats.csv")
    rt = pd.read_csv(loc / "routing/seats.csv")
    off = official_seats(district)
    fig, ax = plt.subplots(figsize=(F.W, F.H))
    off.plot(ax=ax, color="#F7F8FA", edgecolor=F.GREY, linewidth=0.9)
    for veh, xs, ys in lines:
        if veh == "jeep":
            ax.plot(xs, ys, color=F.AMBER, linewidth=0.5, alpha=0.7)
    for veh, xs, ys in lines:
        if veh == "bus":
            ax.plot(xs, ys, color=F.BLUE, linewidth=0.6, alpha=0.6)
    ax.scatter(seats["depot_lon"], seats["depot_lat"], s=200, marker="*", color=F.RED, edgecolor="white", linewidth=1, zorder=5)
    handles = [Line2D([], [], color=F.BLUE, lw=2, label=f"bus routes ({int(rt['buses'].sum()):,} buses, up to 6 parties each)"),
               Line2D([], [], color=F.AMBER, lw=2, label=f"jeep routes ({int(rt['jeeps'].sum()):,} jeeps, 1 party)"),
               Line2D([], [], marker="*", ls="", color=F.RED, markersize=14, label="dispatch centre")]
    ax.legend(handles=handles, loc="upper left", frameon=True, facecolor="white", edgecolor="none", framealpha=0.9, fontsize=10.5)
    F.map_axis(ax, off, pad=0.02)
    F.map_layout(fig, top=0.86, bottom=0.04)
    F.suptitle(fig, f"{NAMES[district]}: every polling party's route to its station on the eve of the poll",
               f"{int(rt['stations'].sum()):,} parties · {int(rt['buses'].sum()):,} buses + {int(rt['jeeps'].sum()):,} jeeps · "
               f"{rt['vehicle_km'].sum():,.0f} vehicle-km out and back · last party in place by {rt['latest_arrival'].max()}")
    F.source(fig, "OR-Tools vehicle routing with time windows (leave 13:00, in place by 18:00), drawn on OpenStreetMap roads.")
    save(fig, f"R3_routes_{district}")


# ---------------------------------------------------------------- one seat close-ups
def catchments_map(district=F.SHOW_DISTRICT, seat=F.SHOW_SEAT, name=F.SHOW_SEAT_NAME):
    loc = run_dir(district) / "location/base"
    a = pd.read_csv(loc / "assignments.csv")
    a = a[a["seat"] == seat]
    pts = pd.read_csv(run_dir(district) / "demand_points.csv").set_index("pid")
    sites = pd.read_csv(loc / "sites.csv")
    sites = sites[sites["seat"] == seat]
    geo = F.gpd()
    off = geo.read_file(CODE / f"pilots/{district}/official_2008/assigned_units.geojson")
    units = off[off["ac08_no"] == seat]
    fig, ax = plt.subplots(figsize=(F.W, F.H))
    units.plot(ax=ax, color="#F7F8FA", edgecolor=F.LIGHT, linewidth=0.5)
    geo.GeoSeries([units.geometry.union_all()]).boundary.plot(ax=ax, color=F.INK, linewidth=1.1)
    cmap = mpl.colors.LinearSegmentedColormap.from_list("w", ["#9CC3A0", "#188038", "#E37400", "#D93025"])
    norm = mpl.colors.Normalize(0, 2.0)
    for _, r in a.sort_values("walk_km").iterrows():
        f, t = pts.loc[r["from"]], pts.loc[r["to"]]
        if r["from"] != r["to"]:
            ax.plot([f["lon"], t["lon"]], [f["lat"], t["lat"]], color=cmap(norm(r["walk_km"])), linewidth=0.9, alpha=0.85, zorder=2)
    ax.scatter(pts.loc[a["from"].unique(), "lon"], pts.loc[a["from"].unique(), "lat"], s=6, color=F.MUTED, zorder=3)
    ax.scatter(sites["lon"], sites["lat"], s=np.clip(sites["stations"] * 12, 12, 120), marker="^", color=F.INK, zorder=4)
    sm = mpl.cm.ScalarMappable(cmap=cmap, norm=norm)
    cax = fig.add_axes([0.70, 0.12, 0.22, 0.02])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_label("walk from home to station (km)", fontsize=10, color=F.MUTED)
    cb.outline.set_visible(False)
    F.map_axis(ax, units, pad=0.005)
    F.map_layout(fig, top=0.86, bottom=0.04)
    own = a[a["from"] == a["to"]]["electors"].sum() / a["electors"].sum()
    F.suptitle(fig, f"{name}: who votes where",
               f"Each line joins a hamlet to the station it is assigned to (▲, size = stations there) · "
               f"{100 * own:.0f}% of electors vote in their own hamlet, so no line is drawn for them")
    F.source(fig, "Assignments from the optimised location model, 2024 rolls, 1,200 cap, 2 km walk.")
    save(fig, "R4_catchments_karad_south")


def sectors_map(district=F.SHOW_DISTRICT, seat=F.SHOW_SEAT, name=F.SHOW_SEAT_NAME):
    import shapely
    loc = run_dir(district) / "location/base"
    sec = pd.read_csv(loc / "routing/sectors.csv")
    sec = sec[sec["seat"] == seat]
    sites = pd.read_csv(loc / "sites.csv").set_index("pid")
    sec = sec.join(sites[["lon", "lat"]], on="pid")
    geo = F.gpd()
    off = geo.read_file(CODE / f"pilots/{district}/official_2008/assigned_units.geojson")
    units = off[off["ac08_no"] == seat]
    hulls = []
    for base, g in sec.groupby("sector_base"):
        pts = shapely.multipoints(np.c_[g["lon"], g["lat"]])
        hulls.append({"base": base, "geometry": shapely.convex_hull(pts).buffer(0.004)})
    hg = geo.GeoDataFrame(hulls, geometry="geometry", crs=4326)
    hg["fill"] = colour_seats(hg, "base")
    colour = dict(zip(hg["base"], hg["fill"]))
    fig, ax = plt.subplots(figsize=(F.W, F.H))
    geo.GeoSeries([units.geometry.union_all()]).boundary.plot(ax=ax, color=F.INK, linewidth=1.1)
    hg.plot(ax=ax, color=hg["fill"], alpha=0.45, edgecolor="white", linewidth=0.8)
    for base, g in sec.groupby("sector_base"):
        b = g[g["pid"] == base].iloc[0]
        for _, r in g.iterrows():
            ax.plot([b["lon"], r["lon"]], [b["lat"], r["lat"]], color=F.MUTED, linewidth=0.5, alpha=0.7)
        ax.scatter(g["lon"], g["lat"], s=18, color=colour[base], edgecolor=F.INK, linewidth=0.4, zorder=3)
        ax.scatter([b["lon"]], [b["lat"]], s=120, marker="P", color=F.INK, zorder=4)
    rt = pd.read_csv(loc / "routing/seats.csv").set_index("seat").loc[seat]
    handles = [Line2D([], [], marker="P", ls="", color=F.INK, markersize=10, label="sector officer's base"),
               Line2D([], [], marker="o", ls="", color="#A8C5F0", markeredgecolor=F.INK, markersize=7, label="polling premises in the sector")]
    ax.legend(handles=handles, loc="lower left", frameon=True, facecolor="white", edgecolor="none", framealpha=0.9)
    F.map_axis(ax, units, pad=0.005)
    F.map_layout(fig, top=0.86, bottom=0.04)
    F.suptitle(fig, f"{name}: {int(rt['sectors'])} sectors, each covered by one sector officer",
               f"At most 12 premises per sector (ECI handbook) · officer base chosen to minimise driving time · "
               f"response time: mean {rt['mean_response_min']:.0f} min, 90th percentile {rt['p90_response_min']:.0f} min")
    F.source(fig, "Capacitated p-median (Gurobi) on road driving times. The officer carries spare EVMs and VVPATs on polling day.")
    save(fig, "R5_sectors_karad_south")


# ---------------------------------------------------------------- polling day by station
def waits_map(district="sangli"):
    loc = run_dir(district) / "location"
    for tag in ("base", "cap1500"):
        f = loc / tag / "simulation" / "stations_map40.csv"
        if not f.exists():
            subprocess.run([sys.executable, "-m", "logistics.simulate", "--district", district, "--tag", tag, "--reps", "20",
                            "--service-s", "40", "--no-balk", "--label", "map40"], cwd=CODE, check=True, capture_output=True)
    off = official_seats(district)
    fig, axes = plt.subplots(1, 2, figsize=(F.W, 5.6))
    cmap = mpl.colors.LinearSegmentedColormap.from_list("q", ["#B5DDA4", "#F3E08A", "#F2A19A", "#B3261E"])
    shares = {}
    norm = mpl.colors.Normalize(0, 60)
    for ax, tag, title in ((axes[0], "cap1500", "1,500 electors per station"), (axes[1], "base", "1,200 electors per station")):
        st = pd.read_csv(loc / tag / "simulation" / "stations_map40.csv")
        sites = pd.read_csv(loc / tag / "sites.csv").set_index("pid")
        g = st.groupby("pid").agg(p95=("p95_wait", "max"), n=("station", "count")).join(sites[["lon", "lat"]])
        off.plot(ax=ax, color="#F7F8FA", edgecolor=F.GREY, linewidth=0.8)
        g = g.sort_values("p95")
        ax.scatter(g["lon"], g["lat"], s=np.clip(g["n"] * 6, 6, 70), c=g["p95"], cmap=cmap, norm=norm, edgecolor="none", zorder=3)
        share = (st["p95_wait"] > 30).mean()
        shares[tag] = share
        ax.set_title(f"{title}\n{100 * share:.0f}% of stations have a 95th-percentile wait over 30 min", fontsize=12.5)
        F.map_axis(ax, off, pad=0.01)
    sm = mpl.cm.ScalarMappable(cmap=cmap, norm=norm)
    cax = fig.add_axes([0.3, 0.13, 0.4, 0.028])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal", extend="max")
    cb.set_label("95th-percentile wait at the station (min)", fontsize=10.5, color=F.MUTED)
    cb.outline.set_visible(False)
    F.map_layout(fig, top=0.76, bottom=0.24)
    F.suptitle(fig, f"{NAMES[district]} on polling day: long queues at {100 * shares['cap1500']:.0f}% of stations at 1,500, "
                    f"{100 * shares['base']:.0f}% at 1,200",
               "Simulated waits at every polling premises (worst station in the building) · 40 s per voter · 20 simulated days")
    F.source(fig, "Monte Carlo queue simulation on the optimised station plan for each cap; arrivals follow 2024's hourly turnout.")
    save(fig, f"R6_waits_{district}")


# ---------------------------------------------------------------- Lok Sabha
def loksabha_map():
    g = F.statewide()
    bal = pd.read_csv(RUNS / "representation/pc_balanced/pc_assignment.csv")[["ac", "pc"]]
    g = g.merge(bal.rename(columns={"ac": "AC_NO", "pc": "pc_bal"}), on="AC_NO", how="left")
    off = g.dissolve(by="PC_NO").reset_index()
    new = g.dissolve(by="pc_bal").reset_index()
    pcs = pd.read_csv(RUNS / "representation/lok_sabha_pcs.csv")
    rule = pcs[pcs["plan"] == "balanced_2024"].set_index("pc")["rule"].fillna("")
    reserved_off = {"AMRAVATI": "SC", "RAMTEK": "SC", "SHIRDI": "SC", "LATUR": "SC", "SOLAPUR": "SC",
                    "NANDURBAR": "ST", "GADCHIROLI-CHIMUR": "ST", "DINDORI": "ST", "PALGHAR": "ST"}
    off["res"] = off["PC_NAME"].str.replace(r"\s*\((SC|ST)\)", "", regex=True).str.strip().str.upper().map(reserved_off).fillna("")
    new["res"] = new["pc_bal"].map(rule).fillna("")
    off["fill"] = colour_seats(off, "PC_NO")
    new["fill"] = colour_seats(new, "pc_bal")
    fig, axes = plt.subplots(1, 2, figsize=(F.W, F.H))
    for ax, d, title in ((axes[0], off, "Official 48 Lok Sabha seats"), (axes[1], new, "Our regrouping on 2024 rolls")):
        d.plot(ax=ax, color=d["fill"], edgecolor="white", linewidth=0.9)
        g.boundary.plot(ax=ax, color="white", linewidth=0.2)
        res = d[d["res"] != ""]
        res.boundary.plot(ax=ax, color=F.INK, linewidth=1.6)
        for _, r in res.iterrows():
            p = r.geometry.representative_point()
            ax.annotate(r["res"], (p.x, p.y), ha="center", va="center", fontsize=8.5, fontweight="bold", color=F.INK,
                        bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.8))
        ax.set_title(title, fontsize=13)
        F.map_axis(ax, d)
    F.map_layout(fig, top=0.82, bottom=0.05)
    F.suptitle(fig, "Lok Sabha seats: the official 48 and our regrouping of the same Assembly seats",
               "Each colour is one Lok Sabha seat of 6 Assembly seats · thick outline + label = reserved seat (5 SC, 4 ST)")
    F.source(fig, "Regrouping: set-partitioning MIP on 2024 electors; reservation by the Delimitation Act's share rule on Census 2011 SC/ST counts.")
    save(fig, "R7_lok_sabha_seats")


MAPS = {
    "seats": lambda: [seats_map(d) for d in NAMES],
    "city": seats_city,
    "stations": lambda: [stations_map(d) for d in NAMES],
    "routes": lambda: [routes_map(d) for d in NAMES],
    "catchments": catchments_map, "sectors": sectors_map,
    "waits": waits_map, "loksabha": loksabha_map,
}


def main():
    for n in sys.argv[1:] or list(MAPS):
        try:
            MAPS[n]()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"FAILED {n}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
