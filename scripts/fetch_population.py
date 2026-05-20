#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne + France avec évolution inter-annuelle.

Stratégie populations légales :
  - Année "récente" : 2021 (priorité)
  - Année "ancienne" : essaie 2020, 2019, 2018, 2017 dans l'ordre
    et prend la PREMIÈRE qui télécharge et parse correctement.
  - Si une seule année est disponible : population sans évolution.
  - Recherche via l'API data.gouv.fr (titre + organisation INSEE).
"""

import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import requests

DEP_HAUTE_MARNE  = "52"
GEO_API_BASE     = "https://geo.api.gouv.fr"
OUTPUT_DIR       = "data/population"
INSEE_ORG_ID     = "5369992aa3a729239d204c47"

# Années cibles : on essaie dans l'ordre, on prend la première qui marche
YEAR_RECENT = 2021
YEARS_OLD   = [2020, 2019, 2018, 2017]   # fallback cascade


# ------------------------------------------------------------
# Recherche sur data.gouv.fr (API search par titre + organisation)
# ------------------------------------------------------------

def search_popleg_dataset(year: int) -> str | None:
    """
    Cherche le dataset 'populations légales en {year}' via l'API search
    data.gouv.fr filtrée sur l'organisation INSEE.
    Retourne l'URL du premier fichier CSV/ZIP trouvé, ou None.
    """
    print(f"   Recherche populations légales {year} sur data.gouv.fr...")
    try:
        resp = requests.get(
            "https://www.data.gouv.fr/api/1/datasets/",
            params={
                "q":            f"populations légales {year}",
                "organization": INSEE_ORG_ID,
                "page_size":    5,
            },
            timeout=20,
        )
        resp.raise_for_status()
        datasets = resp.json().get("data", [])
    except Exception as e:
        print(f"   ⚠️  Recherche data.gouv.fr échouée ({year}) : {e}")
        return None

    if not datasets:
        print(f"   ⚠️  Aucun dataset trouvé pour {year}")
        return None

    year_str = str(year)
    for ds in datasets:
        title = ds.get("title", "").lower()
        # S'assurer que c'est bien l'année demandée
        if year_str not in title and year_str not in ds.get("slug", ""):
            continue

        for res in ds.get("resources", []):
            url   = (res.get("url")    or "").lower()
            fmt   = (res.get("format") or "").lower()
            title_r = (res.get("title") or "").lower()
            is_data = fmt in ("csv", "zip") or url.endswith((".csv", ".zip"))
            is_doc  = any(w in title_r for w in ("notice", "readme", "doc", "dictionnaire"))
            if is_data and not is_doc:
                real_url = res["url"]
                print(f"   → Trouvé : {ds.get('title','?')[:60]}")
                print(f"     Ressource : ...{real_url[-55:]}")
                return real_url

    # Fallback : essai direct du slug canonique
    slug = f"populations-legales-en-{year}"
    try:
        r2 = requests.get(
            f"https://www.data.gouv.fr/api/1/datasets/{slug}/",
            timeout=15,
        )
        if r2.status_code == 200:
            for res in r2.json().get("resources", []):
                url   = (res.get("url")    or "").lower()
                fmt   = (res.get("format") or "").lower()
                title_r = (res.get("title") or "").lower()
                if (fmt in ("csv", "zip") or url.endswith((".csv", ".zip"))) \
                        and not any(w in title_r for w in ("notice", "readme", "doc")):
                    print(f"   → Via slug direct : ...{res['url'][-55:]}")
                    return res["url"]
    except Exception:
        pass

    print(f"   ⚠️  Aucune ressource CSV/ZIP utilisable pour {year}")
    return None


# ------------------------------------------------------------
# Téléchargement + parsing du fichier INSEE
# ------------------------------------------------------------

def download_and_parse(url: str, year: int) -> dict:
    """Télécharge le fichier et retourne {code_commune: pop_municipale}."""
    try:
        resp = requests.get(url, timeout=120, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"   ⚠️  Téléchargement échoué : {e}")
        return {}

    content = resp.content

    # ZIP ou CSV direct ?
    raw = None
    if content[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            csv_files = sorted(
                [n for n in z.namelist()
                 if n.lower().endswith((".csv", ".txt"))
                 and not any(w in n.lower() for w in ("notice", "readme", "doc"))],
                key=lambda n: z.getinfo(n).file_size,
                reverse=True,
            )
            if not csv_files:
                csv_files = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not csv_files:
                print("   ⚠️  Aucun CSV dans le ZIP")
                return {}
            print(f"   → Fichier ZIP sélectionné : {csv_files[0]}")
            raw = z.read(csv_files[0])
        except Exception as e:
            print(f"   ⚠️  Erreur ZIP : {e}")
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
        print("   ⚠️  Encodage inconnu")
        return {}

    # Parsing CSV
    try:
        first_line = text.split("\n")[0]
        delimiter  = ";" if first_line.count(";") >= first_line.count(",") else ","
        reader     = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows       = list(reader)
        if not rows:
            print("   ⚠️  CSV vide après parsing")
            return {}

        keys   = list(rows[0].keys())
        year2  = str(year)[-2:]

        # Colonne code commune
        codgeo_col = next(
            (k for k in keys if k.upper().strip() in ("CODGEO", "COM", "CODE_COM", "CODECOM")),
            None,
        )

        # Colonne population municipale — noms possibles selon millésime
        pmun_tests = [
            f"PMUN{year2}", f"PMUN{year}",
            f"P{year2}_POP", f"P{year}_POP",
            "PMUN",
        ]
        pmun_col = next(
            (k for k in keys if k.upper().strip() in [t.upper() for t in pmun_tests]),
            None,
        )
        if pmun_col is None:
            pmun_col = next((k for k in keys if "PMUN" in k.upper()), None)

        if not codgeo_col:
            print(f"   ⚠️  Colonne code commune introuvable. Colonnes dispo : {keys[:10]}")
            return {}
        if not pmun_col:
            print(f"   ⚠️  Colonne PMUN introuvable. Colonnes dispo : {keys[:10]}")
            return {}

        print(f"   → Colonnes : CODGEO='{codgeo_col}' | PMUN='{pmun_col}'")

        result = {}
        for row in rows:
            code = row.get(codgeo_col, "").strip()
            val  = row.get(pmun_col,  "").strip()
            if code and val:
                try:
                    result[code] = int(float(val))
                except ValueError:
                    pass

        print(f"   → {len(result):,} communes chargées (année {year})")
        return result

    except Exception as e:
        print(f"   ⚠️  Erreur parsing : {e}")
        return {}


def fetch_popleg(year: int) -> dict:
    url = search_popleg_dataset(year)
    return download_and_parse(url, year) if url else {}


def fetch_popleg_with_fallback(years: list[int]) -> tuple[int | None, dict]:
    """Essaie les années dans l'ordre et retourne la première qui marche."""
    for year in years:
        data = fetch_popleg(year)
        if data:
            return year, data
        print(f"   → {year} sans données utilisables, essai suivant...")
    return None, {}


