#!/usr/bin/env python3
"""
fetch_weather.py
----------------
Interroge l'API Open-Meteo pour une grille de points sur la France
et la Haute-Marne. Génère :
  - GeoJSON de points (chargeable directement dans QGIS via URL raw GitHub)
  - GeoTIFF géoréférencés par variable (chargeable dans QGIS comme raster)
  - CSV (bonus, lisible partout)
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import requests

try:
    from scipy.interpolate import griddata
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    print("⚠️  scipy non disponible — GeoTIFF désactivés")

try:
    import rasterio
    from rasterio.transform import from_bounds
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False
    print("⚠️  rasterio non disponible — GeoTIFF désactivés")

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
    "temperature_2m":  {"label": "Température (°C)",        "unit": "°C"},
    "precipitation":   {"label": "Précipitations (mm/h)",   "unit": "mm/h"},
    "cloud_cover":     {"label": "Couverture nuageuse (%)", "unit": "%"},
    "wind_speed_10m":  {"label": "Vitesse du vent (m/s)",   "unit": "m/s"},
}

OUTPUT_DIR   = "data/weather"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
CHUNK_SIZE   = 50    # 50 pts × ~35 chars ≈ 1750 chars/URL — pas de risque de 414
RETRY_DELAY  = 5


# ------------------------------------------------------------
# Grille & API
# ------------------------------------------------------------

def generate_grid_points(bbox, resolution):
    lon_min, lat_min, lon_max, lat_max = bbox
    lons = np.arange(lon_min, lon_max + resolution * 0.5, resolution)
    lats = np.arange(lat_min, lat_max + resolution * 0.5, resolution)
    g_lon, g_lat = np.meshgrid(lons, lats)
    return g_lon.flatten(), g_lat.flatten()


def fetch_openmeteo_chunk(lats, lons, retries=3):
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
            return [data] if isinstance(data, dict) else data
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"   ⚠️  Tentative {attempt+1}/{retries} : {e}. Retry dans {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                raise


def extract_records(api_results):
    records = []
    for r in api_results:
        current = r.get("current", {})
        rec = {"lat": r["latitude"], "lon": r["longitude"]}
        for var in VARIABLES:
            rec[var] = current.get(var)
        records.append(rec)
    return records


# ------------------------------------------------------------
# GeoJSON (chargeable dans QGIS via URL raw GitHub)
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
            "last_updated": timestamp,
            "zone": zone_name,
            "nb_points": len(features),
            "qgis_url": f"https://raw.githubusercontent.com/YOUR_USER/haute-marne-opendata/main/data/weather/{zone_name}_weather.geojson",
            "variables": {k: v for k, v in VARIABLES.items()},
        },
        "features": features,
    }

    path = os.path.join(OUTPUT_DIR, f"{zone_name}_weather.geojson")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"✓  {path} ({len(features)} points)")


# ------------------------------------------------------------
# CSV (bonus)
# ------------------------------------------------------------

def save_csv(records, zone_name, timestamp):
    valid = [r for r in records if r.get("temperature_2m") is not None]
    if not valid:
        return

    path = os.path.join(OUTPUT_DIR, f"{zone_name}_weather.csv")
    fieldnames = ["lon", "lat"] + list(VARIABLES.keys()) + ["last_updated"]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in valid:
            row = {k: round(r[k], 6) if isinstance(r.get(k), float) else r.get(k)
                   for k in fieldnames if k != "last_updated"}
            row["last_updated"] = timestamp
            writer.writerow(row)
    print(f"✓  {path} ({len(valid)} lignes)")


# ------------------------------------------------------------
# GeoTIFF géoréférencé (chargeable directement dans QGIS comme raster)
# ------------------------------------------------------------

def save_geotiff(records, zone_name, var_name, bbox, raster_size):
    """
    Génère un GeoTIFF EPSG:4326 interpolé (méthode linéaire scipy).
    Directement chargeable dans QGIS : Couche > Ajouter une couche raster
    ou par URL raw GitHub si le projet QGIS est configuré en HTTP.
    NoData = -9999.
    """
    if not (HAS_SCIPY and HAS_RASTERIO):
        return

    valid = [(r["lon"], r["lat"], r[var_name]) for r in records if r.get(var_name) is not None]
    if len(valid) < 4:
        print(f"   ⚠️  Pas assez de points pour {var_name} ({zone_name})")
        return

    pts_lon = np.array([v[0] for v in valid])
    pts_lat = np.array([v[1] for v in valid])
    pts_val = np.array([v[2] for v in valid])

    lon_min, lat_min, lon_max, lat_max = bbox
    w, h = raster_size

    grid_lons = np.linspace(lon_min, lon_max, w)
    grid_lats = np.linspace(lat_min, lat_max, h)
    g_lon2d, g_lat2d = np.meshgrid(grid_lons, grid_lats)

    grid_vals = griddata(
        (pts_lon, pts_lat), pts_val,
        (g_lon2d, g_lat2d),
        method="linear",
    ).astype(np.float32)

    NODATA = -9999.0
    grid_vals = np.where(np.isnan(grid_vals), NODATA, grid_vals)

    # rasterio attend l'axe Y inversé (nord en haut)
    grid_vals_flipped = np.flipud(grid_vals)

    transform = from_bounds(lon_min, lat_min, lon_max, lat_max, w, h)

    path = os.path.join(OUTPUT_DIR, f"{zone_name}_{var_name}.tif")
    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=h, width=w,
        count=1,
        dtype=np.float32,
        crs="EPSG:4326",
        transform=transform,
        nodata=NODATA,
        compress="lzw",           # compression sans perte
    ) as dst:
        dst.write(grid_vals_flipped, 1)
        dst.update_tags(
            variable=var_name,
            label=VARIABLES[var_name]["label"],
            unit=VARIABLES[var_name]["unit"],
            zone=zone_name,
            source="Open-Meteo CC BY 4.0",
        )

    size_kb = os.path.getsize(path) / 1024
    print(f"✓  {path} ({w}×{h} px, {size_kb:.0f} Ko, EPSG:4326)")


# ------------------------------------------------------------
# Traitement d'une zone
# ------------------------------------------------------------

def process_zone(zone_name, zone_config, timestamp):
    print(f"\n📍 Zone : {zone_name}")
    lons, lats = generate_grid_points(zone_config["bbox"], zone_config["resolution"])
    total = len(lons)
    n_chunks = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
    print(f"   Grille : {total} points → {n_chunks} requêtes GET")

    all_records = []
    for i in range(0, total, CHUNK_SIZE):
        chunk_lats = lats[i: i + CHUNK_SIZE]
        chunk_lons = lons[i: i + CHUNK_SIZE]
        n_end = min(i + CHUNK_SIZE, total)
        print(f"   Chunk {i+1}–{n_end}...")
        results = fetch_openmeteo_chunk(chunk_lats, chunk_lons)
        all_records.extend(extract_records(results))
        if n_end < total:
            time.sleep(0.2)

    save_geojson(all_records, zone_name, timestamp)
    save_csv(all_records, zone_name, timestamp)
    for var_name in VARIABLES:
        save_geotiff(all_records, zone_name, var_name, zone_config["bbox"], zone_config["raster_size"])


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
        "zones": list(ZONES.keys()),
        "variables": {k: v for k, v in VARIABLES.items()},
        "outputs_per_zone": ["_weather.geojson", "_weather.csv",
                             "_temperature_2m.tif", "_precipitation.tif",
                             "_cloud_cover.tif", "_wind_speed_10m.tif"],
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
