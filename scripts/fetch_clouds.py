#!/usr/bin/env python3
"""
fetch_clouds.py
---------------
Télécharge l'image nuages mondiale (EUMETSAT CC0, ~3h)
et génère pour chaque zone :
  - <zone>.jpg  : JPEG recadré (aperçu rapide)
  - <zone>.tif  : GeoTIFF géoréférencé EPSG:4326 (import direct QGIS)

Note : Haute-Marne ne fait que ~29×27 px natifs dans l'image 8192×4096.
L'image est upscalée pour être lisible mais reste pixelisée par nature
(limite physique d'une photo satellite mondiale).
Pour haute résolution, utiliser les WMS NASA GIBS ou EUMETSAT (voir metadata.json).
"""

import io
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import requests
from PIL import Image

try:
    import rasterio
    from rasterio.transform import from_bounds
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False
    print("⚠️  rasterio non disponible — GeoTIFF désactivés")

SOURCE_URL        = "https://clouds.matteason.co.uk/images/8192x4096/clouds.jpg"
SOURCE_RESOLUTION = (8192, 4096)

ZONES = {
    "world":       (-180.0, -90.0,  180.0,  90.0),
    "france":      (  -5.14, 41.33,   9.56,  51.09),
    "haute-marne": (   4.70, 47.50,   5.95,  48.65),
}

OUTPUT_DIR   = "data/clouds"
JPEG_QUALITY = 90
MIN_PX_SIZE  = 80   # taille minimum souhaitée (upscale si nécessaire)


def lonlat_to_pixel(lon, lat, w, h):
    x = int((lon + 180.0) / 360.0 * w)
    y = int((90.0 - lat) / 180.0 * h)
    return max(0, min(w - 1, x)), max(0, min(h - 1, y))


def crop_to_bbox(img, bbox):
    w, h = img.size
    lon_min, lat_min, lon_max, lat_max = bbox
    x1, y1 = lonlat_to_pixel(lon_min, lat_max, w, h)
    x2, y2 = lonlat_to_pixel(lon_max, lat_min, w, h)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Bbox invalide : ({x1},{y1})→({x2},{y2})")
    return img.crop((x1, y1, x2, y2))


def save_geotiff(img_pil: Image.Image, bbox: tuple, path: str, zone_name: str):
    """
    Génère un GeoTIFF RGB géoréférencé EPSG:4326 depuis une image PIL.
    Directement chargeable dans QGIS via Couche > Ajouter une couche raster.
    """
    if not HAS_RASTERIO:
        return

    lon_min, lat_min, lon_max, lat_max = bbox
    w, h = img_pil.size

    arr = np.array(img_pil)   # shape: (h, w, 3), uint8, RGB
    # rasterio attend (bands, h, w)
    arr_bands = np.moveaxis(arr, -1, 0)   # → (3, h, w)

    transform = from_bounds(lon_min, lat_min, lon_max, lat_max, w, h)

    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=h, width=w,
        count=3,            # R, G, B
        dtype=np.uint8,
        crs="EPSG:4326",
        transform=transform,
        compress="lzw",
    ) as dst:
        dst.write(arr_bands)
        # Définir l'interprétation des bandes (important pour QGIS)
        dst.update_tags(1, DESCRIPTION="Red")
        dst.update_tags(2, DESCRIPTION="Green")
        dst.update_tags(3, DESCRIPTION="Blue")
        dst.update_tags(
            source="EUMETSAT via clouds.matteason.co.uk",
            license="CC0 1.0",
            zone=zone_name,
            bbox=str(bbox),
        )

    size_kb = os.path.getsize(path) / 1024
    print(f"✓  {path} ({w}×{h} px, {size_kb:.0f} Ko, EPSG:4326, RGB)")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"⬇️  Téléchargement {SOURCE_URL} ...")
    try:
        resp = requests.get(SOURCE_URL, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    print(f"✓  Image reçue : {img.size[0]}×{img.size[1]} px")

    saved = {}
    for zone_name, bbox in ZONES.items():
        cropped = img if zone_name == "world" else crop_to_bbox(img, bbox)
        pw, ph  = cropped.size
        native_pw, native_ph = pw, ph

        # Upscale si trop petit pour être lisible
        if zone_name != "world" and (pw < MIN_PX_SIZE or ph < MIN_PX_SIZE):
            scale  = max(MIN_PX_SIZE // max(pw, 1), MIN_PX_SIZE // max(ph, 1), 2) * 3
            new_w, new_h = pw * scale, ph * scale
            # Nearest pour garder le look satellite (pas de faux flou)
            cropped = cropped.resize((new_w, new_h), Image.NEAREST)
            print(f"   ⚠️  {zone_name} : {pw}×{ph} px natifs → upscalé ×{scale} → {new_w}×{new_h} px")
            pw, ph = new_w, new_h

        # --- JPEG ---
        jpg_path = os.path.join(OUTPUT_DIR, f"{zone_name}.jpg")
        cropped.save(jpg_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        size_kb = os.path.getsize(jpg_path) / 1024
        print(f"✓  {jpg_path} ({pw}×{ph} px, {size_kb:.0f} Ko)")

        # --- GeoTIFF géoréférencé ---
        tif_path = os.path.join(OUTPUT_DIR, f"{zone_name}.tif")
        save_geotiff(cropped, bbox, tif_path, zone_name)

        saved[zone_name] = {
            "jpg":        f"{zone_name}.jpg",
            "tif":        f"{zone_name}.tif",
            "width_px":   pw,
            "height_px":  ph,
            "native_px":  f"{native_pw}×{native_ph}",
            "bbox":       {"lon_min": bbox[0], "lat_min": bbox[1], "lon_max": bbox[2], "lat_max": bbox[3]},
            "crs":        "EPSG:4326",
        }

    metadata = {
        "last_updated":   timestamp,
        "source":         "clouds.matteason.co.uk",
        "source_url":     SOURCE_URL,
        "source_license": "CC0 1.0 — Contains modified EUMETSAT data",
        "resolution_note": (
            "Image 8192×4096 → ~29×27 px natifs sur Haute-Marne. "
            "GeoTIFF fournis mais pixelisés à l'échelle départementale. "
            "Pour haute résolution, utiliser wms_alternatives ci-dessous."
        ),
        "outputs_per_zone": ["<zone>.jpg (aperçu)", "<zone>.tif (GeoTIFF EPSG:4326 pour QGIS)"],
        "wms_alternatives": {
            "NASA_GIBS_MODIS_Terra": {
                "url":   "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi",
                "layer": "MODIS_Terra_CorrectedReflectance_TrueColor",
                "type":  "WMS — sans clé API",
            },
            "EUMETSAT_Meteosat": {
                "url":   "https://eumetview.eumetsat.int/geoserver/wms",
                "layer": "BAS:METEOSAT_0DEG_VIS006",
                "type":  "WMS — sans clé API",
            },
            "OpenWeatherMap_nuages": {
                "url":    "https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid={API_KEY}",
                "type":   "XYZ tiles — clé gratuite sur openweathermap.org",
                "signup": "https://openweathermap.org/api",
            },
        },
        "zones": saved,
    }

    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"✓  {meta_path}")
    print("\n✅ Nuages mis à jour.")


if __name__ == "__main__":
    main()
