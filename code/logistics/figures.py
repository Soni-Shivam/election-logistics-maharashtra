"""Slide figures for the presentation: maps and charts from every model's results.

One look across the deck: grey for context, one blue for "optimised / ours", red only for
"breaks a rule" (over 2 km, over ±10%), a blue-grey-red diverging scale for seat deviation.
Each figure's title states its finding. Images go to ``figures/`` at 16:9, 200 dpi.

Usage:
    python -m logistics.figures                # all figures
    python -m logistics.figures drift recom    # some of them
"""

from __future__ import annotations

import json
import math
import sys

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from .common import CODE, ROOT, RUNS, read_geojson, run_dir  # noqa: E402

OUT = ROOT / "figures"
INK, MUTED, GREY, LIGHT = "#202124", "#5F6368", "#BDC1C6", "#ECEFF1"
BLUE, RED, GREEN, AMBER = "#1A73E8", "#D93025", "#188038", "#E37400"
DEV = LinearSegmentedColormap.from_list("dev", ["#1A56A8", "#8FB3E0", "#F1F3F4", "#F2A19A", "#B3261E"])
DEV_NORM = TwoSlopeNorm(vmin=-40, vcenter=0, vmax=40)
W, H = 13.33, 7.5  # inches, 16:9

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 12, "axes.titlesize": 17, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.titlepad": 14, "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": GREY, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "text.color": INK, "savefig.dpi": 200, "savefig.bbox": "tight", "savefig.facecolor": "white",
})

DISTRICTS = ["sangli", "satara", "nashik", "pune"]
SHOW_DISTRICT, SHOW_SEAT, SHOW_SEAT_NAME = "satara", 260, "Karad South"


def save(fig, name: str) -> None:
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / f"{name}.png")
    plt.close(fig)
    print("wrote", name)


def suptitle(fig, title: str, sub: str | None = None) -> None:
    fig.text(0.012, 0.975, title, fontsize=19, fontweight="bold", va="top", color=INK)
    if sub:
        fig.text(0.012, 0.925, sub, fontsize=12.5, va="top", color=MUTED)


def source(fig, text: str) -> None:
    fig.text(0.012, 0.01, text, fontsize=9.5, color=MUTED, va="bottom")


def map_axis(ax, gdf=None, pad=0.0):
    ax.set_axis_off()
    ax.set_anchor("N")
    if gdf is not None:
        x0, y0, x1, y1 = gdf.total_bounds
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)
        ax.set_aspect(1 / math.cos(math.radians((y0 + y1) / 2)))


def map_layout(fig, top=0.84, bottom=0.2):
    fig.subplots_adjust(top=top, bottom=bottom, left=0.01, right=0.99, wspace=0.03)


def dev_colorbar(fig, ax, label="Electors vs the district's average seat"):
    sm = mpl.cm.ScalarMappable(cmap=DEV, norm=DEV_NORM)
    cax = fig.add_axes([0.22, 0.115, 0.56, 0.022])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal", extend="both")
    cb.set_ticks([-40, -20, -10, 0, 10, 20, 40])
    cb.set_ticklabels(["−40%", "−20%", "−10%", "fair", "+10%", "+20%", "+40%"])
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=10, colors=MUTED)
    cb.set_label(label + "   (blue: each vote counts more · red: each vote counts less)", fontsize=10.5, color=MUTED)


# ---------------------------------------------------------------- data helpers
def statewide():
    from .representation import statewide_acs
    g = statewide_acs()
    e = pd.read_csv(CODE / "analysis/official_audit/electors_by_seat.csv")
    return g.merge(e[["ac_no", "dev_2009_pct", "dev_2024_pct", "electors_2024"]].rename(columns={"electors_2024": "e24"}),
                   left_on="AC_NO", right_on="ac_no", how="left")


def gpd():
    import geopandas
    return geopandas


def units_gdf(district: str):
    from .common import DISTRICTS as D
    return gpd().read_file(D[district]["units"])


# ---------------------------------------------------------------- section 1 / title
def fig_title_map():
    g = statewide()
    dist = g.dissolve(by="district_2008").reset_index()
    fig, ax = plt.subplots(figsize=(W, H))
    g.plot(ax=ax, column="dev_2024_pct", cmap=DEV, norm=DEV_NORM, edgecolor="white", linewidth=0.25)
    dist.boundary.plot(ax=ax, color="white", linewidth=1.0)
    study = dist[dist["district_2008"].isin(["Sangli", "Satara", "Nashik", "Pune"])]
    study.boundary.plot(ax=ax, color=INK, linewidth=1.8)
    for _, r in study.iterrows():
        p = r.geometry.representative_point()
        ax.annotate(r["district_2008"], (p.x, p.y), fontsize=12, fontweight="bold", color=INK, ha="center",
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.8))
    map_axis(ax, g)
    map_layout(fig, top=0.99, bottom=0.15)
    dev_colorbar(fig, ax)
    save(fig, "01_title_map")


# ---------------------------------------------------------------- section 3: constituencies
def fig_drift_statewide():
    g = statewide()
    dist = g.dissolve(by="district_2008").reset_index()
    fig, axes = plt.subplots(1, 2, figsize=(W, 6.9))
    for ax, col, yr in ((axes[0], "dev_2009_pct", 2009), (axes[1], "dev_2024_pct", 2024)):
        g.plot(ax=ax, column=col, cmap=DEV, norm=DEV_NORM, edgecolor="white", linewidth=0.2)
        dist.boundary.plot(ax=ax, color="white", linewidth=0.8)
        n10 = int((g[col].abs() > 10).sum())
        n20 = int((g[col].abs() > 20).sum())
        ax.set_title(f"{yr}:  {n10} seats off by >10%,  {n20} by >20%", fontsize=14, loc="left")
        map_axis(ax, g)
    big = g.loc[g["e24"].idxmax()]
    small = g.loc[g["e24"].idxmin()]
    suptitle(fig, "The 2008 map has drifted: a third of seats are now more than 10% off",
             f"Registered electors per seat vs its district's average seat · largest {big['AC_NAME']} "
             f"{big['e24']:,.0f}, smallest {small['AC_NAME']} {small['e24']:,.0f} ({big['e24'] / small['e24']:.1f}×)")
    map_layout(fig)
    dev_colorbar(fig, axes, label="Electors vs the district's average seat")
    source(fig, "Source: ECI results via Wikipedia constituency pages (2009, 2024); seat polygons DataMeet. Same boundaries in both years.")
    save(fig, "03b_drift_statewide")


