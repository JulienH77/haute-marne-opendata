#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne + France.

Stratégie population historique :
  Le fichier "base-cc-evol-struct-pop-2021" de l'INSEE contient DÉJÀ
  les colonnes pour plusieurs années de recensement (2021, 2016, 2011, 2006...).
  → UN SEUL téléchargement, plusieurs années disponibles.

  Si ce fichier échoue, fallback sur les séries historiques data.gouv.fr.

Années cibles : 2022/2021 (récente) + 2016, 2011 (historiques dans le même fichier).
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

DEP_HAUTE_MARNE = "52"
GEO_API_BASE    = "https://geo.api.gouv.fr"
OUTPUT_DIR      = "data/population"

# Fichier unique qui contient PLUSIEURS années de recensement
# Ce fichier "base-cc-evol-struct-pop" est publié par l'INSEE sur data.gouv.fr
# et contient des colonnes PMUN21 (2021), PMUN16 (2016), PMUN11 (2011), PMUN06 (2006)
MULTI_YEAR_SOURCES = [
    # Fichier 2021 — contient PMUN21, PMUN16, PMUN11, PMUN06
    "https://www.data.gouv.fr/fr/datasets/r/d9de1a5f-47ec-41c6-b9b2-cf21dfde019a",
    "https://www.data.gouv.fr/fr/datasets/r/6a4e7a5b-e8f2-499c-8d14-0ae19e7e0f21",
    # Slug direct data.gouv.fr
    "https://www.data.gouv.fr/fr/datasets/populations-legales-en-2021/",
]

# Fallback : fichier séries historiques (1968→aujourd'hui)
HISTORIQUE_SOURCES = [
    "https://www.data.gouv.fr/fr/datasets/populations-legales-des-communes-depuis-1968/",
]

# Années souhaitées (récentes → anciennes)
YEAR_TARGETS = [2022, 2021, 2020, 2019, 2018, 2016, 2011, 2006]


# -------------------------------------------------------
# Téléchargement + parsing défensif
# -------------------------------------------------------

