#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne + France avec séries historiques complètes.

Sources INSEE :
  - Fichier "base-cc-evol-struct-pop-2021" → 2021, 2016, 2011, 2006
  - Fichier "séries historiques" INSEE    → 1999, 1990, 1982, 1975, 1968

Ces deux fichiers couvrent TOUTES les années demandées :
  2023¹, 2021, 2016, 2011, 2006, 1999, 1990, 1982, 1975, 1968
  ¹ 2023 si disponible, sinon 2021.

Stratégie robuste :
  - Plusieurs URLs tentées pour chaque fichier
  - Recherche API data.gouv.fr si URLs directes échouent
  - Parsing automatique de toutes les colonnes PMUN/POP disponibles
  - Logging exhaustif des colonnes pour diagnostic en cas d'échec
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

DEP   = "52"
GEO   = "https://geo.api.gouv.fr"
OUT   = "data/population"

# Années souhaitées (récentes → anciennes)
TARGET_YEARS = [2023, 2022, 2021, 2016, 2011, 2006, 1999, 1990, 1982, 1975, 1968]

# ---------------------------------------------------------------
# FICHIER 1 : base-cc-evol-struct-pop — contient 4 années récentes
# colonnes : PMUN21, PMUN16, PMUN11, PMUN06
# ---------------------------------------------------------------
RECENT_URLS = [
    # 2023 (si publié)
    "https://www.data.gouv.fr/fr/datasets/r/c6e06e69-37fe-4ea4-88d1-27e4d4891f5b",
    # 2021 — plusieurs mirrors connus
    "https://www.data.gouv.fr/fr/datasets/r/d9de1a5f-47ec-41c6-b9b2-cf21dfde019a",
    "https://www.data.gouv.fr/fr/datasets/r/6a4e7a5b-e8f2-499c-8d14-0ae19e7e0f21",
    "https://www.data.gouv.fr/fr/datasets/r/07460e6e-b62e-4ad5-b916-e6e16b27e6a5",
]
RECENT_SLUGS = [
    "populations-legales-en-2023",
    "populations-legales-en-2022",
    "populations-legales-en-2021",
]

# ---------------------------------------------------------------
# FICHIER 2 : séries historiques INSEE — contient 1968→1999
# colonnes : PMUN99, PMUN90, PMUN82, PMUN75, PMUN68 (ou nommage différent)
# ---------------------------------------------------------------
HISTO_URLS = [
    # Dataset "Séries historiques des résultats du recensement"
    "https://www.data.gouv.fr/fr/datasets/r/b4119ee9-4f72-4e5c-8a51-0c0a15ca8408",
    "https://www.data.gouv.fr/fr/datasets/r/f7cb9b43-e0d8-4918-9b2c-2b4b4b5a1c34",
    # Fichier "Évolution et structure 2009" qui contient souvent 1999, 1990...
    "https://www.data.gouv.fr/fr/datasets/r/a3e7c5b2-d8f4-4dc7-b98d-61a24aa1b0de",
]
HISTO_SLUGS = [
    "series-historiques-des-resultats-du-recensement-de-la-population",
    "populations-legales-des-communes-depuis-1968",
    "populations-legales-en-2009",  # contient PMUN99, PMUN90, PMUN82...
    "populations-legales-en-2006",  # contient PMUN99, PMUN90...
]


# ---------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------

