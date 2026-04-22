"""
Modèle gravitaire avec zones externes et super-attracteurs internes.

Composantes :
  1. Zones externes  : communes adjacentes (population + emplois)
  2. Super-attracteurs : UCLouvain, Gare d'Ottignies, Parc Scientifique, E411
  3. Projection directionnelle sur la frontière du graphe pour les nœuds passerelles
"""

import numpy as np
from math import radians, sin, cos, sqrt, atan2

# ── Communes externes ────────────────────────────────────────────────────────
# Sources : Statbel 2023 (population), ONSS 2022 (emplois estimés)
EXTERNAL_ZONES = {
    "Wavre":                   {"pop": 35_200, "emp": 18_000, "lat": 50.7175, "lon": 4.6015},
    "Rixensart":               {"pop": 22_000, "emp":  4_000, "lat": 50.7005, "lon": 4.7002},
    "Grez-Doiceau":            {"pop": 13_200, "emp":  2_500, "lat": 50.7414, "lon": 4.6912},
    "Chaumont-Gistoux":        {"pop": 12_100, "emp":  2_000, "lat": 50.6728, "lon": 4.7264},
    "Perwez":                  {"pop":  8_500, "emp":  2_000, "lat": 50.6280, "lon": 4.7930},
    "Mont-Saint-Guibert":      {"pop":  7_100, "emp":  4_200, "lat": 50.6178, "lon": 4.6930},
    "Walhain":                 {"pop":  7_200, "emp":  1_200, "lat": 50.5820, "lon": 4.6710},
    "Gembloux":                {"pop": 27_400, "emp":  9_000, "lat": 50.5660, "lon": 4.7115},
    "Nil-Saint-Vincent":       {"pop":  3_500, "emp":    500, "lat": 50.4950, "lon": 4.6700},
    "Namur":                   {"pop":115_000, "emp": 52_000, "lat": 50.4674, "lon": 4.8717},
    "Court-Saint-Etienne":     {"pop": 12_200, "emp":  3_500, "lat": 50.6005, "lon": 4.5632},
    "Villers-la-Ville":        {"pop": 10_400, "emp":  2_000, "lat": 50.5892, "lon": 4.5290},
    "Nivelles":                {"pop": 28_500, "emp": 12_000, "lat": 50.5978, "lon": 4.3290},
    "Waterloo":                {"pop": 30_200, "emp":  8_000, "lat": 50.7153, "lon": 4.3990},
    "La-Hulpe":                {"pop":  8_100, "emp":  3_000, "lat": 50.7292, "lon": 4.4817},
    "Braine-l-Alleud":         {"pop": 41_000, "emp": 14_000, "lat": 50.6820, "lon": 4.3700},
    "Bruxelles":               {"pop":185_000, "emp":320_000, "lat": 50.8503, "lon": 4.3517},
}

# ── Super-attracteurs internes ───────────────────────────────────────────────
# Points majeurs générateurs de trafic DANS le graphe OLLN
# Chaque attracteur a une masse en "équivalents-voyages/jour"
INTERNAL_ATTRACTORS = {
    "UCLouvain-campus":     {"lat": 50.6683, "lon": 4.6152, "mass": 35_000},
    "Gare-Ottignies":       {"lat": 50.6730, "lon": 4.5701, "mass": 18_000},
    "Parc-Scientifique":    {"lat": 50.6510, "lon": 4.6350, "mass": 12_000},
    "E411-echangeur-8":     {"lat": 50.6461, "lon": 4.6297, "mass":  8_000},
    "Centre-Ottignies":     {"lat": 50.6665, "lon": 4.5720, "mass":  6_000},
    "Centre-LLN":           {"lat": 50.6690, "lon": 4.6110, "mass":  8_000},
}

OLLN_CENTER = {"lat": 50.6712, "lon": 4.5754}


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _bbox(G):
    lats = [d["y"] for _, d in G.nodes(data=True)]
    lons = [d["x"] for _, d in G.nodes(data=True)]
    return min(lats), max(lats), min(lons), max(lons)


def _boundary_point(olln_lat, olln_lon, zone_lat, zone_lon, bbox, margin=0.08):
    """Projette un point sur la frontière bbox dans la direction OLLN→zone."""
    min_lat, max_lat, min_lon, max_lon = bbox
    dlat = zone_lat - olln_lat
    dlon = zone_lon - olln_lon
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
    t *= (1 - margin)
    return olln_lat + t * dlat, olln_lon + t * dlon


