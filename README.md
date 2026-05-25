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
| 👥 Population communale | HM | Mensuelle | GeoJSON | geo.api.gouv.fr + INSEE |
| 🏛️ Départements France | France | Mensuelle | GeoJSON | gregoiredavid + geo.api.gouv.fr |
| 🕐 Horodatage | Tout | À chaque action | JSON | — |

---

## 📁 Structure des fichiers

```
data/
├── last_update.json                    ← Horodatage de chaque dernière mise à jour
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
    ├── haute-marne_communes.geojson    ← Communes + séries 1968–2021
    ├── france_departements.geojson     ← Départements + géométries + population
    └── metadata.json
```

---

## 🕐 Horodatage — `data/last_update.json`

Mis à jour automatiquement à chaque exécution de workflow :

```json
{
  "weather_france":      { "timestamp_utc": "2026-05-25T09:00:00Z", "source": "Open-Meteo", "nb_points": 2300 },
  "weather_haute-marne": { "timestamp_utc": "2026-05-25T09:00:00Z", "source": "Open-Meteo", "nb_points": 400 },
  "population":          { "timestamp_utc": "2026-05-01T03:00:00Z", "annees_dispo": [2021, 2016, 2011, 2006, 1999, 1990, 1982, 1975, 1968] },
  "_last_any_update":    "2026-05-25T09:00:00Z"
}
```

---

## 👥 Champs population — `haute-marne_communes.geojson`

| Champ | Description |
|---|---|
| `population` | Population la plus récente (source INSEE) |
| `population_2021` | Recensement 2021 |
| `population_2016` | Recensement 2016 |
| `population_2011` | Recensement 2011 |
| `population_2006` | Recensement 2006 |
| `population_1999` | Recensement 1999 |
| `population_1990` | Recensement 1990 |
| `population_1982` | Recensement 1982 |
| `population_1975` | Recensement 1975 |
| `population_1968` | Recensement 1968 |
| `evolution_absolue` | Variation (hab.) entre la plus ancienne et la plus récente |
| `evolution_pct` | Variation (%) |
| `evolution_periode` | Ex : `1968–2021` |

**Sources** :
- Années récentes (2006/2011/2016/2021) : fichier `base-cc-evol-struct-pop-2021` INSEE (dataset 6692261)
- Séries historiques (1968/1975/1982/1990/1999) : `base_cc_serie_histo` INSEE (dataset 1893205) + `pop_histo_commune` (dataset 3698339)

---

## 🌡️ Rasters météo (COG)

Les fichiers `.tif` sont des **Cloud Optimized GeoTIFF** :
- Tuilés 512×512, overviews ×2/4/8/16, compression Deflate
- Float32, EPSG:4326, nodata=-9999
- Interpolation bilinéaire (RegularGridInterpolator) + lissage gaussien

---

## 🗺️ Intégration QGIS 3.40

### ① Activer GitHub Pages (étape préalable, une fois)

Pour charger les rasters TIF depuis GitHub dans QGIS, il faut activer GitHub Pages sur le dépôt. Cela permet à GDAL/QGIS de lire les fichiers binaires avec les bons en-têtes HTTP.

**Dans GitHub : Settings → Pages → Branch: main → / (root) → Save**

Une fois activé, les fichiers sont accessibles à :
```
https://julienh77.github.io/haute-marne-opendata/data/...
```

### ② Couches vecteur (GeoJSON) — direct via URL

`Couche > Ajouter une couche vecteur > Source : Protocole HTTP(S)` :

```
# Météo Haute-Marne (points avec température, pluie, vent, nuages)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson

# Communes Haute-Marne (population + séries historiques 1968–2021)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/haute-marne_communes.geojson

# Départements France (avec géométries)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/france_departements.geojson
```

> Les GeoJSON fonctionnent directement avec `raw.githubusercontent.com` car ce sont des fichiers texte.

### ③ Couches raster (COG) — via GitHub Pages

`Couche > Ajouter une couche raster > Source : Protocole HTTP(S)` :

```
# Température Haute-Marne
https://julienh77.github.io/haute-marne-opendata/data/weather/haute-marne_temperature_2m.tif

# Couverture nuageuse France
https://julienh77.github.io/haute-marne-opendata/data/weather/france_cloud_cover.tif

# Vent France
https://julienh77.github.io/haute-marne-opendata/data/weather/france_wind_speed_10m.tif
```

> GitHub Pages sert les `.tif` avec le bon MIME type (`image/tiff`), contrairement à `raw.githubusercontent.com` qui les sert en `text/plain`. QGIS 3.40 peut alors les lire directement comme des COG via GDAL.

> **Pourquoi pas raw.githubusercontent.com pour les TIF ?** Ce domaine sert tous les fichiers en `Content-Type: text/plain`, ce qui empêche GDAL de les identifier comme des rasters binaires.

### ④ Image satellite nuages haute résolution — WMS

`Couche > Ajouter une couche WMS/WMTS > Nouvelle connexion` :

| Service | URL de connexion | Couche |
|---|---|---|
| **NASA GIBS MODIS** (250m, sans clé) | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` | `MODIS_Terra_CorrectedReflectance_TrueColor` |
| **EUMETSAT Meteosat visible** (sans clé) | `https://eumetview.eumetsat.int/geoserver/wms` | `BAS:METEOSAT_0DEG_VIS006` |

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h
Job `fetch-weather` (timeout 30 min) — météo Open-Meteo → GeoJSON + CSV + COG + `last_update.json`

### `update-static.yml` — le 1er de chaque mois
Job `fetch-population` (timeout 30 min) — communes HM + départements France + `last_update.json`

Les deux workflows utilisent `git pull --rebase` avant le push pour éviter les conflits.

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
3. **Settings → Pages → Branch: main → / (root)** ✅ (pour les rasters QGIS)
4. Lancer manuellement les deux workflows

---

## 📜 Licences

| Source | Licence |
|---|---|
| Open-Meteo | CC BY 4.0 — *Weather data by Open-Meteo.com* |
| geo.api.gouv.fr | Licence Ouverte 2.0 (Etalab) |
| INSEE | Licence Ouverte 2.0 (© INSEE) |
| gregoiredavid/france-geojson | MIT |

---

## 🔭 Évolutions envisagées
- **Hydrologie** : Hub'Eau — débits Marne/Aube temps réel
- **Qualité de l'air** : ATMO Grand Est
- **Risques naturels** : Géorisques BRGM
- **Carte interactive** : GitHub Pages avec Leaflet + couches dynamiques