def get_raw(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=60, allow_redirects=True,
                         headers={"User-Agent": "haute-marne-opendata/1.0"})
        r.raise_for_status()
    except Exception as e:
        print(f"      ✗ {e}")
        return None

    content = r.content
    size_kb = len(content) // 1024
    if size_kb < 10:
        print(f"      ✗ Fichier trop petit ({size_kb} Ko) — sûrement une page HTML")
        return None

    if content[:2] == b"PK":          # ZIP
        try:
            z        = zipfile.ZipFile(io.BytesIO(content))
            # Sélectionner le plus grand CSV/TXT (= données, pas notice)
            csvs = sorted(
                [n for n in z.namelist()
                 if n.lower().endswith((".csv", ".txt"))
                 and not any(w in n.lower() for w in ("notice","readme","doc","meta","variable"))],
                key=lambda n: z.getinfo(n).file_size, reverse=True
            )
            if not csvs:
                csvs = [n for n in z.namelist() if n.lower().endswith((".csv",".txt"))]
            if not csvs:
                print(f"      ✗ ZIP vide : {z.namelist()}")
                return None
            print(f"      ZIP → '{csvs[0]}' ({z.getinfo(csvs[0]).file_size//1024} Ko)")
            return z.read(csvs[0])
        except Exception as e:
            print(f"      ✗ ZIP invalide : {e}")
            return None

    print(f"      Reçu CSV direct ({size_kb} Ko)")
    return content


def slug_to_csv_url(slug: str) -> str | None:
    """Interroge l'API data.gouv.fr et retourne l'URL du plus gros CSV/ZIP."""
    try:
        r = requests.get(
            f"https://www.data.gouv.fr/api/1/datasets/{slug}/",
            timeout=15, headers={"User-Agent": "haute-marne-opendata/1.0"}
        )
        if r.status_code != 200:
            return None
        resources = r.json().get("resources", [])
        candidates = [
            res for res in resources
            if (res.get("format","").lower() in ("csv","zip")
                or (res.get("url","").lower().endswith((".csv",".zip"))))
            and not any(w in (res.get("title","").lower())
                        for w in ("notice","readme","doc","dictionnaire","variable"))
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda r: r.get("filesize") or 0)
        return best.get("url")
    except Exception:
        return None


# ---------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------

def parse_csv(raw: bytes) -> dict[int, dict[str, int]]:
    """
    Parse un CSV INSEE et retourne {année: {code_commune: population}}.
    Détecte automatiquement TOUTES les colonnes PMUN* et P*_POP.
    Affiche les colonnes disponibles pour diagnostic.
    """
    text = None
    for enc in ("utf-8-sig", "latin-1", "utf-8", "cp1252"):
        try: text = raw.decode(enc); break
        except UnicodeDecodeError: pass
    if not text:
        print("      ✗ Encodage non détecté")
        return {}

    first = text.split("\n")[0]
    delim = max([";",",","\t","|"], key=lambda d: first.count(d))

    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        rows   = list(reader)
    except Exception as e:
        print(f"      ✗ DictReader : {e}"); return {}

    if not rows:
        print("      ✗ CSV vide"); return {}

    keys = list(rows[0].keys())
    print(f"      Colonnes ({len(keys)}) : {keys[:25]}")

    # --- Code commune ---
    codgeo = next(
        (k for k in keys if k.upper().strip() in
         ("CODGEO","COM","CODE_COM","CODECOM","CODE","INSEE_COM")),
        None
    )
    if not codgeo:
        for k in keys:
            sample = [rows[i].get(k,"").strip() for i in range(min(8,len(rows)))]
            if sample and all(re.match(r"^\d{5}$", v) for v in sample if v):
                codgeo = k; break
    if not codgeo:
        print(f"      ✗ Code commune introuvable. Toutes colonnes : {keys}")
        return {}
    print(f"      CODGEO → '{codgeo}'")

    # --- Colonnes population par année ---
    year_cols: dict[int, str] = {}
    for k in keys:
        ku = k.upper().strip()
        for pat, grp_to_year in [
            (r"^PMUN(\d{2})$",   lambda g: 2000+int(g) if int(g)<=30 else 1900+int(g)),
            (r"^PMUN(\d{4})$",   lambda g: int(g)),
            (r"^P(\d{2})_POP$",  lambda g: 2000+int(g) if int(g)<=30 else 1900+int(g)),
            (r"^P(\d{4})_POP$",  lambda g: int(g)),
            (r"^PTOT(\d{4})$",   lambda g: int(g)),
            (r"^POP(\d{4})$",    lambda g: int(g)),
        ]:
            m = re.match(pat, ku)
            if m:
                year = grp_to_year(m.group(1))
                if 1960 <= year <= 2030:
                    year_cols[year] = k
                break

    if not year_cols:
        print(f"      ✗ Aucune colonne PMUN/POP trouvée. Colonnes : {keys}")
        return {}
    print(f"      Années détectées : {sorted(year_cols.keys())}")

    # --- Extraction ---
    result: dict[int, dict[str, int]] = {y: {} for y in year_cols}
    for row in rows:
        code = str(row.get(codgeo,"")).strip()
        if not re.match(r"^\d{5}$", code):
            continue
        for year, col in year_cols.items():
            v = str(row.get(col,"")).strip()
            if v and v not in ("","nan","NaN","#","-"):
                try: result[year][code] = int(float(v))
                except ValueError: pass

    for y, d in result.items():
        if d: print(f"      {y} : {len(d):,} communes")
    return {y: d for y, d in result.items() if d}


