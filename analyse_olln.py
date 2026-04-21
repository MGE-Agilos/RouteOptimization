"""
Analyse du réseau routier d'Ottignies-Louvain-la-Neuve
Téléchargement OSM + métriques de trafic simulées + optimisations
"""

import osmnx as ox
import networkx as nx
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.cm as cm
import folium
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

CITY = "Ottignies-Louvain-la-Neuve, Belgium"

# ─── 1. Téléchargement du graphe routier ────────────────────────────────────
print("1. Téléchargement du réseau routier OSM...")
G = ox.graph_from_place(CITY, network_type="drive", simplify=True)
print(f"   Nœuds: {G.number_of_nodes()}, Arêtes: {G.number_of_edges()}")

# Projet en mètres pour calculs de distance
G_proj = ox.project_graph(G)

# ─── 2. Métriques de centralité (proxy du trafic) ───────────────────────────
print("2. Calcul des métriques de centralité (betweenness)...")
# Betweenness centrality = combien de chemins courts passent par chaque arête
# → bon proxy du volume de trafic potentiel
edge_bc = nx.edge_betweenness_centrality(G, normalized=True, weight="length")
nx.set_edge_attributes(G, edge_bc, "betweenness")

node_bc = nx.betweenness_centrality(G, normalized=True, weight="length")
nx.set_node_attributes(G, node_bc, "betweenness")

# ─── 3. Conversion en GeoDataFrame ──────────────────────────────────────────
print("3. Conversion en GeoDataFrame...")
nodes, edges = ox.graph_to_gdfs(G)
edges = edges.reset_index()

# Simulation de charge de trafic basée sur :
#  - type de route (highway)
#  - centralité betweenness
#  - longueur de route
highway_weights = {
    "motorway": 1.0, "motorway_link": 0.9,
    "trunk": 0.85, "trunk_link": 0.8,
    "primary": 0.75, "primary_link": 0.7,
    "secondary": 0.55, "secondary_link": 0.5,
    "tertiary": 0.35, "tertiary_link": 0.3,
    "residential": 0.15, "living_street": 0.08,
    "unclassified": 0.2, "service": 0.1,
}

def get_hw_weight(hw):
    if isinstance(hw, list):
        hw = hw[0]
    return highway_weights.get(str(hw), 0.2)

edges["hw_weight"] = edges["highway"].apply(get_hw_weight)
edges["betweenness"] = edges["betweenness"].fillna(0)

bc_max = edges["betweenness"].max()
if bc_max > 0:
    edges["bc_norm"] = edges["betweenness"] / bc_max
else:
    edges["bc_norm"] = 0

# Score de trafic composite (0-100)
edges["traffic_score"] = (
    0.60 * edges["bc_norm"] +
    0.40 * edges["hw_weight"]
) * 100

# Catégorie de trafic
def categorize(score):
    if score >= 65: return "Critique"
    if score >= 40: return "Élevé"
    if score >= 20: return "Modéré"
    return "Faible"

edges["traffic_cat"] = edges["traffic_score"].apply(categorize)

# ─── 4. Identification des goulots d'étranglement ───────────────────────────
print("4. Identification des goulots d'étranglement...")
bottlenecks = edges[edges["traffic_cat"] == "Critique"].copy()
bottlenecks_high = edges[edges["traffic_cat"].isin(["Critique", "Élevé"])].copy()

top_critical = bottlenecks.nlargest(15, "traffic_score")[
    ["name", "highway", "traffic_score", "traffic_cat", "length", "geometry"]
]

# ─── 5. Carte statique matplotlib ────────────────────────────────────────────
print("5. Génération de la carte statique...")
fig, axes = plt.subplots(1, 2, figsize=(20, 14))
fig.patch.set_facecolor("#1a1a2e")

cmap = plt.cm.RdYlGn_r
norm = mcolors.Normalize(vmin=0, vmax=100)

def plot_network(ax, title):
    ax.set_facecolor("#16213e")
    for _, row in edges.iterrows():
        try:
            xs, ys = row.geometry.xy
            color = cmap(norm(row["traffic_score"]))
            lw = 0.5 + row["traffic_score"] / 40
            ax.plot(xs, ys, color=color, linewidth=lw, alpha=0.8)
        except Exception:
            pass
    ax.set_title(title, color="white", fontsize=14, pad=10)
    ax.tick_params(colors="gray")
    for spine in ax.spines.values():
        spine.set_edgecolor("gray")

