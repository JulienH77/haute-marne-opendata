# 🌍 haute-marne-opendata

Centralisation et mise à jour automatique de données opendata géographiques pour la **France** et la **Haute-Marne (52)**, pilotée par **GitHub Actions**.

---

## 📦 Données disponibles

| Couche | Zone | Fréquence | Formats | Source |
|---|---|---|---|---|
| 🌡️ Température (2m) | France / HM | **3h** | GeoJSON + CSV + COG | [Open-Meteo](https://open-meteo.com) |
| 🌧️ Précipitations | France / HM | **3h** | GeoJSON + CSV + COG | [Open-Meteo](https://open-meteo.com) |
| 🌫️ Couverture nuageuse | France / HM | **3h** | GeoJSON + CSV + COG | [Open-Meteo](https://open-meteo.com) |
| 💨 Vent (10m) | France / HM | **3h** | GeoJSON + CSV + COG | [Open-Meteo](https://open-meteo.com) |
| 👥 Population communale | HM | Mensuelle | GeoJSON | geo.api.gouv.fr + **INSEE Mélodi API** |
| 🏛️ Départements France | France | Mensuelle | GeoJSON | gregoiredavid + geo.api.gouv.fr |
| 🕐 Horodatage | Tout | À chaque action | JSON | — |

---

## 📁 Structure des fichiers

```
data/
├── last_update.json                    ← Horodatage de chaque mise à jour
│
├── weather/
│   ├── france_weather.geojson          ← ~2300 pts, résolution 0.25° (~27 km)
│   ├── france_weather.csv
│   ├── france_temperature_2m.tif       ← COG Float32, 600×400 px, EPSG:4326
│   ├── france_precipitation.tif
│   ├── france_cloud_cover.tif
│   ├── france_wind_speed_10m.tif
│   ├── haute-marne_weather.geojson     ← ~400 pts, résolution 0.05° (~5 km)
│   ├── haute-marne_weather.csv
│   ├── haute-marne_temperature_2m.tif  ← COG Float32, 600×600 px, EPSG:4326
│   ├── haute-marne_precipitation.tif
│   ├── haute-marne_cloud_cover.tif
│   └── haute-marne_wind_speed_10m.tif
│
└── population/
    ├── haute-marne_communes.geojson    ← Communes + séries historiques
    ├── france_departements.geojson     ← Départements + géométries + population
    └── metadata.json
```

---

## 🕐 Horodatage — `data/last_update.json`

```json
{
  "weather_france":      { "timestamp_utc": "2026-05-27T09:00:00Z", "nb_points": 2300 },
  "weather_haute-marne": { "timestamp_utc": "2026-05-27T09:00:00Z", "nb_points": 400 },
  "population":          { "timestamp_utc": "2026-05-01T03:00:00Z",
                           "source": "INSEE API Mélodi",
                           "annees_dispo": [2023, 2022, 2021, 2016, 2011, 2006] },
  "_last_any_update":    "2026-05-27T09:00:00Z"
}
```

---

## 👥 Champs population — `haute-marne_communes.geojson`

Source : **API INSEE Mélodi** (`https://api.insee.fr/melodi/data/DS_POPULATIONS_HISTORIQUES`) — sans clé d'accès, données officielles.

| Champ | Description |
|---|---|
| `population` | Population la plus récente (INSEE Mélodi) |
| `population_source` | Ex : `INSEE Mélodi 2023` |
| `population_2023` | Population municipale 2023 |
| `population_2022` | Population municipale 2022 |
| `population_2021` | Population municipale 2021 |
| `population_2016` | Population municipale 2016 |
| `population_2011` | Population municipale 2011 |
| `population_2006` | Population municipale 2006 |
| `population_1999` | Recensement général 1999 (si disponible dans Mélodi) |
| `population_1990` | Recensement général 1990 (si disponible) |
| `population_1982` | Recensement général 1982 (si disponible) |
| `population_1975` | Recensement général 1975 (si disponible) |
| `population_1968` | Recensement général 1968 (si disponible) |
| `evolution_absolue` | Variation (hab.) entre l'année la plus ancienne et la plus récente |
| `evolution_pct` | Variation (%) |
| `evolution_periode` | Ex : `1968–2023` |

---

## 🌡️ Rasters météo (COG)

**Cloud Optimized GeoTIFF** — tuilés 512×512, overviews ×2/4/8/16, Deflate.
Interpolation bilinéaire (RegularGridInterpolator) + lissage gaussien — pas d'artefacts triangulaires.

| Zone | Résolution source | Taille raster sortie |
|---|---|---|
| France | 0.25° (~27 km) | 600×400 px |
| Haute-Marne | 0.05° (~5 km) | 600×600 px |

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h
Job `fetch-weather` (timeout 30 min) — Open-Meteo → GeoJSON + CSV + COG

### `update-static.yml` — le 1er de chaque mois
Job `fetch-population` (timeout 30 min) — INSEE Mélodi → communes HM toutes années + départements France

---

## 🗺️ Intégration QGIS 3.40

### ① Activer GitHub Pages (une fois, pour les rasters)

**Settings → Pages → Branch: main → / (root) → Save**

Les TIF seront alors accessibles à : `https://julienh77.github.io/haute-marne-opendata/data/...`

### ② Couches vecteur (GeoJSON) — `Couche > Ajouter couche vecteur > Protocole HTTP`

```
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/haute-marne_communes.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/france_departements.geojson
```

### ③ Couches raster (COG) — via GitHub Pages

`Couche > Ajouter couche raster > Protocole HTTP` :

```
https://julienh77.github.io/haute-marne-opendata/data/weather/haute-marne_temperature_2m.tif
https://julienh77.github.io/haute-marne-opendata/data/weather/france_cloud_cover.tif
https://julienh77.github.io/haute-marne-opendata/data/weather/haute-marne_wind_speed_10m.tif
```

> GitHub Pages sert les `.tif` avec le bon MIME type. `raw.githubusercontent.com` les sert en `text/plain`, ce qui bloque QGIS.

### ④ WMS nuages satellite — `Couche > Ajouter couche WMS/WMTS`

| Service | URL | Couche |
|---|---|---|
| **NASA GIBS MODIS** (250m, sans clé) | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` | `MODIS_Terra_CorrectedReflectance_TrueColor` |
| **EUMETSAT Meteosat** (sans clé) | `https://eumetview.eumetsat.int/geoserver/wms` | `BAS:METEOSAT_0DEG_VIS006` |

---

## 📐 Zones de découpage

| Zone | Bbox | CRS |
|---|---|---|
| France métro | -5.14, 41.33, 9.56, 51.09 | EPSG:4326 |
| Haute-Marne | 4.70, 47.50, 5.95, 48.65 | EPSG:4326 |

---

## 🔧 Lancement manuel
**Actions → workflow → Run workflow**

## 🚀 Installation (fork)
1. Forker le dépôt
2. **Settings → Actions → General → Read and write permissions** ✅
3. **Settings → Pages → Branch: main → / (root)** ✅
4. Lancer manuellement les deux workflows

---

## 📜 Licences

| Source | Licence |
|---|---|
| Open-Meteo | CC BY 4.0 — *Weather data by Open-Meteo.com* |
| INSEE Mélodi API | Licence Ouverte 2.0 (© INSEE) |
| geo.api.gouv.fr | Licence Ouverte 2.0 (Etalab) |
| gregoiredavid/france-geojson | MIT |

---

## 🔭 Évolutions envisagées
- **Hydrologie** : Hub'Eau — débits Marne/Aube temps réel
- **Qualité de l'air** : ATMO Grand Est
- **Risques naturels** : Géorisques BRGM
- **Carte interactive** : GitHub Pages avec Leaflet
