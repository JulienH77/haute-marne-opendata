#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Récupère les données de population pour la Haute-Marne (dép. 52)
et pour la France (niveau département).

Sources :
  - geo.api.gouv.fr  → géométries communales + population actuelle
  - INSEE             → populations légales 2015 et 2021 pour calculer l'évolution

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

# URLs des fichiers populations légales INSEE (ZIP contenant un CSV)
# Ces fichiers sont les "base-cc-evol-struct-pop" publiés par l'INSEE
POPLEG_URLS = {
    2015: "https://www.insee.fr/fr/statistiques/fichier/2028582/base_cc_evol_struct_pop_2015_csv.zip",
    2021: "https://www.insee.fr/fr/statistiques/fichier/6692261/base-cc-evol-struct-pop-2021_csv.zip",
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
    """Récupère le GeoJSON de tous les départements avec population."""
    url = f"{GEO_API_BASE}/departements"
    params = {
        "fields": "nom,code,codeRegion,population",
        "format": "geojson",
        "geometry": "contour",
    }
    print("   Récupération des départements...")
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------
# Populations légales INSEE
# ------------------------------------------------------------

def fetch_popleg_insee(year: int) -> dict:
    """
    Télécharge et parse le fichier ZIP des populations légales INSEE.
    Retourne un dict {code_commune: population_municipale}.
    """
    url = POPLEG_URLS.get(year)
    if not url:
        print(f"⚠️  Pas d'URL configurée pour l'année {year}")
        return {}

    print(f"   Téléchargement populations légales {year}...")
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"⚠️  Impossible de télécharger {year} : {e}")
        return {}

    try:
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        # Trouver le fichier CSV ou TXT dans le ZIP
        csv_candidates = [
            n for n in z.namelist()
            if n.lower().endswith(".csv") or n.lower().endswith(".txt")
        ]
        if not csv_candidates:
            print(f"⚠️  Aucun CSV trouvé dans le ZIP {year}")
            return {}

        raw = z.read(csv_candidates[0])

        # Détecter l'encodage (INSEE utilise souvent latin-1)
        text = None
        for encoding in ("utf-8-sig", "latin-1", "utf-8"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            print(f"⚠️  Encodage non détecté pour {year}")
            return {}

        # Détecter le séparateur
        first_line = text.split("\n")[0]
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","

        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)

        # Trouver les colonnes clés (noms peuvent varier selon le millésime)
        if not rows:
            return {}

        # Colonne code géo
        codgeo_col = next(
            (k for k in rows[0].keys() if "CODGEO" in k.upper()), None
        )
        # Colonne population municipale
        pmun_col = next(
            (k for k in rows[0].keys() if k.upper() in ("PMUN", "P_POP", "PMUN21", "PMUN20", "PMUN19", "PMUN18", "PMUN17", "PMUN16", "PMUN15")),
            None,
        )
        if pmun_col is None:
            # Fallback : chercher la première colonne contenant PMUN
            pmun_col = next(
                (k for k in rows[0].keys() if "PMUN" in k.upper()), None
            )

        if not codgeo_col or not pmun_col:
            print(f"⚠️  Colonnes CODGEO/PMUN non trouvées pour {year}. Colonnes disponibles : {list(rows[0].keys())[:10]}")
            return {}

        pop_dict = {}
        for row in rows:
            code = row.get(codgeo_col, "").strip()
            pmun = row.get(pmun_col, "").strip()
            if code and pmun:
                try:
                    pop_dict[code] = int(float(pmun))
                except ValueError:
                    pass

        print(f"   → {len(pop_dict)} communes chargées (année {year})")
        return pop_dict

    except Exception as e:
        print(f"⚠️  Erreur lors du parsing {year} : {e}")
        return {}


# ------------------------------------------------------------
# Enrichissement avec évolution
# ------------------------------------------------------------

