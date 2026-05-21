#!/usr/bin/env python3
"""
fetch_weather.py
----------------
API Open-Meteo → grille de points France + Haute-Marne.

Corrections v4 :
  - Résolution 0.25° (France) et 0.05° (HM) — pas natif ERA5 d'Open-Meteo
  - Interpolation RegularGridInterpolator (pas de triangulation Delaunay)
    → plus d'artefacts triangulaires dans les rasters
  - Lissage gaussien adaptatif pour des transitions douces
  - GeoTIFF + GeoJSON + CSV
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import requests
import scipy.ndimage as ndimage
from scipy.interpolate import RegularGridInterpolator

try:
    import rasterio
    from rasterio.transform import from_bounds
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False
    print("⚠️  rasterio non disponible — GeoTIFF désactivés")

# -------------------------------------------------------
# Zones
# -------------------------------------------------------
ZONES = {
    "france": {
        "bbox": (-5.14, 41.33, 9.56, 51.09),
        "resolution": 0.25,          # ~27 km — résolution native Open-Meteo ERA5
        "raster_size": (600, 400),   # pixels du GeoTIFF de sortie
        "smooth_sigma": 3.0,         # lissage gaussien (pixels)
    },
    "haute-marne": {
        "bbox": (4.70, 47.50, 5.95, 48.65),
        "resolution": 0.05,          # ~5 km
        "raster_size": (600, 600),
        "smooth_sigma": 4.0,
    },
}

VARIABLES = {
    "temperature_2m":  {"label": "Température (°C)",        "unit": "°C"},
    "precipitation":   {"label": "Précipitations (mm/h)",   "unit": "mm/h"},
    "cloud_cover":     {"label": "Couverture nuageuse (%)", "unit": "%"},
    "wind_speed_10m":  {"label": "Vitesse du vent (m/s)",   "unit": "m/s"},
}

OUTPUT_DIR    = "data/weather"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
CHUNK_SIZE    = 50
RETRY_DELAY   = 5
NODATA        = -9999.0


# -------------------------------------------------------
# Grille & API
# -------------------------------------------------------
def generate_grid(bbox, resolution):
    lon_min, lat_min, lon_max, lat_max = bbox
    lons = np.arange(lon_min, lon_max + resolution * 0.5, resolution)
    lats = np.arange(lat_min, lat_max + resolution * 0.5, resolution)
    g_lon, g_lat = np.meshgrid(lons, lats)
    return g_lon.flatten(), g_lat.flatten(), lons, lats


def fetch_chunk(lats, lons, retries=3):
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
            r = requests.get(OPENMETEO_URL, params=params, timeout=60)
            r.raise_for_status()
            d = r.json()
            return [d] if isinstance(d, dict) else d
        except requests.RequestException as e:
            if attempt < retries - 1:
                print(f"   ⚠️  Tentative {attempt+1}/{retries} : {e}. Retry {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                raise


def extract_records(api_results):
    out = []
    for r in api_results:
        cur = r.get("current", {})
        rec = {"lat": round(r["latitude"], 4), "lon": round(r["longitude"], 4)}
        for v in VARIABLES:
            rec[v] = cur.get(v)
        out.append(rec)
    return out


# -------------------------------------------------------
# Sauvegarde GeoJSON
# -------------------------------------------------------
def save_geojson(records, zone_name, timestamp):
    features = []
    for r in records:
        if r.get("temperature_2m") is None:
            continue
        props = {k: v for k, v in r.items() if k not in ("lat", "lon")}
        props["last_updated"] = timestamp
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
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
        },
        "features": features,
    }
    path = os.path.join(OUTPUT_DIR, f"{zone_name}_weather.geojson")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"✓  {path} ({len(features)} points)")


# -------------------------------------------------------
# Sauvegarde CSV
# -------------------------------------------------------
def save_csv(records, zone_name, timestamp):
    valid = [r for r in records if r.get("temperature_2m") is not None]
    if not valid:
        return
    path = os.path.join(OUTPUT_DIR, f"{zone_name}_weather.csv")
    fields = ["lon", "lat"] + list(VARIABLES.keys()) + ["last_updated"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in valid:
            row = {k: (round(r[k], 5) if isinstance(r.get(k), float) else r.get(k))
                   for k in fields if k != "last_updated"}
            row["last_updated"] = timestamp
            w.writerow(row)
    print(f"✓  {path} ({len(valid)} lignes)")


# -------------------------------------------------------
# Interpolation propre (RegularGridInterpolator + gaussien)
# -------------------------------------------------------
def interpolate_raster(records, var_name, bbox, raster_size, lons_src, lats_src, smooth_sigma):
    """
    Reconstruit la grille source depuis les points, puis interpole
    avec RegularGridInterpolator (bilinéaire sur grille régulière →
    AUCUN artefact triangulaire). Lissage gaussien final.
    """
    valid = [(r["lon"], r["lat"], r[var_name])
             for r in records if r.get(var_name) is not None]
    if len(valid) < 4:
        return None

    pts_lon = np.array([v[0] for v in valid])
    pts_lat = np.array([v[1] for v in valid])
    pts_val = np.array([v[2] for v in valid])

    # --- Reconstruction de la matrice source sur la grille d'origine ---
    n_lat, n_lon = len(lats_src), len(lons_src)
    grid_src = np.full((n_lat, n_lon), np.nan, dtype=np.float64)

    for lon, lat, val in zip(pts_lon, pts_lat, pts_val):
        i_lat = int(np.argmin(np.abs(lats_src - lat)))
        i_lon = int(np.argmin(np.abs(lons_src - lon)))
        grid_src[i_lat, i_lon] = val

    # Remplissage des NaN résiduels (bords) par nearest-neighbor
    nan_mask = np.isnan(grid_src)
    if nan_mask.any():
        from scipy.ndimage import distance_transform_edt
        _, idx = distance_transform_edt(nan_mask, return_distances=True, return_indices=True)
        grid_src[nan_mask] = grid_src[idx[0][nan_mask], idx[1][nan_mask]]

    # --- RegularGridInterpolator bilinéaire ---
    interp = RegularGridInterpolator(
        (lats_src, lons_src),
        grid_src,
        method="linear",
        bounds_error=False,
        fill_value=None,   # extrapoler légèrement aux bords
    )

    lon_min, lat_min, lon_max, lat_max = bbox
    w, h = raster_size
    tgt_lons = np.linspace(lon_min, lon_max, w)
    tgt_lats = np.linspace(lat_min, lat_max, h)
    tg_lon2d, tg_lat2d = np.meshgrid(tgt_lons, tgt_lats)
    pts_q = np.stack([tg_lat2d.ravel(), tg_lon2d.ravel()], axis=-1)

    grid_vals = interp(pts_q).reshape(h, w).astype(np.float32)

    # --- Lissage gaussien pour transitions douces ---
    grid_vals = ndimage.gaussian_filter(grid_vals, sigma=smooth_sigma)

    return grid_vals


# -------------------------------------------------------
# Sauvegarde GeoTIFF
# -------------------------------------------------------
def save_geotiff(grid_vals, var_name, zone_name, bbox, raster_size, timestamp):
    if not HAS_RASTERIO or grid_vals is None:
        return

    lon_min, lat_min, lon_max, lat_max = bbox
    w, h = raster_size

    arr = np.where(np.isnan(grid_vals), NODATA, grid_vals).astype(np.float32)
    arr_flipped = np.flipud(arr)  # nord en haut

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
        compress="lzw",
    ) as dst:
        dst.write(arr_flipped, 1)
        dst.update_tags(
            variable=var_name,
            label=VARIABLES[var_name]["label"],
            unit=VARIABLES[var_name]["unit"],
            zone=zone_name,
            last_updated=timestamp,
            source="Open-Meteo CC BY 4.0",
        )

    size_kb = os.path.getsize(path) / 1024
    print(f"✓  {path} ({w}×{h} px, {size_kb:.0f} Ko)")


# -------------------------------------------------------
# Traitement d'une zone
# -------------------------------------------------------
def process_zone(zone_name, cfg, timestamp):
    print(f"\n📍 Zone : {zone_name}  (résolution {cfg['resolution']}°)")
    lons_flat, lats_flat, lons_src, lats_src = generate_grid(cfg["bbox"], cfg["resolution"])
    total = len(lons_flat)
    print(f"   Grille : {total} points → {(total + CHUNK_SIZE - 1) // CHUNK_SIZE} requêtes GET")

    all_records = []
    for i in range(0, total, CHUNK_SIZE):
        chunk_lats = lats_flat[i: i + CHUNK_SIZE]
        chunk_lons = lons_flat[i: i + CHUNK_SIZE]
        n_end = min(i + CHUNK_SIZE, total)
        print(f"   Chunk {i+1}–{n_end}...")
        results = fetch_chunk(chunk_lats, chunk_lons)
        all_records.extend(extract_records(results))
        if n_end < total:
            time.sleep(0.2)

    save_geojson(all_records, zone_name, timestamp)
    save_csv(all_records, zone_name, timestamp)

    for var_name in VARIABLES:
        grid = interpolate_raster(
            all_records, var_name,
            cfg["bbox"], cfg["raster_size"],
            lons_src, lats_src,
            cfg["smooth_sigma"],
        )
        save_geotiff(grid, var_name, zone_name, cfg["bbox"], cfg["raster_size"], timestamp)


# -------------------------------------------------------
# Main
# -------------------------------------------------------
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    errors = []
    for zone_name, cfg in ZONES.items():
        try:
            process_zone(zone_name, cfg, timestamp)
        except Exception as e:
            msg = f"Erreur {zone_name} : {e}"
            print(f"❌ {msg}", file=sys.stderr)
            errors.append(msg)

    with open(os.path.join(OUTPUT_DIR, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": timestamp,
            "source": "Open-Meteo (https://open-meteo.com)",
            "license": "CC BY 4.0",
            "zones": {k: {
                "bbox": v["bbox"], "resolution_deg": v["resolution"],
                "raster_size": v["raster_size"],
            } for k, v in ZONES.items()},
            "variables": {k: v for k, v in VARIABLES.items()},
            "errors": errors,
        }, f, ensure_ascii=False, indent=2)

    if errors:
        print(f"\n⚠️  {len(errors)} erreur(s)", file=sys.stderr)
        sys.exit(1)
    print("\n✅ Données météo mises à jour.")


if __name__ == "__main__":
    main()