def fetch_source(urls: list[str], slugs: list[str], label: str) -> dict[int, dict[str, int]]:
    """
    Essaie une liste d'URLs directes puis une liste de slugs data.gouv.fr.
    Retourne le premier résultat non vide.
    """
    print(f"\n   [{label}]")

    all_urls = list(urls)
    for slug in slugs:
        print(f"   Résolution slug '{slug}'...")
        u = slug_to_csv_url(slug)
        if u and u not in all_urls:
            all_urls.append(u)
            print(f"   → {u[-65:]}")

    for i, url in enumerate(all_urls, 1):
        print(f"   Tentative {i}/{len(all_urls)} : ...{url[-65:]}")
        raw = get_raw(url)
        if raw:
            data = parse_csv(raw)
            if data:
                return data

    print(f"   ✗ Toutes les sources ont échoué pour [{label}]")
    return {}


# ---------------------------------------------------------------
# Géographies
# ---------------------------------------------------------------

def fetch_communes(dep: str) -> dict:
    r = requests.get(f"{GEO}/communes", timeout=60, params={
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson", "geometry": "contour",
    })
    r.raise_for_status()
    return r.json()


def fetch_departements() -> dict:
    r = requests.get(f"{GEO}/departements", timeout=120, params={
        "fields": "nom,code,codeRegion,population",
        "format": "geojson", "geometry": "contour",
    })
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return {"type": "FeatureCollection", "features": data}
    return data


# ---------------------------------------------------------------
# Enrichissement
# ---------------------------------------------------------------

def enrich(geojson: dict, all_pop: dict[int, dict[str, int]]) -> dict:
    """
    Ajoute à chaque feature :
      - population_{year}   pour chaque année disponible
      - evolution_absolue / evolution_pct entre la plus récente et la plus ancienne
    """
    years_asc  = sorted(all_pop.keys())
    years_desc = list(reversed(years_asc))
    year_max   = years_desc[0]  if years_desc else None
    year_min   = years_asc[0]   if len(years_asc) > 1 else None

    nb_ok = 0
    for feat in geojson.get("features", []):
        props = feat["properties"]
        code  = props.get("code", "")

        # Remplir toutes les années
        for year, pop_dict in all_pop.items():
            props[f"population_{year}"] = pop_dict.get(code)

        # Population principale = année la plus récente
        if year_max:
            p = all_pop[year_max].get(code)
            if p is not None:
                props["population"]        = p
                props["population_source"] = f"INSEE {year_max}"
            else:
                props["population_source"] = "geo.api.gouv.fr"

        # Évolution entre extremes
        if year_max and year_min:
            p_max = all_pop[year_max].get(code)
            p_min = all_pop[year_min].get(code)
            if p_max is not None and p_min is not None and p_min > 0:
                props["evolution_absolue"] = p_max - p_min
                props["evolution_pct"]     = round((p_max - p_min) / p_min * 100, 2)
                props["evolution_periode"] = f"{year_min}–{year_max}"
                nb_ok += 1
            else:
                props["evolution_absolue"] = None
                props["evolution_pct"]     = None
                props["evolution_periode"] = f"{year_min}–{year_max}"
        else:
            props["evolution_absolue"] = None
            props["evolution_pct"]     = None
            props["evolution_periode"] = "données historiques manquantes"

    total = len(geojson.get("features",[]))
    print(f"   → Évolution : {nb_ok}/{total} communes")
    print(f"   → Années dans GeoJSON : {years_desc}")
    return geojson


