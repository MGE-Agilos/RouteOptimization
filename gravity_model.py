"""
Modèle gravitaire avec zones externes (communes adjacentes).

Chaque commune externe est mappée sur un nœud PASSERELLE distinct,
calculé par projection directionnelle sur la frontière du graphe OLLN.
Cela évite que plusieurs communes dans la même direction partagent
le même nœud d'entrée.

Formule : T_ij = K × mass_i × attract_j / d_ij²
"""

import numpy as np
from math import radians, sin, cos, sqrt, atan2

# ── Communes adjacentes ──────────────────────────────────────────────────────
# Sources : Statbel 2023 (population), ONSS 2022 (emplois estimés)
EXTERNAL_ZONES = {
    "Wavre":                   {"pop": 35_200, "emp": 18_000, "lat": 50.7175, "lon": 4.6015},
    "Rixensart":               {"pop": 22_000, "emp":  4_000, "lat": 50.7005, "lon": 4.7002},
    "Perwez":                  {"pop":  8_500, "emp":  2_000, "lat": 50.6280, "lon": 4.7930},
    "Mont-Saint-Guibert":      {"pop":  7_100, "emp":  4_200, "lat": 50.6178, "lon": 4.6930},
    "Gembloux":                {"pop": 27_400, "emp":  9_000, "lat": 50.5660, "lon": 4.7115},
    "Nil-Saint-Vincent":       {"pop":  3_500, "emp":    500, "lat": 50.4950, "lon": 4.6700},
    "Namur":                   {"pop":115_000, "emp": 52_000, "lat": 50.4674, "lon": 4.8717},
    "Court-Saint-Etienne":     {"pop": 12_200, "emp":  3_500, "lat": 50.6005, "lon": 4.5632},
    "Villers-la-Ville":        {"pop": 10_400, "emp":  2_000, "lat": 50.5892, "lon": 4.5290},
    "Braine-l-Alleud":         {"pop": 41_000, "emp": 14_000, "lat": 50.6820, "lon": 4.3700},
    "Bruxelles":               {"pop":185_000, "emp":320_000, "lat": 50.8503, "lon": 4.3517},
}

OLLN_CENTER = {"lat": 50.6712, "lon": 4.5754}


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _bbox(G):
    """Retourne (min_lat, max_lat, min_lon, max_lon) du graphe."""
    lats = [d["y"] for _, d in G.nodes(data=True)]
    lons = [d["x"] for _, d in G.nodes(data=True)]
    return min(lats), max(lats), min(lons), max(lons)


def _boundary_point(olln_lat, olln_lon, zone_lat, zone_lon, bbox, margin=0.08):
    """
    Projette un point sur la frontière de la bbox dans la direction
    OLLN-centre → zone, avec une marge pour rester dans le graphe.

    Returns (lat, lon) du point passerelle.
    """
    min_lat, max_lat, min_lon, max_lon = bbox
    dlat = zone_lat - olln_lat
    dlon = zone_lon - olln_lon

    # Paramètre t auquel le rayon atteint chaque bord de la bbox
    candidates = []
    if dlat > 0:
        candidates.append((max_lat - olln_lat) / dlat)
    elif dlat < 0:
        candidates.append((min_lat - olln_lat) / dlat)
    if dlon > 0:
        candidates.append((max_lon - olln_lon) / dlon)
    elif dlon < 0:
        candidates.append((min_lon - olln_lon) / dlon)

    t = min((c for c in candidates if c > 0), default=0.9)
    t *= (1 - margin)   # reculer légèrement à l'intérieur de la frontière

    return olln_lat + t * dlat, olln_lon + t * dlon