def fig_pune_then_now():
    g = statewide()
    p = g[g["district_2008"] == "Pune"].copy()
    fig, axes = plt.subplots(1, 2, figsize=(W, H))
    labels = {205: ("Chinchwad", (-150, 55)), 207: ("Bhosari", (170, 25)), 213: ("Hadapsar", (110, -55)),
              215: ("Kasba Peth", (-150, -45)), 196: ("Ambegaon", (0, 0))}
    for ax, col, yr in ((axes[0], "dev_2009_pct", 2009), (axes[1], "dev_2024_pct", 2024)):
        p.plot(ax=ax, column=col, cmap=DEV, norm=DEV_NORM, edgecolor="white", linewidth=0.6)
        for ac, (name, off) in labels.items():
            r = p[p["AC_NO"] == ac].iloc[0]
            pt = r.geometry.representative_point()
            ax.annotate(f"{name}\n{r[col]:+.0f}%".replace("-", "−"), (pt.x, pt.y), xytext=off, textcoords="offset points",
                        fontsize=10, ha="center", va="center", color=INK, fontweight="bold" if ac in (205, 215) else "normal",
                        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=GREY if off != (0, 0) else "none", alpha=0.9),
                        arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8) if off != (0, 0) else None)
        ratio = p["e24" if yr == 2024 else "electors_2009"].max() / p["e24" if yr == 2024 else "electors_2009"].min()
        ax.set_title(f"{yr}:  largest seat = {ratio:.1f}× the smallest", fontsize=14, loc="left")
        map_axis(ax, p)
    suptitle(fig, "Pune's city-edge seats filled up while the old city stood still",
             "Same 21 boundaries in 2009 and 2024 · electors per seat vs Pune's average seat")
    map_layout(fig)
    dev_colorbar(fig, axes)
    source(fig, "Source: ECI results via Wikipedia constituency pages; seat polygons DataMeet.")
    save(fig, "03c_pune_then_now")


def fig_official_vs_ours():
    geo = gpd()
    units = units_gdf("pune")
    talukas = units.dissolve(by="taluka").reset_index()
    maps = {"Official 2008 seats": CODE / "pilots/pune/official_2008/constituencies.geojson",
            "Our optimised seats": CODE / "pilots/pune/runs/balanced_s714/constituencies.geojson"}
    mc = pd.read_csv(CODE / "analysis/official_audit/map_comparison.csv")
    mc = mc[mc["district"] == "Pune"].set_index("map")
    fig, axes = plt.subplots(1, 2, figsize=(W, H))
    target = units["population"].sum() / 21
    for ax, (title, path) in zip(axes, maps.items()):
        c = geo.read_file(path)
        c["dev"] = 100 * (c["population"] / target - 1)
        c.plot(ax=ax, column="dev", cmap=DEV, norm=DEV_NORM, edgecolor="white", linewidth=0.8)
        talukas.boundary.plot(ax=ax, color=INK, linewidth=0.7, linestyle=(0, (3, 2)), alpha=0.7)
        key = "official_2008" if "Official" in title else "balanced_s714"
        r = mc.loc[key]
        ax.set_title(f"{title}\n{int(r['seats_outside_10pct'])} of 21 seats off ±10% · largest ÷ smallest "
                     f"{r['size_ratio']:.2f}\n{int(r['talukas_split'])} talukas split", fontsize=12, loc="left")
        map_axis(ax, c)
    suptitle(fig, "Our seats are all within ±10%; the price is cutting more talukas",
             "Pune district, Census 2011 population on the same village and ward units · dashed lines = taluka boundaries")
    map_layout(fig, top=0.8)
    dev_colorbar(fig, axes, label="Population vs the target seat (Census 2011)")
    source(fig, "City seats of the official map are rebuilt approximately from 1 km cells. Our map: ReCom + simulated annealing, balanced weights.")
    save(fig, "03d_official_vs_ours_pune")


def fig_tradeoff():
    mc = pd.read_csv(CODE / "analysis/official_audit/map_comparison.csv")
    fig, axes = plt.subplots(1, 4, figsize=(W, H * 0.72), sharey=False)
    marker = {"balanced": "o", "locality": "s", "taluka": "^"}
    for ax, d in zip(axes, ["Sangli", "Satara", "Nashik", "Pune"]):
        m = mc[mc["district"] == d]
        o = m[m["kind"] == "official"].iloc[0]
        for k, mk in marker.items():
            r = m[m["kind"] == k]
            ax.scatter(r["mean_abs_dev_pct"], r["talukas_split"], s=70, marker=mk, color=BLUE, alpha=0.85,
                       edgecolor="white", linewidth=0.8, label=f"ours, {k} weights")
        ax.scatter([o["mean_abs_dev_pct"]], [o["talukas_split"]], s=140, marker="D", color=INK, zorder=3, label="official 2008")
        ax.annotate("official", (o["mean_abs_dev_pct"], o["talukas_split"]), xytext=(6, 6), textcoords="offset points", fontsize=10)
        ax.axvline(10, color=RED, linewidth=1, linestyle=(0, (4, 3)))
        ax.set_title(d, fontsize=14)
        ax.set_xlabel("mean seat-size deviation (%)")
        ax.set_xlim(0, max(12, o["mean_abs_dev_pct"] * 1.15))
        ax.set_ylim(0, m["talukas_split"].max() * 1.25)
        ax.grid(axis="both", color=LIGHT)
        ax.yaxis.set_major_locator(mpl.ticker.MaxNLocator(integer=True))
    axes[0].set_ylabel("talukas split")
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h[:4], l[:4], loc="lower center", ncol=4, frameon=False, bbox_to_anchor=(0.5, -0.06))
    suptitle(fig, "Every optimised map is fairer, but splits 1.2–4.7× as many talukas",
             "Each dot is one map (Census 2011) · left = more equal seats, low = more talukas kept whole · red line = ±10% average")
    fig.subplots_adjust(top=0.78, wspace=0.35)
    save(fig, "03e_fairness_tradeoff")


def fig_loksabha():
    g = statewide()
    pcs = pd.read_csv(RUNS / "representation/lok_sabha_pcs.csv")
    bal = pd.read_csv(RUNS / "representation/pc_balanced/pc_assignment.csv")[["ac", "pc"]]
    g2 = g.merge(bal.rename(columns={"ac": "AC_NO", "pc": "pc_bal"}), on="AC_NO", how="left")
    off = g2.dissolve(by="PC_NO", aggfunc={"e24": "sum"}).reset_index()
    new = g2.dissolve(by="pc_bal", aggfunc={"e24": "sum"}).reset_index()
    fig, axes = plt.subplots(1, 2, figsize=(W, H))
    summ = pd.read_csv(RUNS / "representation/lok_sabha_summary.csv").set_index("plan")
    for ax, d, key, title in ((axes[0], off, "official", "Official 48 Lok Sabha seats"),
                              (axes[1], new, "balanced_2024", "Same Assembly seats, regrouped on 2024 rolls")):
        d["dev"] = 100 * (d["e24"] / d["e24"].mean() - 1)
        d.plot(ax=ax, column="dev", cmap=DEV, norm=DEV_NORM, edgecolor="white", linewidth=0.7)
        r = summ.loc[key]
        ax.set_title(f"{title}\nlargest ÷ smallest {r['size_ratio_2024']:.2f} · {int(r['outside_10pct_2024'])} of 48 off ±10%",
                     fontsize=12.5, loc="left")
        map_axis(ax, d)
    p = pcs[pcs["plan"] == "official"].copy()
    p["name"] = p["name"].str.replace("?", "–", regex=False).str.replace("SINDHUDUR$", "SINDHUDURG", regex=True)
    big, small = p.loc[p["electors_2024"].idxmax()], p.loc[p["electors_2024"].idxmin()]
    suptitle(fig, "Regrouping narrows Lok Sabha imbalance, but cannot close it without new Assembly seats",
             f"Electors per Lok Sabha seat vs the state average, 2024 · official largest {big['name'].title()} {big['electors_2024'] / 1e5:.1f} lakh, "
             f"smallest {small['name'].title()} {small['electors_2024'] / 1e5:.1f} lakh")
    map_layout(fig, top=0.8)
    dev_colorbar(fig, axes, label="Electors vs the state's average Lok Sabha seat")
    source(fig, "Regrouping: set-partitioning MIP over connected groups of 6 Assembly seats (group_lok_sabha.py).")
    save(fig, "03f_lok_sabha")


