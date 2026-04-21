"""
Application de visualisation et simulation du trafic routier
Ottignies-Louvain-la-Neuve
  Score    : OD pondéré par POI (ranking relatif)
  Véhicules: betweenness intra-type, ancré sur la base (redistribution réelle)
"""

import os
import copy
import pickle
import warnings

import numpy as np
from flask import Flask, jsonify, request, render_template
from shapely.geometry import LineString

from traffic_model import (
    load_poi_attractiveness,
    simulate_od_traffic,
    od_scores,
    compute_vehicles,
)

warnings.filterwarnings("ignore")

app = Flask(__name__)

CITY      = "Ottignies-Louvain-la-Neuve, Belgium"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "precomputed")
CACHE_PKL = os.path.join(CACHE_DIR, "data.pkl")

# ── État global ───────────────────────────────────────────────────────────────
G_original       = None
attractiveness   = None
model_meta       = {}
base_type_max_bc = None
base_geojson     = None
base_stats       = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_str(val):
    if isinstance(val, list): return val[0] if val else ""
    return val or ""


def score_to_category(score):
    if score >= 65: return "Critique"
    if score >= 40: return "Élevé"
    if score >= 20: return "Modéré"
    return "Faible"


def graph_to_geojson(G, scores, vehicles=None):
    if vehicles is None:
        vehicles = {}
    features = []
    for u, v, key, data in G.edges(keys=True, data=True):
        geom = data.get("geometry")
        if geom is None:
            geom = LineString([
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"]),
            ])
        score    = scores.get((u, v, key), 0.0)
        hw       = get_str(data.get("highway", "unclassified"))
        name     = get_str(data.get("name", ""))
        maxspeed = get_str(data.get("maxspeed", ""))
        lanes    = get_str(data.get("lanes", ""))
        oneway   = bool(data.get("oneway", False))
        length   = round(float(data.get("length", 0)), 1)
        edge_id  = f"{u}:{v}:{key}"

        features.append({
            "type": "Feature",
            "id": edge_id,
            "geometry": geom.__geo_interface__,
            "properties": {
                "edge_id":   edge_id,
                "name":      name or "Rue sans nom",
                "highway":   hw,
                "score":     round(score, 1),
                "category":  score_to_category(score),
                "maxspeed":  maxspeed,
                "lanes":     lanes,
                "oneway":    oneway,
                "length":    length,
                "vehicles":  vehicles.get((u, v, key), 0),
                "veh_delta": None,
                "delta":     0.0,
                "sign":      None,
                "closed":    False,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def compute_stats(geojson):
    feats  = geojson["features"]
    scores = [f["properties"]["score"] for f in feats]
    cats   = [f["properties"]["category"] for f in feats]
    if not scores:
        return {"total": 0, "avg": 0, "max": 0,
                "critique": 0, "eleve": 0, "modere": 0, "faible": 0}
    return {
        "total":    len(scores),
        "avg":      round(float(np.mean(scores)), 1),
        "max":      round(float(np.max(scores)), 1),
        "critique": cats.count("Critique"),
        "eleve":    cats.count("Élevé"),
        "modere":   cats.count("Modéré"),
        "faible":   cats.count("Faible"),
    }


def parse_edge_id(edge_id):
    parts = edge_id.rsplit(":", 2)
    if len(parts) != 3: return None
    try: return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError: return None


def apply_signs(G_base, signs):
    G = G_base.copy()
    for sign in signs:
        parsed = parse_edge_id(sign.get("edge_id", ""))
        if parsed is None: continue
        u, v, key = parsed
        stype = sign.get("type", "")
        if not G.has_edge(u, v, key): continue

        if stype in ("closure", "deviation"):
            G.remove_edge(u, v, key)
            if G.has_edge(v, u):
                for k in list(G[v][u].keys()): G.remove_edge(v, u, k)
        elif stype == "zone30":
            G[u][v][key]["length"] = G[u][v][key].get("length", 100) * 2.5
        elif stype == "zone50":
            G[u][v][key]["length"] = G[u][v][key].get("length", 100) * 1.5
        elif stype == "priority":
            G[u][v][key]["length"] = G[u][v][key].get("length", 100) * 0.4
        elif stype == "oneway":
            if G.has_edge(v, u):
                for k in list(G[v][u].keys()): G.remove_edge(v, u, k)
    return G


# ── Startup ───────────────────────────────────────────────────────────────────

def _startup():
    global G_original, attractiveness, model_meta
    global base_type_max_bc, base_geojson, base_stats

    import osmnx as ox

    if os.path.exists(CACHE_PKL):
        print("Chargement depuis le cache précalculé...")
        with open(CACHE_PKL, "rb") as f:
            cache = pickle.load(f)
        G_original       = cache["G_original"]
        attractiveness   = cache["attractiveness"]
        model_meta       = cache["model_meta"]
        base_type_max_bc = cache["base_type_max_bc"]
        base_geojson     = cache["base_geojson"]
        base_stats       = cache["base_stats"]
        print(f"  Cache chargé : {G_original.number_of_nodes()} nœuds · "
              f"{G_original.number_of_edges()} arêtes · "
              f"{model_meta.get('n_poi', 0)} POI")
        return

    print(f"Chargement OSM : {CITY}...")
    G_original = ox.graph_from_place(CITY, network_type="drive", simplify=True)
    print(f"  {G_original.number_of_nodes()} nœuds · {G_original.number_of_edges()} arêtes")

    print("Points d'intérêt OSM...")
    attractiveness, model_meta = load_poi_attractiveness(G_original, CITY)
    if attractiveness is None:
        attractiveness = {n: 1.0 for n in G_original.nodes()}
        model_meta = {"n_poi": 0}

    print("Scores OD (simulation)...")
    od_counts, _  = simulate_od_traffic(G_original, attractiveness, n_samples=3000)
    base_scores   = od_scores(G_original, od_counts)

    print("Véhicules (betweenness intra-type, k=300)...")
    base_veh, base_type_max_bc = compute_vehicles(G_original, k=300)

    base_geojson = graph_to_geojson(G_original, base_scores, base_veh)
    base_stats   = compute_stats(base_geojson)

    vehs = [f["properties"]["vehicles"] for f in base_geojson["features"]]
    print(f"  Véhicules : min={min(vehs)}  moy={int(np.mean(vehs))}  max={max(vehs)}")
    print(f"  {model_meta.get('n_poi', 0)} POI comme attracteurs")

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_PKL, "wb") as f:
        pickle.dump({
            "G_original":       G_original,
            "attractiveness":   attractiveness,
            "model_meta":       model_meta,
            "base_type_max_bc": base_type_max_bc,
            "base_geojson":     base_geojson,
            "base_stats":       base_stats,
        }, f, protocol=4)
    print(f"  Cache sauvegarde -> {CACHE_PKL}")


# ── Routes Flask ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/network")
def get_network():
    return jsonify(base_geojson)


@app.route("/api/stats")
def get_stats():
    return jsonify({**base_stats, **model_meta})


@app.route("/api/simulate", methods=["POST"])
def simulate():
    data  = request.get_json(force=True)
    signs = data.get("signs", [])

    if not signs:
        return jsonify({
            "geojson":    copy.deepcopy(base_geojson),
            "stats":      base_stats,
            "base_stats": base_stats,
        })

    try:
        G_mod = apply_signs(G_original, signs)

        od_mod, _ = simulate_od_traffic(G_mod, attractiveness, n_samples=1500)
        scores_mod = od_scores(G_mod, od_mod)

        vehicles_mod, _ = compute_vehicles(
            G_mod, base_type_max_bc=base_type_max_bc, k=150
        )

        geojson_mod = graph_to_geojson(G_mod, scores_mod, vehicles_mod)

        base_map     = {f["properties"]["edge_id"]: f["properties"]["score"]
                        for f in base_geojson["features"]}
        base_veh_map = {f["properties"]["edge_id"]: f["properties"]["vehicles"]
                        for f in base_geojson["features"]}
        sign_map     = {s["edge_id"]: s for s in signs}

        for feat in geojson_mod["features"]:
            eid = feat["properties"]["edge_id"]
            feat["properties"]["delta"]     = round(
                feat["properties"]["score"] - base_map.get(eid, feat["properties"]["score"]), 1)
            feat["properties"]["veh_delta"] = (
                feat["properties"]["vehicles"] - base_veh_map.get(eid, 0))
            if eid in sign_map:
                feat["properties"]["sign"] = sign_map[eid]["type"]

        mod_ids = {f["properties"]["edge_id"] for f in geojson_mod["features"]}
        for feat in base_geojson["features"]:
            eid = feat["properties"]["edge_id"]
            if eid not in mod_ids and eid in sign_map:
                closed = copy.deepcopy(feat)
                closed["properties"]["closed"]    = True
                closed["properties"]["sign"]      = sign_map[eid]["type"]
                closed["properties"]["vehicles"]  = 0
                closed["properties"]["veh_delta"] = -feat["properties"]["vehicles"]
                closed["properties"]["delta"]     = -feat["properties"]["score"]
                geojson_mod["features"].append(closed)

        return jsonify({
            "geojson":    geojson_mod,
            "stats":      compute_stats(geojson_mod),
            "base_stats": base_stats,
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def reset():
    return jsonify({"geojson": base_geojson, "stats": base_stats})


# Déclenché à l'import (gunicorn) ET à l'exécution directe
_startup()

if __name__ == "__main__":
    print("\nServeur prêt → http://localhost:5000")
    app.run(debug=False, port=5000, use_reloader=False)