def find_gateway_nodes(G, zones=None):
    """
    Pour chaque zone externe, calcule le nœud passerelle par projection
    directionnelle sur la frontière du graphe OLLN.

    Returns {zone_name: node_id}
    """
    import osmnx as ox

    if zones is None:
        zones = EXTERNAL_ZONES

    bbox = _bbox(G)
    olln_lat = OLLN_CENTER["lat"]
    olln_lon = OLLN_CENTER["lon"]

    query_lats, query_lons = [], []
    for zone in zones.values():
        bp_lat, bp_lon = _boundary_point(
            olln_lat, olln_lon,
            zone["lat"], zone["lon"],
            bbox,
        )
        query_lats.append(bp_lat)
        query_lons.append(bp_lon)

    nearest = ox.nearest_nodes(G, X=query_lons, Y=query_lats)

    gateways = {}
    for name, node, bp_lat, bp_lon in zip(
        zones.keys(), nearest, query_lats, query_lons
    ):
        node_data = G.nodes[node]
        dist_km = haversine_km(bp_lat, bp_lon, node_data["y"], node_data["x"])
        print(f"  {name:28s} -> nœud {node}  "
              f"(point frontière ({bp_lat:.4f},{bp_lon:.4f}), "
              f"dist={dist_km:.2f} km)")
        gateways[name] = node

    # Vérifier les collisions
    from collections import Counter
    counts = Counter(gateways.values())
    collisions = {n: [z for z, nd in gateways.items() if nd == n]
                  for n, c in counts.items() if c > 1}
    if collisions:
        print("  /!\\ Collisions détectées :")
        for node, zones_list in collisions.items():
            print(f"       nœud {node} : {', '.join(zones_list)}")

    return gateways


def gravity_trips(G, attractiveness, gateways, n_internal=2000, n_external=2000, seed=42):
    """
    Génère des paires OD gravitaires (internes + externes → internes).

    Returns:
        edge_counts : {(u, v, key): int}
        n_routed    : int
    """
    np.random.seed(seed)
    import networkx as nx

    node_ids  = list(G.nodes())
    attr_arr  = np.array([attractiveness.get(n, 1.0) for n in node_ids], dtype=float)
    attr_arr /= attr_arr.sum()

    edge_counts = {(u, v, k): 0 for u, v, k in G.edges(keys=True)}
    n_routed    = 0

    def route(o, d):
        nonlocal n_routed
        if o == d:
            return
        try:
            path = nx.shortest_path(G, o, d, weight="length")
            n_routed += 1
            for a, b in zip(path[:-1], path[1:]):
                for k in G[a][b]:
                    edge_counts[(a, b, k)] = edge_counts.get((a, b, k), 0) + 1
                    break
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass

    # ── 1. Trajets internes ──────────────────────────────────────────────────
    origins = np.random.choice(len(node_ids), size=n_internal, p=attr_arr)
    dests   = np.random.choice(len(node_ids), size=n_internal, p=attr_arr)
    for i, j in zip(origins, dests):
        route(node_ids[i], node_ids[j])
    n_internal_routed = n_routed

    # ── 2. Trajets externes (gravitaire) ─────────────────────────────────────
    trips_per_zone = max(1, n_external // len(gateways))

    for zone_name, gateway in gateways.items():
        zone = EXTERNAL_ZONES[zone_name]
        mass = zone["pop"] + zone["emp"] * 2

        d_km = haversine_km(zone["lat"], zone["lon"],
                            OLLN_CENTER["lat"], OLLN_CENTER["lon"])
        d_km = max(d_km, 1.0)

        # Poids gravitaire : attractivité POI / distance²
        grav = np.array([
            attractiveness.get(n, 1.0) / (d_km ** 2)
            for n in node_ids
        ], dtype=float)
        if grav.sum() == 0:
            continue
        grav /= grav.sum()

        # Volume proportionnel à la masse (plafonné à 4× la base)
        volume = int(trips_per_zone * min(mass / 40_000, 4.0))
        if volume == 0:
            continue

        dest_indices = np.random.choice(len(node_ids), size=volume, p=grav)
        for idx in dest_indices[:volume // 2]:
            route(gateway, node_ids[idx])
        for idx in dest_indices[volume // 2:]:
            route(node_ids[idx], gateway)

    n_external_routed = n_routed - n_internal_routed
    print(f"  Gravity model : {n_routed} trajets routés "
          f"({n_internal_routed} internes + {n_external_routed} externes)")
    return edge_counts, n_routed