# ------------------------------------------------------------
# Géographies via geo.api.gouv.fr
# ------------------------------------------------------------

def fetch_communes_geojson(dep: str) -> dict:
    params = {
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson",
        "geometry": "contour",
    }
    resp = requests.get(f"{GEO_API_BASE}/communes", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_departements_geojson() -> dict:
    params = {"fields": "nom,code,codeRegion,population", "format": "geojson", "geometry": "contour"}
    resp = requests.get(f"{GEO_API_BASE}/departements", params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return {"type": "FeatureCollection", "features": data} if isinstance(data, list) else data


# ------------------------------------------------------------
# Enrichissement
# ------------------------------------------------------------

def enrich_with_evolution(geojson, pop_old, pop_new, year_old, year_new):
    nb_ok = 0
    for feat in geojson.get("features", []):
        props = feat["properties"]
        code  = props.get("code", "")
        p_new = pop_new.get(code) if pop_new else None
        p_old = pop_old.get(code) if pop_old else None

        if p_new is not None:
            props["population"]        = p_new
            props["population_source"] = f"INSEE {year_new}"
        else:
            props["population_source"] = "geo.api.gouv.fr"

        pop_ref = p_new if p_new is not None else props.get("population")
        props[f"population_{year_new}"] = pop_ref

        if year_old and p_old is not None and pop_ref is not None and p_old > 0:
            props[f"population_{year_old}"] = p_old
            props["evolution_absolue"]       = pop_ref - p_old
            props["evolution_pct"]           = round((pop_ref - p_old) / p_old * 100, 2)
            props["evolution_periode"]       = f"{year_old}–{year_new}"
            nb_ok += 1
        else:
            props[f"population_{year_old if year_old else 'ancienne'}"] = None
            props["evolution_absolue"]  = None
            props["evolution_pct"]      = None
            props["evolution_periode"]  = f"{'?' if not year_old else year_old}–{year_new}"

    total = len(geojson.get("features", []))
    print(f"   → Évolution renseignée : {nb_ok}/{total} communes")
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

    # --- Chargement populations ---
    print("📊 Chargement populations légales INSEE\n")

    print(f"[Année récente : {YEAR_RECENT}]")
    pop_new = fetch_popleg(YEAR_RECENT)

    print(f"\n[Année ancienne : essai cascade {YEARS_OLD}]")
    year_old, pop_old = fetch_popleg_with_fallback(YEARS_OLD)

    print(f"\nRésumé chargement :")
    print(f"  {YEAR_RECENT} : {len(pop_new):,} communes" if pop_new else f"  {YEAR_RECENT} : ÉCHEC")
    print(f"  {year_old}   : {len(pop_old):,} communes" if pop_old else f"  ancienne  : ÉCHEC")

    critical_errors = []

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP_HAUTE_MARNE})")
    try:
        communes_hm = fetch_communes_geojson(DEP_HAUTE_MARNE)
        communes_hm = enrich_with_evolution(communes_hm, pop_old, pop_new, year_old, YEAR_RECENT)
        stats       = compute_stats(communes_hm)

        communes_hm["metadata"] = {
            "source":              "geo.api.gouv.fr + INSEE populations légales (data.gouv.fr)",
            "license":             "Licence Ouverte 2.0 (Etalab)",
            "last_updated":        timestamp,
            "departement":         DEP_HAUTE_MARNE,
            "annee_recente":       YEAR_RECENT,
            "annee_ancienne":      year_old,
            "insee_recent_ok":     bool(pop_new),
            "insee_ancien_ok":     bool(pop_old),
            **stats,
        }

        path = os.path.join(OUTPUT_DIR, "haute-marne_communes.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(communes_hm, f, ensure_ascii=False, indent=2)

        print(f"✓  {path} ({stats.get('nb_communes','?')} communes)")
        pop_tot = stats.get("population_totale", 0)
        print(f"   Population totale   : {pop_tot:,}".replace(",", " "))
        print(f"   Croissance / Déclin : {stats.get('communes_en_croissance','?')} / {stats.get('communes_en_declin','?')}")

    except Exception as e:
        critical_errors.append(f"Communes Haute-Marne : {e}")
        print(f"❌ {critical_errors[-1]}", file=sys.stderr)

    # --- Départements France ---
    print("\n📍 Départements France")
    try:
        deps    = fetch_departements_geojson()
        nb_deps = len(deps.get("features", []))
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
        critical_errors.append(f"Départements France : {e}")
        print(f"❌ {critical_errors[-1]}", file=sys.stderr)

    # --- Metadata ---
    with open(os.path.join(OUTPUT_DIR, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "last_updated":      timestamp,
            "annee_recente":     YEAR_RECENT,
            "annee_ancienne":    year_old,
            "insee_recent_ok":   bool(pop_new),
            "insee_ancien_ok":   bool(pop_old),
            "sources":           {"geometries": "geo.api.gouv.fr", "populations": "INSEE via data.gouv.fr"},
            "critical_errors":   critical_errors,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✓  {OUTPUT_DIR}/metadata.json")

    if critical_errors:
        sys.exit(1)
    if not pop_old:
        print("\n⚠️  Année ancienne indisponible — champ évolution vide ce run.")
    print("\n✅ Données population mises à jour.")


if __name__ == "__main__":
    main()