def fig_recom_mechanics():
    """Three panels on real Sangli units: two seats, the random spanning tree, the cut."""
    import networkx as nx
    import shapely
    geo = gpd()
    a = geo.read_file(CODE / "sangli_road/runs/taluka_s303/assigned_units.geojson")
    a = a[a["population"] > 0].reset_index(drop=True)
    seats = a.groupby("constituency")["population"].sum()
    target = a["population"].sum() / 8
    pair = (3, 4)
    for s1 in seats.index:
        for s2 in seats.index:
            if s1 < s2 and a[a["constituency"] == s1].geometry.union_all().touches(a[a["constituency"] == s2].geometry.union_all()):
                pair = (s1, s2)
                break
        else:
            continue
        break
    m = a[a["constituency"].isin(pair)].reset_index(drop=True)
    pts = m.geometry.representative_point()
    tree_idx = shapely.STRtree(list(m.geometry))
    G = nx.Graph()
    G.add_nodes_from(range(len(m)))
    for i, gi in enumerate(m.geometry):
        for j in tree_idx.query(gi, predicate="intersects"):
            if j > i and gi.boundary.intersection(m.geometry[j].boundary).length > 1e-6:
                G.add_edge(i, int(j))
    rng = np.random.default_rng(7)
    pop = m["population"].to_numpy()
    for _ in range(200):
        for u, v in G.edges:
            G[u][v]["w"] = rng.random()
        T = nx.minimum_spanning_tree(G, weight="w")
        best = None
        for u, v in T.edges:
            T2 = T.copy()
            T2.remove_edge(u, v)
            side = nx.node_connected_component(T2, u)
            ps = pop[list(side)].sum()
            dev = abs(ps / target - 1)
            if dev < 0.10 and abs((pop.sum() - ps) / target - 1) < 0.10 and (best is None or dev < best[0]):
                best = (dev, u, v, side)
        if best:
            break
    _, cu, cv, side = best
    fig, axes = plt.subplots(1, 3, figsize=(W, 5.2))
    xy = np.c_[pts.x, pts.y]
    colA, colB = "#8FB3E0", "#F2C38B"
    # 1: two neighbouring seats
    m.plot(ax=axes[0], color=[colA if c == pair[0] else colB for c in m["constituency"]], edgecolor="white", linewidth=0.4)
    axes[0].set_title("1. Two neighbouring seats", fontsize=13)
    # 2: merge + random spanning tree
    m.plot(ax=axes[1], color=LIGHT, edgecolor="white", linewidth=0.4)
    for u, v in T.edges:
        axes[1].plot(xy[[u, v], 0], xy[[u, v], 1], color=MUTED, linewidth=0.7)
    axes[1].scatter(xy[:, 0], xy[:, 1], s=6, color=INK, zorder=3)
    axes[1].set_title("2. Merge; draw a random\nspanning tree", fontsize=13)
    # 3: cut one edge -> two balanced seats
    m.plot(ax=axes[2], color=[BLUE if i in side else AMBER for i in range(len(m))], alpha=0.55, edgecolor="white", linewidth=0.4)
    for u, v in T.edges:
        if {u, v} != {cu, cv}:
            axes[2].plot(xy[[u, v], 0], xy[[u, v], 1], color=MUTED, linewidth=0.6)
    axes[2].plot(xy[[cu, cv], 0], xy[[cu, cv], 1], color=RED, linewidth=3.5, zorder=4)
    axes[2].annotate("cut here", xy[cu], xytext=(10, 14), textcoords="offset points", color=RED, fontweight="bold")
    p1 = pop[list(side)].sum()
    axes[2].set_title("3. Cut one edge:\ntwo new seats", fontsize=13)
    axes[2].text(0.0, -0.08, f"new seats: {100 * (p1 / target - 1):+.1f}% and {100 * ((pop.sum() - p1) / target - 1):+.1f}% "
                 "of the target, both within ±10%".replace("-", "−"), transform=axes[2].transAxes, fontsize=11, color=MUTED)
    for ax in axes:
        map_axis(ax, m)
    suptitle(fig, "ReCom redraws two seats at a time, keeping both connected and balanced",
             "Real Sangli villages · repeated thousands of times; simulated annealing keeps changes that improve the score")
    fig.subplots_adjust(top=0.78, bottom=0.1, left=0.01, right=0.99, wspace=0.05)
    save(fig, "03a_recom_mechanics")


