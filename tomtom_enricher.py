"""
Enrichissement TomTom : FRC + facteur de congestion par segment OSM.

Pour chaque segment du graphe, on interroge TomTom Flow Segment Data
au point médian du segment. On récupère :
  - functionalRoadClass (FRC0-FRC6) → calibration AADT_MAX plus précise qu'OSM
  - currentSpeed / freeFlowSpeed    → facteur de congestion

Les résultats sont mis en cache dans tomtom_cache.json pour ne pas
re-consommer les 2 500 req/jour du tier gratuit.

Usage :
    python tomtom_enricher.py --key YOUR_API_KEY
    python tomtom_enricher.py --key YOUR_API_KEY --force   # recalcul complet
"""

import argparse
import json
import os
import pickle
import time

import requests

CACHE_FILE = os.path.join(os.path.dirname(__file__), "precomputed", "tomtom_cache.json")
API_BASE   = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

# FRC TomTom → AADT_MAX (véhicules/jour, milieu semi-urbain wallon)
FRC_AADT_MAX = {
    "FRC0": 55000,   # Autoroute
    "FRC1": 30000,   # Route principale majeure
    "FRC2": 20000,   # Route principale
    "FRC3": 11000,   # Route secondaire
    "FRC4":  5000,   # Route de liaison locale
    "FRC5":  1800,   # Route locale importante
    "FRC6":   600,   # Route locale
}


def midpoint(geom):
    """Retourne le point médian d'un segment LineString GeoJSON."""
    coords = geom.get("coordinates", [])
    if not coords:
        return None
    mid = coords[len(coords) // 2]
    return mid[1], mid[0]   # lat, lng


def query_tomtom(lat, lng, api_key, zoom=15):
    """Interroge TomTom Flow Segment Data pour un point."""
    url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/{zoom}/json"
    params = {
        "key":   api_key,
        "point": f"{lat},{lng}",
    }
    try:
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            return r.json().get("flowSegmentData", {})
        return None
    except Exception:
        return None


def enrich(api_key, force=False, delay=0.5):
    """
    Enrichit les segments OSM avec les données TomTom.

    Retourne un dict :
      { edge_id -> {"frc": "FRC3", "aadt_max": 11000,
                    "congestion": 0.82, "freeflow_speed": 50} }
    """
    with open(os.path.join(os.path.dirname(__file__), "precomputed", "data.pkl"), "rb") as f:
        cache = pickle.load(f)
    G       = cache["G_original"]
    geojson = cache["base_geojson"]

    # Charger le cache existant
    existing = {}
    if os.path.exists(CACHE_FILE) and not force:
        with open(CACHE_FILE, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"Cache TomTom existant : {len(existing)} segments déjà enrichis")

    # Sélectionner les segments à interroger :
    # on ignore les résidentielles (trop nombreuses, FRC souvent FRC5/6 de toute façon)
    SKIP_HW = {"residential", "living_street", "service", "footway",
               "cycleway", "path", "track"}

    to_query = []
    for feat in geojson["features"]:
        p = feat["properties"]
        eid = p["edge_id"]
        if eid in existing:
            continue
        if p.get("highway", "residential") in SKIP_HW:
            continue
        mid = midpoint(feat["geometry"])
        if mid:
            to_query.append((eid, mid, feat["geometry"]))

    print(f"Segments à interroger : {len(to_query)}")
    if not to_query:
        print("Rien de nouveau à interroger.")
        return existing

    results = dict(existing)
    errors  = 0

    for i, (eid, (lat, lng), geom) in enumerate(to_query):
        data = query_tomtom(lat, lng, api_key)
        if data:
            frc  = data.get("functionalRoadClass", "FRC4")
            ff   = data.get("freeFlowSpeed", 50)
            curr = data.get("currentSpeed", ff)
            conf = data.get("confidence", 1.0)
            cong = round(curr / ff, 3) if ff > 0 else 1.0   # 1.0 = fluide

            results[eid] = {
                "frc":            frc,
                "aadt_max":       FRC_AADT_MAX.get(frc, 2000),
                "freeflow_speed": ff,
                "congestion":     cong,   # < 1 = congestion, > 1 = rare (nuit)
                "confidence":     conf,
            }
        else:
            errors += 1

        # Sauvegarde intermédiaire toutes les 50 requêtes
        if (i + 1) % 50 == 0:
            _save(results)
            print(f"  {i+1}/{len(to_query)} — {errors} erreurs")

        time.sleep(delay)   # respecter le rate limit

    _save(results)
    print(f"Enrichissement terminé : {len(results)} segments, {errors} erreurs")
    return results


def _save(data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_enrichment():
    """Charge le cache TomTom si disponible, sinon retourne {}."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key",   required=True, help="TomTom API key")
    parser.add_argument("--force", action="store_true", help="Recalcul complet")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Délai entre requêtes en secondes (défaut 0.5)")
    args = parser.parse_args()
    enrich(args.key, force=args.force, delay=args.delay)
