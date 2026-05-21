#!/usr/bin/env python3
"""
fetch_clouds.py — génère JPG + GeoTIFF par zone (sans world.tif : 36 Mo, inutile).
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

SOURCE_URL = "https://clouds.matteason.co.uk/images/8192x4096/clouds.jpg"
OUTPUT_DIR = "data/clouds"
MIN_PX    = 80

# world exclu du GeoTIFF (trop lourd, ~36 Mo)
ZONES = {
    "world":       {"bbox": (-180.0, -90.0,  180.0,  90.0), "tif": False},
    "france":      {"bbox": (  -5.14, 41.33,   9.56,  51.09), "tif": True},
    "haute-marne": {"bbox": (   4.70, 47.50,   5.95,  48.65), "tif": True},
}


def lonlat_to_px(lon, lat, w, h):
    x = int((lon + 180.0) / 360.0 * w)
    y = int((90.0 - lat)  / 180.0 * h)
    return max(0, min(w-1, x)), max(0, min(h-1, y))


def crop(img, bbox):
    w, h = img.size
    x1, y1 = lonlat_to_px(bbox[0], bbox[3], w, h)
    x2, y2 = lonlat_to_px(bbox[2], bbox[1], w, h)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"bbox invalide ({x1},{y1})→({x2},{y2})")
    return img.crop((x1, y1, x2, y2))


def save_tif(pil_img, bbox, path):
    if not HAS_RASTERIO:
        return
    arr = np.moveaxis(np.array(pil_img), -1, 0)   # (3, h, w)
    t   = from_bounds(*bbox, pil_img.size[0], pil_img.size[1])
    with rasterio.open(path, "w", driver="GTiff",
                       height=pil_img.size[1], width=pil_img.size[0],
                       count=3, dtype=np.uint8, crs="EPSG:4326",
                       transform=t, compress="lzw") as dst:
        dst.write(arr)
    print(f"✓  {path} ({pil_img.size[0]}×{pil_img.size[1]} px, "
          f"{os.path.getsize(path)//1024} Ko, EPSG:4326)")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"⬇️  {SOURCE_URL}")
    try:
        resp = requests.get(SOURCE_URL, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ {e}", file=sys.stderr); sys.exit(1)

    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    print(f"✓  {img.size[0]}×{img.size[1]} px reçus")

    saved = {}
    for name, cfg in ZONES.items():
        bbox    = cfg["bbox"]
        do_tif  = cfg["tif"]
        cropped = img if name == "world" else crop(img, bbox)
        pw, ph  = cropped.size
        native  = f"{pw}×{ph}"

        if name != "world" and (pw < MIN_PX or ph < MIN_PX):
            scale   = max(MIN_PX // max(pw,1), MIN_PX // max(ph,1), 2) * 3
            cropped = cropped.resize((pw*scale, ph*scale), Image.NEAREST)
            print(f"   ⚠️  {name} : {native} → upscalé ×{scale}")
            pw, ph = cropped.size

        jpg = os.path.join(OUTPUT_DIR, f"{name}.jpg")
        cropped.save(jpg, format="JPEG", quality=90, optimize=True)
        print(f"✓  {jpg} ({pw}×{ph} px, {os.path.getsize(jpg)//1024} Ko)")

        if do_tif:
            save_tif(cropped, bbox, os.path.join(OUTPUT_DIR, f"{name}.tif"))

        saved[name] = {"jpg": f"{name}.jpg",
                       "tif": f"{name}.tif" if do_tif else None,
                       "native_px": native,
                       "bbox": dict(zip(["lon_min","lat_min","lon_max","lat_max"], bbox))}

    json.dump({
        "last_updated": ts, "source_url": SOURCE_URL,
        "source_license": "CC0 1.0 — Contains modified EUMETSAT data",
        "note": "world.tif non généré (36 Mo, inutile — utiliser world.jpg ou les WMS)",
        "wms": {
            "NASA_GIBS_MODIS": "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi",
            "EUMETSAT":        "https://eumetview.eumetsat.int/geoserver/wms",
        },
        "zones": saved,
    }, open(os.path.join(OUTPUT_DIR, "metadata.json"), "w"), ensure_ascii=False, indent=2)

    print("✅ Nuages OK.")


if __name__ == "__main__":
    main()
