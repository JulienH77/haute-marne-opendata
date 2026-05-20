#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Récupère les données de population pour la Haute-Marne (dép. 52)
et pour la France (niveau département).

Sources :
  - geo.api.gouv.fr  → géométries communales + population actuelle
  - data.gouv.fr     → populations légales INSEE (2015 et 2021)

Outputs :
  data/population/haute-marne_communes.geojson    (communes + pop + évolution)
  data/population/france_departements.geojson     (départements + pop)
  data/population/metadata.json
"""

import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import requests

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

DEP_HAUTE_MARNE = "52"

# URLs stables sur data.gouv.fr (plus fiables que insee.fr direct)
# Dataset "Populations légales" — ressources CSV stables
POPLEG_URLS = {
    2015: "https://www.data.gouv.fr/fr/datasets/r/dbe8a621-a9c4-4bc3-9cae-be1699c5ff25",
    2021: "https://www.data.gouv.fr/fr/datasets/r/6a4e7a5b-e8f2-499c-8d14-0ae19e7e0f21",
}

GEO_API_BASE = "https://geo.api.gouv.fr"
OUTPUT_DIR = "data/population"


# ------------------------------------------------------------
# Récupération géographies
# ------------------------------------------------------------

def fetch_communes_geojson(dep: str) -> dict:
    """Récupère le GeoJSON des communes d'un département avec population."""
    url = f"{GEO_API_BASE}/communes"
    params = {
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson",
        "geometry": "contour",
    }
    print(f"   Récupération des communes du département {dep}...")
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_departements_geojson() -> dict:
    """
    Récupère le GeoJSON de tous les départements avec population.
    L'API peut retourner une liste OU un FeatureCollection selon la version,
    on normalise dans tous les cas vers un FeatureCollection.
    """
    url = f"{GEO_API_BASE}/departements"
    params = {
        "fields": "nom,code,codeRegion,population",
        "format": "geojson",
        "geometry": "contour",
    }
    print("   Récupération des départements...")
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    # Normalisation : l'API retourne parfois une liste de features directement
    if isinstance(data, list):
        return {
            "type": "FeatureCollection",
            "features": data,
        }
    # Parfois un dict avec une clé "features"
    if isinstance(data, dict) and "features" in data:
        return data
    # Cas inattendu : on encapsule quand même
    print("   ⚠️  Format inattendu de l'API départements, tentative d'encapsulation...")
    return {
        "type": "FeatureCollection",
        "features": data if isinstance(data, list) else [],
    }


# ------------------------------------------------------------
# Populations légales INSEE via data.gouv.fr
# ------------------------------------------------------------

