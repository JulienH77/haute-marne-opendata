#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Utilise l'API INSEE Mélodi (sans clé) pour récupérer les séries historiques
de population municipale par commune.

URL : https://api.insee.fr/melodi/data/DS_POPULATIONS_HISTORIQUES
      ?POPREF_MEASURE=PMUN&GEO=COM

Structure de réponse :
  { "observations": [
      { "dimensions": {"GEO": "2025-COM-52001", "TIME_PERIOD": "2023"},
        "measures":   {"OBS_VALUE_NIVEAU": {"value": 197.0}} }
  ]}

La clé GEO contient le code INSEE 5 chiffres : "2025-COM-CCCCC"
On filtre sur DEP 52 pour la Haute-Marne.

Années disponibles dans l'API : 2006→2023 (recensements annuels rénové)
  + 1999, 1990, 1982, 1975, 1968 (recensements généraux) si disponibles.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from collections import defaultdict

import requests

DEP         = "52"
GEO_API     = "https://geo.api.gouv.fr"
MELODI_URL  = "https://api.insee.fr/melodi/data/DS_POPULATIONS_HISTORIQUES"
GREGOR_URL  = ("https://raw.githubusercontent.com/gregoiredavid/france-geojson"
               "/master/departements-version-simplifiee.geojson")
OUT         = "data/population"
LAST_UPDATE = "data/last_update.json"
HEADERS     = {"User-Agent": "haute-marne-opendata/1.0", "Accept": "application/json"}

# Années à récupérer — Mélodi couvre au moins 2006-2023 + les recensements anciens
TARGET_YEARS = [2023, 2022, 2021, 2016, 2011, 2006, 1999, 1990, 1982, 1975, 1968]

# Pour la France entière, on limite pour rester dans le timeout (30 min)
FRANCE_YEARS = [2023, 2016, 2006]

# Taille de page Mélodi (max testé : 1000)
PAGE_SIZE = 1000


# ─── API Mélodi ──────────────────────────────────────────────────────────────

def melodi_fetch_page(time_period: int, start: int = 0) -> list[dict]:
    """Récupère une page de données Mélodi pour une année donnée."""
    params = {
        "POPREF_MEASURE": "PMUN",
        "GEO":            "COM",
        "TIME_PERIOD":    str(time_period),
        "maxResult":      PAGE_SIZE,
        "startPosition":  start,
    }
    try:
        r = requests.get(MELODI_URL, params=params, headers=HEADERS, timeout=60)
        r.raise_for_status()
        return r.json().get("observations", [])
    except Exception as e:
        print(f"      ⚠️  Mélodi {time_period} pos {start} : {e}")
        return []


def melodi_fetch_year(year: int, dep_filter: str | None = None) -> dict[str, int]:
    """
    Télécharge toutes les communes pour une année donnée depuis l'API Mélodi.
    Si dep_filter est fourni (ex: "52"), ne garde que les communes de ce département.
    Retourne {code_insee_5: population}.
    """
    result: dict[str, int] = {}
    start = 0
    page  = 1

    while True:
        obs = melodi_fetch_page(year, start)
        if not obs:
            break

        for o in obs:
            geo  = o.get("dimensions", {}).get("GEO", "")
            val  = o.get("measures",   {}).get("OBS_VALUE_NIVEAU", {}).get("value")
            # GEO = "2025-COM-52001" → code INSEE = "52001"
            m = re.search(r"-COM-(\d{5})$", geo)
            if not m or val is None:
                continue
            code = m.group(1)
            if dep_filter and not code.startswith(dep_filter):
                continue
            result[code] = int(val)

        nb = len(obs)
        print(f"      Page {page} ({start}→{start+nb}) : {nb} obs | trouvées : {len(result)}")
        if nb < PAGE_SIZE:
            break   # dernière page

        start += PAGE_SIZE
        page  += 1
        time.sleep(0.1)   # courtoisie

    return result


