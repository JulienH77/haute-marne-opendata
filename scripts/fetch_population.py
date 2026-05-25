#!/usr/bin/env python3
"""
fetch_population.py
-------------------
Population Haute-Marne + France avec séries historiques complètes.

STRATÉGIE :
  1. Fichier récent (2021, 2016, 2011, 2006)
     → INSEE direct : base-cc-evol-struct-pop-2021_csv.zip (dataset 6692261)
     → Fallback     : data.gouv.fr slug populations-legales-en-2021

  2. Fichier historique (1968, 1975, 1982, 1990, 1999)
     → INSEE direct : base_cc_serie_histo_2023.zip (dataset 1893205)
     → INSEE direct : pop_histo_commune_2023.xlsx  (dataset 3698339 — 1876→2023)
     → Fallback     : data.gouv.fr slugs
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
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

DEP          = "52"
GEO          = "https://geo.api.gouv.fr"
OUT          = "data/population"
LAST_UPDATE  = "data/last_update.json"

TARGET_YEARS = [2023, 2022, 2021, 2016, 2011, 2006, 1999, 1990, 1982, 1975, 1968]

DEPT_GEOM_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
    "departements-version-simplifiee.geojson"
)

# ── Sources par priorité ──────────────────────────────────────────────────────
# Fichier récent : 2021 + 2016 + 2011 + 2006 (colonnes PMUN21, PMUN16, PMUN11, PMUN06)
RECENT_DIRECT = [
    "https://www.insee.fr/fr/statistiques/fichier/6692261/base-cc-evol-struct-pop-2021_csv.zip",
    "https://www.insee.fr/fr/statistiques/fichier/7739582/base-cc-evol-struct-pop-2021_csv.zip",
]
RECENT_SLUGS = ["populations-legales-en-2021", "populations-legales-en-2020"]

# Fichier historique : 1999, 1990, 1982, 1975, 1968 (colonnes PMUN99, PMUN90…)
HISTO_DIRECT = [
    # dataset 1893205 — base-cc-serie-histo (contient PMUN99, PMUN90, PMUN82, PMUN75, PMUN68)
    "https://www.insee.fr/fr/statistiques/fichier/1893205/base_cc_serie_histo_2023.zip",
    "https://www.insee.fr/fr/statistiques/fichier/1893205/base_cc_serie_histo_2022.zip",
    "https://www.insee.fr/fr/statistiques/fichier/1893205/base_cc_serie_histo_2021.zip",
    # dataset 3698339 — pop_histo_commune (XLSX, 1876→2023, colonnes PMUN1968, etc.)
    "https://www.insee.fr/fr/statistiques/fichier/3698339/pop_histo_commune_2023.xlsx",
    "https://www.insee.fr/fr/statistiques/fichier/3698339/pop_histo_commune_2022.xlsx",
]
HISTO_SLUGS = [
    "series-historiques-des-resultats-du-recensement-de-la-population",
    "populations-legales-des-communes-depuis-1968",
    "populations-legales-en-2009",
    "populations-legales-en-2006",
]


# ── Téléchargement ────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "haute-marne-opendata/1.0"}

def get_raw(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=90, allow_redirects=True, headers=HEADERS)
        r.raise_for_status()
    except Exception as e:
        print(f"      ✗ {e}")
        return None

    content = r.content
    size_kb = len(content) // 1024
    if size_kb < 5:
        print(f"      ✗ Trop petit ({size_kb} Ko) — probablement une page HTML")
        return None

    # ZIP
    if content[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            csvs = sorted(
                [n for n in z.namelist()
                 if n.lower().endswith((".csv", ".txt"))
                 and not any(w in n.lower() for w in
                             ("notice", "readme", "doc", "meta", "variable"))],
                key=lambda n: z.getinfo(n).file_size, reverse=True,
            )
            if not csvs:
                csvs = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not csvs:
                print(f"      ✗ ZIP sans CSV : {z.namelist()}")
                return None
            print(f"      ZIP → '{csvs[0]}' ({z.getinfo(csvs[0]).file_size // 1024} Ko)")
            return z.read(csvs[0])
        except Exception as e:
            print(f"      ✗ ZIP : {e}")
            return None

    # XLSX
    url_lower = url.lower()
    if url_lower.endswith(".xlsx") or url_lower.endswith(".xls"):
        print(f"      XLSX reçu ({size_kb} Ko)")
        return content   # retourné tel quel, parsé séparément

    print(f"      CSV direct ({size_kb} Ko)")
    return content


def slug_to_csv_url(slug: str) -> str | None:
    try:
        r = requests.get(
            f"https://www.data.gouv.fr/api/1/datasets/{slug}/",
            timeout=20, headers=HEADERS,
        )
        if r.status_code != 200:
            return None
        resources = r.json().get("resources", [])
        candidates = [
            res for res in resources
            if (res.get("format", "").lower() in ("csv", "zip")
                or res.get("url", "").lower().endswith((".csv", ".zip")))
            and not any(w in (res.get("title") or "").lower()
                        for w in ("notice", "readme", "doc", "dictionnaire"))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.get("filesize") or 0).get("url")
    except Exception:
        return None


# ── Parsing CSV INSEE ─────────────────────────────────────────────────────────

def parse_csv(raw: bytes) -> dict[int, dict[str, int]]:
    text = None
    for enc in ("utf-8-sig", "latin-1", "utf-8", "cp1252"):
        try: text = raw.decode(enc); break
        except UnicodeDecodeError: pass
    if not text:
        print("      ✗ Encodage inconnu"); return {}

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
         ("CODGEO", "COM", "CODE_COM", "CODECOM", "CODE", "INSEE_COM")), None,
    )
    if not codgeo:
        for k in keys:
            sample = [rows[i].get(k, "").strip() for i in range(min(10, len(rows)))]
            if sample and all(re.match(r"^\d{5}$", v) for v in sample if v):
                codgeo = k; break
    if not codgeo:
        print("      ✗ Code commune introuvable"); return {}
    print(f"      CODGEO = '{codgeo}'")

    # Colonnes population
    year_cols: dict[int, str] = {}
    for k in keys:
        ku = k.upper().strip()
        for pat, yfn in [
            (r"^PMUN(\d{2})$",   lambda g: 2000+int(g) if int(g) <= 30 else 1900+int(g)),
            (r"^PMUN(\d{4})$",   lambda g: int(g)),
            (r"^P(\d{2})_POP$",  lambda g: 2000+int(g) if int(g) <= 30 else 1900+int(g)),
            (r"^P(\d{4})_POP$",  lambda g: int(g)),
            (r"^PTOT(\d{4})$",   lambda g: int(g)),
            (r"^POP(\d{4})$",    lambda g: int(g)),
        ]:
            m = re.match(pat, ku)
            if m:
                y = yfn(m.group(1))
                if 1960 <= y <= 2030:
                    year_cols[y] = k
                break

    if not year_cols:
        print(f"      ✗ Aucune colonne PMUN. Colonnes : {keys}"); return {}
    print(f"      Années CSV : {sorted(year_cols.keys())}")

    result: dict[int, dict[str, int]] = {y: {} for y in year_cols}
    for row in rows:
        code = str(row.get(codgeo, "")).strip()
        if not re.match(r"^\d{5}$", code): continue
        for year, col in year_cols.items():
            v = str(row.get(col, "")).strip()
            if v and v not in ("", "nan", "NaN", "#", "-"):
                try: result[year][code] = int(float(v))
                except ValueError: pass

    result = {y: d for y, d in result.items() if d}
    for y, d in result.items():
        print(f"      → {y} : {len(d):,} communes")
    return result


def parse_xlsx(raw: bytes) -> dict[int, dict[str, int]]:
    """Parse un fichier XLSX INSEE (ex. pop_histo_commune_2023.xlsx)."""
    if not HAS_OPENPYXL:
        print("      ✗ openpyxl non installé — XLSX ignoré"); return {}
    try:
        wb  = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws  = wb.active
        rows = list(ws.values)
        wb.close()
    except Exception as e:
        print(f"      ✗ XLSX : {e}"); return {}

    if not rows:
        print("      ✗ XLSX vide"); return {}

    headers = [str(h).strip().upper() if h else "" for h in rows[0]]
    print(f"      Colonnes XLSX ({len(headers)}) : {headers[:20]}")

    # Code commune
    codgeo_idx = next(
        (i for i, h in enumerate(headers) if h in
         ("CODGEO", "COM", "CODE_COM", "CODECOM")), None,
    )
    if codgeo_idx is None:
        print("      ✗ Code commune introuvable dans XLSX"); return {}

    # Colonnes population
    year_cols: dict[int, int] = {}
    for i, h in enumerate(headers):
        for pat, yfn in [
            (r"^PMUN(\d{2})$",   lambda g: 2000+int(g) if int(g) <= 30 else 1900+int(g)),
            (r"^PMUN(\d{4})$",   lambda g: int(g)),
            (r"^P(\d{2})_POP$",  lambda g: 2000+int(g) if int(g) <= 30 else 1900+int(g)),
            (r"^P(\d{4})_POP$",  lambda g: int(g)),
            (r"^PMUN_(\d{4})$",  lambda g: int(g)),
            (r"^POP_(\d{4})$",   lambda g: int(g)),
            (r"^D(\d{4})$",      lambda g: int(g)),   # format alternatif
        ]:
            m = re.match(pat, h)
            if m:
                y = yfn(m.group(1))
                if 1960 <= y <= 2030:
                    year_cols[y] = i
                break

    if not year_cols:
        print(f"      ✗ Aucune colonne PMUN dans XLSX"); return {}
    print(f"      Années XLSX : {sorted(year_cols.keys())}")

    result: dict[int, dict[str, int]] = {y: {} for y in year_cols}
    for row in rows[1:]:
        if not row: continue
        code = str(row[codgeo_idx] or "").strip()
        if not re.match(r"^\d{5}$", code): continue
        for year, col_idx in year_cols.items():
            v = row[col_idx] if col_idx < len(row) else None
            if v is not None:
                try: result[year][code] = int(float(str(v)))
                except (ValueError, TypeError): pass

    result = {y: d for y, d in result.items() if d}
    for y, d in result.items():
        print(f"      → {y} : {len(d):,} communes")
    return result


def parse_auto(raw: bytes, url: str = "") -> dict[int, dict[str, int]]:
    """Détecte le format et parse."""
    url_l = url.lower()
    if url_l.endswith(".xlsx") or url_l.endswith(".xls"):
        # Vérifier la signature XLSX (ZIP)
        if raw[:2] == b"PK":
            return parse_xlsx(raw)
    return parse_csv(raw)


def fetch_source(direct_urls: list[str], slugs: list[str],
                 label: str) -> dict[int, dict[str, int]]:
    print(f"\n   [{label}]")

    # 1. URLs directes INSEE (priorité absolue)
    for i, url in enumerate(direct_urls, 1):
        print(f"   Essai direct {i}/{len(direct_urls)} : ...{url[-60:]}")
        raw = get_raw(url)
        if raw:
            data = parse_auto(raw, url)
            if data:
                print(f"   ✓ Succès via URL directe INSEE")
                return data

    # 2. data.gouv.fr slugs (fallback)
    for slug in slugs:
        print(f"   Slug '{slug}'...")
        url = slug_to_csv_url(slug)
        if not url:
            continue
        print(f"   → ...{url[-60:]}")
        raw = get_raw(url)
        if raw:
            data = parse_auto(raw, url)
            if data:
                print(f"   ✓ Succès via data.gouv.fr slug")
                return data

    print(f"   ✗ Toutes les sources ont échoué pour [{label}]")
    return {}


# ── Géographies ───────────────────────────────────────────────────────────────

def fetch_communes(dep: str) -> dict:
    r = requests.get(f"{GEO}/communes", timeout=60, headers=HEADERS, params={
        "codeDepartement": dep,
        "fields": "nom,code,codeDepartement,codeRegion,population,surface",
        "format": "geojson", "geometry": "contour",
    })
    r.raise_for_status()
    return r.json()


def fetch_departements() -> dict:
    """
    Géométries depuis gregoiredavid/france-geojson (fiable, MIT).
    Populations depuis geo.api.gouv.fr.
    """
    print("   Géométries gregoiredavid/france-geojson...")
    r = requests.get(DEPT_GEOM_URL, timeout=30, headers=HEADERS)
    r.raise_for_status()
    gj = r.json()
    nb = len(gj.get("features", []))
    has_geom = sum(1 for f in gj.get("features", []) if f.get("geometry"))
    print(f"   → {nb} départements, {has_geom} avec géométrie")

    # Populations
    try:
        rp = requests.get(f"{GEO}/departements", timeout=30, headers=HEADERS,
                          params={"fields": "code,nom,population", "format": "json"})
        pop_map = {d.get("code"): d.get("population") for d in rp.json()}
        for feat in gj.get("features", []):
            feat["properties"]["population"] = pop_map.get(
                feat.get("properties", {}).get("code"))
        print(f"   → Populations enrichies ({len(pop_map)} départements)")
    except Exception as e:
        print(f"   ⚠️  Populations départements : {e}")

    return gj


# ── Enrichissement ─────────────────────────────────────────────────────────────

def enrich(geojson, all_pop: dict[int, dict[str, int]]):
    years_asc  = sorted(all_pop.keys())
    years_desc = list(reversed(years_asc))
    y_max      = years_desc[0] if years_desc else None
    y_min      = years_asc[0]  if len(years_asc) > 1 else None

    nb_ok = 0
    for feat in geojson.get("features", []):
        props = feat["properties"]
        code  = props.get("code", "")

        for year, pop_d in all_pop.items():
            props[f"population_{year}"] = pop_d.get(code)

        if y_max:
            p = all_pop[y_max].get(code)
            if p is not None:
                props["population"]        = p
                props["population_source"] = f"INSEE {y_max}"

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
            props["evolution_periode"] = f"{y_min}–{y_max}" if y_min and y_max else "N/A"

    print(f"   → Évolution : {nb_ok}/{len(geojson.get('features', []))} communes")
    print(f"   → Années : {years_desc}")
    return geojson


def stats(gj):
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


def update_last_update(key, ts, extra=None):
    try:
        with open(LAST_UPDATE, encoding="utf-8") as f: data = json.load(f)
    except Exception: data = {}
    data[key] = {"timestamp_utc": ts, **(extra or {})}
    data["_last_any_update"] = ts
    os.makedirs(os.path.dirname(LAST_UPDATE) or ".", exist_ok=True)
    with open(LAST_UPDATE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("📊 Chargement populations INSEE\n")

    pop_recent = fetch_source(RECENT_DIRECT, RECENT_SLUGS,
                              "Fichier récent 2021→2006")
    pop_histo  = fetch_source(HISTO_DIRECT,  HISTO_SLUGS,
                              "Séries historiques 1968→1999")

    all_pop: dict[int, dict[str, int]] = {}
    for src in [pop_recent, pop_histo]:
        for year, data in src.items():
            if year not in all_pop or len(data) > len(all_pop.get(year, {})):
                all_pop[year] = data

    all_pop    = {y: d for y, d in all_pop.items() if y in TARGET_YEARS and d}
    years_ok   = sorted(all_pop.keys(), reverse=True)
    years_miss = [y for y in TARGET_YEARS if y not in all_pop]
    print(f"\n✓  Années chargées : {years_ok}")
    if years_miss: print(f"⚠️  Manquantes     : {years_miss}")

    errors = []

    # --- Communes Haute-Marne ---
    print(f"\n📍 Communes Haute-Marne (dép. {DEP})")
    try:
        communes = fetch_communes(DEP)
        communes = enrich(communes, all_pop)
        s        = stats(communes)
        communes["metadata"] = {
            "source": "geo.api.gouv.fr + INSEE via insee.fr / data.gouv.fr",
            "license": "Licence Ouverte 2.0",
            "last_updated": ts, "departement": DEP,
            "annees_dispo": years_ok, **s,
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

    # --- Départements ---
    print("\n📍 Départements France")
    deps = {"type": "FeatureCollection", "features": []}
    try:
        deps = fetch_departements()
        deps["metadata"] = {
            "source_geom": "gregoiredavid/france-geojson",
            "source_pop":  "geo.api.gouv.fr",
            "license": "Licence Ouverte 2.0 / MIT",
            "last_updated": ts,
            "nb_departements": len(deps.get("features", [])),
        }
        path = os.path.join(OUT, "france_departements.geojson")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(deps, f, ensure_ascii=False, indent=2)
        print(f"✓  {path}")
    except Exception as e:
        errors.append(f"Départements : {e}")
        print(f"❌ {errors[-1]}", file=sys.stderr)

    update_last_update("population", ts, {
        "annees_dispo": years_ok, "annees_manquantes": years_miss,
    })

    with open(os.path.join(OUT, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": ts, "annees_dispo": years_ok,
            "annees_manquantes": years_miss,
            "sources": {
                "geometries_deps": "gregoiredavid/france-geojson",
                "geometries_communes": "geo.api.gouv.fr",
                "populations_recent": "INSEE dataset 6692261 (2021→2006)",
                "populations_histo": "INSEE dataset 1893205 / 3698339 (1968→1999)",
            },
            "critical_errors": errors,
        }, f, ensure_ascii=False, indent=2)

    if errors: sys.exit(1)
    print("\n✅ Population OK.")


if __name__ == "__main__":
    main()
