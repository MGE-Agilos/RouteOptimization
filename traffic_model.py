"""
Modèle de trafic hybride pour Ottignies-Louvain-la-Neuve.

Score (0-100)  : simulation OD pondérée par POI → ranking relatif réaliste
Véhicules/jour : betweenness centrality intra-type, ancré sur la base
  → valeurs absolues calées sur les AADT belges
  → quand une route est fermée : betweenness → 0 → véhicules → 0
  → les routes voisines reçoivent le surplus (betweenness redistribué)
"""

import osmnx as ox
import networkx as nx
import numpy as np
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore")

# ── Poids attracteurs POI ────────────────────────────────────────────────────
POI_WEIGHTS = {
    "university": 12, "college": 9, "school": 7,
    "hospital": 8, "clinic": 4,
    "station": 10, "bus_station": 6,
    "supermarket": 5, "mall": 7, "marketplace": 5,
    "retail": 4, "commercial": 4, "industrial": 3,
    "restaurant": 2, "cafe": 1.5, "bar": 1.5,
    "bank": 2, "pharmacy": 2, "doctors": 2,
    "office": 3, "government": 3, "shop": 2,
    "park": 1, "place_of_worship": 1, "community_centre": 1.5,
    "sports_centre": 2, "leisure": 1,
}

# ── Références AADT Belgique (véhicules/jour) ────────────────────────────────
# Sources : SPW Wallonie, comptages autoroutiers, littérature transport
# Plafond = route la plus chargée de ce type en milieu semi-urbain wallon
AADT_MAX = {
    "motorway": 55000, "motorway_link": 28000,
    "trunk":    30000, "trunk_link":    15000,
    "primary":  20000, "primary_link":  10000,
    "secondary":11000, "secondary_link": 5500,
    "tertiary":  5000, "tertiary_link":  2500,
    "residential":1500, "living_street":  200,
    "unclassified":1800, "service":        400,
}

HIGHWAY_WEIGHTS = {
    "motorway": 1.0, "motorway_link": 0.9,
    "trunk": 0.85, "trunk_link": 0.8,
    "primary": 0.75, "primary_link": 0.7,
    "secondary": 0.55, "secondary_link": 0.5,
    "tertiary": 0.35, "tertiary_link": 0.3,
    "residential": 0.15, "living_street": 0.08,
    "unclassified": 0.2, "service": 0.1,
}


def _hw(data):
    hw = data.get("highway", "residential")
    if isinstance(hw, list): hw = hw[0]
    return str(hw)


def _poi_weight(row):
    for tag in ["amenity", "shop", "office", "landuse", "leisure", "public_transport"]:
        val = row.get(tag)
        if val and not isinstance(val, float):
            if val in POI_WEIGHTS: return POI_WEIGHTS[val]
            if tag == "shop":   return 2.0
            if tag == "office": return 3.0
            if tag == "public_transport": return 4.0
            if tag == "landuse" and val in ("retail", "commercial"): return 3.0
            if tag == "landuse" and val == "industrial": return 2.5
    return 1.0


# ── 1. Chargement POI ────────────────────────────────────────────────────────

def load_poi_attractiveness(G, place_name):
    """Attractivité nodal basée sur les POI OSM proches."""
    tags = {
        "amenity": True, "shop": True, "office": True,
        "public_transport": True,
        "landuse": ["retail", "commercial", "industrial", "residential"],
        "leisure": ["sports_centre", "stadium", "park"],
    }
    print("  Téléchargement des points d'intérêt OSM...")
    try:
        features = ox.features_from_place(place_name, tags=tags)
    except Exception as e:
        print(f"  /!\\ POI non disponibles: {e}")
        return None, {}

    poi_x, poi_y, poi_w = [], [], []
    for _, row in features.iterrows():
        try:
            c = row.geometry.centroid
            poi_x.append(c.x); poi_y.append(c.y); poi_w.append(_poi_weight(row))
        except Exception:
            continue

    n_poi = len(poi_x)
    print(f"  {n_poi} points d'intérêt chargés")
    if n_poi == 0:
        return None, {}

    nearest = ox.nearest_nodes(G, X=poi_x, Y=poi_y)
    attractiveness = {n: 1.0 for n in G.nodes()}
    for node_id, w in zip(nearest, poi_w):
        attractiveness[node_id] = attractiveness.get(node_id, 1.0) + w

    meta = {"n_poi": n_poi,
            "top_attractors": sorted(attractiveness.items(), key=lambda x: -x[1])[:5]}
    return attractiveness, meta


# ── 2. Score OD (ranking relatif) ────────────────────────────────────────────

