#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne et France, avec évolution 2015→2021.
Utilise l'API data.gouv.fr pour trouver dynamiquement les ressources INSEE
(plus robuste que des URLs hardcodées qui changent à chaque millésime).
"""

import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import requests

DEP_HAUTE_MARNE = "52"
GEO_API_BASE    = "https://geo.api.gouv.fr"
OUTPUT_DIR      = "data/population"

# IDs stables des datasets sur data.gouv.fr
# "Populations légales" — l'API renvoie les ressources et leurs URLs à jour
DATAGOUV_DATASET_POPLEG = "5359b4c5c751df7ef0a3c28d"


# ------------------------------------------------------------
# Recherche dynamique des fichiers INSEE sur data.gouv.fr
# ------------------------------------------------------------

def find_popleg_resource(year: int) -> str | None:
    """
    Interroge l'API data.gouv.fr pour trouver l'URL de téléchargement
    du fichier populations légales pour une année donnée.
    Cherche dans les ressources du dataset la plus récente correspondant à l'année.
    """
    api_url = f"https://www.data.gouv.fr/api/1/datasets/{DATAGOUV_DATASET_POPLEG}/"
    try:
        resp = requests.get(api_url, timeout=30)
        resp.raise_for_status()
        dataset = resp.json()
    except Exception as e:
        print(f"   ⚠️  Impossible d'interroger data.gouv.fr : {e}")
        return None

    resources = dataset.get("resources", [])
    year_str = str(year)

    # Chercher une ressource CSV/ZIP contenant l'année dans le titre ou l'URL
    candidates = []
    for res in resources:
        title = (res.get("title") or "").lower()
        url   = (res.get("url") or "").lower()
        fmt   = (res.get("format") or "").lower()
        if year_str in title or year_str in url:
            if fmt in ("csv", "zip") or url.endswith(".csv") or url.endswith(".zip"):
                candidates.append((res.get("last_modified", ""), res.get("url", "")))

    if not candidates:
        print(f"   ⚠️  Aucune ressource CSV/ZIP trouvée pour {year} dans le dataset popleg")
        return None

    # Prendre la plus récente
    candidates.sort(reverse=True)
    url = candidates[0][1]
    print(f"   → Ressource {year} trouvée : {url[:80]}...")
    return url


def fetch_popleg_from_url(url: str, year: int) -> dict:
    """Télécharge et parse un fichier populations légales INSEE (ZIP ou CSV)."""
    try:
        resp = requests.get(url, timeout=120, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"   ⚠️  Téléchargement {year} échoué : {e}")
        return {}

    content = resp.content

    # ZIP ou CSV direct ?
    if content[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            csv_files = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not csv_files:
                print(f"   ⚠️  Aucun CSV dans le ZIP {year}")
                return {}
            raw = z.read(csv_files[0])
        except Exception as e:
            print(f"   ⚠️  Erreur ZIP {year} : {e}")
            return {}
    else:
        raw = content

    # Décodage
    text = None
    for enc in ("utf-8-sig", "latin-1", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        print(f"   ⚠️  Encodage inconnu {year}")
        return {}

    # Parsing CSV
    try:
        first_line = text.split("\n")[0]
        delimiter  = ";" if first_line.count(";") >= first_line.count(",") else ","
        reader     = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows       = list(reader)
        if not rows:
            return {}

        keys = list(rows[0].keys())

        # Colonne code commune
        codgeo_col = next((k for k in keys if k.upper().strip() == "CODGEO"), None)

        # Colonne population municipale : essayer plusieurs noms possibles
        year2 = str(year)[-2:]
        pmun_candidates = [
            f"PMUN{year2}", f"PMUN{year}", f"P{year}_POP",
            "PMUN", "P_POP",
        ]
        pmun_col = next(
            (k for k in keys if k.upper().strip() in [c.upper() for c in pmun_candidates]),
            None
        )
        if pmun_col is None:
            pmun_col = next((k for k in keys if "PMUN" in k.upper()), None)

        if not codgeo_col or not pmun_col:
            print(f"   ⚠️  Colonnes introuvables pour {year}. Colonnes dispo : {keys[:10]}")
            return {}

        result = {}
        for row in rows:
            code = row.get(codgeo_col, "").strip()
            val  = row.get(pmun_col, "").strip()
            if code and val:
                try:
                    result[code] = int(float(val))
                except ValueError:
                    pass

        print(f"   → {len(result)} communes parsées (année {year}, colonne '{pmun_col}')")
        return result

    except Exception as e:
        print(f"   ⚠️  Erreur parsing {year} : {e}")
        return {}


def fetch_popleg_insee(year: int) -> dict:
    """Point d'entrée : trouve l'URL puis télécharge."""
    print(f"   Recherche populations légales {year} sur data.gouv.fr...")
    url = find_popleg_resource(year)
    if not url:
        return {}
    return fetch_popleg_from_url(url, year)


# ------------------------------------------------------------
# Géographies via geo.api.gouv.fr
# ------------------------------------------------------------

def fetch_communes_geojson(dep: str) -> dict:
    url    = f"{GEO_API_BASE}/communes"
    params = {
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson",
        "geometry": "contour",
    }
    print(f"   Récupération communes département {dep}...")
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_departements_geojson() -> dict:
    url    = f"{GEO_API_BASE}/departements"
    params = {"fields": "nom,code,codeRegion,population", "format": "geojson", "geometry": "contour"}
    print("   Récupération départements...")
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return {"type": "FeatureCollection", "features": data}
    return data


# ------------------------------------------------------------
# Enrichissement évolution
# ------------------------------------------------------------

def enrich_with_evolution(geojson, pop_old, pop_new, year_old, year_new):
    for feature in geojson.get("features", []):
        props = feature["properties"]
        code  = props.get("code", "")

        p_new = pop_new.get(code)
        p_old = pop_old.get(code)

        if p_new is not None:
            props["population"]        = p_new
            props["population_source"] = f"INSEE {year_new}"
        else:
            props["population_source"] = "geo.api.gouv.fr"

        pop_ref = p_new if p_new is not None else props.get("population")

        if p_old is not None and pop_ref is not None and p_old > 0:
            props[f"population_{year_old}"] = p_old
            props[f"population_{year_new}"] = pop_ref
            props["evolution_absolue"]       = pop_ref - p_old
            props["evolution_pct"]           = round((pop_ref - p_old) / p_old * 100, 2)
            props["evolution_periode"]       = f"{year_old}–{year_new}"
        else:
            props[f"population_{year_old}"] = None
            props[f"population_{year_new}"] = pop_ref
            props["evolution_absolue"]       = None
            props["evolution_pct"]           = None
            props["evolution_periode"]       = f"{year_old}–{year_new} (données manquantes)"

    return geojson


def compute_stats(geojson):
    features = geojson.get("features", [])
    pops  = [f["properties"].get("population")     for f in features if f["properties"].get("population")     is not None]
    evols = [f["properties"].get("evolution_pct")  for f in features if f["properties"].get("evolution_pct")  is not None]
    if not pops:
        return {}
    return {
        "nb_communes":          len(features),
        "population_totale":    sum(pops),
        "population_min":       min(pops),
        "population_max":       max(pops),
        "communes_avec_evolution": len(evols),
        "communes_en_croissance":  sum(1 for e in evols if e > 0),
        "communes_en_declin":      sum(1 for e in evols if e < 0),
        "communes_stables":        sum(1 for e in evols if e == 0),
    }


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    pop_2015 = fetch_popleg_insee(2015)
    pop_2021 = fetch_popleg_insee(2021)
    insee_ok = bool(pop_2015 and pop_2021)
    if not insee_ok:
        print(f"   ⚠️  Données INSEE partielles : 2015={'OK' if pop_2015 else 'ABSENT'}, 2021={'OK' if pop_2021 else 'ABSENT'}")

    critical_errors = []

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP_HAUTE_MARNE})")
    try:
        communes_hm = fetch_communes_geojson(DEP_HAUTE_MARNE)
        communes_hm = enrich_with_evolution(communes_hm, pop_2015, pop_2021, 2015, 2021)
        stats       = compute_stats(communes_hm)

        communes_hm["metadata"] = {
            "source": "geo.api.gouv.fr + INSEE populations légales (data.gouv.fr)",
            "license": "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp,
            "departement": DEP_HAUTE_MARNE,
            "insee_data_available": insee_ok,
            **stats,
        }

        path = os.path.join(OUTPUT_DIR, "haute-marne_communes.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(communes_hm, f, ensure_ascii=False, indent=2)

        print(f"✓  {path} ({stats.get('nb_communes','?')} communes)")
        print(f"   Population totale    : {stats.get('population_totale', '?'):,}".replace(",", " "))
        print(f"   Avec évolution       : {stats.get('communes_avec_evolution', '?')}")
        print(f"   Croissance/Déclin    : {stats.get('communes_en_croissance','?')} / {stats.get('communes_en_declin','?')}")

    except Exception as e:
        msg = f"Communes Haute-Marne : {e}"
        print(f"❌ {msg}", file=sys.stderr)
        critical_errors.append(msg)

    # --- Départements France ---
    print("\n📍 Départements France")
    try:
        deps     = fetch_departements_geojson()
        nb_deps  = len(deps.get("features", []))
        deps["metadata"] = {
            "source": "geo.api.gouv.fr",
            "license": "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp,
            "nb_departements": nb_deps,
        }
        path = os.path.join(OUTPUT_DIR, "france_departements.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        print(f"✓  {path} ({nb_deps} départements)")

    except Exception as e:
        msg = f"Départements France : {e}"
        print(f"❌ {msg}", file=sys.stderr)
        critical_errors.append(msg)

    # --- Metadata ---
    metadata = {
        "last_updated": timestamp,
        "sources": {"geometries": "geo.api.gouv.fr", "populations": "INSEE via data.gouv.fr"},
        "license": "Licence Ouverte 2.0",
        "insee_data_available": insee_ok,
        "critical_errors": critical_errors,
    }
    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {meta_path}")

    if critical_errors:
        print(f"\n❌ {len(critical_errors)} erreur(s) critique(s)", file=sys.stderr)
        sys.exit(1)

    if not insee_ok:
        print("\n⚠️  Evolution de population absente (INSEE indisponible ce run).")

    print("\n✅ Données population mises à jour.")


if __name__ == "__main__":
    main()
