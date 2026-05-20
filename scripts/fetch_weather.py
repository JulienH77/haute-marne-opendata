#!/usr/bin/env python3
"""
fetch_weather.py
----------------
Interroge l'API Open-Meteo (https://open-meteo.com, CC BY 4.0)
pour une grille de points sur la France et la Haute-Marne.

Utilise des requêtes GET avec chunks de 50 points max pour éviter
l'erreur 414 "Request-URI Too Large" (500 pts en GET = URL > 8 Ko).
50 pts × ~35 chars = ~1750 chars par URL, bien sous la limite.
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
        "resolution": 0.5,
        "raster_size": (400, 300),
    },
    "haute-marne": {
        "bbox": (4.70, 47.50, 5.95, 48.65),
        "resolution": 0.07,
        "raster_size": (500, 500),
    },
}

VARIABLES = {
    "temperature_2m": {
        "label": "Température (°C)", "cmap": "RdYlBu_r",
        "unit": "°C", "vmin": -20, "vmax": 45,
    },
    "precipitation": {
        "label": "Précipitations (mm/h)", "cmap": "Blues",
        "unit": "mm/h", "vmin": 0, "vmax": 15,
    },
    "cloud_cover": {
        "label": "Couverture nuageuse (%)", "cmap": "Greys",
        "unit": "%", "vmin": 0, "vmax": 100,
    },
    "wind_speed_10m": {
        "label": "Vitesse du vent (m/s)", "cmap": "YlOrRd",
        "unit": "m/s", "vmin": 0, "vmax": 30,
    },
}

OUTPUT_DIR = "data/weather"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# 50 points par requête GET = URL ~1700 chars, bien sous la limite de 8 Ko
CHUNK_SIZE = 50
RETRY_DELAY = 5


# ------------------------------------------------------------
# Grille de points
# ------------------------------------------------------------

def generate_grid_points(bbox, resolution):
    lon_min, lat_min, lon_max, lat_max = bbox
    lons = np.arange(lon_min, lon_max + resolution * 0.5, resolution)
    lats = np.arange(lat_min, lat_max + resolution * 0.5, resolution)
    g_lon, g_lat = np.meshgrid(lons, lats)
    return g_lon.flatten(), g_lat.flatten()


# ------------------------------------------------------------
# Appel API Open-Meteo (GET, multi-points par virgules)
# ------------------------------------------------------------

def fetch_openmeteo_chunk(lats, lons, retries=3):
    """
    Requête GET avec latitude/longitude séparés par des virgules.
    Open-Meteo accepte jusqu'à ~100 points en GET tant que l'URL reste raisonnable.
    Avec CHUNK_SIZE=50 l'URL fait ~1700 chars — pas de risque de 414.
    """
    params = {
        "latitude":  ",".join(f"{v:.4f}" for v in lats),
        "longitude": ",".join(f"{v:.4f}" for v in lons),
        "current":   ",".join(VARIABLES.keys()),
        "wind_speed_unit": "ms",
        "timezone":  "Europe/Paris",
        "forecast_days": 1,
    }

    for attempt in range(retries):
        try:
            resp = requests.get(OPENMETEO_URL, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                data = [data]
            return data
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"   ⚠️  Tentative {attempt + 1}/{retries} : {e}. Retry dans {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                raise


def extract_records(api_results):
    records = []
    for result in api_results:
        current = result.get("current", {})
        record = {"lat": result["latitude"], "lon": result["longitude"]}
        for var in VARIABLES:
            record[var] = current.get(var)
        records.append(record)
    return records


# ------------------------------------------------------------
# GeoJSON
# ------------------------------------------------------------

def save_geojson(records, zone_name, timestamp):
    features = []
    for r in records:
        if r.get("temperature_2m") is None:
            continue
        props = {k: v for k, v in r.items() if k not in ("lat", "lon")}
        props["last_updated"] = timestamp
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(r["lon"], 4), round(r["lat"], 4)]},
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
        },
        "features": features,
    }

    path = os.path.join(OUTPUT_DIR, f"{zone_name}_weather.geojson")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"✓  {path} ({len(features)} points)")


# ------------------------------------------------------------
# PNG raster
# ------------------------------------------------------------

def save_raster_png(records, zone_name, var_name, bbox, raster_size):
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
    g_lon = np.linspace(lon_min, lon_max, w)
    g_lat = np.linspace(lat_min, lat_max, h)
    g_lon2d, g_lat2d = np.meshgrid(g_lon, g_lat)

    grid_vals = griddata((pts_lon, pts_lat), pts_val, (g_lon2d, g_lat2d), method="linear")

    var_info = VARIABLES[var_name]
    fig, ax = plt.subplots(figsize=(w / 100, h / 100), dpi=100)
    im = ax.imshow(
        grid_vals, origin="lower",
        extent=[lon_min, lon_max, lat_min, lat_max],
        cmap=var_info["cmap"], vmin=var_info["vmin"], vmax=var_info["vmax"],
        interpolation="bilinear", aspect="auto",
    )
    cbar = plt.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label(var_info["label"], fontsize=9)
    ax.set_title(f"{var_info['label']} — {zone_name.replace('-', ' ').title()}", fontsize=10, pad=6)
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

def process_zone(zone_name, zone_config, timestamp):
    print(f"\n📍 Zone : {zone_name}")
    lons, lats = generate_grid_points(zone_config["bbox"], zone_config["resolution"])
    total = len(lons)
    print(f"   Grille : {total} points — {(total + CHUNK_SIZE - 1) // CHUNK_SIZE} requêtes GET de {CHUNK_SIZE} pts max")

    all_records = []
    for i in range(0, total, CHUNK_SIZE):
        chunk_lats = lats[i : i + CHUNK_SIZE]
        chunk_lons = lons[i : i + CHUNK_SIZE]
        n_end = min(i + CHUNK_SIZE, total)
        print(f"   Chunk {i + 1}–{n_end}...")
        results = fetch_openmeteo_chunk(chunk_lats, chunk_lons)
        all_records.extend(extract_records(results))
        if n_end < total:
            time.sleep(0.2)  # Courtoisie envers l'API

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
        print(f"\n⚠️  {len(errors)} erreur(s)", file=sys.stderr)
        sys.exit(1)

    print("\n✅ Données météo mises à jour avec succès.")


if __name__ == "__main__":
    main()
