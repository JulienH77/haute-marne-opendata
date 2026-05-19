# 🌍 haute-marne-opendata

Centralisation et mise à jour automatique de données opendata géographiques pour la **France** et la **Haute-Marne (52)**, entièrement pilotée par **GitHub Actions** — sans QGIS ni infrastructure tierce.

---

## 📦 Données disponibles

| Couche | Zone | Fréquence | Format | Source |
|---|---|---|---|---|
| ☁️ Nuages | Monde / France / Haute-Marne | **3h** | JPG | EUMETSAT via [matteason/live-cloud-maps](https://github.com/matteason/live-cloud-maps) |
| 🌡️ Température (2m) | France / Haute-Marne | **3h** | GeoJSON + PNG | [Open-Meteo](https://open-meteo.com) |
| 🌧️ Précipitations | France / Haute-Marne | **3h** | GeoJSON + PNG | [Open-Meteo](https://open-meteo.com) |
| 💨 Vent (10m) | France / Haute-Marne | **3h** | GeoJSON + PNG | [Open-Meteo](https://open-meteo.com) |
| 👥 Population communale | Haute-Marne | Mensuelle | GeoJSON | [geo.api.gouv.fr](https://geo.api.gouv.fr) + INSEE |
| 📈 Évolution population (2015→2021) | Haute-Marne | Mensuelle | GeoJSON | INSEE |
| 🏛️ Départements France | France | Mensuelle | GeoJSON | [geo.api.gouv.fr](https://geo.api.gouv.fr) |

---

## 📁 Structure des fichiers générés

```
data/
├── clouds/
│   ├── world.jpg                       # Image nuages mondiale (4096×2048)
│   ├── france.jpg                      # Recadrée France métropolitaine
│   ├── haute-marne.jpg                 # Recadrée Haute-Marne
│   └── metadata.json                   # Timestamp, source, résolution
│
├── weather/
│   ├── france_weather.geojson          # Grille France (~600 pts) — toutes variables
│   ├── haute-marne_weather.geojson     # Grille Haute-Marne (~120 pts) — toutes variables
│   ├── france_temperature_2m.png       # Raster interpolé
│   ├── france_precipitation.png
│   ├── france_cloud_cover.png
│   ├── france_wind_speed_10m.png
│   ├── haute-marne_temperature_2m.png
│   ├── haute-marne_precipitation.png
│   ├── haute-marne_cloud_cover.png
│   ├── haute-marne_wind_speed_10m.png
│   └── metadata.json
│
└── population/
    ├── haute-marne_communes.geojson    # Communes + population + évolution
    ├── france_departements.geojson     # Départements + population
    └── metadata.json
```

---

## ⚙️ GitHub Actions

### `update-dynamic.yml` — toutes les 3h
Déclenché à **00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC** :
- Télécharge et recadre l'image nuages mondiale
- Interroge l'API Open-Meteo pour une grille de points sur France et Haute-Marne
- Génère les GeoJSON + PNG rasters interpolés
- Commit & push automatique avec `[skip ci]`

### `update-static.yml` — le 1er de chaque mois
- Récupère les communes de Haute-Marne (géométries + population)
- Télécharge les fichiers populations légales INSEE
- Calcule les évolutions et génère les GeoJSON enrichis

---

## 🚀 Mise en place

### 1. Forker / cloner ce dépôt

```bash
git clone https://github.com/<vous>/haute-marne-opendata.git
cd haute-marne-opendata
```

### 2. Activer les permissions GitHub Actions

Dans **Settings → Actions → General** :
- ✅ "Allow all actions"
- ✅ "Read and write permissions" (sous *Workflow permissions*)

### 3. Premier lancement manuel

Dans l'onglet **Actions**, déclencher manuellement :
- `Mise à jour données dynamiques` → génère nuages + météo
- `Mise à jour données statiques` → génère population

### 4. Utilisation locale (optionnel)

```bash
pip install -r scripts/requirements.txt
python scripts/fetch_clouds.py
python scripts/fetch_weather.py
python scripts/fetch_population.py
```

---

## 🗺️ Intégration QGIS

Les fichiers GeoJSON sont directement utilisables dans QGIS :

```
# Ajouter une couche depuis URL brute GitHub :
https://raw.githubusercontent.com/<vous>/haute-marne-opendata/main/data/weather/haute-marne_weather.geojson
```

Pour les images raster (PNG + JPG), utiliser **"Ajouter une couche raster"** avec le world file correspondant (coordonnées dans `metadata.json`).

---

## 📐 Zones de découpage

| Zone | Bbox (lon_min, lat_min, lon_max, lat_max) | CRS |
|---|---|---|
| Monde | -180, -90, 180, 90 | EPSG:4326 |
| France métropolitaine | -5.14, 41.33, 9.56, 51.09 | EPSG:4326 |
| Haute-Marne | 4.70, 47.50, 5.95, 48.65 | EPSG:4326 |

---

## 📜 Licences et attributions

| Source | Licence | Attribution requise |
|---|---|---|
| EUMETSAT (nuages via matteason) | CC0 1.0 | `Contains modified EUMETSAT data` |
| Open-Meteo | CC BY 4.0 | `Weather data by Open-Meteo.com` |
| geo.api.gouv.fr | Licence Ouverte 2.0 | `geo.api.gouv.fr — Etalab` |
| INSEE | Licence Ouverte 2.0 | `© INSEE` |

---

## 🔭 Données supplémentaires envisagées

- **Qualité de l'air** : [ATMO Grand Est](https://www.atmo-grandest.eu/) (API sur demande gratuite)
- **Hydrologie** : [Hub'Eau](https://hubeau.eaufrance.fr/) — débits Marne/Aube en temps réel
- **Risques naturels** : [Géorisques BRGM](https://www.georisques.gouv.fr/) — inondations, mouvements de terrain
- **Occupation du sol** : [Corine Land Cover](https://www.data.gouv.fr/fr/datasets/corine-land-cover-occupation-des-sols-en-france/) (millésime 2018)
- **Ensoleillement** : Open-Meteo `sunshine_duration` / `shortwave_radiation`