plot_network(axes[0], "Score de trafic – réseau complet")
axes[0].set_xlabel("Longitude", color="gray")
axes[0].set_ylabel("Latitude", color="gray")

# Zoom sur les axes critiques
for _, row in bottlenecks_high.iterrows():
    try:
        xs, ys = row.geometry.xy
        color = "#ff4444" if row["traffic_cat"] == "Critique" else "#ff8800"
        axes[1].plot(xs, ys, color=color, linewidth=2.5, alpha=0.9)
    except Exception:
        pass

# Fond pour axes non critiques
for _, row in edges[~edges["traffic_cat"].isin(["Critique", "Élevé"])].iterrows():
    try:
        xs, ys = row.geometry.xy
        axes[1].plot(xs, ys, color="#2a4a6a", linewidth=0.5, alpha=0.5)
    except Exception:
        pass

axes[1].set_facecolor("#16213e")
axes[1].set_title("Axes critiques & élevés (rouge/orange)", color="white", fontsize=14, pad=10)
axes[1].set_xlabel("Longitude", color="gray")
axes[1].tick_params(colors="gray")
for spine in axes[1].spines.values():
    spine.set_edgecolor("gray")

# Colorbar
sm = cm.ScalarMappable(cmap=cmap, norm=norm)
sm.set_array([])
cbar = fig.colorbar(sm, ax=axes[0], orientation="vertical", shrink=0.7, pad=0.02)
cbar.set_label("Score de trafic (0=faible, 100=critique)", color="white", fontsize=10)
cbar.ax.yaxis.set_tick_params(color="white")
plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")

# Légende
from matplotlib.patches import Patch
legend_elements = [
    Patch(facecolor="#ff4444", label="Critique (≥65)"),
    Patch(facecolor="#ff8800", label="Élevé (40-65)"),
    Patch(facecolor="#2a4a6a", label="Modéré/Faible (<40)"),
]
axes[1].legend(handles=legend_elements, loc="upper right",
               facecolor="#1a1a2e", edgecolor="gray",
               labelcolor="white", fontsize=10)

