"""
Modèle de trafic hybride pour Ottignies-Louvain-la-Neuve.

Véhicules/jour = betweenness_veh + gravity_veh + local_access_veh
  betweenness : trafic de transit (structurel, ancré AADT belge)
  gravity     : flux OD gravitaires (communes + super-attracteurs)
  local_access: trafic d'accès riverains (bâtiments OSM)
Score (0-100) : vehicles / 100, plafonné à 100
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

# ── Boost de betweenness pour routes nationales (SPW Wallonie) ───────────────
# Les routes nationales en bordure du graphe sont sous-estimées par betweenness.
# Ces facteurs corrigent la part structurelle de trafic de transit sur les axes
# désignés. Sources : comptages SPW 2021-2023 + ajustement au graphe local.
NATIONAL_BC_BOOST = {
    "N275": 3.5,   # Chaussée de Bruxelles - axe N-S majeur Ottignies
    "N233": 3.0,   # Bvd Baudouin 1er / Rue de Namur - axe principal LLN
    "N237": 2.5,   # Av Provinciale / Av des Combattants
    "N239": 2.0,   # Avenue Albert 1er
    "N250": 2.0,   # Boulevard de Lauzelle
    "N232": 1.5,   # Chaussée de la Croix / Av Reine Astrid
    "N238a": 1.5,  # Avenue de Masaya
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


# ── 2. Trafic d'accès local (bâtiments OSM) ──────────────────────────────────

BUILDING_TRIPS = {
    "house": 10, "detached": 10, "semidetached_house": 8,
    "terrace": 6, "apartments": 15, "residential": 8,
    "commercial": 40, "retail": 45, "supermarket": 80, "mall": 120,
    "office": 25, "industrial": 20, "warehouse": 15,
    "school": 30, "university": 100, "college": 60,
    "hospital": 60, "clinic": 25,
    "hotel": 30, "restaurant": 20,
    "yes": 8,
}


def compute_local_access(G, place_name):
    """
    Trafic d'accès local à partir des bâtiments OSM.

    Chaque bâtiment génère un nombre de déplacements/jour (aller+retour)
    selon son type. Ces déplacements sont affectés au segment de route le
    plus proche du bâtiment.

    Returns:
        local_veh : {(u, v, key): int}
        n_buildings : int
    """
    print("  Téléchargement des bâtiments OSM...")
    try:
        buildings = ox.features_from_place(place_name, tags={"building": True})
    except Exception as e:
        print(f"  /!\\ Bâtiments non disponibles: {e}")
        return {(u, v, k): 0 for u, v, k in G.edges(keys=True)}, 0

    bld_x, bld_y, bld_trips = [], [], []
    for _, row in buildings.iterrows():
        try:
            c = row.geometry.centroid
            btype = row.get("building", "yes")
            if isinstance(btype, float):
                btype = "yes"
            trips = BUILDING_TRIPS.get(str(btype), BUILDING_TRIPS["yes"])
            bld_x.append(c.x)
            bld_y.append(c.y)
            bld_trips.append(trips)
        except Exception:
            continue

    n_buildings = len(bld_x)
    print(f"  {n_buildings} bâtiments chargés")
    if n_buildings == 0:
        return {(u, v, k): 0 for u, v, k in G.edges(keys=True)}, 0

    nearest_nodes = ox.nearest_nodes(G, X=bld_x, Y=bld_y)
    local_veh = {(u, v, k): 0 for u, v, k in G.edges(keys=True)}

    for node_id, trips in zip(nearest_nodes, bld_trips):
        for nbr in G.neighbors(node_id):
            for key in G[node_id][nbr]:
                edge = (node_id, nbr, key)
                if edge in local_veh:
                    local_veh[edge] += trips
                    break
            break

    total_local = sum(local_veh.values())
    print(f"  Trafic local total : {total_local:,} veh·traversées/jour")
    return local_veh, n_buildings


def vehicle_scores(vehicles, scale=100):
    """
    Score 0-100 basé uniquement sur le nombre de véhicules/jour.

    score = min(100, vehicles / scale)

    Avec scale=100 (défaut) :
      Critique (≥ 65) → ≥ 6 500 veh/jour
      Élevé    (≥ 40) → ≥ 4 000 veh/jour
      Modéré   (≥ 20) → ≥ 2 000 veh/jour
      Faible   (< 20) →  < 2 000 veh/jour
    """
    return {
        edge_id: min(100.0, round(veh / scale, 1))
        for edge_id, veh in vehicles.items()
    }


# ── 3. Véhicules par betweenness intra-type (ancré sur la base) ─────────────

def compute_vehicles(G, base_type_max_bc=None, k=200, tomtom=None,
                     gravity_counts=None, gravity_scale=1.0,
                     local_access=None):
    """
    Véhicules/jour = betweenness_veh + gravity_veh + local_access_veh.

    Args:
        G               : graphe (base ou modifié)
        base_type_max_bc: {hw: max_bc} calculé sur le graphe de base.
        k               : nb de sources pour l'approximation betweenness.
        tomtom          : dict enrichissement TomTom ou None.
        gravity_counts  : {(u,v,key): int} traversées simulées gravité.
        gravity_scale   : float, facteur de conversion traversées -> veh/jour.
        local_access    : {(u,v,key): int} veh/jour accès local bâtiments.

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
        # 90th percentile par type : évite qu'un seul outlier écrase tout le type
        anchor = {}
        for hw, items in type_bc_values.items():
            bcs = [bc for _, bc in items]
            anchor[hw] = float(np.percentile(bcs, 90)) if bcs else 1e-9
            anchor[hw] = max(anchor[hw], 1e-9)
    else:
        anchor = base_type_max_bc

    betweenness_veh = {}
    for hw, items in type_bc_values.items():
        hw_anchor = max(anchor.get(hw, 1e-9), 1e-9)
        aadt_max_default = AADT_MAX.get(hw, 1500)

        for (u, v, key), bc in items:
            bc_norm  = bc / hw_anchor
            edge_id  = f"{u}:{v}:{key}"
            tt       = (tomtom or {}).get(edge_id)

            # Boost conditionnel pour routes nationales sous-estimées (bc_norm < 0.3)
            # N'affecte que les routes en bordure de graphe, pas les axes déjà centraux
            ref = G[u][v][key].get("ref", "")
            if isinstance(ref, list): ref = ref[0] if ref else ""
            boost = NATIONAL_BC_BOOST.get(str(ref), 1.0)
            if boost > 1.0 and bc_norm < 0.30:
                bc_norm_boosted = min(bc_norm * boost, 0.85)
            else:
                bc_norm_boosted = min(bc_norm, 1.5)

            if tt:
                # Prendre le MAX entre OSM et TomTom : TomTom FRC peut sous-classer
                aadt_max    = max(aadt_max_default, tt["aadt_max"])
                congestion  = tt.get("congestion", 1.0)
                cong_factor = min(1.0 / max(congestion, 0.1), 2.0)
                veh = int(bc_norm_boosted * aadt_max * cong_factor)
            else:
                veh = int(bc_norm_boosted * aadt_max_default)

            betweenness_veh[(u, v, key)] = veh

    for u, v, key in G.edges(keys=True):
        if (u, v, key) not in betweenness_veh:
            betweenness_veh[(u, v, key)] = 0

    vehicles = {}
    for edge in G.edges(keys=True):
        u, v, key = edge
        bw  = betweenness_veh.get(edge, 0)
        gv  = int((gravity_counts or {}).get(edge, 0) * gravity_scale)
        lv  = (local_access or {}).get(edge, 0)
        vehicles[edge] = bw + gv + lv

    return vehicles, anchor