def fetch_melodi_for_years(
    years: list[int],
    dep_filter: str | None = None,
    label: str = ""
) -> dict[int, dict[str, int]]:
    """
    Récupère les données Mélodi pour une liste d'années.
    Retourne {année: {code_insee: population}}.
    """
    all_pop: dict[int, dict[str, int]] = {}
    for year in years:
        print(f"   [{label}] Année {year}...")
        data = melodi_fetch_year(year, dep_filter)
        if data:
            all_pop[year] = data
            n = len(data)
            print(f"   ✓  {year} : {n:,} communes")
        else:
            print(f"   ⚠️  {year} : aucune donnée")
        time.sleep(0.3)
    return all_pop


# ─── Géographies ─────────────────────────────────────────────────────────────

def fetch_communes_geojson(dep: str) -> dict:
    r = requests.get(f"{GEO_API}/communes", timeout=60, headers=HEADERS, params={
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson", "geometry": "contour",
    })
    r.raise_for_status()
    return r.json()


def fetch_departements_geojson() -> dict:
    print("   Géométries gregoiredavid/france-geojson...")
    r = requests.get(GREGOR_URL, timeout=30, headers=HEADERS)
    r.raise_for_status()
    gj = r.json()
    nb = len(gj.get("features", []))
    has_geom = sum(1 for f in gj.get("features", []) if f.get("geometry"))
    print(f"   → {nb} départements, {has_geom} avec géométrie")

    # Populations actuelles via geo.api.gouv.fr
    try:
        rp = requests.get(f"{GEO_API}/departements", timeout=30, headers=HEADERS,
                          params={"fields": "code,nom,population", "format": "json"})
        pop_map = {d.get("code"): d.get("population") for d in rp.json()}
        for feat in gj.get("features", []):
            feat["properties"]["population"] = pop_map.get(
                feat.get("properties", {}).get("code"))
        print(f"   → {len(pop_map)} populations enrichies")
    except Exception as e:
        print(f"   ⚠️  Populations départements : {e}")

    return gj


# ─── Enrichissement ──────────────────────────────────────────────────────────

def enrich_communes(geojson: dict, all_pop: dict[int, dict[str, int]]) -> dict:
    years_desc = sorted(all_pop.keys(), reverse=True)
    y_max = years_desc[0]  if years_desc               else None
    y_min = years_desc[-1] if len(years_desc) > 1      else None

    nb_ok = 0
    for feat in geojson.get("features", []):
        props = feat["properties"]
        code  = props.get("code", "")

        # Remplir chaque année disponible
        for year, pop_dict in all_pop.items():
            props[f"population_{year}"] = pop_dict.get(code)

        # Population principale = plus récente
        if y_max and all_pop.get(y_max, {}).get(code) is not None:
            props["population"]        = all_pop[y_max][code]
            props["population_source"] = f"INSEE Mélodi {y_max}"

        # Évolution entre extrêmes
        p_max = all_pop.get(y_max, {}).get(code) if y_max else None
        p_min = all_pop.get(y_min, {}).get(code) if y_min else None
        if p_max and p_min and p_min > 0:
            props["evolution_absolue"] = p_max - p_min
            props["evolution_pct"]     = round((p_max - p_min) / p_min * 100, 2)
            props["evolution_periode"] = f"{y_min}–{y_max}"
            nb_ok += 1
        else:
            props["evolution_absolue"] = None
            props["evolution_pct"]     = None
            props["evolution_periode"] = (f"{y_min}–{y_max}"
                                          if y_min and y_max else "N/A")

    total = len(geojson.get("features", []))
    print(f"   → Évolution : {nb_ok}/{total} communes")
    print(f"   → Années dans GeoJSON : {years_desc}")
    return geojson