def stats(gj: dict) -> dict:
    feats = gj.get("features",[])
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


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    os.makedirs(OUT, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Téléchargement des données de population ---
    print("📊 Chargement populations légales INSEE\n")

    # Fichier récent (2021 + 2016 + 2011 + 2006)
    pop_recent = fetch_source(RECENT_URLS, RECENT_SLUGS, "Fichier récent 2021→2006")

    # Fichier historique (1999 → 1968)
    pop_histo  = fetch_source(HISTO_URLS,  HISTO_SLUGS,  "Séries historiques 1999→1968")

    # Fusion des deux sources
    all_pop: dict[int, dict[str, int]] = {}
    for src in [pop_recent, pop_histo]:
        for year, data in src.items():
            if year not in all_pop or len(data) > len(all_pop[year]):
                all_pop[year] = data

    # Garder uniquement les années cibles
    all_pop = {y: d for y, d in all_pop.items() if y in TARGET_YEARS and d}

    years_found = sorted(all_pop.keys(), reverse=True)
    print(f"\n✓  Années chargées : {years_found}")
    missing = [y for y in TARGET_YEARS if y not in all_pop]
    if missing:
        print(f"⚠️  Années manquantes : {missing}")

    errors = []

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP})")
    try:
        communes = fetch_communes(DEP)
        communes = enrich(communes, all_pop)
        s        = stats(communes)
        communes["metadata"] = {
            "source":         "geo.api.gouv.fr + INSEE via data.gouv.fr",
            "license":        "Licence Ouverte 2.0 (Etalab)",
            "last_updated":   ts,
            "departement":    DEP,
            "annees_dispo":   years_found,
            **s,
        }
        path = os.path.join(OUT, "haute-marne_communes.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(communes, f, ensure_ascii=False, indent=2)
        print(f"✓  {path} ({s.get('nb_communes','?')} communes)")
        print(f"   Population totale   : {s.get('population_totale',0):,}".replace(",", " "))
        print(f"   Croissance / Déclin : {s.get('communes_en_croissance','?')} / {s.get('communes_en_declin','?')}")
    except Exception as e:
        errors.append(f"Communes HM : {e}"); print(f"❌ {errors[-1]}", file=sys.stderr)

    # --- Départements France ---
    print("\n📍 Départements France")
    try:
        deps = fetch_departements()
        nb   = len(deps.get("features",[]))
        has_geom = sum(1 for f in deps.get("features",[]) if f.get("geometry"))
        print(f"   → {nb} départements, {has_geom} avec géométrie")
        deps["metadata"] = {
            "source": "geo.api.gouv.fr", "license": "Licence Ouverte 2.0",
            "last_updated": ts, "nb_departements": nb,
        }
        path = os.path.join(OUT, "france_departements.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        print(f"✓  {path}")
    except Exception as e:
        errors.append(f"Départements : {e}"); print(f"❌ {errors[-1]}", file=sys.stderr)

    # --- Metadata ---
    with open(os.path.join(OUT, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "last_updated":    ts,
            "annees_dispo":    years_found,
            "annees_manquantes": missing,
            "critical_errors": errors,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✓  {OUT}/metadata.json")

    if errors: sys.exit(1)
    print("\n✅ Population OK.")


if __name__ == "__main__":
    main()
