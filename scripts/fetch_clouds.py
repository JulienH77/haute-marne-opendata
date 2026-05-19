#!/usr/bin/env python3
"""
fetch_clouds.py
---------------
Télécharge l'image nuages mondiale depuis clouds.matteason.co.uk
(données EUMETSAT, CC0, mise à jour toutes les 3h) et la recadre
sur la France métropolitaine et la Haute-Marne.

Outputs :
  data/clouds/world.jpg
  data/clouds/france.jpg
  data/clouds/haute-marne.jpg
  data/clouds/metadata.json
"""

import io
import json
import os
import sys
from datetime import datetime, timezone

import requests
from PIL import Image

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

SOURCE_URL = "https://clouds.matteason.co.uk/images/4096x2048/clouds.jpg"
SOURCE_RESOLUTION = (4096, 2048)  # largeur x hauteur en pixels

# Bounding boxes (lon_min, lat_min, lon_max, lat_max) en EPSG:4326
ZONES = {
    "world": (-180.0, -90.0, 180.0, 90.0),
    "france": (-5.14, 41.33, 9.56, 51.09),
    "haute-marne": (4.70, 47.50, 5.95, 48.65),
}

OUTPUT_DIR = "data/clouds"
JPEG_QUALITY = 88


# ------------------------------------------------------------
# Fonctions
# ------------------------------------------------------------

def lonlat_to_pixel(lon: float, lat: float, img_width: int, img_height: int):
    """Convertit des coordonnées géographiques en pixels (projection équirectangulaire)."""
    x = int((lon + 180.0) / 360.0 * img_width)
    y = int((90.0 - lat) / 180.0 * img_height)
    # Clamp pour rester dans les bornes
    x = max(0, min(img_width - 1, x))
    y = max(0, min(img_height - 1, y))
    return x, y


def crop_to_bbox(img: Image.Image, bbox: tuple) -> Image.Image:
    """Recadre une image équirectangulaire sur une bounding box géographique."""
    w, h = img.size
    lon_min, lat_min, lon_max, lat_max = bbox

    x1, y1 = lonlat_to_pixel(lon_min, lat_max, w, h)  # coin haut-gauche
    x2, y2 = lonlat_to_pixel(lon_max, lat_min, w, h)  # coin bas-droit

    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Bbox invalide après conversion pixels : ({x1},{y1}) → ({x2},{y2})")

    return img.crop((x1, y1, x2, y2))


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Téléchargement ---
    print(f"⬇️  Téléchargement de l'image nuages mondiale...")
    print(f"    URL : {SOURCE_URL}")
    try:
        resp = requests.get(SOURCE_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ Erreur de téléchargement : {e}", file=sys.stderr)
        sys.exit(1)

    img = Image.open(io.BytesIO(resp.content))
    print(f"✓  Image reçue : {img.size[0]}×{img.size[1]} px, mode {img.mode}")

    # S'assurer que l'image est en RGB (pas de canal alpha parasite)
    if img.mode != "RGB":
        img = img.convert("RGB")

    # --- Recadrage par zone ---
    saved_files = {}
    for zone_name, bbox in ZONES.items():
        out_path = os.path.join(OUTPUT_DIR, f"{zone_name}.jpg")

        if zone_name == "world":
            cropped = img
        else:
            cropped = crop_to_bbox(img, bbox)

        cropped.save(out_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        size_kb = os.path.getsize(out_path) / 1024
        print(f"✓  {out_path}  ({cropped.size[0]}×{cropped.size[1]} px, {size_kb:.0f} Ko)")

        saved_files[zone_name] = {
            "file": f"{zone_name}.jpg",
            "width_px": cropped.size[0],
            "height_px": cropped.size[1],
            "bbox": {
                "lon_min": bbox[0],
                "lat_min": bbox[1],
                "lon_max": bbox[2],
                "lat_max": bbox[3],
            },
        }

    # --- Metadata ---
    metadata = {
        "last_updated": timestamp,
        "source": "clouds.matteason.co.uk",
        "source_url": SOURCE_URL,
        "source_license": "CC0 1.0 Universal",
        "attribution": "Contains modified EUMETSAT data",
        "original_resolution": f"{SOURCE_RESOLUTION[0]}×{SOURCE_RESOLUTION[1]}",
        "projection": "EPSG:4326 (équirectangulaire)",
        "zones": saved_files,
    }

    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"✓  {meta_path}")

    print("\n✅ Nuages mis à jour avec succès.")


if __name__ == "__main__":
    main()