def get_raw_content(url: str) -> bytes | None:
    """Télécharge une URL et retourne les bytes bruts (gère ZIP et CSV direct)."""
    try:
        r = requests.get(url, timeout=90, allow_redirects=True,
                         headers={"User-Agent": "haute-marne-opendata/1.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"      ⚠️  Téléchargement : {e}")
        return None

    content = r.content
    size_kb = len(content) / 1024
    print(f"      Reçu : {size_kb:.0f} Ko")

    if len(content) < 1000:
        print(f"      ⚠️  Fichier trop petit ({size_kb:.0f} Ko) — probablement une page HTML")
        return None

    if content[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            # Prendre le plus gros CSV/TXT (= le fichier de données, pas la notice)
            csv_files = sorted(
                [n for n in z.namelist()
                 if n.lower().endswith((".csv", ".txt"))
                 and not any(w in n.lower() for w in ("notice", "readme", "doc", "meta"))],
                key=lambda n: z.getinfo(n).file_size, reverse=True,
            )
            if not csv_files:
                csv_files = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not csv_files:
                print(f"      ⚠️  ZIP vide : {z.namelist()}")
                return None
            print(f"      ZIP → '{csv_files[0]}' ({z.getinfo(csv_files[0]).file_size // 1024} Ko)")
            return z.read(csv_files[0])
        except Exception as e:
            print(f"      ⚠️  ZIP invalide : {e}")
            return None
    return content


def decode_text(raw: bytes) -> str | None:
    for enc in ("utf-8-sig", "latin-1", "utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def parse_multi_year_csv(raw: bytes) -> dict[int, dict[str, int]]:
    """
    Parse un fichier populations légales INSEE et extrait TOUTES les années
    disponibles (colonnes PMUN\d\d ou P\d\d_POP).
    Retourne {année: {code_commune: population}}.
    """
    text = decode_text(raw)
    if not text:
        print("      ⚠️  Encodage non détecté")
        return {}

    first_line = text.split("\n")[0]
    delimiters = [";", ",", "\t", "|"]
    delimiter  = max(delimiters, key=lambda d: first_line.count(d))

    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows   = list(reader)
    except Exception as e:
        print(f"      ⚠️  Erreur CSV : {e}")
        return {}

    if not rows:
        print("      ⚠️  CSV vide")
        return {}

    keys = list(rows[0].keys())
    print(f"      Colonnes ({len(keys)}) : {keys[:20]}")

    # --- Colonne code commune ---
    codgeo_col = next(
        (k for k in keys if k.upper().strip() in ("CODGEO", "COM", "CODE_COM", "CODECOM", "CODE")),
        None,
    )
    if not codgeo_col:
        # Détection par contenu : colonne dont les valeurs ressemblent à des codes INSEE
        for k in keys:
            vals = [rows[i].get(k, "").strip() for i in range(min(10, len(rows)))]
            if vals and all(re.match(r"^\d{5}$", v) for v in vals if v):
                codgeo_col = k
                break
    if not codgeo_col:
        print(f"      ⚠️  Colonne code commune introuvable. Colonnes : {keys}")
        return {}
    print(f"      Code commune : '{codgeo_col}'")

    # --- Colonnes population par année ---
    # Patterns : PMUN21, PMUN2021, P21_POP, P2021_POP, PTOT21...
    year_cols = {}
    for k in keys:
        ku = k.upper().strip()
        # PMUN + 2 chiffres
        m = re.match(r"^PMUN(\d{2})$", ku)
        if m:
            y2 = int(m.group(1))
            year = 2000 + y2 if y2 <= 30 else 1900 + y2
            year_cols[year] = k
            continue
        # PMUN + 4 chiffres
        m = re.match(r"^PMUN(\d{4})$", ku)
        if m:
            year_cols[int(m.group(1))] = k
            continue
        # P + 2 chiffres + _POP
        m = re.match(r"^P(\d{2})_POP$", ku)
        if m:
            y2 = int(m.group(1))
            year = 2000 + y2 if y2 <= 30 else 1900 + y2
            year_cols[year] = k
            continue
        # P + 4 chiffres + _POP
        m = re.match(r"^P(\d{4})_POP$", ku)
        if m:
            year_cols[int(m.group(1))] = k
            continue

    if not year_cols:
        print(f"      ⚠️  Aucune colonne PMUN trouvée. Colonnes : {keys}")
        return {}

    print(f"      Années trouvées : {sorted(year_cols.keys())} → {year_cols}")

    # --- Extraction ---
    result: dict[int, dict[str, int]] = {y: {} for y in year_cols}
    for row in rows:
        code = str(row.get(codgeo_col, "")).strip()
        if not re.match(r"^\d{5}$", code):
            continue
        for year, col in year_cols.items():
            val = str(row.get(col, "")).strip()
            if val and val not in ("", "nan", "NaN", "#"):
                try:
                    result[year][code] = int(float(val))
                except ValueError:
                    pass

    for y, d in result.items():
        print(f"      → {y} : {len(d):,} communes")
    return result


def fetch_all_years_data() -> dict[int, dict[str, int]]:
    """
    Tente de charger toutes les années de population disponibles.
    Stratégie :
      1. Fichiers multi-années connus (contiennent PMUN21 + PMUN16 + PMUN11...)
      2. Recherche API data.gouv.fr sur les datasets population
      3. Retourne le meilleur résultat trouvé
    """
    all_years: dict[int, dict[str, int]] = {}

    # --- Stratégie 1 : fichiers multi-années hardcodés ---
    print("\n[Stratégie 1 : fichiers INSEE multi-années]")
    for url in MULTI_YEAR_SOURCES:
        print(f"   Essai : {url[:70]}...")
        raw = get_raw_content(url)
        if raw:
            years_data = parse_multi_year_csv(raw)
            if years_data:
                # Fusionner en gardant le max de communes par année
                for y, d in years_data.items():
                    if y not in all_years or len(d) > len(all_years[y]):
                        all_years[y] = d
                if all_years:
                    print(f"   ✓  Années récupérées : {sorted(all_years.keys())}")
                    break

    # --- Stratégie 2 : recherche API data.gouv.fr ---
    if not all_years:
        print("\n[Stratégie 2 : recherche API data.gouv.fr]")
        slugs_to_try = [
            "populations-legales-en-2021",
            "populations-legales-en-2020",
            "populations-legales-en-2019",
        ]
        for slug in slugs_to_try:
            try:
                r = requests.get(
                    f"https://www.data.gouv.fr/api/1/datasets/{slug}/",
                    timeout=15, headers={"User-Agent": "haute-marne-opendata/1.0"}
                )
                if r.status_code != 200:
                    continue
                resources = r.json().get("resources", [])
                for res in sorted(resources, key=lambda x: x.get("filesize", 0) or 0, reverse=True):
                    url  = res.get("url", "")
                    fmt  = (res.get("format") or "").lower()
                    name = (res.get("title") or "").lower()
                    is_data = fmt in ("csv", "zip") or url.lower().endswith((".csv", ".zip"))
                    is_doc  = any(w in name for w in ("notice", "readme", "doc", "dictionnaire"))
                    if is_data and not is_doc:
                        print(f"   Essai : {url[-65:]}")
                        raw = get_raw_content(url)
                        if raw:
                            years_data = parse_multi_year_csv(raw)
                            if years_data:
                                all_years.update(years_data)
                                break
                if all_years:
                    break
            except Exception as e:
                print(f"   ⚠️  {slug} : {e}")

    return all_years


# -------------------------------------------------------
# Géographies geo.api.gouv.fr
# -------------------------------------------------------

def fetch_communes_geojson(dep: str) -> dict:
    params = {
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson",
        "geometry": "contour",
    }
    r = requests.get(f"{GEO_API_BASE}/communes", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_departements_geojson() -> dict:
    """
    Récupère les départements avec géométries.
    Utilise geometry=contour et format=geojson pour avoir les polygones.
    """
    params = {
        "fields": "nom,code,codeRegion,population",
        "format": "geojson",
        "geometry": "contour",
    }
    r = requests.get(f"{GEO_API_BASE}/departements", params=params, timeout=120)
    r.raise_for_status()
    data = r.json()

    # Normalisation selon le type retourné
    if isinstance(data, list):
        # Liste brute de features → encapsuler
        return {"type": "FeatureCollection", "features": data}
    if isinstance(data, dict):
        if "features" in data:
            return data
        # Dict sans "features" : probablement un seul département
        return {"type": "FeatureCollection", "features": [data]}

    return {"type": "FeatureCollection", "features": []}


# -------------------------------------------------------
# Enrichissement
# -------------------------------------------------------

def enrich_communes(geojson, all_years_data: dict[int, dict[str, int]]):
    """
    Ajoute à chaque commune :
      - population_{year} pour chaque année disponible
      - evolution_absolue / evolution_pct entre la plus récente et la plus ancienne
    """
    available_years = sorted(all_years_data.keys(), reverse=True)
    year_recent = available_years[0]  if available_years else None
    year_old    = available_years[-1] if len(available_years) > 1 else None

    nb_ok = 0
    for feat in geojson.get("features", []):
        props = feat["properties"]
        code  = props.get("code", "")

        # Remplir toutes les années disponibles
        for year, pop_dict in all_years_data.items():
            val = pop_dict.get(code)
            props[f"population_{year}"] = val

        # Mettre à jour la population principale avec la plus récente
        if year_recent:
            p_recent = all_years_data[year_recent].get(code)
            if p_recent is not None:
                props["population"]        = p_recent
                props["population_source"] = f"INSEE {year_recent}"
            else:
                props["population_source"] = "geo.api.gouv.fr"

        # Calcul évolution
        if year_recent and year_old and year_recent != year_old:
            p_new = all_years_data[year_recent].get(code)
            p_old = all_years_data[year_old].get(code)
            if p_new is not None and p_old is not None and p_old > 0:
                props["evolution_absolue"] = p_new - p_old
                props["evolution_pct"]     = round((p_new - p_old) / p_old * 100, 2)
                props["evolution_periode"] = f"{year_old}–{year_recent}"
                nb_ok += 1
            else:
                props["evolution_absolue"] = None
                props["evolution_pct"]     = None
                props["evolution_periode"] = f"{year_old if year_old else '?'}–{year_recent}"
        else:
            props["evolution_absolue"] = None
            props["evolution_pct"]     = None
            props["evolution_periode"] = "données historiques manquantes"

    total = len(geojson.get("features", []))
    print(f"   → Évolution calculée : {nb_ok}/{total} communes")
    if available_years:
        print(f"   → Années disponibles dans GeoJSON : {available_years}")
    return geojson


def compute_stats(geojson):
    features = geojson.get("features", [])
    pops  = [f["properties"].get("population") for f in features if f["properties"].get("population") is not None]
    evols = [f["properties"].get("evolution_pct") for f in features if f["properties"].get("evolution_pct") is not None]
    if not pops:
        return {}
    return {
        "nb_communes":             len(features),
        "population_totale":       sum(pops),
        "communes_avec_evolution": len(evols),
        "communes_en_croissance":  sum(1 for e in evols if e > 0),
        "communes_en_declin":      sum(1 for e in evols if e < 0),
    }


# -------------------------------------------------------
# Main
# -------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("📊 Chargement populations légales INSEE (multi-années)")
    all_years_data = fetch_all_years_data()

    if not all_years_data:
        print("⚠️  Aucune donnée INSEE chargée — population sans historique")
    else:
        print(f"\n✓  Années chargées : {sorted(all_years_data.keys())}")

    critical_errors = []

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP_HAUTE_MARNE})")
    try:
        print("   Récupération géométries...")
        communes_hm = fetch_communes_geojson(DEP_HAUTE_MARNE)
        communes_hm = enrich_communes(communes_hm, all_years_data)
        stats       = compute_stats(communes_hm)

        available_years = sorted(all_years_data.keys(), reverse=True)
        communes_hm["metadata"] = {
            "source":          "geo.api.gouv.fr + INSEE populations légales (data.gouv.fr)",
            "license":         "Licence Ouverte 2.0 (Etalab)",
            "last_updated":    timestamp,
            "departement":     DEP_HAUTE_MARNE,
            "annees_dispo":    available_years,
            "annee_recente":   available_years[0]  if available_years else None,
            "annee_ancienne":  available_years[-1] if len(available_years) > 1 else None,
            **stats,
        }

        path = os.path.join(OUTPUT_DIR, "haute-marne_communes.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(communes_hm, f, ensure_ascii=False, indent=2)

        print(f"✓  {path} ({stats.get('nb_communes','?')} communes)")
        print(f"   Population totale   : {stats.get('population_totale', 0):,}".replace(",", " "))
        print(f"   Croissance / Déclin : {stats.get('communes_en_croissance','?')} / {stats.get('communes_en_declin','?')}")

    except Exception as e:
        critical_errors.append(f"Communes HM : {e}")
        print(f"❌ {critical_errors[-1]}", file=sys.stderr)

    # --- Départements France (avec géométries) ---
    print("\n📍 Départements France")
    try:
        print("   Récupération géométries contour...")
        deps    = fetch_departements_geojson()
        nb_deps = len(deps.get("features", []))

        # Vérifier que les géométries sont bien présentes
        has_geom = sum(1 for f in deps.get("features", []) if f.get("geometry") is not None)
        print(f"   → {nb_deps} départements, {has_geom} avec géométrie")

        if has_geom == 0:
            # Retry avec une approche différente
            print("   ⚠️  Aucune géométrie — retry avec geometry=bbox...")
            params2 = {
                "fields": "nom,code,codeRegion,population",
                "format": "geojson",
                "geometry": "bbox",
            }
            r2 = requests.get(f"{GEO_API_BASE}/departements", params=params2, timeout=120)
            r2.raise_for_status()
            deps2 = r2.json()
            if isinstance(deps2, list):
                deps2 = {"type": "FeatureCollection", "features": deps2}
            has_geom2 = sum(1 for f in deps2.get("features", []) if f.get("geometry") is not None)
            print(f"   → Retry : {has_geom2} géométries bbox trouvées")
            if has_geom2 > has_geom:
                deps = deps2

        deps["metadata"] = {
            "source":       "geo.api.gouv.fr",
            "license":      "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp,
            "nb_departements": nb_deps,
        }

        path = os.path.join(OUTPUT_DIR, "france_departements.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        print(f"✓  {path} ({nb_deps} départements)")

    except Exception as e:
        critical_errors.append(f"Départements : {e}")
        print(f"❌ {critical_errors[-1]}", file=sys.stderr)

    # --- Metadata ---
    with open(os.path.join(OUTPUT_DIR, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "last_updated":   timestamp,
            "annees_dispo":   sorted(all_years_data.keys(), reverse=True),
            "sources": {
                "geometries":  "geo.api.gouv.fr",
                "populations": "INSEE via data.gouv.fr",
            },
            "critical_errors": critical_errors,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {OUTPUT_DIR}/metadata.json")

    if critical_errors:
        sys.exit(1)
    print("\n✅ Données population mises à jour.")


if __name__ == "__main__":
    main()