# ---------------------------------------------------------------- section 4: polling stations
def fig_village_zoom():
    import shapely
    from .demand import DENSE_SHARE, medoid, pieces_for_unit, read_grid
    from .common import local_xy_km
    units = units_gdf(SHOW_DISTRICT)
    pts = pd.read_csv(run_dir(SHOW_DISTRICT) / "demand_points.csv")
    cand = pts[(pts["ac08_no"] == SHOW_SEAT) & (pts["kind"] == "VILLAGE")].groupby("unit_id").size().sort_values(ascending=False)
    uid = cand.index[3]
    u = units[units["id"] == uid].iloc[0]
    glon, glat, gpop, cell = read_grid(SHOW_DISTRICT)
    inside = shapely.contains_xy(u.geometry, glon, glat)
    lon, lat, w = glon[inside], glat[inside], gpop[inside]
    keep = w >= DENSE_SHARE * w.max()
    xy = local_xy_km(lon[keep], lat[keep], float(lat.mean()))
    el = float(pts.loc[pts["unit_id"] == uid, "electors_2024"].sum())
    pieces = pieces_for_unit(xy, w[keep], el, 0.25)
    fig, axes = plt.subplots(1, 2, figsize=(W, H))
    gs = gpd().GeoSeries([u.geometry])
    s = 2.2
    # left: the old model
    for ax in axes:
        gs.boundary.plot(ax=ax, color=INK, linewidth=1.2)
    axes[0].scatter(lon, lat, c=w, cmap="Greys", s=s * 4, marker="s", vmin=0, vmax=np.quantile(w, 0.98))
    axes[0].scatter([u["demand_lon"]], [u["demand_lat"]], s=260, color=RED, marker="o", edgecolor="white", linewidth=1.5, zorder=4)
    axes[0].annotate("old model: everyone lives here,\nwalk inside the village = 0 km", (u["demand_lon"], u["demand_lat"]),
                     xytext=(14, -40), textcoords="offset points", color=RED, fontsize=11)
    axes[0].set_title("WorldPop 100 m cells (darker = more people)", fontsize=12.5)
    # right: settlements + medoids
    palette = plt.cm.tab10.colors
    lk, ltk = lon[keep], lat[keep]
    total_walk = []
    for j, idx in enumerate(pieces):
        c = palette[j % 10]
        axes[1].scatter(lk[idx], ltk[idx], s=s * 4, marker="s", color=c, alpha=0.75)
        m = medoid(xy[idx], w[keep][idx])
        d = np.linalg.norm(xy[idx] - xy[idx][m], axis=1)
        ik = (float(d @ w[keep][idx] / w[keep][idx].sum()) + 0.383 * cell) * 1.25
        total_walk.append(ik)
        axes[1].scatter([lk[idx][m]], [ltk[idx][m]], s=150, marker="*", color=INK, edgecolor="white", linewidth=0.8, zorder=4)
        share = w[keep][idx].sum() / w[keep].sum() * el
        axes[1].annotate(f"{share:,.0f}", (lk[idx][m], ltk[idx][m]), xytext=(7, 4), textcoords="offset points",
                         fontsize=9.5, color=INK, fontweight="bold",
                         bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75))
    axes[1].set_title(f"New model: {len(pieces)} settlements, each with its own centre (★)", fontsize=12.5)
    axes[1].text(0.0, -0.04, f"Labels = 2024 electors per settlement · walk inside a settlement: "
                 f"{min(total_walk):.2f}–{max(total_walk):.2f} km (the old model said 0)",
                 transform=axes[1].transAxes, fontsize=11, color=MUTED, va="top")
    for ax in axes:
        map_axis(ax, gs, pad=0.003)
    suptitle(fig, "Villages are not one point: voters live in several settlements, some far from the centre",
             f"{u['name']} village, {SHOW_SEAT_NAME} seat · settlement = cells ≥ 40% of the village's peak density · "
             "walk inside = mean distance to its centre × 1.25")
    map_layout(fig, top=0.84, bottom=0.1)
    source(fig, "Source: WorldPop 2011 100 m population grid; Census 2011 village polygons (SHRUG); electors scaled to the 2024 roll.")
    save(fig, "04a_village_zoom")


def seat_walks(district: str, scenario: str, seat: int) -> pd.DataFrame:
    a = pd.read_csv(run_dir(district) / "location" / scenario / "assignments.csv")
    a = a[a["seat"] == seat]
    a["ew"] = a["electors"] * a["walk_km"]
    g = a.groupby("from").agg(electors=("electors", "sum"), ew=("ew", "sum"), worst=("walk_km", "max")).reset_index()
    g["walk"] = g["ew"] / g["electors"]
    return g


def fig_access_before_after():
    geo = gpd()
    pts = pd.read_csv(run_dir(SHOW_DISTRICT) / "demand_points.csv")
    off = geo.read_file(CODE / f"pilots/{SHOW_DISTRICT}/official_2008/assigned_units.geojson")
    seat_units = off[off["ac08_no"] == SHOW_SEAT]
    outline = geo.GeoSeries([seat_units.geometry.union_all()])
    fig, axes = plt.subplots(1, 2, figsize=(W, H))
    rows = {}
    for ax, scen, title in ((axes[0], "village_rule", "One polling centre per village (rule of thumb)"),
                            (axes[1], "base", "Optimised: stations placed in hamlets")):
        wv = seat_walks(SHOW_DISTRICT, scen, SHOW_SEAT).merge(pts[["pid", "lon", "lat"]], left_on="from", right_on="pid")
        sites = pd.read_csv(run_dir(SHOW_DISTRICT) / "location" / scen / "sites.csv")
        sites = sites[sites["seat"] == SHOW_SEAT]
        seat_units.plot(ax=ax, color="none", edgecolor=LIGHT, linewidth=0.4)
        outline.boundary.plot(ax=ax, color=INK, linewidth=1.2)
        far = wv["worst"] > 2.0
        ax.scatter(wv.loc[~far, "lon"], wv.loc[~far, "lat"], s=np.clip(wv.loc[~far, "electors"] / 25, 4, 120), color=GREEN, alpha=0.55, edgecolor="none")
        ax.scatter(wv.loc[far, "lon"], wv.loc[far, "lat"], s=np.clip(wv.loc[far, "electors"] / 25, 8, 140), color=RED, alpha=0.9, edgecolor="white", linewidth=0.4)
        ax.scatter(sites["lon"], sites["lat"], s=16, marker="^", color=INK, zorder=4)
        seats = pd.read_csv(run_dir(SHOW_DISTRICT) / "location" / scen / "seats.csv").set_index("seat").loc[SHOW_SEAT]
        rows[scen] = seats
        ax.set_title(f"{title}\n{int(seats['stations'])} stations · {seats['pct_over_2km']:.1f}% of electors walk over 2 km",
                     fontsize=12.5)
        map_axis(ax, outline, pad=0.01)
    handles = [Line2D([], [], marker="o", ls="", color=GREEN, label="hamlet within 2 km of its station", markersize=9, alpha=0.7),
               Line2D([], [], marker="o", ls="", color=RED, label="hamlet over 2 km away", markersize=9),
               Line2D([], [], marker="^", ls="", color=INK, label="polling station site", markersize=8)]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.01))
    map_layout(fig, top=0.8, bottom=0.08)
    v, b = rows["village_rule"], rows["base"]
    suptitle(fig, f"{SHOW_SEAT_NAME}: placing stations by hamlet takes {v['pct_over_2km']:.0f}% of voters off long walks, with fewer stations",
             "Dot size = electors in the hamlet · walk = inside the hamlet + along roads and footpaths to the station")
    save(fig, "04b_access_before_after")


