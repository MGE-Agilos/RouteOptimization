"""
Exporte le graphe depuis precomputed/data.pkl vers docs/data/graph.json
pour la Supabase Edge Function de simulation.

Usage : python export_graph.py
"""

import pickle, json, os

CACHE_PKL = os.path.join(os.path.dirname(__file__), "precomputed", "data.pkl")
OUT_JSON   = os.path.join(os.path.dirname(__file__), "docs", "data", "graph.json")

print("Chargement du cache...")
with open(CACHE_PKL, "rb") as f:
    cache = pickle.load(f)

G                = cache["G_original"]
gravity_counts   = cache.get("base_gravity_counts", {})
gravity_scale    = float(cache.get("base_gravity_scale", 1.0))
local_access     = cache.get("base_local_access", {})
base_type_max_bc = cache.get("base_type_max_bc", {})

print(f"  {G.number_of_nodes()} noeuds · {G.number_of_edges()} aretes")

# ── Nœuds ────────────────────────────────────────────────────────────────────
node_adj = {}
for nid, data in G.nodes(data=True):
    node_adj[str(nid)] = {"x": data["x"], "y": data["y"], "out": []}

# ── Arêtes ───────────────────────────────────────────────────────────────────
edges = []
for u, v, key, data in G.edges(keys=True, data=True):
    def get_str(val):
        if isinstance(val, list): return str(val[0]) if val else ""
        return str(val) if val else ""

    edge_id = f"{u}:{v}:{key}"
    hw      = get_str(data.get("highway", "residential"))
    ref     = get_str(data.get("ref", ""))
    length  = float(data.get("length", 100))

    edges.append({
        "id":      edge_id,
        "u":       str(u),
        "v":       str(v),
        "key":     key,
        "length":  round(length, 2),
        "highway": hw,
        "ref":     ref,
        "gravity": int(gravity_counts.get((u, v, key), 0)),
        "local":   int(local_access.get((u, v, key), 0)),
    })

    # Adjacence pour Dijkstra
    node_adj[str(u)]["out"].append({"to": str(v), "eid": edge_id, "len": round(length, 2)})

# ── Paramètres du modèle ─────────────────────────────────────────────────────
AADT_MAX = {
    "motorway": 55000, "motorway_link": 28000,
    "trunk":    30000, "trunk_link":    15000,
    "primary":  20000, "primary_link":  10000,
    "secondary":11000, "secondary_link": 5500,
    "tertiary":  5000, "tertiary_link":  2500,
    "residential":1500, "living_street":  200,
    "unclassified":1800, "service":        400,
}

NATIONAL_BC_BOOST = {
    "N275": 3.5, "N233": 3.0, "N237": 2.5,
    "N239": 2.0, "N250": 2.0, "N232": 1.5, "N238a": 1.5,
}

output = {
    "nodes":          list(node_adj.keys()),
    "node_adj":       node_adj,
    "edges":          edges,
    "base_type_max_bc": {k: float(v) for k, v in base_type_max_bc.items()},
    "gravity_scale":  gravity_scale,
    "aadt_max":       AADT_MAX,
    "national_bc_boost": NATIONAL_BC_BOOST,
}

os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(output, f, separators=(",", ":"))

size_kb = os.path.getsize(OUT_JSON) / 1024
print(f"Exporte -> {OUT_JSON}")
print(f"  {len(edges)} aretes · {len(node_adj)} noeuds · {size_kb:.0f} KB")
