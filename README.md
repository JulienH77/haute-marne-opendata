# 🌍 haute-marne-opendata

Centralisation et mise à jour automatique de données opendata géographiques pour la **France** et la **Haute-Marne (52)**, entièrement pilotée par **GitHub Actions** — sans serveur ni infrastructure tierce.

---

## 📦 Données disponibles

| Couche | Zone | Fréquence | Formats | Source |
|---|---|---|---|---|
| ☁️ Nuages | Monde / France / Haute-Marne | **3h** | JPG + GeoTIFF EPSG:4326 | EUMETSAT via [matteason](https://github.com/matteason/live-cloud-maps) |
| 🌡️ Température (2m) | France / Haute-Marne | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 🌧️ Précipitations | France / Haute-Marne | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 💨 Vent (10m) | France / Haute-Marne | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 🌫️ Couverture nuageuse | France / Haute-Marne | **3h** | GeoJSON + CSV + GeoTIFF | [Open-Meteo](https://open-meteo.com) |
| 👥 Population communale | Haute-Marne | Mensuelle | GeoJSON | geo.api.gouv.fr + INSEE |
| 📈 Évolution population | Haute-Marne | Mensuelle | GeoJSON (champs intégrés) | INSEE via data.gouv.fr |
| 🏛️ Départements France | France | Mensuelle | GeoJSON | geo.api.gouv.fr |

---

## 📁 Structure des fichiers générés

```
data/
├── clouds/
│   ├── world.jpg / world.tif
│   ├── france.jpg / france.tif
│   ├── haute-marne.jpg / haute-marne.tif
│   └── metadata.json
│
├── weather/
│   ├── france_weather.geojson          ← Points grille 0.25° (~27 km)
│   ├── france_weather.csv
│   ├── france_temperature_2m.tif       ← Raster lissé EPSG:4326 (600×400 px)
│   ├── france_precipitation.tif
│   ├── france_cloud_cover.tif
│   ├── france_wind_speed_10m.tif
│   ├── haute-marne_weather.geojson     ← Points grille 0.05° (~5 km)
│   ├── haute-marne_weather.csv
│   ├── haute-marne_temperature_2m.tif  ← Raster lissé EPSG:4326 (600×600 px)
│   ├── haute-marne_precipitation.tif
│   ├── haute-marne_cloud_cover.tif
│   ├── haute-marne_wind_speed_10m.tif
│   └── metadata.json
│
└── population/
    ├── haute-marne_communes.geojson    ← Communes + population toutes années dispo
    │                                      (population_2021, population_2016,
    │                                       population_2011, evolution_pct...)
    ├── france_departements.geojson     ← Départements + géométrie + population
    └── metadata.json
```

---

## 🌡️ Détails météo

Les rasters GeoTIFF météo sont générés avec :
- **Interpolation RegularGridInterpolator** (bilinéaire sur grille régulière) — pas d'artefacts triangulaires
- **Lissage gaussien** post-interpolation pour des transitions douces
- Résolution source : **0.25°** (~27 km) pour la France, **0.05°** (~5 km) pour la Haute-Marne
- Format de sortie : GeoTIFF Float32, EPSG:4326, compression LZW, nodata=-9999

---

## 👥 Détails population

Le fichier `haute-marne_communes.geojson` contient pour chaque commune :

| Champ | Description |
|---|---|
| `population` | Population municipale (année la plus récente disponible) |
| `population_2021` | Population INSEE 2021 |
| `population_2016` | Population INSEE 2016 |
| `population_2011` | Population INSEE 2011 |
| `population_2006` | Population INSEE 2006 (si disponible) |
| `evolution_absolue` | Variation absolue (hab.) entre l'année la plus ancienne et la plus récente |
| `evolution_pct` | Variation relative (%) |
| `evolution_periode` | Ex : `2011–2021` |
| `population_source` | Source de la donnée (`INSEE 2021` ou `geo.api.gouv.fr`) |

Les données multi-années proviennent d'un **unique fichier INSEE** (`base-cc-evol-struct-pop`) qui contient historiquement plusieurs millésimes de recensement.

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h
Déclenché à **00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC** :
- Nuages → JPG + GeoTIFF (8192×4096 source)
- Météo → GeoJSON + CSV + GeoTIFF rasters lissés
- Commit & push avec `git pull --rebase` (anti-conflit)

### `update-static.yml` — le 1er de chaque mois
- Communes Haute-Marne avec toutes les années de recensement disponibles
- Départements France avec géométries

---

## 🗺️ Intégration QGIS 3.40

### Couche vecteur (GeoJSON)
`Couche > Ajouter une couche vecteur > Protocole HTTP/HTTPS` :

```
# Météo Haute-Marne (points, toutes variables)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson

# Communes Haute-Marne (population + évolution)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/haute-marne_communes.geojson

# Départements France (avec géométries)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/france_departements.geojson
```

### Couche raster (GeoTIFF)
`Couche > Ajouter une couche raster > Protocole HTTP/HTTPS` :

```
# Température Haute-Marne
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_temperature_2m.tif

# Couverture nuageuse France
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/france_cloud_cover.tif

# Nuages (image satellite géoréférencée)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/clouds/france.tif
```

### WMS haute résolution (nuages satellite)
`Couche > Ajouter une couche WMS/WMTS > Nouvelle connexion` :

| Service | URL de connexion | Couche |
|---|---|---|
| NASA GIBS MODIS Terra (250m, sans clé) | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` | `MODIS_Terra_CorrectedReflectance_TrueColor` |
| EUMETSAT Meteosat visible | `https://eumetview.eumetsat.int/geoserver/wms` | `BAS:METEOSAT_0DEG_VIS006` |

---

## 📐 Zones de découpage

| Zone | Bbox (lon_min, lat_min, lon_max, lat_max) | CRS |
|---|---|---|
| Monde | -180, -90, 180, 90 | EPSG:4326 |
| France métropolitaine | -5.14, 41.33, 9.56, 51.09 | EPSG:4326 |
| Haute-Marne | 4.70, 47.50, 5.95, 48.65 | EPSG:4326 |

---

## 🔧 Lancement manuel
Dans l'onglet **Actions** → cliquer sur le workflow → **"Run workflow"**.

## 🚀 Installation (fork)
1. Forker ce dépôt
2. **Settings → Actions → General** → **"Read and write permissions"**
3. Lancer manuellement les deux workflows

---

## 📜 Licences

| Source | Licence | Attribution |
|---|---|---|
| EUMETSAT (nuages) | CC0 1.0 | `Contains modified EUMETSAT data` |
| Open-Meteo | CC BY 4.0 | `Weather data by Open-Meteo.com` |
| geo.api.gouv.fr | Licence Ouverte 2.0 | `geo.api.gouv.fr — Etalab` |
| INSEE | Licence Ouverte 2.0 | `© INSEE` |

---

## 🔭 Évolutions envisagées

- **Hydrologie** : [Hub'Eau](https://hubeau.eaufrance.fr/) — débits Marne/Aube temps réel
- **Qualité de l'air** : [ATMO Grand Est](https://www.atmo-grandest.eu/) — API gratuite
- **Risques naturels** : [Géorisques BRGM](https://www.georisques.gouv.fr/)
- **Ensoleillement** : Open-Meteo `sunshine_duration`