def fig_walk_cdf():
    scen = [("village_rule", "One centre per village", RED, "-"), ("no_intra", "Old model (0 km inside village)", AMBER, "-"),
            ("walk3", "3 km walking limit", GREY, "-"), ("base", "Optimised (2 km, hamlets)", BLUE, "-")]
    fig, ax = plt.subplots(figsize=(W, H * 0.85))
    ytxt = iter([62, 55, 48, 41])
    ax.text(2.55, 69, "electors walking over 2 km", fontsize=11, color=MUTED, fontweight="bold")
    for key, label, color, ls in scen:
        a = pd.concat([pd.read_csv(run_dir(d) / "location" / key / "assignments.csv") for d in DISTRICTS])
        a = a.sort_values("walk_km")
        cum = a["electors"].cumsum() / a["electors"].sum()
        ax.plot(a["walk_km"], 100 * cum, color=color, linewidth=2.6 if key == "base" else 1.8, label=label, linestyle=ls)
        over = 100 * a.loc[a["walk_km"] > 2, "electors"].sum() / a["electors"].sum()
        ax.text(2.55, next(ytxt), f"{over:4.1f}%   {label}", color=color, fontsize=11,
                fontweight="bold" if key == "base" else "normal")
    ax.axvline(2, color=INK, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(2.03, 3, "ECI norm: 2 km", color=INK, fontsize=10.5)
    ax.set_xlim(0, 4)
    ax.set_ylim(0, 101)
    ax.set_xlabel("walk from home to polling station (km)")
    ax.set_ylabel("electors within this distance (%)")
    ax.grid(color=LIGHT)
    ax.set_title("Optimising where stations go puts 99.6% of 1.9 crore electors within 2 km")
    source(fig, "All four districts, 2024 electors, 1,200 cap · walk includes the walk inside the voter's own settlement.")
    save(fig, "04c_walk_distribution")


def fig_validation():
    v = pd.read_csv(RUNS / "summary/validation.csv")
    keep = [("polling stations", "Pune district", "Pune district stations"),
            ("polling stations", "Nashik district", "Nashik district stations (2 km rule)"),
            ("polling stations, seat-calibrated rolls", "Hadapsar AC (roll parts)", "Hadapsar seat stations (seat-calibrated)"),
            ("polling stations", "Hadapsar AC (roll parts)", "Hadapsar seat stations (pooled city)"),
            ("polling stations, seat-calibrated rolls", "Nashik East AC", "Nashik East seat stations (seat-calibrated)"),
            ("buses, 11 urban Pune ACs, seat-calibrated rolls", "PMPML lent for PMC + PCMC seats", "Buses, Pune's 11 urban seats")]
    rows = []
    for chk, scope, label in keep:
        r = v[(v["check"] == chk) & (v["scope"] == scope)]
        if len(r):
            rows.append((label, float(r["actual_2024"].iloc[0]), float(r["model_2024_rolls"].iloc[0]), float(r["error_pct"].iloc[0])))
    sc = pd.read_csv(RUNS / "summary/scenarios.csv")
    n3 = sc[(sc["district"] == "nashik") & (sc["scenario"] == "walk3")]["stations"].sum()
    rows.insert(2, ("Nashik district stations (3 km rule)", 4922.0, float(n3), round(100 * (n3 / 4922 - 1), 1)))
    tc = pd.read_csv(RUNS / "summary/report/turnout_curve.csv")
    t = tc[(tc["service_s"] == 40) & (tc["hour"] == "17:00")]
    rows.append(("Votes cast by 17:00 (share of day)", 88.15, float(t["simulated_pct"].mean()),
                 round(100 * (t["simulated_pct"].mean() / 88.15 - 1), 1)))
    fig, ax = plt.subplots(figsize=(W, H * 0.85))
    ax.axvspan(-10, 10, color=LIGHT, zorder=0)
    ax.axvline(0, color=INK, linewidth=1)
    for i, (label, act, mod, err) in enumerate(rows[::-1]):
        good = abs(err) <= 10
        ax.scatter([err], [i], s=140, color=GREEN if good else AMBER, zorder=3)
        ax.plot([0, err], [i, i], color=GREEN if good else AMBER, linewidth=2)
        if err < -25:
            ax.text(err, i + 0.32, f"{err:+.1f}%".replace("-", "−"), ha="center", va="bottom", fontsize=10.5, fontweight="bold", color=INK)
        else:
            ax.text(err + (1.5 if err >= 0 else -1.5), i, f"{err:+.1f}%".replace("-", "−"), ha="left" if err >= 0 else "right",
                    va="center", fontsize=10.5, fontweight="bold", color=INK)
        ax.text(41, i, f"real {act:,.0f}  ·  model {mod:,.0f}" if act > 100 else f"real {act:.1f}  ·  model {mod:.1f}",
                va="center", ha="right", fontsize=10, color=MUTED)
    ax.set_xlim(-40, 42)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows[::-1]], fontsize=11.5)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.set_xticks([-30, -20, -10, 0, 10, 20])
    ax.set_xticklabels(["−30%", "−20%", "−10%", "0", "+10%", "+20%"])
    ax.set_xlabel("model vs real 2024 (grey band = within ±10%)")
    ax.set_title("The model reproduces the 2024 election where data exists to check it")
    source(fig, "Sources: Deccan Herald, The Week (PTI), Punekar News, Maharashtra CEO reports; see the report's sources list. "
                "The bus gap partly reflects police and reserve staff riding in real buses.")
    save(fig, "07_validation_scorecard")


