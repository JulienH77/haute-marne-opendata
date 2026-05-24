#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne + France.

Sources :
  Géométries départements → gregoiredavid/france-geojson (GitHub, stable)
  Population actuelle     → geo.api.gouv.fr
  Population historique   → 2 fichiers INSEE via API data.gouv.fr :
    1) base-cc-evol-struct-pop-YYYY → 2021, 2016, 2011, 2006
    2) Séries historiques 1968→1999 → 1999, 1990, 1982, 1975, 1968

Approche robuste pour data.gouv.fr :
  - L'API JSON /api/1/datasets/{slug}/ retourne les vraies URLs des ressources
  - On prend la ressource CSV/ZIP la plus lourde (= données, pas notice)
  - Plusieurs slugs testés en cascade
"""

import csv
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

import requests

try:
    import fiona
    from fiona.crs import from_epsg
    HAS_FIONA = True
except ImportError:
    HAS_FIONA = False

DEP      = "52"
GEO      = "https://geo.api.gouv.fr"
OUT      = "data/population"
LAST_UPDATE = "data/last_update.json"

TARGET_YEARS = [2023, 2022, 2021, 2016, 2011, 2006, 1999, 1990, 1982, 1975, 1968]

# Source externe fiable pour les géométries de départements
# (geo.api.gouv.fr/departements retourne les données mais sans géométries dans certaines configurations)
DEPT_GEOM_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
    "departements-version-simplifiee.geojson"
)

# Slugs data.gouv.fr pour les fichiers récents (contiennent PMUN21+PMUN16+PMUN11+PMUN06)
RECENT_SLUGS = [
    "populations-legales-en-2021",
    "populations-legales-en-2020",
    "populations-legales-en-2022",
]

# Slugs pour les séries historiques (contiennent 1968→1999)
HISTO_SLUGS = [
    "series-historiques-des-resultats-du-recensement-de-la-population",
    "populations-legales-des-communes-depuis-1968",
    "populations-legales-en-2009",   # contient souvent PMUN99, PMUN90, ...
    "populations-legales-en-2006",
]


# ── Téléchargement via API data.gouv.fr ─────────────────────────────────────

def get_resource_urls_from_slug(slug: str) -> list[str]:
    """
    Interroge l'API data.gouv.fr et retourne les URLs directes des ressources
    CSV/ZIP utiles (triées du plus lourd au plus léger).
    """
    try:
        r = requests.get(
            f"https://www.data.gouv.fr/api/1/datasets/{slug}/",
            timeout=20,
            headers={"User-Agent": "haute-marne-opendata/1.0"},
        )
        if r.status_code != 200:
            print(f"      API {slug} → HTTP {r.status_code}")
            return []
        resources = r.json().get("resources", [])
    except Exception as e:
        print(f"      API {slug} → {e}")
        return []

    useful = []
    for res in resources:
        url   = res.get("url", "")
        fmt   = (res.get("format") or "").lower()
        title = (res.get("title") or "").lower()
        size  = res.get("filesize") or 0

        is_data = fmt in ("csv", "zip") or url.lower().endswith((".csv", ".zip"))
        is_doc  = any(w in title for w in
                      ("notice", "readme", "doc", "dictionnaire", "variable", "meta"))
        if is_data and not is_doc:
            useful.append((size, url))

    useful.sort(reverse=True)
    return [u for _, u in useful]


def get_raw(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=90, allow_redirects=True,
                         headers={"User-Agent": "haute-marne-opendata/1.0"})
        r.raise_for_status()
    except Exception as e:
        print(f"      ✗ {e}")
        return None

    content = r.content
    size_kb = len(content) // 1024
    if size_kb < 5:
        print(f"      ✗ Trop petit ({size_kb} Ko) — page HTML ?")
        return None

    # ZIP ?
    if content[:2] == b"PK":
        try:
            z    = zipfile.ZipFile(io.BytesIO(content))
            csvs = sorted(
                [n for n in z.namelist()
                 if n.lower().endswith((".csv", ".txt"))
                 and not any(w in n.lower()
                             for w in ("notice", "readme", "doc", "meta", "variable"))],
                key=lambda n: z.getinfo(n).file_size, reverse=True,
            )
            if not csvs:
                csvs = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not csvs:
                print(f"      ✗ ZIP vide : {z.namelist()}")
                return None
            print(f"      ZIP → '{csvs[0]}' ({z.getinfo(csvs[0]).file_size // 1024} Ko)")
            return z.read(csvs[0])
        except Exception as e:
            print(f"      ✗ ZIP : {e}")
            return None

    print(f"      CSV direct ({size_kb} Ko)")
    return content


# ── Parsing CSV INSEE ────────────────────────────────────────────────────────

def parse_popleg(raw: bytes) -> dict[int, dict[str, int]]:
    """
    Extrait TOUTES les colonnes PMUN*/POP* disponibles.
    Retourne {année: {code_commune: population}}.
    """
    text = None
    for enc in ("utf-8-sig", "latin-1", "utf-8", "cp1252"):
        try: text = raw.decode(enc); break
        except UnicodeDecodeError: pass
    if not text:
        print("      ✗ Encodage inconnu")
        return {}

    first = text.split("\n")[0]
    delim = max([";", ",", "\t", "|"], key=lambda d: first.count(d))

    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        rows   = list(reader)
    except Exception as e:
        print(f"      ✗ CSV : {e}"); return {}

    if not rows:
        print("      ✗ CSV vide"); return {}

    keys = list(rows[0].keys())
    print(f"      Colonnes ({len(keys)}) : {keys[:20]}")

    # Code commune
    codgeo = next(
        (k for k in keys if k.upper().strip() in
         ("CODGEO", "COM", "CODE_COM", "CODECOM", "CODE", "INSEE_COM")),
        None,
    )
    if not codgeo:
        for k in keys:
            sample = [rows[i].get(k, "").strip() for i in range(min(10, len(rows)))]
            if sample and all(re.match(r"^\d{5}$", v) for v in sample if v):
                codgeo = k; break
    if not codgeo:
        print(f"      ✗ Code commune introuvable")
        return {}
    print(f"      CODGEO = '{codgeo}'")

    # Colonnes population par année
    year_cols: dict[int, str] = {}
    for k in keys:
        ku = k.upper().strip()
        for pat, yfn in [
            (r"^PMUN(\d{2})$",  lambda g: 2000+int(g) if int(g) <= 30 else 1900+int(g)),
            (r"^PMUN(\d{4})$",  lambda g: int(g)),
            (r"^P(\d{2})_POP$", lambda g: 2000+int(g) if int(g) <= 30 else 1900+int(g)),
            (r"^P(\d{4})_POP$", lambda g: int(g)),
            (r"^PTOT(\d{4})$",  lambda g: int(g)),
            (r"^POP(\d{4})$",   lambda g: int(g)),
        ]:
            m = re.match(pat, ku)
            if m:
                y = yfn(m.group(1))
                if 1960 <= y <= 2030:
                    year_cols[y] = k
                break

    if not year_cols:
        print(f"      ✗ Aucune colonne PMUN trouvée. Colonnes : {keys}")
        return {}
    print(f"      Années : {sorted(year_cols.keys())}")

    result: dict[int, dict[str, int]] = {y: {} for y in year_cols}
    for row in rows:
        code = str(row.get(codgeo, "")).strip()
        if not re.match(r"^\d{5}$", code):
            continue
        for year, col in year_cols.items():
            v = str(row.get(col, "")).strip()
            if v and v not in ("", "nan", "NaN", "#", "-"):
                try: result[year][code] = int(float(v))
                except ValueError: pass

    result = {y: d for y, d in result.items() if d}
    for y, d in result.items():
        print(f"      → {y} : {len(d):,} communes")
    return result


def fetch_from_slugs(slugs: list[str], label: str) -> dict[int, dict[str, int]]:
    print(f"\n   [{label}]")
    for slug in slugs:
        print(f"   Slug '{slug}' :")
        urls = get_resource_urls_from_slug(slug)
        print(f"   → {len(urls)} URL(s) disponible(s)")
        for i, url in enumerate(urls[:3], 1):   # max 3 URLs par slug
            print(f"   Essai {i} : ...{url[-60:]}")
            raw = get_raw(url)
            if raw:
                data = parse_popleg(raw)
                if data:
                    return data
    return {}


# ── Géographies ──────────────────────────────────────────────────────────────

def fetch_communes(dep: str) -> dict:
    r = requests.get(f"{GEO}/communes", timeout=60, params={
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson", "geometry": "contour",
    })
    r.raise_for_status()
    return r.json()


def fetch_departements_with_geom() -> dict:
    """
    Récupère les géométries depuis gregoiredavid/france-geojson (fiable),
    puis enrichit avec les populations de geo.api.gouv.fr.
    """
    print("   Géométries depuis gregoiredavid/france-geojson...")
    r_geom = requests.get(DEPT_GEOM_URL, timeout=30,
                          headers={"User-Agent": "haute-marne-opendata/1.0"})
    r_geom.raise_for_status()
    geojson = r_geom.json()
    print(f"   → {len(geojson.get('features', []))} départements avec géométrie")

    # Populations depuis geo.api.gouv.fr
    print("   Populations depuis geo.api.gouv.fr...")
    r_pop = requests.get(f"{GEO}/departements", timeout=60, params={
        "fields": "code,nom,population",
        "format": "json",
    })
    pop_by_code: dict[str, int | None] = {}
    if r_pop.status_code == 200:
        for dep in r_pop.json():
            pop_by_code[dep.get("code", "")] = dep.get("population")
    print(f"   → {len(pop_by_code)} populations récupérées")

    # Enrichissement
    for feat in geojson.get("features", []):
        code = feat.get("properties", {}).get("code", "")
        feat["properties"]["population"] = pop_by_code.get(code)

    return geojson


# ── Enrichissement communes ──────────────────────────────────────────────────

def enrich_communes(geojson, all_pop: dict[int, dict[str, int]]):
    years_asc  = sorted(all_pop.keys())
    years_desc = list(reversed(years_asc))
    year_max   = years_desc[0] if years_desc else None
    year_min   = years_asc[0]  if len(years_asc) > 1 else None

    nb_ok = 0
    for feat in geojson.get("features", []):
        props = feat["properties"]
        code  = props.get("code", "")

        for year, pop_dict in all_pop.items():
            props[f"population_{year}"] = pop_dict.get(code)

        if year_max:
            p = all_pop[year_max].get(code)
            if p is not None:
                props["population"]        = p
                props["population_source"] = f"INSEE {year_max}"
            else:
                props["population_source"] = "geo.api.gouv.fr"

        pop_max = all_pop.get(year_max, {}).get(code) if year_max else None
        pop_min = all_pop.get(year_min, {}).get(code) if year_min else None
        if pop_max is not None and pop_min is not None and pop_min > 0:
            props["evolution_absolue"] = pop_max - pop_min
            props["evolution_pct"]     = round((pop_max - pop_min) / pop_min * 100, 2)
            props["evolution_periode"] = f"{year_min}–{year_max}"
            nb_ok += 1
        else:
            props["evolution_absolue"] = None
            props["evolution_pct"]     = None
            props["evolution_periode"] = (f"{year_min}–{year_max}"
                                          if year_min and year_max else "N/A")

    total = len(geojson.get("features", []))
    print(f"   → Évolution : {nb_ok}/{total} communes")
    print(f"   → Années dans GeoJSON : {years_desc}")
    return geojson


def stats(gj):
    feats = gj.get("features", [])
    pops  = [f["properties"].get("population") for f in feats
             if f["properties"].get("population") is not None]
    evols = [f["properties"].get("evolution_pct") for f in feats
             if f["properties"].get("evolution_pct") is not None]
    if not pops: return {}
    return {
        "nb_communes":             len(feats),
        "population_totale":       sum(pops),
        "communes_avec_evolution": len(evols),
        "communes_en_croissance":  sum(1 for e in evols if e > 0),
        "communes_en_declin":      sum(1 for e in evols if e < 0),
    }


# ── GeoPackage ───────────────────────────────────────────────────────────────

def save_population_gpkg(communes_gj, deps_gj, timestamp):
    if not HAS_FIONA:
        print("   ⚠️  fiona non disponible — GeoPackage désactivé")
        return

    path = os.path.join(OUT, "population.gpkg")

    # Schéma communes
    first = communes_gj["features"][0]["properties"] if communes_gj["features"] else {}
    schema_c = {
        "geometry": "MultiPolygon",
        "properties": {k: "float" if isinstance(v, float) else
                          ("int" if isinstance(v, int) else "str")
                       for k, v in first.items()},
    }

    mode = "w"
    for layer, gj, schema in [
        ("communes_hm", communes_gj, schema_c),
    ]:
        try:
            with fiona.open(path, mode, driver="GPKG",
                            crs="EPSG:4326", schema=schema,
                            layer=layer) as dst:
                for feat in gj.get("features", []):
                    if feat.get("geometry"):
                        try:
                            dst.write({
                                "geometry": feat["geometry"],
                                "properties": {
                                    k: feat["properties"].get(k)
                                    for k in schema["properties"]
                                },
                            })
                        except Exception:
                            pass
            mode = "a"   # append pour les couches suivantes
        except Exception as e:
            print(f"   ⚠️  Layer {layer} : {e}")

    # Départements
    if deps_gj.get("features"):
        first_d = deps_gj["features"][0]["properties"]
        schema_d = {
            "geometry": "MultiPolygon",
            "properties": {k: "float" if isinstance(v, float) else
                              ("int" if isinstance(v, int) else "str")
                           for k, v in first_d.items()},
        }
        try:
            with fiona.open(path, "a", driver="GPKG",
                            crs="EPSG:4326", schema=schema_d,
                            layer="departements_france") as dst:
                for feat in deps_gj.get("features", []):
                    if feat.get("geometry"):
                        try:
                            dst.write({
                                "geometry": feat["geometry"],
                                "properties": {
                                    k: feat["properties"].get(k)
                                    for k in schema_d["properties"]
                                },
                            })
                        except Exception:
                            pass
        except Exception as e:
            print(f"   ⚠️  Layer departements : {e}")

    size_kb = os.path.getsize(path) // 1024
    print(f"✓  {path} (GeoPackage, {size_kb} Ko)")


# ── last_update.json ─────────────────────────────────────────────────────────

def update_last_update(key: str, timestamp: str, extra: dict = None):
    os.makedirs(os.path.dirname(LAST_UPDATE) if os.path.dirname(LAST_UPDATE) else ".", exist_ok=True)
    try:
        with open(LAST_UPDATE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data[key] = {"timestamp_utc": timestamp, **(extra or {})}
    data["_last_any_update"] = timestamp
    with open(LAST_UPDATE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Population historique
    print("📊 Chargement populations légales INSEE\n")
    pop_recent = fetch_from_slugs(RECENT_SLUGS, "Fichier récent 2021→2006")
    pop_histo  = fetch_from_slugs(HISTO_SLUGS,  "Séries historiques 1999→1968")

    all_pop: dict[int, dict[str, int]] = {}
    for src in [pop_recent, pop_histo]:
        for year, data in src.items():
            if year not in all_pop or len(data) > len(all_pop.get(year, {})):
                all_pop[year] = data

    all_pop    = {y: d for y, d in all_pop.items() if y in TARGET_YEARS and d}
    years_ok   = sorted(all_pop.keys(), reverse=True)
    years_miss = [y for y in TARGET_YEARS if y not in all_pop]
    print(f"\n✓  Années chargées : {years_ok}")
    if years_miss:
        print(f"⚠️  Années manquantes : {years_miss}")

    errors = []

    # Communes Haute-Marne
    print(f"\n📍 Communes Haute-Marne (dép. {DEP})")
    try:
        communes = fetch_communes(DEP)
        communes = enrich_communes(communes, all_pop)
        s        = stats(communes)
        communes["metadata"] = {
            "source": "geo.api.gouv.fr + INSEE via data.gouv.fr",
            "license": "Licence Ouverte 2.0",
            "last_updated": ts, "departement": DEP,
            "annees_dispo": years_ok, **s,
        }
        path = os.path.join(OUT, "haute-marne_communes.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(communes, f, ensure_ascii=False, indent=2)
        print(f"✓  {path} ({s.get('nb_communes','?')} communes)")
        print(f"   Population : {s.get('population_totale', 0):,}".replace(",", " "))
        print(f"   ↑{s.get('communes_en_croissance','?')} / ↓{s.get('communes_en_declin','?')}")
    except Exception as e:
        errors.append(f"Communes HM : {e}")
        print(f"❌ {errors[-1]}", file=sys.stderr)

    # Départements France (géométries depuis gregoiredavid)
    print("\n📍 Départements France")
    deps = {"type": "FeatureCollection", "features": []}
    try:
        deps = fetch_departements_with_geom()
        has_geom = sum(1 for f in deps.get("features", []) if f.get("geometry"))
        deps["metadata"] = {
            "source_geom": "gregoiredavid/france-geojson",
            "source_pop":  "geo.api.gouv.fr",
            "license": "Licence Ouverte 2.0",
            "last_updated": ts,
            "nb_departements": len(deps.get("features", [])),
            "nb_avec_geometrie": has_geom,
        }
        path = os.path.join(OUT, "france_departements.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        print(f"✓  {path} ({has_geom} géométries)")
    except Exception as e:
        errors.append(f"Départements : {e}")
        print(f"❌ {errors[-1]}", file=sys.stderr)

    # GeoPackage
    print("\n📦 GeoPackage population")
    if "communes" in dir():
        try:
            save_population_gpkg(communes, deps, ts)
        except Exception as e:
            print(f"   ⚠️  GeoPackage : {e}")

    # last_update.json
    update_last_update("population", ts, {
        "annees_dispo": years_ok,
        "annees_manquantes": years_miss,
    })

    # Metadata
    with open(os.path.join(OUT, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": ts,
            "annees_dispo": years_ok,
            "annees_manquantes": years_miss,
            "sources": {
                "geometries_deps": "gregoiredavid/france-geojson",
                "geometries_communes": "geo.api.gouv.fr",
                "populations": "INSEE via data.gouv.fr",
            },
            "critical_errors": errors,
        }, f, ensure_ascii=False, indent=2)

    if errors: sys.exit(1)
    print("\n✅ Population OK.")


if __name__ == "__main__":
    main()
