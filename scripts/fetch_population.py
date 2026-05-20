#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne et France, avec évolution 2015→2021.

Source populations légales : data.gouv.fr (slugs stables par année).
Les slugs "populations-legales-en-YYYY" sont maintenus par l'INSEE
et ne changent pas d'une publication à l'autre.
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

# Slugs stables data.gouv.fr — correspondent aux pages :
# https://www.data.gouv.fr/fr/datasets/populations-legales-en-2021/
# https://www.data.gouv.fr/fr/datasets/populations-legales-en-2015/
POPLEG_SLUGS = {
    2015: "populations-legales-en-2015",
    2021: "populations-legales-en-2021",
}


# ------------------------------------------------------------
# Recherche de l'URL de téléchargement via le slug data.gouv.fr
# ------------------------------------------------------------

def find_csv_url_by_slug(year: int) -> str | None:
    """
    Récupère l'URL du fichier CSV/ZIP du dataset via son slug data.gouv.fr.
    Trie les ressources par date de modification pour prendre la plus récente.
    """
    slug = POPLEG_SLUGS.get(year)
    if not slug:
        return None

    api_url = f"https://www.data.gouv.fr/api/1/datasets/{slug}/"
    print(f"   Recherche dataset '{slug}'...")
    try:
        resp = requests.get(api_url, timeout=30)
        resp.raise_for_status()
        dataset = resp.json()
    except Exception as e:
        print(f"   ⚠️  data.gouv.fr inaccessible pour {year} : {e}")
        return None

    resources = dataset.get("resources", [])
    if not resources:
        print(f"   ⚠️  Aucune ressource dans le dataset {year}")
        return None

    # Filtrer : fichiers CSV ou ZIP uniquement
    candidates = []
    for res in resources:
        url    = (res.get("url") or "").lower()
        fmt    = (res.get("format") or "").lower()
        title  = (res.get("title") or "").lower()
        mdate  = res.get("last_modified") or res.get("created_at") or ""

        is_csv = fmt in ("csv", "zip") or url.endswith(".csv") or url.endswith(".zip")
        # Exclure les fichiers de métadonnées ou documentation
        is_doc = any(w in title for w in ("notice", "readme", "doc", "description", "dictionnaire"))

        if is_csv and not is_doc:
            candidates.append((mdate, res.get("url", "")))

    if not candidates:
        print(f"   ⚠️  Aucun CSV/ZIP utile dans le dataset {year}")
        print(f"   Ressources disponibles : {[r.get('title','?') for r in resources[:5]]}")
        return None

    # Prendre la ressource la plus récente
    candidates.sort(reverse=True)
    chosen_url = candidates[0][1]
    print(f"   → {len(candidates)} candidat(s), URL retenue : ...{chosen_url[-60:]}")
    return chosen_url


# ------------------------------------------------------------
# Téléchargement + parsing du CSV INSEE
# ------------------------------------------------------------

