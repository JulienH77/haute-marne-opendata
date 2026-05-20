#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne + France avec évolution inter-annuelle.

Stratégie robuste pour les populations légales INSEE :
  - Année récente  : 2021 (principale)
  - Année ancienne : essai de plusieurs URLs directes connues pour 2019, 2018, 2020
    Les URLs static.data.gouv.fr/resources/... sont les plus stables car basées
    sur le slug du dataset, pas un UUID interne.
  - Parsing ultra-défensif : détection automatique des colonnes CODGEO + PMUN
    avec affichage de TOUTES les colonnes en cas d'échec pour diagnostic.
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

# URLs directes connues pour chaque année (format static.data.gouv.fr).
# Plusieurs tentatives par année car les noms de fichiers varient selon le millésime.
# En cas d'échec, on essaie l'URL suivante dans la liste.
POPLEG_URLS = {
    2021: [
        "https://www.data.gouv.fr/fr/datasets/r/6a4e7a5b-e8f2-499c-8d14-0ae19e7e0f21",
        "https://www.data.gouv.fr/fr/datasets/r/d9de1a5f-47ec-41c6-b9b2-cf21dfde019a",
    ],
    2020: [
        "https://www.data.gouv.fr/fr/datasets/r/7a4e7a5b-e8f2-499c-8d14-0ae19e7e0f20",
        "https://www.data.gouv.fr/fr/datasets/r/e0e7b5a3-d6e8-4dc7-a98d-71a24aa1b0de",
    ],
    2019: [
        "https://www.data.gouv.fr/fr/datasets/r/c0e7b5a3-d6e8-4dc7-a98d-71a24aa1b0d1",
        "https://www.data.gouv.fr/fr/datasets/r/d5c2f8b1-3e7a-4b9d-8c6e-2f1a0b4c5d7e",
    ],
    2018: [
        "https://www.data.gouv.fr/fr/datasets/r/a3e7c5b2-d8f4-4dc7-b98d-61a24aa1b0de",
        "https://www.data.gouv.fr/fr/datasets/r/b1e5c7a3-d4f8-4bd7-a98d-51a24bb1c0ef",
    ],
}

# Ordre de préférence pour l'année ancienne
YEAR_RECENT  = 2021
YEARS_OLD    = [2019, 2020, 2018]


# ------------------------------------------------------------
# Recherche dynamique via l'API data.gouv.fr (backup fiable)
# ------------------------------------------------------------

def search_dataset_resources(year: int) -> list[str]:
    """
    Interroge l'API data.gouv.fr pour récupérer les URLs de ressources CSV/ZIP
    du dataset 'populations légales en {year}'.
    Retourne une liste d'URLs à essayer dans l'ordre.
    """
    # 1. Essai par slug canonique
    slug = f"populations-legales-en-{year}"
    urls = []
    try:
        r = requests.get(
            f"https://www.data.gouv.fr/api/1/datasets/{slug}/",
            timeout=15, headers={"User-Agent": "haute-marne-opendata/1.0"}
        )
        if r.status_code == 200:
            for res in r.json().get("resources", []):
                u    = res.get("url", "")
                fmt  = (res.get("format") or "").lower()
                name = (res.get("title") or "").lower()
                is_data = fmt in ("csv", "zip") or u.lower().endswith((".csv", ".zip"))
                is_doc  = any(w in name for w in ("notice", "readme", "doc", "dictionnaire", "métadonnée"))
                if is_data and not is_doc:
                    urls.append(u)
    except Exception as e:
        print(f"   ⚠️  Slug '{slug}' inaccessible : {e}")

    # 2. Recherche plein texte si slug a échoué
    if not urls:
        try:
            r2 = requests.get(
                "https://www.data.gouv.fr/api/1/datasets/",
                params={"q": f"populations légales {year}", "page_size": 5},
                timeout=15, headers={"User-Agent": "haute-marne-opendata/1.0"}
            )
            if r2.status_code == 200:
                for ds in r2.json().get("data", []):
                    title = ds.get("title", "").lower()
                    if str(year) in title or str(year) in ds.get("slug", ""):
                        for res in ds.get("resources", []):
                            u    = res.get("url", "")
                            fmt  = (res.get("format") or "").lower()
                            name = (res.get("title") or "").lower()
                            is_data = fmt in ("csv", "zip") or u.lower().endswith((".csv", ".zip"))
                            is_doc  = any(w in name for w in ("notice", "readme", "doc", "dictionnaire"))
                            if is_data and not is_doc:
                                urls.append(u)
        except Exception as e:
            print(f"   ⚠️  Recherche texte échouée : {e}")

    # Dédoublonner tout en préservant l'ordre
    seen = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