def stats(gj: dict) -> dict:
    feats = gj.get("features", [])
    pops  = [f["properties"].get("population") for f in feats
             if f["properties"].get("population") is not None]
    evols = [f["properties"].get("evolution_pct") for f in feats
             if f["properties"].get("evolution_pct") is not None]
    if not pops: return {}
    return {
        "nb_communes": len(feats), "population_totale": sum(pops),
        "communes_avec_evolution": len(evols),
        "communes_en_croissance": sum(1 for e in evols if e > 0),
        "communes_en_declin":     sum(1 for e in evols if e < 0),
    }


def update_last_update(key: str, ts: str, extra: dict | None = None):
    try:
        with open(LAST_UPDATE, encoding="utf-8") as f: data = json.load(f)
    except Exception: data = {}
    data[key] = {"timestamp_utc": ts, **(extra or {})}
    data["_last_any_update"] = ts
    os.makedirs(os.path.dirname(LAST_UPDATE) or ".", exist_ok=True)
    with open(LAST_UPDATE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    errors = []

    # ── 1. Population Haute-Marne (toutes années cibles) ──────────────────────
    print(f"\n📊 Données Mélodi — Haute-Marne (dép. {DEP})\n")
    pop_hm = fetch_melodi_for_years(TARGET_YEARS, dep_filter=DEP, label="HM")
    years_hm = sorted(pop_hm.keys(), reverse=True)
    print(f"\n✓  Années HM disponibles : {years_hm}")

    print(f"\n📍 Communes Haute-Marne")
    try:
        communes = fetch_communes_geojson(DEP)
        communes = enrich_communes(communes, pop_hm)
        s        = stats(communes)
        communes["metadata"] = {
            "source": "geo.api.gouv.fr (géométries) + INSEE Mélodi API (populations)",
            "license": "Licence Ouverte 2.0 (Etalab / INSEE)",
            "melodi_url": MELODI_URL,
            "last_updated": ts, "departement": DEP,
            "annees_dispo": years_hm, **s,
        }
        path = os.path.join(OUT, "haute-marne_communes.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(communes, f, ensure_ascii=False, indent=2)
        print(f"✓  {path} ({s.get('nb_communes','?')} communes)")
        print(f"   Pop. totale : {s.get('population_totale', 0):,}".replace(",", " "))
        print(f"   ↑{s.get('communes_en_croissance','?')} / ↓{s.get('communes_en_declin','?')}")
    except Exception as e:
        errors.append(f"Communes HM : {e}")
        print(f"❌ {errors[-1]}", file=sys.stderr)

    # ── 2. Départements France (géométries + population) ─────────────────────
    print("\n📍 Départements France")
    try:
        deps = fetch_departements_geojson()
        deps["metadata"] = {
            "source_geom": "gregoiredavid/france-geojson (MIT)",
            "source_pop":  "geo.api.gouv.fr",
            "license": "Licence Ouverte 2.0 / MIT",
            "last_updated": ts,
            "nb_departements": len(deps.get("features", [])),
        }
        path = os.path.join(OUT, "france_departements.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        nb_g = sum(1 for f in deps.get("features", []) if f.get("geometry"))
        print(f"✓  {path} ({nb_g} géométries)")
    except Exception as e:
        errors.append(f"Départements : {e}")
        print(f"❌ {errors[-1]}", file=sys.stderr)

    # ── 3. last_update.json ──────────────────────────────────────────────────
    years_miss = [y for y in TARGET_YEARS if y not in pop_hm]
    update_last_update("population", ts, {
        "source":           "INSEE API Mélodi",
        "annees_dispo":     years_hm,
        "annees_manquantes": years_miss,
    })

    with open(os.path.join(OUT, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": ts,
            "source": "INSEE API Mélodi (https://api.insee.fr/melodi)",
            "annees_dispo_hm": years_hm,
            "annees_manquantes": years_miss,
            "critical_errors": errors,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {OUT}/metadata.json")

    if errors: sys.exit(1)
    if years_miss: print(f"\n⚠️  Années sans données Mélodi : {years_miss}")
    print("\n✅ Population OK.")


if __name__ == "__main__":
    main()
