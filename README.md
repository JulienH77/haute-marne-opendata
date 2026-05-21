# 🌍 haute-marne-opendata

Centralisation et mise à jour automatique de données opendata géographiques pour la **France** et la **Haute-Marne (52)**, entièrement pilotée par **GitHub Actions** — sans serveur ni infrastructure tierce.

---

## 📦 Données disponibles

| Couche | Zone | Fréquence | Formats | Source |
|---|---|---|---|---|
| ☁️ Nuages | Monde / France / HM | **3h** | JPG + GeoTIFF EPSG:4326 | EUMETSAT via [matteason](https://github.com/matteason/live-cloud-maps) |
| 🌡️ Température (2m) | France / HM | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 🌧️ Précipitations | France / HM | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 💨 Vent (10m) | France / HM | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 🌫️ Couverture nuageuse | France / HM | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 👥 Population communale | HM | Mensuelle | GeoJSON | geo.api.gouv.fr + INSEE |
| 🏛️ Départements France | France | Mensuelle | GeoJSON | geo.api.gouv.fr |

---

## 📁 Fichiers générés

```
data/
├── clouds/
│   ├── world.jpg                       ← Nuages mondiaux (JPEG, aperçu)
│   ├── france.jpg / france.tif         ← France (JPEG + GeoTIFF EPSG:4326)
│   ├── haute-marne.jpg / haute-marne.tif
│   └── metadata.json
│
├── weather/
│   ├── france_weather.geojson          ← ~2300 points, résolution 0.25°
│   ├── france_weather.csv
│   ├── france_temperature_2m.tif       ← Raster lissé, 600×400 px, EPSG:4326
│   ├── france_precipitation.tif
│   ├── france_cloud_cover.tif
│   ├── france_wind_speed_10m.tif
│   ├── haute-marne_weather.geojson     ← ~400 points, résolution 0.05°
│   ├── haute-marne_weather.csv
│   ├── haute-marne_temperature_2m.tif  ← Raster lissé, 600×600 px, EPSG:4326
│   ├── haute-marne_precipitation.tif
│   ├── haute-marne_cloud_cover.tif
│   ├── haute-marne_wind_speed_10m.tif
│   └── metadata.json
│
└── population/
    ├── haute-marne_communes.geojson
    ├── france_departements.geojson
    └── metadata.json
```

---

## 👥 Champs population dans `haute-marne_communes.geojson`

| Champ | Description |
|---|---|
| `population` | Population la plus récente (INSEE) |
| `population_source` | Ex : `INSEE 2021` |
| `population_2023` | Population municipale INSEE 2023 (si disponible) |
| `population_2021` | Population municipale INSEE 2021 |
| `population_2016` | Population municipale INSEE 2016 |
| `population_2011` | Population municipale INSEE 2011 |
| `population_2006` | Population municipale INSEE 2006 |
| `population_1999` | Population municipale INSEE 1999 |
| `population_1990` | Population municipale INSEE 1990 |
| `population_1982` | Population municipale INSEE 1982 |
| `population_1975` | Population municipale INSEE 1975 |
| `population_1968` | Population municipale INSEE 1968 |
| `evolution_absolue` | Variation (hab.) entre l'année la plus ancienne et la plus récente |
| `evolution_pct` | Variation (%) sur la même période |
| `evolution_periode` | Ex : `1968–2021` |

**Sources** : deux fichiers INSEE combinés :
- `base-cc-evol-struct-pop-2021` → 2006, 2011, 2016, 2021
- Séries historiques INSEE → 1968, 1975, 1982, 1990, 1999

---

## 🌡️ Rasters météo

Générés avec **RegularGridInterpolator** (bilinéaire sur grille régulière, sans artefacts triangulaires) + **lissage gaussien**. Format GeoTIFF Float32, EPSG:4326, nodata=-9999, compression LZW.

| Zone | Résolution source | Taille GeoTIFF sortie |
|---|---|---|
| France | 0.25° (~27 km) | 600×400 px |
| Haute-Marne | 0.05° (~5 km) | 600×600 px |

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h

Deux **jobs parallèles** (pas de timeout global) :

| Job | Timeout | Contenu |
|---|---|---|
| `fetch-clouds` | 10 min | Nuages → JPG + GeoTIFF france/HM |
| `fetch-weather` | 30 min | Météo → GeoJSON + CSV + GeoTIFF (8 rasters) |

> `world.tif` non généré (36 Mo, impraticable — utiliser world.jpg ou les WMS listés dans metadata.json).

### `update-static.yml` — le 1er de chaque mois

- Communes Haute-Marne avec séries historiques 1968→2021
- Départements France avec géométries

---

## 🗺️ Intégration QGIS 3.40

### Couche vecteur (GeoJSON) — `Couche > Ajouter une couche vecteur > Protocole HTTP`

```
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/haute-marne_communes.geojson
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/france_departements.geojson
```

### Couche raster (GeoTIFF) — `Couche > Ajouter une couche raster > Protocole HTTP`

```
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_temperature_2m.tif
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/france_cloud_cover.tif
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/clouds/france.tif
```

### WMS nuages haute résolution — `Couche > Ajouter une couche WMS/WMTS`

| Service | URL |
|---|---|
| NASA GIBS MODIS (250m, sans clé) | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` → `MODIS_Terra_CorrectedReflectance_TrueColor` |
| EUMETSAT Meteosat visible | `https://eumetview.eumetsat.int/geoserver/wms` → `BAS:METEOSAT_0DEG_VIS006` |

---

## 📐 Zones de découpage

| Zone | Bbox | CRS |
|---|---|---|
| Monde | -180, -90, 180, 90 | EPSG:4326 |
| France métro | -5.14, 41.33, 9.56, 51.09 | EPSG:4326 |
| Haute-Marne | 4.70, 47.50, 5.95, 48.65 | EPSG:4326 |

---

## 📜 Licences

| Source | Licence |
|---|---|
| EUMETSAT (nuages) | CC0 1.0 — *Contains modified EUMETSAT data* |
| Open-Meteo | CC BY 4.0 — *Weather data by Open-Meteo.com* |
| geo.api.gouv.fr | Licence Ouverte 2.0 (Etalab) |
| INSEE | Licence Ouverte 2.0 (© INSEE) |

---

## 🔧 Lancement manuel
**Actions → workflow → Run workflow**

## 🚀 Installation (fork)
1. Forker ce dépôt
2. **Settings → Actions → General → Read and write permissions** ✅
3. Lancer manuellement les deux workflows

---

## 🔭 Évolutions envisagées
- **Hydrologie** : Hub'Eau — débits Marne/Aube en temps réel
- **Qualité de l'air** : ATMO Grand Est
- **Risques naturels** : Géorisques BRGM
- **Ensoleillement** : Open-Meteo `sunshine_duration`