def fetch_popleg(year: int) -> dict:
    """
    Télécharge le fichier populations légales et retourne
    un dict {code_commune: population_municipale}.
    Non bloquant en cas d'échec.
    """
    url = find_csv_url_by_slug(year)
    if not url:
        return {}

    print(f"   Téléchargement {year}...")
    try:
        resp = requests.get(url, timeout=120, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"   ⚠️  Téléchargement échoué ({year}) : {e}")
        return {}

    content = resp.content

    # ZIP ou CSV direct ?
    raw = None
    if content[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            # Chercher le fichier de données principal (pas les notices)
            csv_files = [
                n for n in z.namelist()
                if n.lower().endswith((".csv", ".txt"))
                and not any(w in n.lower() for w in ("notice", "readme", "doc"))
            ]
            if not csv_files:
                csv_files = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not csv_files:
                print(f"   ⚠️  Aucun CSV dans le ZIP ({year})")
                return {}
            # Prendre le plus gros fichier (le fichier de données, pas la notice)
            csv_files.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
            chosen = csv_files[0]
            print(f"   → Fichier dans ZIP : {chosen}")
            raw = z.read(chosen)
        except Exception as e:
            print(f"   ⚠️  Erreur ZIP ({year}) : {e}")
            return {}
    else:
        raw = content

    # Décodage
    text = None
    for enc in ("utf-8-sig", "latin-1", "utf-8", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        print(f"   ⚠️  Encodage inconnu ({year})")
        return {}

    # Parsing CSV
    try:
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            return {}

        first_line = lines[0]
        delimiter  = ";" if first_line.count(";") >= first_line.count(",") else ","
        reader     = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows       = list(reader)

        if not rows:
            print(f"   ⚠️  CSV vide ({year})")
            return {}

        keys   = list(rows[0].keys())
        year2  = str(year)[-2:]

        # --- Colonne code géo ---
        codgeo_col = next(
            (k for k in keys if k.upper().strip() in ("CODGEO", "COM", "CODE_COM", "CODECOM")),
            None
        )

        # --- Colonne population municipale ---
        # Les noms varient : PMUN21, PMUN15, PMUN2021, P21_POP, P15_POP...
        pmun_candidates = [
            f"PMUN{year2}",
            f"PMUN{year}",
            f"P{year2}_POP",
            f"P{year}_POP",
            "PMUN",
        ]
        pmun_col = next(
            (k for k in keys if k.upper().strip() in [c.upper() for c in pmun_candidates]),
            None
        )
        if pmun_col is None:
            # Fallback : première colonne contenant PMUN
            pmun_col = next((k for k in keys if "PMUN" in k.upper()), None)

        if not codgeo_col:
            print(f"   ⚠️  Colonne CODGEO introuvable ({year}). Colonnes : {keys[:12]}")
            return {}
        if not pmun_col:
            print(f"   ⚠️  Colonne PMUN introuvable ({year}). Colonnes : {keys[:12]}")
            return {}

        print(f"   → Colonnes utilisées : CODGEO='{codgeo_col}', PMUN='{pmun_col}'")

        result = {}
        for row in rows:
            code = row.get(codgeo_col, "").strip()
            val  = row.get(pmun_col,  "").strip()
            if code and val:
                try:
                    result[code] = int(float(val))
                except ValueError:
                    pass

        print(f"   → {len(result)} communes ({year})")
        return result

    except Exception as e:
        print(f"   ⚠️  Erreur parsing ({year}) : {e}")
        return {}


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
    print(f"   Communes département {dep}...")
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_departements_geojson() -> dict:
    url    = f"{GEO_API_BASE}/departements"
    params = {"fields": "nom,code,codeRegion,population", "format": "geojson", "geometry": "contour"}
    print("   Départements...")
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return {"type": "FeatureCollection", "features": data} if isinstance(data, list) else data


# ------------------------------------------------------------
# Enrichissement évolution
# ------------------------------------------------------------

def enrich_with_evolution(geojson, pop_old, pop_new, year_old, year_new):
    nb_with_evolution = 0
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
            nb_with_evolution += 1
        else:
            props[f"population_{year_old}"] = None
            props[f"population_{year_new}"] = pop_ref
            props["evolution_absolue"]       = None
            props["evolution_pct"]           = None
            props["evolution_periode"]       = f"{year_old}–{year_new}"

    print(f"   → {nb_with_evolution}/{len(geojson.get('features', []))} communes avec évolution renseignée")
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


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("📊 Chargement populations légales INSEE via data.gouv.fr")
    pop_2015 = fetch_popleg(2015)
    pop_2021 = fetch_popleg(2021)
    insee_ok = bool(pop_2015 and pop_2021)

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
        pop_tot = stats.get('population_totale', 0)
        print(f"   Population totale    : {pop_tot:,}".replace(",", " "))
        print(f"   Croissance / Déclin  : {stats.get('communes_en_croissance','?')} / {stats.get('communes_en_declin','?')}")

    except Exception as e:
        msg = f"Communes Haute-Marne : {e}"
        print(f"❌ {msg}", file=sys.stderr)
        critical_errors.append(msg)

    # --- Départements France ---
    print("\n📍 Départements France")
    try:
        deps    = fetch_departements_geojson()
        nb_deps = len(deps.get("features", []))
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
    meta = {
        "last_updated": timestamp,
        "sources": {"geometries": "geo.api.gouv.fr", "populations": "INSEE via data.gouv.fr"},
        "license": "Licence Ouverte 2.0",
        "insee_data_available": insee_ok,
        "pop_2015_nb_communes": len(pop_2015),
        "pop_2021_nb_communes": len(pop_2021),
        "critical_errors": critical_errors,
    }
    with open(os.path.join(OUTPUT_DIR, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {OUTPUT_DIR}/metadata.json")

    if critical_errors:
        sys.exit(1)

    if not insee_ok:
        print("\n⚠️  Données INSEE partiellement indisponibles — évolution absente ce run.")

    print("\n✅ Données population mises à jour.")


if __name__ == "__main__":
    main()
