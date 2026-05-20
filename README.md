# 🌍 haute-marne-opendata

Centralisation et mise à jour automatique de données opendata géographiques pour la **France** et la **Haute-Marne (52)**, entièrement pilotée par **GitHub Actions** — sans serveur ni infrastructure tierce.

---

## 📦 Données disponibles

| Couche | Zone | Fréquence | Formats | Source |
|---|---|---|---|---|
| ☁️ Nuages | Monde / France / Haute-Marne | **3h** | JPG + **GeoTIFF EPSG:4326** | EUMETSAT via [matteason/live-cloud-maps](https://github.com/matteason/live-cloud-maps) |
| 🌡️ Température (2m) | France / Haute-Marne | **3h** | GeoJSON + CSV + **GeoTIFF EPSG:4326** | [Open-Meteo](https://open-meteo.com) |
| 🌧️ Précipitations | France / Haute-Marne | **3h** | GeoJSON + CSV + **GeoTIFF EPSG:4326** | [Open-Meteo](https://open-meteo.com) |
| 💨 Vent (10m) | France / Haute-Marne | **3h** | GeoJSON + CSV + **GeoTIFF EPSG:4326** | [Open-Meteo](https://open-meteo.com) |
| ☁️ Couverture nuageuse | France / Haute-Marne | **3h** | GeoJSON + CSV + **GeoTIFF EPSG:4326** | [Open-Meteo](https://open-meteo.com) |
| 👥 Population communale | Haute-Marne | Mensuelle | GeoJSON | [geo.api.gouv.fr](https://geo.api.gouv.fr) + INSEE |
| 📈 Évolution population | Haute-Marne | Mensuelle | GeoJSON | INSEE via [data.gouv.fr](https://data.gouv.fr) |
| 🏛️ Départements France | France | Mensuelle | GeoJSON | [geo.api.gouv.fr](https://geo.api.gouv.fr) |

---

## 📁 Structure des fichiers générés

```
data/
├── clouds/
│   ├── world.jpg / world.tif           ← Image nuages mondiale
│   ├── france.jpg / france.tif         ← Recadrée France métropolitaine
│   ├── haute-marne.jpg / haute-marne.tif  ← Recadrée Haute-Marne (upscalée)
│   └── metadata.json                   ← Timestamp, bbox, alternatives WMS
│
├── weather/
│   ├── france_weather.geojson          ← Grille ~630 pts France (toutes variables)
│   ├── france_weather.csv              ← Même données en CSV
│   ├── france_temperature_2m.tif       ← Raster interpolé EPSG:4326
│   ├── france_precipitation.tif
│   ├── france_cloud_cover.tif
│   ├── france_wind_speed_10m.tif
│   ├── haute-marne_weather.geojson     ← Grille ~323 pts Haute-Marne
│   ├── haute-marne_weather.csv
│   ├── haute-marne_temperature_2m.tif
│   ├── haute-marne_precipitation.tif
│   ├── haute-marne_cloud_cover.tif
│   ├── haute-marne_wind_speed_10m.tif
│   └── metadata.json
│
└── population/
    ├── haute-marne_communes.geojson    ← Communes + pop + évolution inter-annuelle
    ├── france_departements.geojson     ← Départements + population
    └── metadata.json
```

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h
Déclenché à **00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC** :
- Télécharge et recadre l'image nuages mondiale (8192×4096) → JPG + GeoTIFF
- Interroge Open-Meteo pour une grille de points sur France et Haute-Marne
- Génère GeoJSON + CSV + GeoTIFF rasters interpolés pour chaque variable
- Commit & push automatique avec `[skip ci]`

### `update-static.yml` — le 1er de chaque mois
- Récupère les communes de Haute-Marne + leurs géométries
- Télécharge les fichiers populations légales INSEE (via data.gouv.fr)
- Calcule les évolutions et génère les GeoJSON enrichis

---

## 🗺️ Intégration QGIS 3.40

### Charger un GeoJSON directement depuis GitHub

`Couche > Ajouter une couche vecteur > Protocole (HTTP/HTTPS/cloud)` :

```
# Météo Haute-Marne (points avec température, pluie, vent, nuages)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson

# Communes Haute-Marne (population + évolution)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/haute-marne_communes.geojson

# Départements France
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/population/france_departements.geojson
```

### Charger un GeoTIFF météo depuis GitHub

`Couche > Ajouter une couche raster > Protocole (HTTP/HTTPS/cloud)` :

```
# Température Haute-Marne (raster interpolé, EPSG:4326)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/haute-marne_temperature_2m.tif

# Précipitations France
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/weather/france_precipitation.tif

# Nuages France (image satellite géoréférencée)
https://raw.githubusercontent.com/JulienH77/haute-marne-opendata/main/data/clouds/france.tif
```

### Ajouter un flux WMS nuages haute résolution

Pour une image satellite nuages haute résolution (meilleure que le GeoTIFF inclus) :

`Couche > Ajouter une couche WMS/WMTS > Nouvelle connexion` :

| Service | URL |
|---|---|
| **NASA GIBS MODIS Terra** (250m, sans clé) | `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi` |
| **EUMETSAT Meteosat visible** | `https://eumetview.eumetsat.int/geoserver/wms` |

Couches à sélectionner :
- NASA : `MODIS_Terra_CorrectedReflectance_TrueColor`
- EUMETSAT : `BAS:METEOSAT_0DEG_VIS006`

---

## 🔧 Lancement manuel

Dans l'onglet **Actions** du dépôt :
1. Cliquer sur le workflow souhaité dans la colonne gauche
2. Bouton **"Run workflow"** → **"Run workflow"** (branche `main`)

---

## 🚀 Installation (nouveau fork)

1. Forker ce dépôt
2. **Settings → Actions → General** → cocher **"Read and write permissions"**
3. Lancer manuellement les deux workflows pour le premier remplissage

---

## 📐 Zones de découpage

| Zone | Bbox (lon_min, lat_min, lon_max, lat_max) | CRS |
|---|---|---|
| Monde | -180, -90, 180, 90 | EPSG:4326 |
| France métropolitaine | -5.14, 41.33, 9.56, 51.09 | EPSG:4326 |
| Haute-Marne | 4.70, 47.50, 5.95, 48.65 | EPSG:4326 |

---

## 📜 Licences et attributions

| Source | Licence | Attribution |
|---|---|---|
| EUMETSAT (nuages via matteason) | CC0 1.0 | `Contains modified EUMETSAT data` |
| Open-Meteo | CC BY 4.0 | `Weather data by Open-Meteo.com` |
| geo.api.gouv.fr | Licence Ouverte 2.0 | `geo.api.gouv.fr — Etalab` |
| INSEE | Licence Ouverte 2.0 | `© INSEE` |

---

## 🔭 Données supplémentaires envisagées

- **Hydrologie** : [Hub'Eau](https://hubeau.eaufrance.fr/) — débits Marne/Aube en temps réel (API REST, sans clé)
- **Qualité de l'air** : [ATMO Grand Est](https://www.atmo-grandest.eu/) — API sur demande gratuite
- **Risques naturels** : [Géorisques BRGM](https://www.georisques.gouv.fr/)
- **Ensoleillement** : Open-Meteo `sunshine_duration` / `shortwave_radiation`
