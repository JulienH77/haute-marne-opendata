# 🌍 haute-marne-opendata

Centralisation et mise à jour automatique de données opendata géographiques pour la **France** et la **Haute-Marne (52)**, pilotée par **GitHub Actions** — sans serveur ni infrastructure tierce.

---

## 📦 Données disponibles

| Couche | Zone | Fréquence | Formats | Source |
|---|---|---|---|---|
| 🌡️ Température (2m) | France / HM | **3h** | GeoJSON + CSV + COG + GeoPackage | [Open-Meteo](https://open-meteo.com) |
| 🌧️ Précipitations | France / HM | **3h** | GeoJSON + CSV + COG + GeoPackage | [Open-Meteo](https://open-meteo.com) |
| 🌫️ Couverture nuageuse | France / HM | **3h** | GeoJSON + CSV + COG + GeoPackage | [Open-Meteo](https://open-meteo.com) |
| 💨 Vent (10m) | France / HM | **3h** | GeoJSON + CSV + COG + GeoPackage | [Open-Meteo](https://open-meteo.com) |
| 👥 Population communale | HM | Mensuelle | GeoJSON + GeoPackage | geo.api.gouv.fr + INSEE |
| 🏛️ Départements France | France | Mensuelle | GeoJSON + GeoPackage | gregoiredavid + geo.api.gouv.fr |
| 🕐 Horodatage | Tout | À chaque action | JSON | — |

---

## 📁 Fichiers générés

```
data/
├── last_update.json                    ← Date/heure UTC de chaque dernière mise à jour
│
├── weather/
│   ├── metadata.json
│   ├── weather.gpkg                    ← GeoPackage : tous les points météo (France + HM)
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
    ├── metadata.json
    ├── population.gpkg                 ← GeoPackage : communes HM + départements France
    ├── haute-marne_communes.geojson    ← Communes + séries 1968–2021
    └── france_departements.geojson     ← Départements + géométries + population
```

---

## 🕐 Horodatage — `data/last_update.json`

```json
{
  "weather_france":    { "timestamp_utc": "2026-05-24T09:00:00Z", "source": "Open-Meteo", "nb_points": 2300 },
  "weather_haute-marne": { "timestamp_utc": "2026-05-24T09:00:00Z", "source": "Open-Meteo", "nb_points": 400 },
  "population":        { "timestamp_utc": "2026-05-01T03:00:00Z", "annees_dispo": [2021, 2016, 2011, 2006, 1999, ...] },
  "_last_any_update":  "2026-05-24T09:00:00Z"
}
```

---

## 👥 Champs population — `haute-marne_communes.geojson`

| Champ | Description |
|---|---|
| `population` | Population la plus récente (INSEE) |
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

**Sources** : `base-cc-evol-struct-pop-2021` (2006→2021) + séries historiques INSEE (1968→1999).

---

## 🌡️ Rasters météo (COG)

Les fichiers `.tif` sont des **Cloud Optimized GeoTIFF (COG)** :
- Tuilés (512×512), avec overviews ×2/4/8/16
- Compression Deflate, Float32, nodata=-9999
- Interpolation bilinéaire (RegularGridInterpolator) + lissage gaussien

---

## 🗺️ Intégration QGIS 3.40

### Couche vecteur — GeoJSON ou GeoPackage

**GeoPackage (recommandé)** — un seul fichier, toutes les couches :
```
Couche > Ajouter couche vecteur > GeoPackage > Protocole HTTP :
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/weather.gpkg
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/population.gpkg
```

**GeoJSON** :
```
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/haute-marne_communes.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/france_departements.geojson
```

### Couche raster — COG via `/vsicurl/`

Les COG sont lisibles directement dans QGIS sans téléchargement complet.

`Couche > Ajouter couche raster > Source = Protocol / HTTP` :
```
/vsicurl/https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_temperature_2m.tif
/vsicurl/https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/france_cloud_cover.tif
/vsicurl/https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_wind_speed_10m.tif
```

> **Astuce QGIS** : dans le gestionnaire de sources de données, choisir "Protocole : HTTP(S), cloud, etc." et préfixer l'URL avec `/vsicurl/`.

### Image satellite nuages haute résolution — WMS

`Couche > Ajouter couche WMS/WMTS > Nouvelle connexion` :

| Service | URL | Couche |
|---|---|---|
| NASA GIBS MODIS (250m, sans clé) | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` | `MODIS_Terra_CorrectedReflectance_TrueColor` |
| EUMETSAT Meteosat visible | `https://eumetview.eumetsat.int/geoserver/wms` | `BAS:METEOSAT_0DEG_VIS006` |

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h
Job `fetch-weather` (timeout 30 min) — météo Open-Meteo → GeoJSON + CSV + COG + GeoPackage + `last_update.json`

### `update-static.yml` — le 1er de chaque mois
Job `fetch-population` (timeout 30 min) — communes HM + départements France → GeoJSON + GeoPackage + `last_update.json`

Les deux workflows utilisent `git pull --rebase` avant le push pour éviter les conflits.

---

## 📐 Zones de découpage

| Zone | Bbox | CRS |
|---|---|---|
| France métro | -5.14, 41.33, 9.56, 51.09 | EPSG:4326 |
| Haute-Marne | 4.70, 47.50, 5.95, 48.65 | EPSG:4326 |

---

## 📜 Licences

| Source | Licence |
|---|---|
| Open-Meteo | CC BY 4.0 — *Weather data by Open-Meteo.com* |
| geo.api.gouv.fr | Licence Ouverte 2.0 (Etalab) |
| INSEE | Licence Ouverte 2.0 (© INSEE) |
| gregoiredavid/france-geojson | MIT |

---

## 🔧 Lancement manuel
**Actions → workflow → Run workflow**

## 🚀 Installation (fork)
1. Forker le dépôt
2. **Settings → Actions → General → Read and write permissions** ✅
3. Lancer manuellement les deux workflows

---

## 🔭 Évolutions envisagées
- **Hydrologie** : Hub'Eau — débits Marne/Aube temps réel
- **Qualité de l'air** : ATMO Grand Est
- **Risques naturels** : Géorisques BRGM
- **Page carte** : GitHub Pages avec Leaflet + couches dynamiques depuis ce repo