# ---------------------------------------------------------------- section 5: transport
def fig_route_map():
    from scipy.sparse.csgraph import dijkstra
    from .network import Network
    geo = gpd()
    d, seat = SHOW_DISTRICT, SHOW_SEAT
    loc = run_dir(d) / "location"
    sites = pd.read_csv(loc / "base/sites.csv")
    sites = sites[sites["seat"] == seat].set_index("pid")
    routes = pd.read_csv(loc / "base/routing/routes.csv")
    routes = routes[routes["seat"] == seat]
    sb = pd.read_csv(loc / "base/seats.csv").set_index("seat").loc[seat]
    shq = pd.read_csv(loc / "depot_hq/seats.csv").set_index("seat").loc[seat]
    rt = pd.read_csv(loc / "base/routing/seats.csv").set_index("seat").loc[seat]
    rhq = pd.read_csv(loc / "depot_hq/routing/seats.csv").set_index("seat").loc[seat]
    lon = np.r_[sb["depot_lon"], sites["lon"].to_numpy()]
    lat = np.r_[sb["depot_lat"], sites["lat"].to_numpy()]
    net = Network(d).local(lon, lat, pad_deg=0.05)
    nodes, _ = net.snap(lon, lat, "drive")
    pos = {p: i + 1 for i, p in enumerate(sites.index)}
    _, pred = dijkstra(net.drive_min, directed=False, indices=nodes, return_predecessors=True)
    def path(a, b):
        out, v, row = [], nodes[b], pred[a]
        while v >= 0 and v != nodes[a]:
            out.append(v)
            v = row[v]
        out.append(nodes[a])
        return out[::-1]
    off = geo.read_file(CODE / f"pilots/{d}/official_2008/assigned_units.geojson")
    outline = geo.GeoSeries([off[off["ac08_no"] == seat].geometry.union_all()])
    fig, ax = plt.subplots(figsize=(W, H))
    outline.boundary.plot(ax=ax, color=GREY, linewidth=1.2)
    for _, r in routes.iterrows():
        seq = [0] + [pos[p] for p in r["stops"].split(";") if p in pos]
        color, lw = (BLUE, 1.6) if r["vehicle"] == "bus" else (AMBER, 1.1)
        for a, b in zip(seq[:-1], seq[1:]):
            pth = path(a, b)
            ax.plot(net.lon[pth], net.lat[pth], color=color, linewidth=lw, alpha=0.75, solid_capstyle="round")
    ax.scatter(sites["lon"], sites["lat"], s=np.clip(sites["stations"] * 10, 10, 90), color=INK, zorder=4, edgecolor="white", linewidth=0.5)
    ax.scatter([sb["depot_lon"]], [sb["depot_lat"]], s=520, marker="*", color=BLUE, edgecolor="white", linewidth=1.5, zorder=6)
    ax.annotate(f"optimised dispatch centre\n({sb['depot_name']})", (sb["depot_lon"], sb["depot_lat"]), xytext=(-330, -20),
                textcoords="offset points", fontsize=11, fontweight="bold", color=BLUE, zorder=8,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.9), arrowprops=dict(arrowstyle="-", color=BLUE, lw=1))
    if (shq["depot_lon"], shq["depot_lat"]) != (sb["depot_lon"], sb["depot_lat"]):
        ax.scatter([shq["depot_lon"]], [shq["depot_lat"]], s=420, marker="*", facecolor="none", edgecolor=MUTED, linewidth=1.6, zorder=6)
        ax.annotate(f"usual choice, largest town ({shq['depot_name']}):\n{rhq['vehicle_km']:,.0f} vehicle-km", (shq["depot_lon"], shq["depot_lat"]),
                    xytext=(120, 60), textcoords="offset points", fontsize=10.5, color=MUTED,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.9), arrowprops=dict(arrowstyle="-", color=MUTED, lw=1))
    handles = [Line2D([], [], color=BLUE, lw=2, label=f"bus route ({int(rt['buses'])} buses, 6 parties each)"),
               Line2D([], [], color=AMBER, lw=1.5, label=f"jeep route ({int(rt['jeeps'])} jeeps, 1 party)"),
               Line2D([], [], marker="o", ls="", color=INK, label="polling premises (size = stations)", markersize=7)]
    ax.legend(handles=handles, loc="lower left", frameon=True, facecolor="white", edgecolor="none", framealpha=0.9)
    map_axis(ax, outline, pad=0.01)
    map_layout(fig, top=0.86, bottom=0.05)
    suptitle(fig, f"{SHOW_SEAT_NAME}: {int(rt['buses'])} buses and {int(rt['jeeps'])} jeeps put {int(rt['stations'])} polling parties "
                  f"in place by {rt['latest_arrival']}",
             f"Routes on the real road network from the dispatch centre on the eve of the poll · {rt['vehicle_km']:,.0f} vehicle-km "
             f"out and back, {100 * (1 - rt['vehicle_km'] / rhq['vehicle_km']):.0f}% less than dispatching from the largest town")
    source(fig, "OR-Tools vehicle routing with time windows (leave 13:00, arrive by 18:00); road times from OpenStreetMap by road class.")
    save(fig, "05a_route_map")


def fig_km_bridge():
    sc = pd.read_csv(RUNS / "summary/scenarios.csv")
    rows = {}
    for d in DISTRICTS:
        hq = pd.read_csv(run_dir(d) / "location/depot_hq/routing/seats.csv")
        b = pd.read_csv(run_dir(d) / "location/base/routing/seats.csv")
        rows[d] = (hq["star_km_4legs"].sum(), hq["vehicle_km"].sum(), b["vehicle_km"].sum())
    A = sum(r[0] for r in rows.values())
    B = sum(r[1] for r in rows.values())
    C = sum(r[2] for r in rows.values())
    steps = [("Every station its own\nround trip from the\nlargest town", A, "total"),
             ("Route buses and jeeps\n(shared trips)", B - A, "delta"),
             ("Choose the dispatch\ncentre by optimisation", C - B, "delta"),
             ("Optimised plan", C, "total")]
    fig, ax = plt.subplots(figsize=(W, H * 0.85))
    run = 0
    for i, (label, v, kind) in enumerate(steps):
        if kind == "total":
            ax.bar(i, v, color=GREY if i == 0 else BLUE, width=0.6)
            ax.text(i, v + A * 0.015, f"{v / 1e3:,.0f}k km", ha="center", fontsize=13, fontweight="bold")
            run = v
        else:
            ax.bar(i, -v, bottom=run + v, color=GREEN, alpha=0.8, width=0.6)
            lab = f"−{-v / 1e3:,.0f}k km\n(−{-100 * v / run:.0f}%)"
            if -v > A * 0.15:
                ax.text(i, run + v / 2, lab, ha="center", va="center", fontsize=13, color="white", fontweight="bold")
            else:
                ax.text(i + 0.36, run + v / 2, lab, ha="left", va="center", fontsize=12, color=GREEN, fontweight="bold")
            if i + 1 < len(steps):
                ax.plot([i + 0.3, i + 0.7], [run + v, run + v], color=GREY, linewidth=1)
            run += v
    ax.plot([0.3, 0.7], [A, A], color=GREY, linewidth=1)
    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels([s[0] for s in steps], fontsize=11)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_ylim(0, A * 1.1)
    ax.set_title(f"Routing and a well-placed dispatch centre cut transport by {100 * (1 - C / A):.0f}%: "
                 f"{A / 1e3:,.0f}k → {C / 1e3:,.0f}k vehicle-km")
    fig.subplots_adjust(bottom=0.2)
    source(fig, "Four districts, 52 seats, 20,196 polling stations · vehicle-km for the eve of the poll and the return trip · green = saving.")
    save(fig, "05b_transport_bridge")