def simulate_od_traffic(G, attractiveness, n_samples=3000, seed=42):
    """Simulation OD → compte de traversées par arête (pour le score 0-100)."""
    np.random.seed(seed)
    node_ids = list(G.nodes())
    if not node_ids:
        return {}, 0

    weights = np.array([attractiveness.get(n, 1.0) for n in node_ids], dtype=float)
    weights /= weights.sum()

    edge_counts = {(u, v, k): 0 for u, v, k in G.edges(keys=True)}
    origins = np.random.choice(len(node_ids), size=n_samples, p=weights)
    dests   = np.random.choice(len(node_ids), size=n_samples, p=weights)

    hits = 0
    for i, j in zip(origins, dests):
        o, d = node_ids[i], node_ids[j]
        if o == d: continue
        try:
            path = nx.shortest_path(G, o, d, weight="length")
            hits += 1
            for a, b in zip(path[:-1], path[1:]):
                for k in G[a][b]:
                    edge_counts[(a, b, k)] = edge_counts.get((a, b, k), 0) + 1
                    break
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    print(f"  {hits}/{n_samples} trajets OD routés")
    return edge_counts, hits


def od_scores(G, od_counts):
    """Score 0-100 à partir des comptes OD (normalisation globale)."""
    max_od = max(od_counts.values(), default=1)
    scores = {}
    for u, v, k, data in G.edges(keys=True, data=True):
        od     = od_counts.get((u, v, k), 0)
        hw     = _hw(data)
        hw_w   = HIGHWAY_WEIGHTS.get(hw, 0.2)
        lanes  = data.get("lanes", 1)
        if isinstance(lanes, list): lanes = lanes[0]
        try:    lanes = max(1, min(int(lanes), 4))
        except: lanes = 1
        od_norm = od / max_od if max_od > 0 else 0
        score   = (0.70 * od_norm + 0.20 * hw_w + 0.10 / lanes) * 100
        scores[(u, v, k)] = min(100.0, round(score, 1))
    return scores


# ── 3. Véhicules par betweenness intra-type (ancré sur la base) ─────────────

def compute_vehicles(G, base_type_max_bc=None, k=200, tomtom=None):
    """
    Betweenness centrality intra-type → véhicules/jour.

    Si tomtom est fourni ({edge_id: {frc, aadt_max, congestion, …}}),
    l'AADT_MAX utilisé pour chaque segment est remplacé par la valeur
    TomTom (basée sur le functionalRoadClass), plus précise qu'OSM.
    Le facteur de congestion TomTom (currentSpeed/freeFlowSpeed) est
    appliqué en inverse : moins de vitesse → plus de trafic.

    Args:
        G               : graphe (base ou modifié)
        base_type_max_bc: {hw: max_bc} calculé sur le graphe de base.
        k               : nb de sources pour l'approximation betweenness.
        tomtom          : dict enrichissement TomTom ou None.

    Returns:
        vehicles        : {(u,v,key): int}
        type_max_bc     : {hw: float}
    """
    k_actual = min(k, G.number_of_nodes())
    edge_bc  = nx.edge_betweenness_centrality(
        G, normalized=False, weight="length", k=k_actual
    )

    type_bc_values = defaultdict(list)
    for (u, v, key), bc in edge_bc.items():
        hw = _hw(G[u][v][key])
        type_bc_values[hw].append(((u, v, key), bc))

    if base_type_max_bc is None:
        anchor = {hw: max((bc for _, bc in items), default=1e-9)
                  for hw, items in type_bc_values.items()}
    else:
        anchor = base_type_max_bc

    vehicles = {}
    for hw, items in type_bc_values.items():
        hw_anchor = max(anchor.get(hw, 1e-9), 1e-9)
        aadt_max_default = AADT_MAX.get(hw, 1500)

        for (u, v, key), bc in items:
            bc_norm  = bc / hw_anchor
            edge_id  = f"{u}:{v}:{key}"
            tt       = (tomtom or {}).get(edge_id)

            if tt:
                # TomTom disponible : AADT_MAX basé sur FRC + facteur congestion
                aadt_max   = tt["aadt_max"]
                congestion = tt.get("congestion", 1.0)
                # congestion < 1 = ralenti = plus de trafic → inverse
                cong_factor = min(1.0 / max(congestion, 0.1), 2.0)
                veh = int(min(bc_norm, 2.5) * aadt_max * cong_factor)
            else:
                veh = int(min(bc_norm, 2.5) * aadt_max_default)

            vehicles[(u, v, key)] = veh

    for u, v, key in G.edges(keys=True):
        if (u, v, key) not in vehicles:
            vehicles[(u, v, key)] = 0

    return vehicles, anchor
