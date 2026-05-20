#!/usr/bin/env python3
"""
fetch_clouds.py
---------------
Télécharge l'image nuages mondiale (EUMETSAT CC0, mise à jour ~3h)
et la recadre sur la France et la Haute-Marne.

Note résolution : même en 8192×4096, Haute-Marne (1.25°×1.15°) ne fait
que ~28×26 pixels dans l'image satellite mondiale. Pour un meilleur visuel
à l'échelle départementale, utiliser les services WMS listés dans metadata.json.
"""

import io
import json
import os
import sys
from datetime import datetime, timezone

import requests
from PIL import Image

# Image haute résolution (8192×4096)
SOURCE_URL        = "https://clouds.matteason.co.uk/images/8192x4096/clouds.jpg"
SOURCE_RESOLUTION = (8192, 4096)

ZONES = {
    "world":       (-180.0, -90.0, 180.0, 90.0),
    "france":      (-5.14,  41.33,   9.56, 51.09),
    "haute-marne": (4.70,   47.50,   5.95, 48.65),
}

OUTPUT_DIR    = "data/clouds"
JPEG_QUALITY  = 90
MIN_PX_WARN   = 100   # Avertissement si l'image recadrée est très petite


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


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"⬇️  Téléchargement {SOURCE_URL} ...")
    try:
        resp = requests.get(SOURCE_URL, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ Erreur téléchargement : {e}", file=sys.stderr)
        sys.exit(1)

    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    print(f"✓  Image reçue : {img.size[0]}×{img.size[1]} px")

    saved = {}
    for zone_name, bbox in ZONES.items():
        out_path = os.path.join(OUTPUT_DIR, f"{zone_name}.jpg")
        cropped = img if zone_name == "world" else crop_to_bbox(img, bbox)
        pw, ph = cropped.size

        # Upscale si trop petit pour être lisible (Haute-Marne ~28×26 px)
        if zone_name != "world" and (pw < MIN_PX_WARN or ph < MIN_PX_WARN):
            scale = max(MIN_PX_WARN // pw, MIN_PX_WARN // ph, 1) * 4
            new_w, new_h = pw * scale, ph * scale
            cropped = cropped.resize((new_w, new_h), Image.NEAREST)
            print(f"   ⚠️  {zone_name} : {pw}×{ph} px natifs → upscalé ×{scale} → {new_w}×{new_h} px (pixelisé)")
            pw, ph = new_w, new_h

        cropped.save(out_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        size_kb = os.path.getsize(out_path) / 1024
        print(f"✓  {out_path}  ({pw}×{ph} px, {size_kb:.0f} Ko)")

        saved[zone_name] = {
            "file": f"{zone_name}.jpg",
            "width_px": pw, "height_px": ph,
            "bbox": {"lon_min": bbox[0], "lat_min": bbox[1], "lon_max": bbox[2], "lat_max": bbox[3]},
        }

    metadata = {
        "last_updated": timestamp,
        "source": "clouds.matteason.co.uk",
        "source_url": SOURCE_URL,
        "source_license": "CC0 1.0 — Contains modified EUMETSAT data",
        "resolution_limitation": (
            "L'image mondiale 8192×4096 donne ~28×26 px natifs sur Haute-Marne. "
            "Pour un visuel satellite haute résolution à l'échelle départementale, "
            "utiliser les services WMS ci-dessous."
        ),
        "wms_alternatives": {
            "EUMETSAT_Meteosat_visible": {
                "url": "https://eumetview.eumetsat.int/geoserver/wms",
                "layer": "BAS:METEOSAT_0DEG_VIS006",
                "format": "image/png",
                "description": "Satellite visible haute résolution (EUMETSAT, gratuit)",
                "qgis_connection_type": "WMS",
            },
            "Copernicus_sentinel_hub": {
                "url": "https://services.sentinel-hub.com/ogc/wms/{INSTANCE_ID}",
                "description": "Sentinel-2 nuages temps quasi-réel (compte gratuit requis)",
                "signup": "https://www.sentinel-hub.com/",
            },
            "NASA_GIBS_MODIS_Terra": {
                "url": "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi",
                "layer": "MODIS_Terra_CorrectedReflectance_TrueColor",
                "format": "image/jpeg",
                "description": "MODIS Terra couleurs vraies (NASA, CC0, ~250m résolution)",
                "qgis_connection_type": "WMS",
            },
            "OpenWeatherMap_cloud_tiles": {
                "url": "https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid={API_KEY}",
                "description": "Tuiles nuages temps réel (clé API gratuite requise)",
                "signup": "https://openweathermap.org/api",
                "qgis_connection_type": "XYZ",
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