def enrich_with_evolution(geojson: dict, pop_old: dict, pop_new: dict, year_old: int, year_new: int) -> dict:
    """
    Ajoute les champs d'évolution de population à chaque feature du GeoJSON.
    Priorité aux données INSEE sur les données geo.api.gouv.fr.
    """
    for feature in geojson.get("features", []):
        props = feature["properties"]
        code = props.get("code", "")

        p_new = pop_new.get(code)
        p_old = pop_old.get(code)

        # Mise à jour de la population avec la valeur INSEE si disponible
        if p_new is not None:
            props["population"] = p_new
            props["population_source"] = f"INSEE {year_new}"
        else:
            props["population_source"] = "geo.api.gouv.fr"

        # Calcul de l'évolution
        pop_ref_new = p_new if p_new is not None else props.get("population")
        if p_old is not None and pop_ref_new is not None and p_old > 0:
            variation_abs = pop_ref_new - p_old
            variation_pct = round((pop_ref_new - p_old) / p_old * 100, 2)
            props[f"population_{year_old}"] = p_old
            props[f"population_{year_new}"] = pop_ref_new
            props["evolution_absolue"] = variation_abs
            props["evolution_pct"] = variation_pct
            props["evolution_periode"] = f"{year_old}–{year_new}"
        else:
            props["evolution_absolue"] = None
            props["evolution_pct"] = None
            props["evolution_periode"] = f"{year_old}–{year_new}"

    return geojson


# ------------------------------------------------------------
# Statistiques de synthèse
# ------------------------------------------------------------

def compute_stats(geojson: dict) -> dict:
    """Calcule des statistiques globales sur les communes."""
    features = geojson.get("features", [])
    pops = [f["properties"].get("population") for f in features if f["properties"].get("population")]
    evols = [f["properties"].get("evolution_pct") for f in features if f["properties"].get("evolution_pct") is not None]
    
    if not pops:
        return {}

    return {
        "nb_communes": len(features),
        "population_totale": sum(pops),
        "population_min": min(pops),
        "population_max": max(pops),
        "population_mediane": sorted(pops)[len(pops) // 2],
        "evolution_pct_min": min(evols) if evols else None,
        "evolution_pct_max": max(evols) if evols else None,
        "communes_en_croissance": sum(1 for e in evols if e > 0),
        "communes_en_declin": sum(1 for e in evols if e < 0),
    }


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    errors = []

    # --- Chargement des populations légales INSEE ---
    pop_2015 = fetch_popleg_insee(2015)
    pop_2021 = fetch_popleg_insee(2021)

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP_HAUTE_MARNE})")
    try:
        communes_hm = fetch_communes_geojson(DEP_HAUTE_MARNE)
        communes_hm = enrich_with_evolution(communes_hm, pop_2015, pop_2021, 2015, 2021)
        stats_hm = compute_stats(communes_hm)

        communes_hm["metadata"] = {
            "source": "geo.api.gouv.fr + INSEE populations légales",
            "license": "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp,
            "departement": DEP_HAUTE_MARNE,
            "description": "Population communale et évolution 2015–2021",
            **stats_hm,
        }

        path_hm = os.path.join(OUTPUT_DIR, "haute-marne_communes.geojson")
        with open(path_hm, "w", encoding="utf-8") as f:
            json.dump(communes_hm, f, ensure_ascii=False, indent=2)
        print(f"✓  {path_hm} ({stats_hm.get('nb_communes', '?')} communes)")
        print(f"   Population totale : {stats_hm.get('population_totale', '?'):,}".replace(",", " "))
        if stats_hm.get("communes_en_croissance") is not None:
            print(f"   En croissance : {stats_hm['communes_en_croissance']} | En déclin : {stats_hm['communes_en_declin']}")

    except Exception as e:
        msg = f"Erreur Haute-Marne communes : {e}"
        print(f"❌ {msg}", file=sys.stderr)
        errors.append(msg)

    # --- Départements France ---
    print("\n📍 Départements France")
    try:
        deps = fetch_departements_geojson()
        deps["metadata"] = {
            "source": "geo.api.gouv.fr",
            "license": "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp,
            "description": "Géométries et population des départements français",
            "nb_departements": len(deps.get("features", [])),
        }

        path_deps = os.path.join(OUTPUT_DIR, "france_departements.geojson")
        with open(path_deps, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        print(f"✓  {path_deps} ({len(deps.get('features', []))} départements)")

    except Exception as e:
        msg = f"Erreur départements France : {e}"
        print(f"❌ {msg}", file=sys.stderr)
        errors.append(msg)

    # --- Metadata globale ---
    metadata = {
        "last_updated": timestamp,
        "sources": {
            "geometries": "geo.api.gouv.fr (Etalab)",
            "populations": "INSEE populations légales",
        },
        "license": "Licence Ouverte 2.0 (Etalab / INSEE)",
        "annees_disponibles": sorted(POPLEG_URLS.keys()),
        "errors": errors,
    }
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {meta_path}")

    if errors:
        print(f"\n⚠️  {len(errors)} erreur(s) — voir metadata.json", file=sys.stderr)
        sys.exit(1)

    print("\n✅ Données de population mises à jour avec succès.")


if __name__ == "__main__":
    main()
