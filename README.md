# 🌍 haute-marne-opendata

Centralisation et mise à jour automatique de données opendata géographiques pour la **France** et la **Haute-Marne (52)**, entièrement pilotée par **GitHub Actions** — sans serveur ni infrastructure tierce.

---

## 📦 Données disponibles

| Couche | Zone | Fréquence | Formats | Source |
|---|---|---|---|---|
| 🌡️ Température (2m) | France / HM | **3h** | GeoJSON + CSV + GeoTIFF EPSG:4326 | [Open-Meteo](https://open-meteo.com) |
| 🌧️ Précipitations | France / HM | **3h** | GeoJSON + CSV + GeoTIFF EPSG:4326 | [Open-Meteo](https://open-meteo.com) |
| 🌫️ Couverture nuageuse (%) | France / HM | **3h** | GeoJSON + CSV + GeoTIFF EPSG:4326 | [Open-Meteo](https://open-meteo.com) |
| 💨 Vent (10m) | France / HM | **3h** | GeoJSON + CSV + GeoTIFF EPSG:4326 | [Open-Meteo](https://open-meteo.com) |
| 👥 Population communale | HM | Mensuelle | GeoJSON | geo.api.gouv.fr + INSEE |
| 🏛️ Départements France | France | Mensuelle | GeoJSON | geo.api.gouv.fr |

> **Note nuages** : la couverture nuageuse est fournie par Open-Meteo sous forme de pourcentage (0–100 %) dans `*_cloud_cover.tif`. Pour une image satellite visible haute résolution, utiliser les flux WMS listés en bas de ce README.

---

## 📁 Fichiers générés

```
data/
├── weather/
│   ├── france_weather.geojson          ← ~2300 points, résolution 0.25° (~27 km)
│   ├── france_weather.csv
│   ├── france_temperature_2m.tif       ← Raster Float32, 600×400 px, EPSG:4326
│   ├── france_precipitation.tif
│   ├── france_cloud_cover.tif          ← Couverture nuageuse 0–100 %
│   ├── france_wind_speed_10m.tif
│   ├── haute-marne_weather.geojson     ← ~400 points, résolution 0.05° (~5 km)
│   ├── haute-marne_weather.csv
│   ├── haute-marne_temperature_2m.tif  ← Raster Float32, 600×600 px, EPSG:4326
│   ├── haute-marne_precipitation.tif
│   ├── haute-marne_cloud_cover.tif
│   ├── haute-marne_wind_speed_10m.tif
│   └── metadata.json
│
└── population/
    ├── haute-marne_communes.geojson    ← Communes + séries historiques 1968–2021
    ├── france_departements.geojson
    └── metadata.json
```

---

## 🌡️ Détails météo — rasters GeoTIFF

| Paramètre | Variable Open-Meteo | Unité | nodata |
|---|---|---|---|
| Température | `temperature_2m` | °C | -9999 |
| Précipitations | `precipitation` | mm/h | -9999 |
| Couverture nuageuse | `cloud_cover` | % (0–100) | -9999 |
| Vent | `wind_speed_10m` | m/s | -9999 |

Interpolation **RegularGridInterpolator bilinéaire** + **lissage gaussien** — pas d'artefacts triangulaires. Modèle source : ERA5 / ECMWF via Open-Meteo (même modèle que Windy).

---

## 👥 Champs population — `haute-marne_communes.geojson`

| Champ | Description |
|---|---|
| `population` | Population la plus récente (source INSEE) |
| `population_source` | Ex : `INSEE 2021` |
| `population_2021` | Recensement 2021 |
| `population_2016` | Recensement 2016 |
| `population_2011` | Recensement 2011 |
| `population_2006` | Recensement 2006 |
| `population_1999` | Recensement 1999 |
| `population_1990` | Recensement 1990 |
| `population_1982` | Recensement 1982 |
| `population_1975` | Recensement 1975 |
| `population_1968` | Recensement 1968 |
| `evolution_absolue` | Variation (hab.) entre la plus ancienne et la plus récente année disponible |
| `evolution_pct` | Variation (%) sur la même période |
| `evolution_periode` | Ex : `1968–2021` |

**Sources** : `base-cc-evol-struct-pop-2021` (2006→2021) + séries historiques INSEE (1968→1999).

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h
Un seul job `fetch-weather` (timeout 30 min) :
- Interroge Open-Meteo en grille régulière sur France et Haute-Marne
- Génère GeoJSON + CSV + 4 GeoTIFF par zone (8 rasters au total)
- Commit & push avec `git pull --rebase` (anti-conflit)

### `update-static.yml` — le 1er de chaque mois
- Communes Haute-Marne + séries historiques INSEE 1968→2021
- Départements France avec géométries

---

## 🗺️ Intégration QGIS 3.40

### Couche vecteur — `Couche > Ajouter une couche vecteur > Protocole HTTP`

```
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/haute-marne_communes.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/france_departements.geojson
```

### Couche raster — `Couche > Ajouter une couche raster > Protocole HTTP`

```
# Couverture nuageuse Haute-Marne (0–100 %, GeoTIFF Float32)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_cloud_cover.tif

# Température France
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/france_temperature_2m.tif

# Vent Haute-Marne
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_wind_speed_10m.tif
```

### Image satellite nuages haute résolution — WMS

`Couche > Ajouter une couche WMS/WMTS > Nouvelle connexion` :

| Service | URL de connexion | Couche recommandée |
|---|---|---|
| **NASA GIBS MODIS Terra** (250 m, sans clé) | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` | `MODIS_Terra_CorrectedReflectance_TrueColor` |
| **EUMETSAT Meteosat visible** (sans clé) | `https://eumetview.eumetsat.int/geoserver/wms` | `BAS:METEOSAT_0DEG_VIS006` |
| **OpenWeatherMap nuages** (clé gratuite) | `https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid={CLE}` | XYZ Tiles — [openweathermap.org/api](https://openweathermap.org/api) |

---

## 📐 Zones de découpage

| Zone | Bbox (lon_min, lat_min, lon_max, lat_max) | CRS |
|---|---|---|
| France métropolitaine | -5.14, 41.33, 9.56, 51.09 | EPSG:4326 |
| Haute-Marne | 4.70, 47.50, 5.95, 48.65 | EPSG:4326 |

---

## 🔧 Lancement manuel
**Actions → workflow → Run workflow**

## 🚀 Installation (fork)
1. Forker ce dépôt
2. **Settings → Actions → General → Read and write permissions** ✅
3. Lancer manuellement les deux workflows une première fois

---

## 📜 Licences

| Source | Licence |
|---|---|
| Open-Meteo | CC BY 4.0 — *Weather data by Open-Meteo.com* |
| geo.api.gouv.fr | Licence Ouverte 2.0 (Etalab) |
| INSEE | Licence Ouverte 2.0 (© INSEE) |

---

## 🔭 Évolutions envisagées
- **Hydrologie** : [Hub'Eau](https://hubeau.eaufrance.fr/) — débits Marne/Aube temps réel (API sans clé)
- **Qualité de l'air** : [ATMO Grand Est](https://www.atmo-grandest.eu/) — API gratuite sur demande
- **Risques naturels** : [Géorisques BRGM](https://www.georisques.gouv.fr/)
- **Ensoleillement** : Open-Meteo `sunshine_duration` / `shortwave_radiation`
- **Carte interactive** : page GitHub Pages avec Leaflet + couches dynamiques depuis ce repo