plt.tight_layout()
plt.savefig(OUTPUT_DIR / "reseau_routier_olln.png", dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close()
print("   → output/reseau_routier_olln.png")

# ─── 6. Carte interactive Folium ────────────────────────────────────────────
print("6. Génération de la carte interactive...")
center = [nodes.geometry.y.mean(), nodes.geometry.x.mean()]
m = folium.Map(location=center, zoom_start=13, tiles="CartoDB dark_matter")

color_map = {"Critique": "#e74c3c", "Élevé": "#e67e22",
             "Modéré": "#f1c40f", "Faible": "#2ecc71"}
weight_map = {"Critique": 5, "Élevé": 3.5, "Modéré": 2, "Faible": 1}

for _, row in edges.iterrows():
    try:
        coords = [(y, x) for x, y in zip(*row.geometry.xy)]
        cat = row["traffic_cat"]
        name = row.get("name", "Rue sans nom")
        if isinstance(name, list):
            name = name[0]
        hw = row.get("highway", "")
        if isinstance(hw, list):
            hw = hw[0]
        popup_text = (
            f"<b>{name}</b><br>"
            f"Type: {hw}<br>"
            f"Score trafic: {row['traffic_score']:.1f}/100<br>"
            f"Catégorie: <b style='color:{color_map[cat]}'>{cat}</b><br>"
            f"Longueur: {row['length']:.0f} m"
        )
        folium.PolyLine(
            coords,
            color=color_map[cat],
            weight=weight_map[cat],
            opacity=0.85,
            popup=folium.Popup(popup_text, max_width=250),
            tooltip=f"{name} – {cat} ({row['traffic_score']:.0f})"
        ).add_to(m)
    except Exception:
        pass

# Marqueurs pour les goulots critiques (top 10)
top10 = bottlenecks.nlargest(10, "traffic_score")
for _, row in top10.iterrows():
    try:
        mid_point = row.geometry.centroid
        name = row.get("name", "Rue sans nom")
        if isinstance(name, list):
            name = name[0]
        folium.Marker(
            location=[mid_point.y, mid_point.x],
            icon=folium.Icon(color="red", icon="warning-sign", prefix="glyphicon"),
            popup=f"⚠️ GOULOT: {name}<br>Score: {row['traffic_score']:.1f}",
            tooltip=f"⚠️ {name}"
        ).add_to(m)
    except Exception:
        pass

# Légende
legend_html = """
<div style="position:fixed; bottom:30px; left:30px; z-index:1000;
     background:#1a1a2e; color:white; padding:15px; border-radius:8px;
     border:1px solid #444; font-size:13px; min-width:200px">
<b>Score de trafic</b><br><br>
<span style="color:#e74c3c">&#9644;&#9644;&#9644;</span> Critique (&ge;65)<br>
<span style="color:#e67e22">&#9644;&#9644;</span> Élevé (40–65)<br>
<span style="color:#f1c40f">&#9644;</span> Modéré (20–40)<br>
<span style="color:#2ecc71">&#9644;</span> Faible (&lt;20)<br><br>
<span style="color:#e74c3c">&#9673;</span> Top 10 goulots
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))
m.save(OUTPUT_DIR / "carte_interactive_olln.html")
print("   → output/carte_interactive_olln.html")

# ─── 7. Rapport CSV des routes critiques ─────────────────────────────────────
print("7. Export du rapport CSV...")
report = edges[["name", "highway", "traffic_score", "traffic_cat", "length", "betweenness"]].copy()
report["name"] = report["name"].apply(lambda x: x[0] if isinstance(x, list) else x)
report["highway"] = report["highway"].apply(lambda x: x[0] if isinstance(x, list) else x)
report = report.sort_values("traffic_score", ascending=False)
report.to_csv(OUTPUT_DIR / "rapport_trafic_olln.csv", index=False, encoding="utf-8-sig")
print("   → output/rapport_trafic_olln.csv")

# ─── 8. Statistiques & optimisations ─────────────────────────────────────────
print("\n" + "="*60)
print("RÉSUMÉ DE L'ANALYSE – Ottignies-Louvain-la-Neuve")
print("="*60)
print(f"Réseau: {G.number_of_nodes()} nœuds | {G.number_of_edges()} arêtes")
print(f"Longueur totale réseau: {edges['length'].sum()/1000:.1f} km\n")

cat_counts = edges["traffic_cat"].value_counts()
for cat in ["Critique", "Élevé", "Modéré", "Faible"]:
    n = cat_counts.get(cat, 0)
    pct = n / len(edges) * 100
    print(f"  {cat:10s}: {n:4d} segments ({pct:.1f}%)")

print(f"\nTop 10 segments les plus chargés:")
top10_display = report.nlargest(10, "traffic_score")[["name", "highway", "traffic_score", "length"]]
print(top10_display.to_string(index=False))

# ─── 9. Graphique distribution ────────────────────────────────────────────────
fig2, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
fig2.patch.set_facecolor("#1a1a2e")

ax1.set_facecolor("#16213e")
ax1.hist(edges["traffic_score"], bins=40, color="#3498db", edgecolor="#1a6a9a", alpha=0.85)
ax1.axvline(65, color="#e74c3c", linestyle="--", label="Seuil critique")
ax1.axvline(40, color="#e67e22", linestyle="--", label="Seuil élevé")
ax1.set_xlabel("Score de trafic", color="white")
ax1.set_ylabel("Nombre de segments", color="white")
ax1.set_title("Distribution des scores de trafic", color="white")
ax1.tick_params(colors="white")
ax1.legend(facecolor="#1a1a2e", edgecolor="gray", labelcolor="white")
for spine in ax1.spines.values():
    spine.set_edgecolor("gray")

ax2.set_facecolor("#16213e")
cats = ["Critique", "Élevé", "Modéré", "Faible"]
colors_bar = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71"]
vals = [cat_counts.get(c, 0) for c in cats]
bars = ax2.bar(cats, vals, color=colors_bar, edgecolor="#333", alpha=0.9)
for bar, val in zip(bars, vals):
    ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2,
             str(val), ha="center", va="bottom", color="white", fontsize=11)
ax2.set_ylabel("Nombre de segments", color="white")
ax2.set_title("Répartition par catégorie", color="white")
ax2.tick_params(colors="white")
for spine in ax2.spines.values():
    spine.set_edgecolor("gray")

plt.tight_layout()
plt.savefig(OUTPUT_DIR / "distribution_trafic.png", dpi=130, bbox_inches="tight",
            facecolor=fig2.get_facecolor())
plt.close()
print("\n   → output/distribution_trafic.png")
print("\nAnalyse terminée. Fichiers dans le dossier output/")