def fig_dispatch_window():
    """Fleet needed vs how late parties leave the dispatch centre (one rural and one urban seat)."""
    from . import routing as R
    from .network import Network
    cache = OUT / "_dispatch_window.csv"
    if not cache.exists():
        rows = []
        for d, seat, label in ((SHOW_DISTRICT, SHOW_SEAT, f"{SHOW_SEAT_NAME} (rural)"), ("pune", 210, "Kothrud (urban)")):
            loc = run_dir(d) / "location/base"
            seat_row = pd.read_csv(loc / "seats.csv").set_index("seat").loc[seat]
            ss = pd.read_csv(loc / "sites.csv")
            ss = ss[ss["seat"] == seat].reset_index(drop=True)
            st = R.stops_for(ss)
            lon = np.r_[seat_row["depot_lon"], st["lon"].to_numpy()]
            lat = np.r_[seat_row["depot_lat"], st["lat"].to_numpy()]
            dmin, dkm = Network(d).local(lon, lat).drive(lon, lat, lon, lat)
            bad = ~np.isfinite(dmin)
            dmin[bad], dkm[bad] = 600, 300
            parties = np.r_[0, st["parties"].to_numpy()]
            for dep in (11, 12, 13, 14, 15, 16):
                R.DEPART_MIN = dep * 60
                res = R.solve_routes(dmin, dkm, parties, 25)
                used = pd.Series([r["vehicle"] for r in res["routes"]]).value_counts()
                hire = sum(R.VEHICLES[r["vehicle"]]["hire"] for r in res["routes"])
                runk = sum(R.VEHICLES[r["vehicle"]]["per_km"] * r["km_one_way_loop"] * 2 for r in res["routes"])
                rows.append({"seat": label, "depart": dep, "buses": int(used.get("bus", 0)), "jeeps": int(used.get("jeep", 0)),
                             "cost": hire + runk, "late": len(res["dropped"])})
                print(label, dep, rows[-1])
            R.DEPART_MIN = 13 * 60
        pd.DataFrame(rows).to_csv(cache, index=False)
    df = pd.read_csv(cache)
    fig, axes = plt.subplots(1, 2, figsize=(W, H * 0.8), sharey=False)
    for ax, (seat, g) in zip(axes, df.groupby("seat", sort=False)):
        g = g.sort_values("depart")
        x = np.arange(len(g))
        ax.bar(x, g["buses"], color=BLUE, width=0.6, label="buses")
        ax.bar(x, g["jeeps"], bottom=g["buses"], color=AMBER, width=0.6, label="jeeps")
        for i, (_, r) in enumerate(g.iterrows()):
            ax.text(i, r["buses"] + r["jeeps"] + 1, f"₹{r['cost'] / 1e5:.1f} L", ha="center", fontsize=10, color=INK)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{h}:00" for h in g["depart"]])
        ax.set_xlabel("parties leave the dispatch centre at (must arrive by 18:00)")
        ax.set_title(seat, fontsize=13)
        ax.grid(axis="y", color=LIGHT)
    axes[0].set_ylabel("vehicles needed")
    axes[0].legend(frameon=False)
    suptitle(fig, "The 18:00 deadline hardly matters: routes take about an hour, so bus seats, not the clock, set the fleet",
             "Vehicles the routing model needs as parties leave later · label = two-day hire + running cost · "
             "changes of a few vehicles are within the routing heuristic's tolerance")
    fig.subplots_adjust(top=0.8)
    save(fig, "05c_dispatch_window")