def fetch_popleg_datagouv(year: int) -> dict:
    """
    Tente de télécharger les populations légales depuis data.gouv.fr.
    Retourne un dict {code_commune: population} ou {} en cas d'échec.
    L'échec est NON BLOQUANT : on log un warning mais on continue.
    """
    url = POPLEG_URLS.get(year)
    if not url:
        return {}

    print(f"   Téléchargement populations légales {year} (data.gouv.fr)...")
    try:
        resp = requests.get(url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"   ⚠️  Populations {year} non disponibles ({e}) — évolution ignorée pour cette année")
        return {}

    content = resp.content
    content_type = resp.headers.get("content-type", "")

    # Détecter si c'est un ZIP ou un CSV direct
    if content[:2] == b"PK" or "zip" in content_type:
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            csv_files = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not csv_files:
                print(f"   ⚠️  Aucun CSV dans le ZIP {year}")
                return {}
            raw = z.read(csv_files[0])
        except Exception as e:
            print(f"   ⚠️  Erreur lecture ZIP {year} : {e}")
            return {}
    else:
        raw = content

    # Décodage
    text = None
    for encoding in ("utf-8-sig", "latin-1", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        print(f"   ⚠️  Encodage inconnu pour {year}")
        return {}

    # Parsing CSV
    try:
        first_line = text.split("\n")[0]
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return {}

        # Trouver les colonnes (noms varient selon millésime INSEE)
        keys = list(rows[0].keys())
        codgeo_col = next((k for k in keys if k.upper().strip() == "CODGEO"), None)
        pmun_col = next(
            (k for k in keys if k.upper().strip() in (
                "PMUN", f"PMUN{str(year)[-2:]}", f"P{year}_POP"
            )),
            None
        )
        if pmun_col is None:
            pmun_col = next((k for k in keys if "PMUN" in k.upper()), None)

        if not codgeo_col or not pmun_col:
            print(f"   ⚠️  Colonnes CODGEO/PMUN introuvables pour {year}. Colonnes : {keys[:8]}")
            return {}

        result = {}
        for row in rows:
            code = row.get(codgeo_col, "").strip()
            val = row.get(pmun_col, "").strip()
            if code and val:
                try:
                    result[code] = int(float(val))
                except ValueError:
                    pass

        print(f"   → {len(result)} communes chargées (année {year})")
        return result

    except Exception as e:
        print(f"   ⚠️  Erreur parsing CSV {year} : {e}")
        return {}


# ------------------------------------------------------------
# Enrichissement avec évolution
# ------------------------------------------------------------

def enrich_with_evolution(geojson: dict, pop_old: dict, pop_new: dict,
                           year_old: int, year_new: int) -> dict:
    for feature in geojson.get("features", []):
        props = feature["properties"]
        code = props.get("code", "")

        p_new = pop_new.get(code)
        p_old = pop_old.get(code)

        if p_new is not None:
            props["population"] = p_new
            props["population_source"] = f"INSEE {year_new}"
        else:
            props["population_source"] = "geo.api.gouv.fr"

        pop_ref = p_new if p_new is not None else props.get("population")
        if p_old is not None and pop_ref is not None and p_old > 0:
            props[f"population_{year_old}"] = p_old
            props[f"population_{year_new}"] = pop_ref
            props["evolution_absolue"] = pop_ref - p_old
            props["evolution_pct"] = round((pop_ref - p_old) / p_old * 100, 2)
            props["evolution_periode"] = f"{year_old}–{year_new}"
        else:
            props["evolution_absolue"] = None
            props["evolution_pct"] = None
            props["evolution_periode"] = f"{year_old}–{year_new}"

    return geojson


def compute_stats(geojson: dict) -> dict:
    features = geojson.get("features", [])
    pops = [f["properties"].get("population") for f in features
            if f["properties"].get("population") is not None]
    evols = [f["properties"].get("evolution_pct") for f in features
             if f["properties"].get("evolution_pct") is not None]

    if not pops:
        return {}

    return {
        "nb_communes": len(features),
        "population_totale": sum(pops),
        "population_min": min(pops),
        "population_max": max(pops),
        "evolution_pct_min": min(evols) if evols else None,
        "evolution_pct_max": max(evols) if evols else None,
        "communes_en_croissance": sum(1 for e in evols if e > 0),
        "communes_en_declin": sum(1 for e in evols if e < 0),
        "communes_sans_evolution": len(features) - len(evols),
    }


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Chargement populations légales (non bloquant si indisponible)
    pop_2015 = fetch_popleg_datagouv(2015)
    pop_2021 = fetch_popleg_datagouv(2021)
    insee_available = bool(pop_2015 or pop_2021)

    critical_errors = []
    warnings = []

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP_HAUTE_MARNE})")
    try:
        communes_hm = fetch_communes_geojson(DEP_HAUTE_MARNE)
        communes_hm = enrich_with_evolution(communes_hm, pop_2015, pop_2021, 2015, 2021)
        stats_hm = compute_stats(communes_hm)

        communes_hm["metadata"] = {
            "source": "geo.api.gouv.fr + INSEE populations légales (data.gouv.fr)",
            "license": "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp,
            "departement": DEP_HAUTE_MARNE,
            "insee_data_available": insee_available,
            **stats_hm,
        }

        path_hm = os.path.join(OUTPUT_DIR, "haute-marne_communes.geojson")
        with open(path_hm, "w", encoding="utf-8") as f:
            json.dump(communes_hm, f, ensure_ascii=False, indent=2)

        print(f"✓  {path_hm} ({stats_hm.get('nb_communes', '?')} communes)")
        print(f"   Population totale : {stats_hm.get('population_totale', '?'):,}".replace(",", " "))
        c = stats_hm.get("communes_en_croissance")
        d = stats_hm.get("communes_en_declin")
        s = stats_hm.get("communes_sans_evolution")
        if c is not None:
            print(f"   Croissance : {c} | Déclin : {d} | Sans données évol. : {s}")

    except Exception as e:
        msg = f"Communes Haute-Marne : {e}"
        print(f"❌ {msg}", file=sys.stderr)
        critical_errors.append(msg)

    # --- Départements France ---
    print("\n📍 Départements France")
    try:
        deps = fetch_departements_geojson()
        nb_deps = len(deps.get("features", []))

        deps["metadata"] = {
            "source": "geo.api.gouv.fr",
            "license": "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp,
            "nb_departements": nb_deps,
        }

        path_deps = os.path.join(OUTPUT_DIR, "france_departements.geojson")
        with open(path_deps, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        print(f"✓  {path_deps} ({nb_deps} départements)")

    except Exception as e:
        msg = f"Départements France : {e}"
        print(f"❌ {msg}", file=sys.stderr)
        critical_errors.append(msg)

    # --- Metadata ---
    metadata = {
        "last_updated": timestamp,
        "sources": {
            "geometries": "geo.api.gouv.fr (Etalab)",
            "populations": "INSEE via data.gouv.fr",
        },
        "license": "Licence Ouverte 2.0",
        "insee_data_available": insee_available,
        "critical_errors": critical_errors,
        "warnings": warnings,
    }
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {meta_path}")

    # Seules les erreurs critiques (communes/deps inaccessibles) font échouer le workflow
    if critical_errors:
        print(f"\n❌ {len(critical_errors)} erreur(s) critique(s)", file=sys.stderr)
        sys.exit(1)

    if not insee_available:
        print("\n⚠️  Données INSEE indisponibles — population de base geo.api.gouv.fr uniquement.")
        print("   L'évolution 2015–2021 sera absente jusqu'à la prochaine exécution.")

    print("\n✅ Données de population mises à jour.")


if __name__ == "__main__":
    main()
