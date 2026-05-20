#!/usr/bin/env python3
"""
fetch_weather.py
----------------
Interroge l'API Open-Meteo (https://open-meteo.com, CC BY 4.0)
pour une grille de points sur la France et la Haute-Marne.

Pour chaque zone, génère :
  - Un GeoJSON de points avec toutes les variables courantes
  - Des PNG rasters interpolés (un par variable)

Variables récupérées :
  - temperature_2m       Température à 2m (°C)
  - precipitation        Précipitations (mm/h)
  - cloud_cover          Couverture nuageuse (%)
  - wind_speed_10m       Vitesse du vent à 10m (m/s)

Outputs :
  data/weather/<zone>_weather.geojson
  data/weather/<zone>_<variable>.png
  data/weather/metadata.json
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import requests

try:
    from scipy.interpolate import griddata
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_PLOTTING = True
except ImportError:
    HAS_PLOTTING = False
    print("⚠️  scipy/matplotlib non disponibles — génération PNG désactivée")


# ------------------------------------------------------------
# Configuration des zones
# ------------------------------------------------------------

ZONES = {
    "france": {
        "bbox": (-5.14, 41.33, 9.56, 51.09),
        "resolution": 0.5,       # degrés entre chaque point
        "raster_size": (400, 300),
    },
    "haute-marne": {
        "bbox": (4.70, 47.50, 5.95, 48.65),
        "resolution": 0.07,
        "raster_size": (500, 500),
    },
}

# ------------------------------------------------------------
# Variables météo et paramètres de visualisation
# ------------------------------------------------------------

VARIABLES = {
    "temperature_2m": {
        "label": "Température (°C)",
        "cmap": "RdYlBu_r",
        "unit": "°C",
        "vmin": -20,
        "vmax": 45,
    },
    "precipitation": {
        "label": "Précipitations (mm/h)",
        "cmap": "Blues",
        "unit": "mm/h",
        "vmin": 0,
        "vmax": 15,
    },
    "cloud_cover": {
        "label": "Couverture nuageuse (%)",
        "cmap": "Greys",
        "unit": "%",
        "vmin": 0,
        "vmax": 100,
    },
    "wind_speed_10m": {
        "label": "Vitesse du vent (m/s)",
        "cmap": "YlOrRd",
        "unit": "m/s",
        "vmin": 0,
        "vmax": 30,
    },
}

OUTPUT_DIR = "data/weather"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# Réduit à 100 par sécurité — l'API multi-points accepte jusqu'à ~100 par requête POST
CHUNK_SIZE = 100
RETRY_DELAY = 5    # secondes entre tentatives


# ------------------------------------------------------------
# Fonctions utilitaires
# ------------------------------------------------------------

def generate_grid_points(bbox: tuple, resolution: float):
    """Génère une grille régulière de points (lon, lat) dans la bbox."""
    lon_min, lat_min, lon_max, lat_max = bbox
    lons = np.arange(lon_min, lon_max + resolution * 0.5, resolution)
    lats = np.arange(lat_min, lat_max + resolution * 0.5, resolution)
    grid_lons, grid_lats = np.meshgrid(lons, lats)
    return grid_lons.flatten(), grid_lats.flatten()


def fetch_openmeteo_chunk(lats: np.ndarray, lons: np.ndarray, retries: int = 3) -> list:
    """
    Interroge l'API Open-Meteo pour un chunk de points via POST (JSON body).
    Évite l'erreur 414 "Request-URI Too Large" des requêtes GET avec beaucoup de points.
    Retourne une liste de dicts de résultats.
    """
    var_list = list(VARIABLES.keys())

    # Corps JSON envoyé en POST — pas de limite de taille d'URL
    payload = {
        "latitude":  [round(float(v), 4) for v in lats],
        "longitude": [round(float(v), 4) for v in lons],
        "current":   var_list,
        "wind_speed_unit": "ms",
        "timezone":  "Europe/Paris",
        "forecast_days": 1,
    }

    headers = {"Content-Type": "application/json"}

    for attempt in range(retries):
        try:
            resp = requests.post(
                OPENMETEO_URL,
                json=payload,
                headers=headers,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            # Si un seul point, l'API retourne un objet (pas une liste)
            if isinstance(data, dict):
                data = [data]
            return data
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"   ⚠️  Tentative {attempt + 1}/{retries} échouée : {e}. Retry dans {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                raise


def extract_records(api_results: list) -> list:
    """Extrait les valeurs courantes de chaque point de la réponse API."""
    records = []
    for result in api_results:
        current = result.get("current", {})
        record = {
            "lat": result["latitude"],
            "lon": result["longitude"],
        }
        for var in VARIABLES:
            record[var] = current.get(var)
        records.append(record)
    return records


# ------------------------------------------------------------
# Sauvegarde GeoJSON
# ------------------------------------------------------------

def save_geojson(records: list, zone_name: str, timestamp: str) -> None:
    """Génère et sauvegarde le GeoJSON de points météo pour une zone."""
    features = []
    for r in records:
        if r.get("temperature_2m") is None:
            continue
        props = {k: v for k, v in r.items() if k not in ("lat", "lon")}
        props["last_updated"] = timestamp
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(r["lon"], 4), round(r["lat"], 4)],
            },
            "properties": props,
        })

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "Open-Meteo (https://open-meteo.com)",
            "license": "CC BY 4.0",
            "attribution": "Weather data by Open-Meteo.com",
            "last_updated": timestamp,
            "zone": zone_name,
            "nb_points": len(features),
            "variables": {k: {"label": v["label"], "unit": v["unit"]} for k, v in VARIABLES.items()},
        },
        "features": features,
    }

    path = os.path.join(OUTPUT_DIR, f"{zone_name}_weather.geojson")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"✓  {path} ({len(features)} points)")


# ------------------------------------------------------------
# Génération des PNG rasters
# ------------------------------------------------------------

def save_raster_png(records: list, zone_name: str, var_name: str, bbox: tuple, raster_size: tuple) -> None:
    """Génère un PNG raster interpolé (méthode linéaire) pour une variable."""
    if not HAS_PLOTTING:
        return

    valid = [(r["lon"], r["lat"], r[var_name]) for r in records if r.get(var_name) is not None]
    if len(valid) < 4:
        print(f"   ⚠️  Pas assez de points valides pour {var_name} sur {zone_name}")
        return

    pts_lon = np.array([v[0] for v in valid])
    pts_lat = np.array([v[1] for v in valid])
    pts_val = np.array([v[2] for v in valid])

    lon_min, lat_min, lon_max, lat_max = bbox
    w, h = raster_size
    grid_lons = np.linspace(lon_min, lon_max, w)
    grid_lats = np.linspace(lat_min, lat_max, h)
    grid_lons2d, grid_lats2d = np.meshgrid(grid_lons, grid_lats)

    grid_vals = griddata(
        (pts_lon, pts_lat), pts_val,
        (grid_lons2d, grid_lats2d),
        method="linear",
    )

    var_info = VARIABLES[var_name]
    fig, ax = plt.subplots(figsize=(w / 100, h / 100), dpi=100)

    im = ax.imshow(
        grid_vals,
        origin="lower",
        extent=[lon_min, lon_max, lat_min, lat_max],
        cmap=var_info["cmap"],
        vmin=var_info["vmin"],
        vmax=var_info["vmax"],
        interpolation="bilinear",
        aspect="auto",
    )

    cbar = plt.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label(f"{var_info['label']}", fontsize=9)
    ax.set_title(
        f"{var_info['label']} — {zone_name.replace('-', ' ').title()}",
        fontsize=10,
        pad=6,
    )
    ax.set_xlabel("Longitude", fontsize=8)
    ax.set_ylabel("Latitude", fontsize=8)
    ax.tick_params(labelsize=7)

    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"{zone_name}_{var_name}.png")
    plt.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    print(f"✓  {path}")


# ------------------------------------------------------------
# Traitement d'une zone
# ------------------------------------------------------------

def process_zone(zone_name: str, zone_config: dict, timestamp: str) -> None:
    """Télécharge les données météo et génère tous les outputs pour une zone."""
    print(f"\n📍 Zone : {zone_name}")
    lons, lats = generate_grid_points(zone_config["bbox"], zone_config["resolution"])
    print(f"   Grille : {len(lons)} points (résolution {zone_config['resolution']}°)")

    all_records = []
    for i in range(0, len(lats), CHUNK_SIZE):
        chunk_lats = lats[i : i + CHUNK_SIZE]
        chunk_lons = lons[i : i + CHUNK_SIZE]
        n_end = min(i + CHUNK_SIZE, len(lats))
        print(f"   Requête POST points {i + 1}–{n_end}...")
        results = fetch_openmeteo_chunk(chunk_lats, chunk_lons)
        all_records.extend(extract_records(results))
        if n_end < len(lats):
            time.sleep(0.3)  # Délai léger entre chunks

    save_geojson(all_records, zone_name, timestamp)

    for var_name in VARIABLES:
        save_raster_png(all_records, zone_name, var_name, zone_config["bbox"], zone_config["raster_size"])


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    errors = []
    for zone_name, zone_config in ZONES.items():
        try:
            process_zone(zone_name, zone_config, timestamp)
        except Exception as e:
            msg = f"Erreur sur {zone_name} : {e}"
            print(f"❌ {msg}", file=sys.stderr)
            errors.append(msg)

    # Metadata globale
    metadata = {
        "last_updated": timestamp,
        "source": "Open-Meteo (https://open-meteo.com)",
        "license": "CC BY 4.0",
        "attribution": "Weather data by Open-Meteo.com",
        "zones": list(ZONES.keys()),
        "variables": {k: {"label": v["label"], "unit": v["unit"]} for k, v in VARIABLES.items()},
        "errors": errors,
    }
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {meta_path}")

    if errors:
        print(f"\n⚠️  {len(errors)} erreur(s) — voir metadata.json", file=sys.stderr)
        sys.exit(1)

    print("\n✅ Données météo mises à jour avec succès.")


if __name__ == "__main__":
    main()
