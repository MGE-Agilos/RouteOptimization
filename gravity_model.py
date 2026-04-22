"""
Modèle gravitaire avec zones externes (communes adjacentes).

Complète la simulation OD interne en ajoutant des flux de transit
provenant des communes voisines d'OLLN.

Formule : T_ij = K × mass_i × attract_j / d_ij²
  mass_i     = population de la zone i (interne ou externe)
  attract_j  = attractivité POI de la zone j (nœud interne)
  d_ij       = distance routière estimée (vol d'oiseau × 1.3)
"""

import numpy as np
from math import radians, sin, cos, sqrt, atan2

# ── Communes adjacentes ──────────────────────────────────────────────────────
# Sources : Statbel 2023 (population), ONSS 2022 (emplois estimés)
EXTERNAL_ZONES = {
    "Wavre":                {"pop": 35_200, "emp": 18_000, "lat": 50.7175, "lon": 4.6015},
    "Court-Saint-Etienne":  {"pop": 12_200, "emp":  3_500, "lat": 50.6005, "lon": 4.5632},
    "Mont-Saint-Guibert":   {"pop":  7_100, "emp":  4_200, "lat": 50.6178, "lon": 4.6930},
    "Gembloux":             {"pop": 27_400, "emp":  9_000, "lat": 50.5660, "lon": 4.7115},
    "Villers-la-Ville":     {"pop": 10_400, "emp":  2_000, "lat": 50.5892, "lon": 4.5290},
    "Braine-l-Alleud":      {"pop": 41_000, "emp": 14_000, "lat": 50.6820, "lon": 4.3700},
    "Namur":                {"pop": 115_000,"emp": 52_000, "lat": 50.4674, "lon": 4.8717},
    "Bruxelles-centre":     {"pop": 185_000,"emp":320_000, "lat": 50.8503, "lon": 4.3517},
}

# OLLN (zone interne de référence)
OLLN_CENTER = {"lat": 50.6680, "lon": 4.6120}


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance orthodromique en km."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def find_gateway_nodes(G, zones):
    """
    Pour chaque zone externe, trouve le nœud OSM le plus proche
    du centroïde dans le graphe OLLN.
    Retourne {zone_name: node_id}.
    """
    import osmnx as ox
    lats = [z["lat"] for z in zones.values()]
    lons = [z["lon"] for z in zones.values()]
    nearest = ox.nearest_nodes(G, X=lons, Y=lats)
    return {name: node for name, node in zip(zones.keys(), nearest)}


def gravity_trips(G, attractiveness, gateways, n_internal=2000, n_external=1500, seed=42):
    """
    Génère des paires OD gravitaires (internes + externes → internes).

    Returns:
        edge_counts : {(u, v, key): int}  comptage sur chaque arête
        n_routed    : int
    """
    np.random.seed(seed)
    import networkx as nx

    node_ids   = list(G.nodes())
    node_index = {n: i for i, n in enumerate(node_ids)}

    # Vecteur d'attractivité interne (POI)
    attr_arr = np.array([attractiveness.get(n, 1.0) for n in node_ids], dtype=float)
    attr_arr /= attr_arr.sum()

    edge_counts = {(u, v, k): 0 for u, v, k in G.edges(keys=True)}
    n_routed    = 0

    # ── 1. Trajets internes (OD classique) ──────────────────────────────────
    origins = np.random.choice(len(node_ids), size=n_internal, p=attr_arr)
    dests   = np.random.choice(len(node_ids), size=n_internal, p=attr_arr)

    for i, j in zip(origins, dests):
        o, d = node_ids[i], node_ids[j]
        if o == d:
            continue
        try:
            path = nx.shortest_path(G, o, d, weight="length")
            n_routed += 1
            for a, b in zip(path[:-1], path[1:]):
                for k in G[a][b]:
                    edge_counts[(a, b, k)] = edge_counts.get((a, b, k), 0) + 1
                    break
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    # ── 2. Trajets externes (gravitaire) ────────────────────────────────────
    # Pour chaque zone externe, calcule un poids gravitaire vers chaque
    # nœud interne : w = mass_ext × attract_node / d²
    ext_trips_per_zone = n_external // max(len(gateways), 1)

    for zone_name, gateway in gateways.items():
        zone = EXTERNAL_ZONES[zone_name]
        mass = zone["pop"] + zone["emp"] * 2   # emplois comptent double

        # Distance vol d'oiseau zone→chaque nœud (approx via centroïde OLLN)
        # On utilise la distance zone→OLLN_CENTER × facteur position nœud
        d_base = haversine_km(zone["lat"], zone["lon"],
                              OLLN_CENTER["lat"], OLLN_CENTER["lon"])
        d_base = max(d_base, 1.0)

        # Poids gravitaire par nœud interne
        grav = np.array([
            attractiveness.get(n, 1.0) / (d_base ** 2)
            for n in node_ids
        ], dtype=float)
        grav_sum = grav.sum()
        if grav_sum == 0:
            continue
        grav /= grav_sum

        # Volume de trips proportionnel à la masse et inversement à la distance
        volume = int(ext_trips_per_zone * min(mass / 50_000, 3.0))
        if volume == 0:
            continue

        # Destinations internes tirées selon la gravité
        dest_indices = np.random.choice(len(node_ids), size=volume, p=grav)

        # Moitié entrée (gateway→dest), moitié sortie (dest→gateway)
        for idx in dest_indices[:volume // 2]:
            d_node = node_ids[idx]
            if gateway == d_node:
                continue
            try:
                path = nx.shortest_path(G, gateway, d_node, weight="length")
                n_routed += 1
                for a, b in zip(path[:-1], path[1:]):
                    for k in G[a][b]:
                        edge_counts[(a, b, k)] = edge_counts.get((a, b, k), 0) + 1
                        break
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue

        for idx in dest_indices[volume // 2:]:
            o_node = node_ids[idx]
            if o_node == gateway:
                continue
            try:
                path = nx.shortest_path(G, o_node, gateway, weight="length")
                n_routed += 1
                for a, b in zip(path[:-1], path[1:]):
                    for k in G[a][b]:
                        edge_counts[(a, b, k)] = edge_counts.get((a, b, k), 0) + 1
                        break
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue

    print(f"  Gravity model : {n_routed} trajets routés "
          f"({n_internal} internes + {n_routed - n_internal} externes)")
    return edge_counts, n_routed