def find_gateway_nodes(G, zones=None):
    """Nœuds passerelles pour zones externes (projection directionnelle)."""
    import osmnx as ox
    if zones is None:
        zones = EXTERNAL_ZONES
    bbox = _bbox(G)
    olln_lat, olln_lon = OLLN_CENTER["lat"], OLLN_CENTER["lon"]

    query_lats, query_lons = [], []
    for zone in zones.values():
        bp_lat, bp_lon = _boundary_point(olln_lat, olln_lon,
                                         zone["lat"], zone["lon"], bbox)
        query_lats.append(bp_lat)
        query_lons.append(bp_lon)

    nearest = ox.nearest_nodes(G, X=query_lons, Y=query_lats)
    gateways = {}
    for name, node, bp_lat, bp_lon in zip(zones.keys(), nearest, query_lats, query_lons):
        node_data = G.nodes[node]
        dist = haversine_km(bp_lat, bp_lon, node_data["y"], node_data["x"])
        print(f"  {name:28s} -> nœud {node} (dist={dist:.2f} km)")
        gateways[name] = node

    from collections import Counter
    counts = Counter(gateways.values())
    collisions = {n: [z for z, nd in gateways.items() if nd == n]
                  for n, c in counts.items() if c > 1}
    if collisions:
        print("  /!\\ Collisions :")
        for node, zlist in collisions.items():
            print(f"       nœud {node} : {', '.join(zlist)}")
    return gateways


def find_attractor_nodes(G):
    """Nœuds OSM les plus proches des super-attracteurs internes."""
    import osmnx as ox
    lats = [a["lat"] for a in INTERNAL_ATTRACTORS.values()]
    lons = [a["lon"] for a in INTERNAL_ATTRACTORS.values()]
    nearest = ox.nearest_nodes(G, X=lons, Y=lats)
    attractor_nodes = {}
    for name, node in zip(INTERNAL_ATTRACTORS.keys(), nearest):
        mass = INTERNAL_ATTRACTORS[name]["mass"]
        print(f"  {name:28s} -> nœud {node} (masse {mass:,} voy/jour)")
        attractor_nodes[name] = {"node": node, "mass": mass}
    return attractor_nodes


def gravity_trips(G, attractiveness, gateways, attractor_nodes=None,
                  n_internal=2000, n_external=2000, seed=42):
    """
    Génère des paires OD gravitaires.

    Trois composantes :
      1. Trajets internes OD (pondérés par attractivité POI)
      2. Trajets externes (communes adjacentes → nœuds internes)
      3. Trajets super-attracteurs (UCLouvain, Gare… ↔ reste du réseau)

    Returns:
        edge_counts : {(u, v, key): int}
        n_routed    : int
        scale       : float  (facteur de conversion → veh/jour)
    """
    np.random.seed(seed)
    import networkx as nx

    node_ids = list(G.nodes())
    attr_arr = np.array([attractiveness.get(n, 1.0) for n in node_ids], dtype=float)
    attr_arr /= attr_arr.sum()

    edge_counts = {(u, v, k): 0 for u, v, k in G.edges(keys=True)}
    n_routed = 0

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
    origs = np.random.choice(len(node_ids), size=n_internal, p=attr_arr)
    dests = np.random.choice(len(node_ids), size=n_internal, p=attr_arr)
    for i, j in zip(origs, dests):
        route(node_ids[i], node_ids[j])
    n_internal_routed = n_routed

    # ── 2. Trajets externes (gravitaire) ─────────────────────────────────────
    trips_per_zone = max(1, n_external // max(len(gateways), 1))
    for zone_name, gateway in gateways.items():
        zone = EXTERNAL_ZONES[zone_name]
        mass = zone["pop"] + zone["emp"] * 2
        d_km = max(haversine_km(zone["lat"], zone["lon"],
                                OLLN_CENTER["lat"], OLLN_CENTER["lon"]), 1.0)
        grav = np.array([attractiveness.get(n, 1.0) / (d_km**2)
                         for n in node_ids], dtype=float)
        if grav.sum() == 0:
            continue
        grav /= grav.sum()
        volume = int(trips_per_zone * min(mass / 40_000, 4.0))
        if volume == 0:
            continue
        dest_idx = np.random.choice(len(node_ids), size=volume, p=grav)
        for idx in dest_idx[:volume // 2]:
            route(gateway, node_ids[idx])
        for idx in dest_idx[volume // 2:]:
            route(node_ids[idx], gateway)
    n_external_routed = n_routed - n_internal_routed

    # ── 3. Super-attracteurs internes ────────────────────────────────────────
    n_attractor_routed = 0
    if attractor_nodes:
        for att_name, att_info in attractor_nodes.items():
            att_node = att_info["node"]
            att_mass = att_info["mass"]
            # Volume de trips proportionnel à la masse de l'attracteur
            volume = int(n_internal * min(att_mass / 30_000, 2.0))
            if volume == 0:
                continue
            # Origines pondérées par attractivité (tous les nœuds)
            orig_idx = np.random.choice(len(node_ids), size=volume, p=attr_arr)
            for idx in orig_idx[:volume // 2]:
                route(node_ids[idx], att_node)
            for idx in orig_idx[volume // 2:]:
                route(att_node, node_ids[idx])
            n_attractor_routed += volume

    n_routed_total = n_routed
    print(f"  Gravity : {n_routed_total} trajets routés "
          f"({n_internal_routed} internes "
          f"+ {n_external_routed} externes "
          f"+ {n_routed_total - n_internal_routed - n_external_routed} attracteurs)")

    # Facteur de conversion trajets simulés → veh/jour
    # Population OLLN ~33k + zones externes ~600k → ~150k voy/jour auto
    total_daily_trips = 150_000
    n_sim = max(n_routed_total, 1)
    scale = total_daily_trips / n_sim

    return edge_counts, n_routed_total, scale