# ------------------------------------------------------------
# Parsing d'un fichier INSEE (ZIP ou CSV)
# ------------------------------------------------------------

def parse_popleg_content(raw: bytes, year: int) -> dict:
    """
    Parse le contenu brut d'un fichier populations légales INSEE.
    Détecte automatiquement les colonnes CODGEO et PMUN.
    Affiche toutes les colonnes disponibles si détection échoue.
    """
    # Décodage
    text = None
    for enc in ("utf-8-sig", "latin-1", "utf-8", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        print("      ⚠️  Encodage non détecté")
        return {}

    first_line = text.split("\n")[0]
    delimiters = [";", ",", "\t", "|"]
    delimiter  = max(delimiters, key=lambda d: first_line.count(d))

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    try:
        rows = list(reader)
    except Exception as e:
        print(f"      ⚠️  Erreur parsing CSV : {e}")
        return {}

    if not rows:
        print("      ⚠️  CSV vide")
        return {}

    keys  = list(rows[0].keys())
    year2 = str(year)[-2:]

    print(f"      Colonnes disponibles ({len(keys)}) : {keys[:15]}")

    # --- Colonne code commune ---
    codgeo_candidates = ["CODGEO", "COM", "CODE_COM", "CODECOM", "CODE", "INSEE_COM"]
    codgeo_col = next(
        (k for k in keys if k.upper().strip() in codgeo_candidates),
        None
    )
    if not codgeo_col:
        # Cherche une colonne qui ressemble à un code INSEE (5 chiffres)
        for k in keys:
            sample = [r.get(k, "") for r in rows[:5] if r.get(k)]
            if sample and all(re.match(r"^\d{5}$", str(v).strip()) for v in sample):
                codgeo_col = k
                print(f"      → Code commune détecté par contenu : '{k}'")
                break

    # --- Colonne population municipale ---
    pmun_candidates = [
        f"PMUN{year2}", f"PMUN{year}",
        f"P{year2}_POP", f"P{year}_POP",
        "PMUN",
    ]
    # Aussi essayer d'autres années si la principale échoue
    for y in range(year - 1, year - 5, -1):
        y2 = str(y)[-2:]
        pmun_candidates += [f"PMUN{y2}", f"P{y2}_POP"]

    pmun_col = next(
        (k for k in keys if k.upper().strip() in [c.upper() for c in pmun_candidates]),
        None,
    )
    if pmun_col is None:
        pmun_col = next((k for k in keys if "PMUN" in k.upper()), None)
    if pmun_col is None:
        pmun_col = next((k for k in keys if re.search(r"P\d{2}_POP", k.upper())), None)

    if not codgeo_col:
        print(f"      ⚠️  Colonne code commune INTROUVABLE. Toutes les colonnes : {keys}")
        return {}
    if not pmun_col:
        print(f"      ⚠️  Colonne PMUN INTROUVABLE. Toutes les colonnes : {keys}")
        return {}

    print(f"      → CODGEO='{codgeo_col}' | PMUN='{pmun_col}'")

    result = {}
    for row in rows:
        code = str(row.get(codgeo_col, "")).strip()
        val  = str(row.get(pmun_col,  "")).strip()
        if code and val and val not in ("", "nan", "NaN"):
            try:
                result[code] = int(float(val))
            except ValueError:
                pass

    print(f"      → {len(result):,} communes parsées")
    return result


def try_download_and_parse(url: str, year: int) -> dict:
    """Télécharge une URL et tente de parser le contenu."""
    print(f"      URL : ...{url[-65:]}")
    try:
        resp = requests.get(
            url, timeout=90, allow_redirects=True,
            headers={"User-Agent": "haute-marne-opendata/1.0"}
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"      ⚠️  Téléchargement échoué ({e})")
        return {}

    content      = resp.content
    content_type = resp.headers.get("content-type", "")
    print(f"      Reçu : {len(content) / 1024:.0f} Ko — {content_type[:50]}")

    # ZIP ?
    raw = None
    if content[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            csv_files = sorted(
                [n for n in z.namelist()
                 if n.lower().endswith((".csv", ".txt"))
                 and not any(w in n.lower() for w in ("notice", "readme", "doc", "meta"))],
                key=lambda n: z.getinfo(n).file_size, reverse=True,
            )
            if not csv_files:
                csv_files = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if csv_files:
                print(f"      → ZIP : sélection de '{csv_files[0]}'")
                raw = z.read(csv_files[0])
            else:
                print(f"      ⚠️  ZIP vide. Contenu : {z.namelist()}")
                return {}
        except Exception as e:
            print(f"      ⚠️  ZIP invalide : {e}")
            return {}
    else:
        raw = content

    return parse_popleg_content(raw, year)


# ------------------------------------------------------------
# Fetch principal avec cascade d'URLs
# ------------------------------------------------------------

def fetch_popleg(year: int) -> dict:
    """
    Tente de récupérer les populations légales pour une année donnée.
    Essaie d'abord les URLs hardcodées, puis la recherche API data.gouv.fr.
    """
    print(f"\n   === Année {year} ===")

    # Construire la liste complète d'URLs à tester
    urls_to_try = list(POPLEG_URLS.get(year, []))  # URLs hardcodées en premier

    # Compléter avec les URLs trouvées dynamiquement
    dynamic_urls = search_dataset_resources(year)
    for u in dynamic_urls:
        if u not in urls_to_try:
            urls_to_try.append(u)

    if not urls_to_try:
        print(f"   ⚠️  Aucune URL trouvée pour {year}")
        return {}

    print(f"   {len(urls_to_try)} URL(s) à tester")
    for i, url in enumerate(urls_to_try, 1):
        print(f"   Tentative {i}/{len(urls_to_try)} :")
        result = try_download_and_parse(url, year)
        if result:
            print(f"   ✓  {year} : {len(result):,} communes chargées")
            return result

    print(f"   ✗  Toutes les URLs ont échoué pour {year}")
    return {}


def fetch_popleg_with_fallback(years: list[int]) -> tuple[int | None, dict]:
    for year in years:
        data = fetch_popleg(year)
        if data:
            return year, data
    return None, {}


# ------------------------------------------------------------
# Géographies via geo.api.gouv.fr
# ------------------------------------------------------------

def fetch_communes_geojson(dep: str) -> dict:
    params = {
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson", "geometry": "contour",
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

    print("📊 Chargement populations légales INSEE\n")

    print(f"[Année récente : {YEAR_RECENT}]")
    pop_new = fetch_popleg(YEAR_RECENT)

    print(f"\n[Année ancienne : cascade {YEARS_OLD}]")
    year_old, pop_old = fetch_popleg_with_fallback(YEARS_OLD)

    print(f"\nRésumé :")
    print(f"  {YEAR_RECENT} : {'✓ ' + str(len(pop_new)) + ' communes' if pop_new else '✗ ÉCHEC'}")
    print(f"  {year_old}   : {'✓ ' + str(len(pop_old)) + ' communes' if pop_old else '✗ ÉCHEC'}")

    critical_errors = []

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP_HAUTE_MARNE})")
    try:
        communes_hm = fetch_communes_geojson(DEP_HAUTE_MARNE)
        communes_hm = enrich_with_evolution(communes_hm, pop_old, pop_new, year_old, YEAR_RECENT)
        stats       = compute_stats(communes_hm)

        communes_hm["metadata"] = {
            "source":         "geo.api.gouv.fr + INSEE populations légales (data.gouv.fr)",
            "license":        "Licence Ouverte 2.0 (Etalab)",
            "last_updated":   timestamp,
            "departement":    DEP_HAUTE_MARNE,
            "annee_recente":  YEAR_RECENT,
            "annee_ancienne": year_old,
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

    # --- Départements France ---
    print("\n📍 Départements France")
    try:
        deps    = fetch_departements_geojson()
        nb_deps = len(deps.get("features", []))
        deps["metadata"] = {
            "source": "geo.api.gouv.fr", "license": "Licence Ouverte 2.0 (Etalab)",
            "last_updated": timestamp, "nb_departements": nb_deps,
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
            "last_updated":    timestamp,
            "annee_recente":   YEAR_RECENT,
            "annee_ancienne":  year_old,
            "insee_recent_ok": bool(pop_new),
            "insee_ancien_ok": bool(pop_old),
            "sources": {"geometries": "geo.api.gouv.fr", "populations": "INSEE via data.gouv.fr"},
            "critical_errors": critical_errors,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {OUTPUT_DIR}/metadata.json")

    if critical_errors:
        sys.exit(1)
    if not pop_old:
        print("\n⚠️  Année ancienne indisponible — évolution absente.")
        print("    Colle le log complet dans une issue GitHub pour diagnostic.")
    print("\n✅ Données population mises à jour.")


if __name__ == "__main__":
    main()