def fig_receipt_rush():
    """When polling parties reach the receipt centre after polls close (vehicles wait with the party)."""
    fig, ax = plt.subplots(figsize=(W, H * 0.8))
    colors = {"sangli": "#9AA0A6", "satara": "#5F6368", "nashik": "#80868B", "pune": BLUE}
    peaks = []
    for d in DISTRICTS:
        r = pd.read_csv(run_dir(d) / "location/base/routing/routes.csv")
        # close 18:00 + ~60 min of sealing and forms, then the route run in reverse
        t = 19 * 60 + r["arrival_last_min"].to_numpy()
        w = r["parties"].to_numpy()
        o = np.argsort(t)
        cum = np.cumsum(w[o]) / w.sum()
        ax.plot(t[o] / 60, 100 * cum, color=colors[d], linewidth=2.4 if d == "pune" else 1.6, label=d.title())
        half = t[o][np.searchsorted(cum, 0.5)] / 60
        ax.scatter([half], [50], color=colors[d], zorder=3)
        ts, ws = t[o], w[o]
        peak = max(ws[(ts >= a) & (ts < a + 60)].sum() for a in ts) / w.sum()
        peaks.append(peak)
    ax.axhline(50, color=GREY, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.set_xticks(range(19, 24))
    ax.set_xticklabels([f"{h}:00" for h in range(19, 24)])
    ax.set_xlim(19, 23)
    ax.set_ylabel("polling parties back at the receipt centre (%)")
    ax.set_xlabel("time on polling night")
    ax.grid(color=LIGHT)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title(f"Parties stream back from 19:15 to 23:00, and the busiest hour brings {100 * min(peaks):.0f}–{100 * max(peaks):.0f}% of them: "
                 "staff the counters for that peak", fontsize=15)
    source(fig, "Approximation: polls close 18:00, ~60 min of sealing and forms, vehicles wait with their party and run the route in reverse.")
    save(fig, "05d_receipt_rush")


# ---------------------------------------------------------------- section 6: polling day
def fig_turnout():
    tc = pd.read_csv(RUNS / "summary/report/turnout_curve.csv")
    t40 = tc[tc["service_s"] == 40]
    hours = ["09:00", "11:00", "13:00", "15:00", "17:00"]
    x = [0] + [int(h[:2]) - 7 for h in hours] + [11]
    fig, ax = plt.subplots(figsize=(W, H * 0.8))
    rep = [0] + [float(t40[t40["hour"] == h]["reported_pct"].iloc[0]) for h in hours] + [100]
    for d, g in t40.groupby("District"):
        sim = [0] + [float(g[g["hour"] == h]["simulated_pct"].iloc[0]) for h in hours]
        ax.plot(x[:-1], sim, color=BLUE, alpha=0.55, linewidth=1.6)
    ax.plot([], [], color=BLUE, linewidth=1.6, label="simulated stations (one line per district)")
    ax.plot(x, rep, color=INK, linewidth=2.2, linestyle=(0, (5, 3)), marker="o", label="reported, Maharashtra 2024")
    ax.set_xticks(range(0, 12, 2))
    ax.set_xticklabels([f"{h}:00" for h in range(7, 19, 2)])
    ax.set_ylabel("share of the day's votes cast (%)")
    ax.grid(color=LIGHT)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("The simulated polling day reproduces 2024's hourly turnout within one point")
    source(fig, "Calibrated at 40 s per voter (best fit: 0.6 points mean error; 50 s: 1.3; 60 s: 2.0). Reported: Maharashtra CEO via Deccan Herald, The Wire.")
    save(fig, "06a_turnout_validation")


def fig_queue_trace():
    from .simulate import CURVE_F, CURVE_T, run_queue
    rng = np.random.default_rng(3)
    grid = np.linspace(0, 1, 2001)
    inv = np.interp(grid, CURVE_F, CURVE_T)
    mins = np.arange(0, 14 * 60 + 1, 5)
    sig = math.sqrt(math.log(1 + 0.45 ** 2))
    mu = math.log(40 / 60) - sig ** 2 / 2
    fig, ax = plt.subplots(figsize=(W, H * 0.85))
    for size, color, off in ((1500, RED, (10, 40)), (1200, BLUE, (10, 14))):
        traces = []
        for _ in range(400):
            n = rng.binomial(size, 0.66)
            arr = np.sort(np.interp(rng.random(n), grid, inv))
            serv = rng.lognormal(mu, sig, n)
            waits, _ = run_queue(arr, serv, np.inf, np.inf, False, 0.0, 1.0, rng.random(n))
            start = arr + waits
            q = np.searchsorted(arr, mins, side="right") - np.searchsorted(np.sort(start), mins, side="right")
            traces.append(q)
        tr = np.array(traces)
        med, lo, hi = np.median(tr, 0), np.quantile(tr, 0.1, 0), np.quantile(tr, 0.9, 0)
        hrs = 7 + mins / 60
        ax.fill_between(hrs, lo, hi, color=color, alpha=0.15)
        ax.plot(hrs, med, color=color, linewidth=2.4, label=f"{size:,} electors per station")
        after = hrs[np.argmax((hrs >= 18) & (med <= 0.5))]
        ax.annotate(f"{size:,}: line clears ≈ {int(after)}:{int(round((after % 1) * 60)):02d}", (after, 0.5), xytext=off,
                    textcoords="offset points", color=color, fontsize=10.5, fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color=color, lw=0.8))
    ax.axvline(18, color=INK, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.text(18.05, ax.get_ylim()[1] * 0.92, "polls close 18:00:\neveryone in line still votes", fontsize=10.5, color=INK)
    ax.set_xticks(range(7, 22))
    ax.set_xticklabels([f"{h}:00" if h % 2 == 1 else "" for h in range(7, 22)])
    ax.set_xlim(7, 21)
    ax.set_ylabel("people waiting in line")
    ax.grid(color=LIGHT)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("At 1,500 electors the afternoon queue never clears before closing")
    source(fig, "A full station at 66% turnout, 40 s per voter (calibrated), one voting compartment · median of 400 simulated days, band = 10th–90th percentile.")
    save(fig, "06b_queue_through_the_day")


def fig_cap_value():
    cv = pd.read_csv(RUNS / "summary/report/cap_value.csv")
    fig, axes = plt.subplots(1, 2, figsize=(W, H * 0.8), gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axes[0]
    y = np.arange(len(cv))[::-1]
    for yi, (_, r) in zip(y, cv.iterrows()):
        ax.plot([r["wait_1200_min"], r["wait_1500_min"]], [yi, yi], color=GREY, linewidth=3, zorder=1)
        ax.scatter([r["wait_1500_min"]], [yi], s=170, color=GREY, zorder=2)
        ax.scatter([r["wait_1200_min"]], [yi], s=170, color=BLUE, zorder=3)
        ax.text(r["wait_1500_min"] + 0.8, yi, f"{r['wait_1500_min']:.0f} min", va="center", color=MUTED)
        ax.text(r["wait_1200_min"] - 0.8, yi, f"{r['wait_1200_min']:.1f}", va="center", ha="right", color=BLUE, fontweight="bold")
    ax.set_yticks(y)
    ax.set_yticklabels(cv["District"])
    ax.set_xlim(-3, 34)
    ax.set_xlabel("average wait per voter (min)")
    ax.set_title("Average wait: 1,500 cap (grey) → 1,200 cap (blue)", fontsize=13)
    ax.grid(axis="x", color=LIGHT)
    ax = axes[1]
    ax.barh(y, cv["rs_per_voter_hour_saved"], color=BLUE, height=0.55)
    ax.axvline(50, color=RED, linewidth=1.5, linestyle=(0, (4, 3)))
    ax.set_axisbelow(True)
    ax.text(51, y.min() - 0.45, "₹50 = value of an hour\nof a voter's time", color=RED, fontsize=10, va="center")
    ax.set_ylim(y.min() - 0.8, y.max() + 0.5)
    for yi, (_, r) in zip(y, cv.iterrows()):
        ax.text(r["rs_per_voter_hour_saved"] + 1, yi, f"₹{r['rs_per_voter_hour_saved']:.0f} per hour saved  (₹{r['extra_cost_per_elector']:.2f}/elector)",
                va="center", fontsize=10.5)
    ax.set_yticks(y)
    ax.set_yticklabels([])
    ax.set_xlim(0, 80)
    ax.set_xlabel("₹ spent per voter-hour of waiting saved")
    ax.set_title("Cost of the 1,200 cap per hour of waiting saved", fontsize=13)
    ax.grid(axis="x", color=LIGHT)
    suptitle(fig, "The 1,200 cap cuts waits by 10–23 minutes at ₹15–39 per voter-hour: worth it in every district",
             "Optimised plan for each cap, 40 s per voter, nobody leaving the queue · extra cost covers stations, premises, walking and transport")
    fig.subplots_adjust(top=0.8, wspace=0.08)
    save(fig, "06c_cap_cost_benefit")


def fig_failures():
    rv = pd.read_csv(RUNS / "summary/report/reserves.csv")
    pol = ["10% spares", "20% spares", "30% spares"]
    fig, ax = plt.subplots(figsize=(W, H * 0.75))
    ds = list(dict.fromkeys(rv["District"]))
    x = np.arange(len(ds))
    for i, p in enumerate(pol):
        v = [float(rv[(rv["District"] == d) & (rv["policy"] == p)]["mean_downtime_min_if_failed"].iloc[0]) for d in ds]
        bars = ax.bar(x + (i - 1) * 0.25, v, width=0.24, color=BLUE, alpha=0.35 + 0.3 * i, label=p)
        for b, val in zip(bars, v):
            ax.text(b.get_x() + b.get_width() / 2, val + 0.4, f"{val:.0f}", ha="center", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(ds)
    ax.set_ylabel("minutes a failed station stops voting")
    ax.grid(axis="y", color=LIGHT)
    ax.set_axisbelow(True)
    ax.set_ylim(0, 42)
    ax.legend(frameon=False, ncol=3, loc="upper right")
    share = rv[rv["policy"] == "20% spares"]["stations_with_failure"].mean()
    ax.set_title(f"Tripling spare EVMs saves only ~4 minutes per failure: the sector officer's drive is the bottleneck")
    source(fig, f"About {100 * share:.0f}% of stations replace a BU, CU or VVPAT (Assam 2024 rates); downtime = officer's drive + 20 min to replace and re-seal.")
    save(fig, "06d_evm_failures")


def fig_architecture():
    import shutil
    from glob import glob
    shots = sorted(glob(str(ROOT.parent.parent / ".claude/projects/*/*/tool-results/*oiuhlc.png")))
    if shots:
        shutil.copy(shots[-1], OUT / "02_architecture.png")
        print("wrote 02_architecture (from the report diagram)")


FIGS = {
    "title": fig_title_map, "architecture": fig_architecture,
    "recom": fig_recom_mechanics, "drift": fig_drift_statewide, "pune": fig_pune_then_now,
    "ours": fig_official_vs_ours, "tradeoff": fig_tradeoff, "loksabha": fig_loksabha,
    "village": fig_village_zoom, "access": fig_access_before_after, "walk": fig_walk_cdf,
    "route": fig_route_map, "bridge": fig_km_bridge, "window": fig_dispatch_window, "receipt": fig_receipt_rush,
    "turnout": fig_turnout, "queue": fig_queue_trace, "cap": fig_cap_value, "failures": fig_failures,
    "validation": fig_validation,
}


def main() -> None:
    names = sys.argv[1:] or list(FIGS)
    for n in names:
        try:
            FIGS[n]()
        except Exception as e:  # keep going; report what failed
            print(f"FAILED {n}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
