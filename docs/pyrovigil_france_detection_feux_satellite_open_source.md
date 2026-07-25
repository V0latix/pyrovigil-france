# Détection rapide des feux de forêt en France avec données spatiales open source

_Date de rédaction : 22 juillet 2026_  
_Périmètre initial : France métropolitaine + Corse_  
_Nom de travail proposé : **PyroVigil France**_

---

## 1. Résumé décisionnel

Ton idée est réaliste : il existe des données ouvertes spatiales qui permettent de détecter des feux actifs en France, principalement via des capteurs thermiques embarqués sur satellites. En revanche, il faut être précis sur ce que l’on peut promettre :

- on peut construire un outil qui **détecte et affiche automatiquement des anomalies thermiques compatibles avec des feux** ;
- on peut réduire les faux positifs en croisant ces anomalies avec les forêts, la végétation, les zones industrielles, la météo et l’historique ;
- on ne peut pas garantir une détection instantanée de tous les départs de feu, car les satellites ne voient pas en permanence chaque point, et les nuages, fumées, pixels trop gros ou petits feux peuvent créer des faux négatifs.

La meilleure stratégie pour un MVP France est :

1. **NASA FIRMS VIIRS/MODIS** pour récupérer des points de feux actifs quasi temps réel.
2. **EFFIS / Copernicus** pour comparer, visualiser et enrichir avec danger feu, surfaces brûlées, couches européennes.
3. **BD Forêt IGN ou Masque Forêt IGN** pour filtrer les points qui tombent dans ou près d’espaces forestiers.
4. **Météo-France / Open-Meteo** pour récupérer vent, température, humidité, sécheresse approximative ou danger public.
5. **PostGIS** pour stocker, dédupliquer, clusteriser et scorer les événements.
6. **MapLibre / Leaflet** pour la carte.
7. **Alerting Discord / email / webhook** seulement sur les événements à score élevé.

Conclusion sur l’existant : il y a déjà des services publics ou grand public qui affichent les feux actifs ou le risque, mais je n’ai pas trouvé de projet français open-source qui fasse exactement : **fusion satellite open data + filtrage France + scoring opérationnel + API développeur + alertes configurables**.

---

## 2. Objectif produit

Construire un outil qui surveille la France et détecte automatiquement des départs de feu probables à partir de données spatiales ouvertes.

### Objectif MVP

Créer une application web qui :

- récupère les anomalies thermiques récentes sur la France ;
- filtre les événements non pertinents ;
- affiche les points et clusters sur une carte ;
- calcule un score de probabilité / priorité ;
- alerte si un événement récent, fort et plausible apparaît en zone forestière ou végétalisée ;
- conserve l’historique pour backtesting et amélioration du modèle.

### Ce que le MVP ne fait pas encore

- Il ne remplace pas les pompiers, SDIS, préfectures ou systèmes officiels.
- Il ne prétend pas confirmer juridiquement un incendie.
- Il ne fait pas encore de computer vision sur images brutes Meteosat/Sentinel.
- Il ne détecte pas forcément les micro-départs avant qu’ils soient visibles thermiquement depuis l’espace.

---

## 3. Projets existants et positionnement

### 3.1 NASA FIRMS

**Type :** service public mondial de détection de feux actifs.  
**Portée :** monde entier, donc France incluse.  
**Sources :** MODIS, VIIRS, Landsat, et certaines sources géostationnaires selon zones/produits.  
**Accès :** API, CSV, KML, Shapefile, carte web.  
**Utilité pour ton projet :** source principale du MVP.

FIRMS donne accès à des points de feux actifs/hotspots en quasi temps réel. C’est probablement le meilleur point de départ pour un prototype, car l’API est simple et les données sont déjà prétraitées.

Liens :
- https://firms.modaps.eosdis.nasa.gov/
- https://firms.modaps.eosdis.nasa.gov/api/
- https://firms.modaps.eosdis.nasa.gov/api/area/

### 3.2 EFFIS — European Forest Fire Information System

**Type :** système européen officiel Copernicus/JRC.  
**Portée :** Europe et pays voisins, donc France incluse.  
**Fonctions :** situation courante, feux actifs, danger feu, surfaces brûlées, historique, statistiques.  
**Utilité :** source de comparaison, enrichissement et validation.

EFFIS est déjà très proche du besoin côté institutionnel. Il fournit un viewer “Current Situation” avec feux actifs, danger feu et couches associées. Ton angle différenciant ne doit donc pas être “faire une carte de feux”, mais plutôt :

- produire une stack open-source déployable ;
- donner une API claire ;
- intégrer des règles de filtrage France ;
- créer de l’alerting ;
- préparer un dataset pour machine learning ;
- rendre le système hackable par développeur.

Liens :
- https://effis.emergency.copernicus.eu/
- https://forest-fire.emergency.copernicus.eu/applications
- https://forest-fire.emergency.copernicus.eu/applications/data-and-services

### 3.3 Météo-France — Météo des forêts

**Type :** information publique de danger feu.  
**Portée :** France.  
**Fonction :** niveau de danger départemental ou zonal selon conditions météo et état de sécheresse de la végétation.  
**Utilité :** priorisation du risque, pas détection d’un feu actif.

Météo des forêts est très utile pour ton score, mais ce n’est pas un système de détection active depuis l’espace. Il répond à la question : “où les conditions sont propices au départ et à la propagation ?”, pas “où un feu vient-il de démarrer ?”.

Liens :
- https://meteofrance.com/meteo-des-forets
- https://meteofrance.com/comprendre-la-vigilance/meteo-des-forets-informer-sensibiliser-le-public-au-danger-incendie

### 3.4 BDIFF — Base de Données sur les Incendies de Forêts en France

**Type :** base nationale historique.  
**Portée :** France.  
**Fonction :** centralise les données sur les incendies de forêt depuis 2006.  
**Utilité :** backtesting, validation historique, analyse statistique.

BDIFF est importante pour entraîner ou évaluer ton modèle, mais elle n’est pas faite pour l’alerte satellite immédiate.

Liens :
- https://bdiff.agriculture.gouv.fr/
- https://bdiff.agriculture.gouv.fr/aide/generalites

### 3.5 Pyronear

**Type :** projet open-source français de détection précoce.  
**Technologie :** caméras au sol + deep learning local/edge.  
**Portée :** France, avec déploiements et collaborations SDIS.  
**Utilité :** très proche du thème, mais pas satellite-first.

Pyronear est probablement le projet français open-source le plus proche philosophiquement. Mais il détecte plutôt la fumée depuis des caméras fixes que des anomalies thermiques depuis l’espace. Il est donc complémentaire à ton idée.

Liens :
- https://pyronear.org/
- https://github.com/pyronear/pyro-vision
- https://www.data.gouv.fr/organizations/pyronear/
- https://www.data.gouv.fr/datasets/images-de-departs-de-feux-sdis-2024

### 3.6 Feux de Forêt / feuxdeforet.fr

**Type :** carte et communauté autour du risque feux de forêt.  
**Portée :** France.  
**Fonction :** carte des feux en cours, vigilance, historique, ressources DFCI selon pages.  
**Utilité :** référence produit grand public / benchmark UX.

Lien :
- https://feuxdeforet.fr/cartes/feux/

### 3.7 Feux.net

**Type :** carte grand public France.  
**Fonction :** agrégation de sources comme EFFIS et Météo-France selon le site.  
**Utilité :** benchmark, pas forcément un outil open-source développeur.

Lien :
- https://feux.net/incendies/carte-feux-france/

### 3.8 OpenFireMap

**Type :** cartographie des ressources de lutte contre l’incendie à partir d’OpenStreetMap.  
**Fonction :** hydrants, points d’eau, casernes, ressources.  
**Utilité :** complément opérationnel, mais pas détection de feux.

Liens :
- https://openfiremap.org/
- https://wiki.openstreetmap.org/wiki/OpenFireMap

### 3.9 Solutions commerciales internationales

Il existe des solutions privées de surveillance incendie par satellite ou caméra, par exemple des constellations thermiques privées, des systèmes IA de caméras ou des services d’alerte. Elles peuvent être meilleures en latence ou précision, mais elles ne répondent pas à ton critère “open source / open data”.

Exemples de catégories :
- satellites thermiques commerciaux ;
- réseaux de caméras avec IA ;
- services d’alerte assurantiels / gouvernementaux ;
- plateformes de gestion de crise.

### 3.10 Conclusion concurrentielle

Ce qui existe déjà :

- cartes publiques ou semi-publiques ;
- services institutionnels Europe/France ;
- systèmes caméra open-source ;
- bases historiques ;
- outils météo de risque.

Ce que je n’ai pas trouvé exactement :

> Un projet open-source français, orienté développeur, qui ingère automatiquement des données satellite ouvertes, filtre sur les forêts françaises, clusterise les anomalies, calcule un score de priorité et expose une API + alertes.

C’est donc un angle pertinent, à condition de bien positionner le projet comme **outil d’aide à la détection et de recherche/prototypage**, pas comme système officiel d’alerte civile.

---

## 4. Sources de données recommandées

### 4.1 Données de feux actifs

| Source | Résolution | Latence | Couverture | Usage |
|---|---:|---:|---|---|
| NASA FIRMS VIIRS S-NPP NRT | 375 m | souvent < quelques heures | Monde | source principale |
| NASA FIRMS VIIRS NOAA-20/21 NRT | 375 m | souvent < quelques heures | Monde | redondance |
| NASA FIRMS MODIS Terra/Aqua NRT | 1 km | souvent < quelques heures | Monde | historique et complément |
| EFFIS Active Fires | MODIS/VIIRS/Sentinel-3 selon couches | NRT | Europe | comparaison/validation |
| Sentinel-3 SLSTR FRP | ~1 km | < 3 h selon EUMETSAT | Monde/Europe | complément thermique |
| MTG / Meteosat FRP | ~1 km à 3 km selon produit | 10 min théorique produit | Europe/Afrique | phase avancée, très utile pour France |

### 4.2 Données de forêt et occupation du sol

| Source | Usage |
|---|---|
| IGN BD Forêt | meilleure source France pour zones forestières |
| IGN Masque Forêt | filtre simple forêt / non-forêt |
| CORINE Land Cover | occupation du sol européenne, facile pour MVP |
| ONF Forêts publiques | utile mais ne couvre que les forêts publiques |
| OpenStreetMap | routes, zones urbaines, points d’eau, casernes, voies d’accès |

Sources :
- https://www.data.gouv.fr/datasets/bd-foret-r/
- https://www.data.gouv.fr/datasets/masque-foret/
- https://www.data.gouv.fr/datasets/corine-land-cover-occupation-des-sols-en-france/
- https://www.data.gouv.fr/datasets/forets-publiques-diffusion-publique/

### 4.3 Données météo

| Source | Usage |
|---|---|
| Météo-France Météo des forêts | niveau de danger officiel public |
| API Vigilance Météo-France | vigilance météo départementale |
| Open-Meteo | météo horaire simple sans clé pour usage non commercial |
| ECMWF / ERA5 / Copernicus Climate Data Store | historique et réanalyse pour backtesting |
| EFFIS Fire Danger Forecast | danger feu européen |

Sources :
- https://meteofrance.com/meteo-des-forets
- https://www.data.gouv.fr/dataservices/api-bulletin-vigilance
- https://open-meteo.com/
- https://forest-fire.emergency.copernicus.eu/applications

### 4.4 Données historiques et validation

| Source | Usage |
|---|---|
| BDIFF | historique officiel France |
| EFFIS Fire History | historique européen |
| Copernicus EMS Rapid Mapping | périmètres post-événement pour gros feux |
| Sentinel-2 / Landsat | images avant/après, indices NBR/dNBR |

---

## 5. Architecture cible

### 5.1 Vue d’ensemble

```text
                ┌──────────────────────────────┐
                │ NASA FIRMS API                │
                │ VIIRS / MODIS active fires    │
                └───────────────┬──────────────┘
                                │
                ┌───────────────▼──────────────┐
                │ Ingestion scheduler           │
                │ Python / cron / worker        │
                └───────────────┬──────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────┐
│ PostgreSQL + PostGIS                                    │
│ - raw_hotspots                                          │
│ - fire_events                                           │
│ - forests                                               │
│ - weather_snapshots                                     │
│ - alerts                                                │
└──────────────────────┬──────────────────────────────────┘
                       │
          ┌────────────┼─────────────┐
          │            │             │
          ▼            ▼             ▼
  Filtering      Clustering      Scoring
  forest mask    DBSCAN/time     risk priority
          │            │             │
          └────────────┼─────────────┘
                       ▼
              ┌────────────────┐
              │ REST API        │
              │ FastAPI         │
              └───────┬────────┘
                      │
       ┌──────────────┼────────────────┐
       ▼              ▼                ▼
  Web map        Discord/email     Exports
  MapLibre       webhook alerts    GeoJSON/CSV
```

### 5.2 Stack technique recommandée

#### Option simple et robuste

- Backend : Python + FastAPI
- Worker : Python + APScheduler ou cron
- Base : PostgreSQL + PostGIS
- Front : Next.js + MapLibre GL
- Déploiement : Docker Compose
- Alertes : Discord webhook + email
- Carto : tuiles IGN / OpenStreetMap / MapTiler selon contraintes
- Hébergement : VPS, Fly.io, Railway, Render, Scaleway, ou Supabase + Edge Functions

#### Option Supabase

Une option très propre :

- Supabase PostgreSQL avec extension PostGIS ;
- Edge Function ou petit worker Python externe pour ingestion ;
- Next.js pour dashboard ;
- Discord webhook pour notifications ;
- stockage fichiers dans Supabase Storage si besoin d’exports.

---

## 6. Modèle de données PostGIS

### 6.1 Tables principales

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE raw_hotspots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    satellite TEXT,
    instrument TEXT,
    acquisition_time TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOGRAPHY(POINT, 4326) GENERATED ALWAYS AS (
        ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
    ) STORED,
    brightness DOUBLE PRECISION,
    bright_ti4 DOUBLE PRECISION,
    bright_ti5 DOUBLE PRECISION,
    frp DOUBLE PRECISION,
    confidence TEXT,
    daynight TEXT,
    raw JSONB NOT NULL,
    inserted_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source, satellite, acquisition_time, latitude, longitude)
);

CREATE INDEX raw_hotspots_geom_idx ON raw_hotspots USING GIST (geom);
CREATE INDEX raw_hotspots_acq_idx ON raw_hotspots (acquisition_time DESC);
CREATE INDEX raw_hotspots_source_idx ON raw_hotspots (source);

CREATE TABLE forests (
    id BIGSERIAL PRIMARY KEY,
    name TEXT,
    forest_type TEXT,
    source TEXT NOT NULL,
    geom GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
);

CREATE INDEX forests_geom_idx ON forests USING GIST (geom);

CREATE TABLE fire_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status TEXT NOT NULL DEFAULT 'candidate',
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    centroid GEOGRAPHY(POINT, 4326) NOT NULL,
    bbox GEOMETRY(POLYGON, 4326),
    hotspot_count INTEGER NOT NULL DEFAULT 0,
    max_frp DOUBLE PRECISION,
    sum_frp DOUBLE PRECISION,
    max_brightness DOUBLE PRECISION,
    forest_distance_m DOUBLE PRECISION,
    in_forest BOOLEAN DEFAULT FALSE,
    department_code TEXT,
    risk_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'low',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX fire_events_centroid_idx ON fire_events USING GIST (centroid);
CREATE INDEX fire_events_last_seen_idx ON fire_events (last_seen DESC);
CREATE INDEX fire_events_score_idx ON fire_events (risk_score DESC);

CREATE TABLE event_hotspots (
    event_id UUID REFERENCES fire_events(id) ON DELETE CASCADE,
    hotspot_id UUID REFERENCES raw_hotspots(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, hotspot_id)
);

CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES fire_events(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    payload JSONB NOT NULL,
    sent_at TIMESTAMPTZ DEFAULT now(),
    delivery_status TEXT DEFAULT 'sent'
);
```

### 6.2 Pourquoi garder `raw_hotspots` ?

Il faut conserver les données brutes pour :

- rejouer les détections ;
- tester d’autres seuils ;
- auditer les faux positifs ;
- entraîner un modèle ;
- comparer FIRMS vs EFFIS vs BDIFF.

---

## 7. Ingestion NASA FIRMS

### 7.1 Sources FIRMS à utiliser

Pour la France, commencer par :

- `VIIRS_SNPP_NRT`
- `VIIRS_NOAA20_NRT`
- `VIIRS_NOAA21_NRT`
- `MODIS_NRT`

Selon disponibilité exacte dans ton compte FIRMS, vérifier la liste des sources via la documentation FIRMS.

### 7.2 Bounding box France métropolitaine

Pour un MVP France métropolitaine + Corse :

```text
min_lon = -5.5
min_lat = 41.0
max_lon = 10.0
max_lat = 51.5
```

Cela inclut la Corse, mais aussi des marges Espagne, Italie, Suisse, Belgique, Allemagne. On filtrera ensuite par polygone France.

### 7.3 Exemple d’appel API FIRMS

Format général :

```text
https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{WEST},{SOUTH},{EAST},{NORTH}/{DAY_RANGE}
```

Exemple :

```bash
curl "https://firms.modaps.eosdis.nasa.gov/api/area/csv/$FIRMS_MAP_KEY/VIIRS_SNPP_NRT/-5.5,41.0,10.0,51.5/1"
```

### 7.4 Script Python d’ingestion

```python
import os
import io
import json
import requests
import pandas as pd
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

FIRMS_MAP_KEY = os.environ["FIRMS_MAP_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]

BBOX_FRANCE = "-5.5,41.0,10.0,51.5"

SOURCES = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT",
]

engine = create_engine(DATABASE_URL)

def fetch_firms_csv(source: str, day_range: int = 1) -> pd.DataFrame:
    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{FIRMS_MAP_KEY}/{source}/{BBOX_FRANCE}/{day_range}"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    if not response.text.strip() or response.text.startswith("No fires"):
        return pd.DataFrame()

    df = pd.read_csv(io.StringIO(response.text))
    df["source"] = "FIRMS"
    df["firms_source"] = source
    return df

def parse_acquisition_time(row) -> datetime:
    acq_date = str(row["acq_date"])
    acq_time = str(row["acq_time"]).zfill(4)
    dt = datetime.strptime(acq_date + acq_time, "%Y-%m-%d%H%M")
    return dt.replace(tzinfo=timezone.utc)

def optional_float(row, key):
    if key not in row or pd.isna(row[key]):
        return None
    return float(row[key])

def normalize_row(row) -> dict:
    raw = row.to_dict()
    return {
        "source": "FIRMS",
        "satellite": str(row.get("satellite", "")),
        "instrument": str(row.get("instrument", row.get("firms_source", ""))),
        "acquisition_time": parse_acquisition_time(row),
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "brightness": optional_float(row, "brightness"),
        "bright_ti4": optional_float(row, "bright_ti4"),
        "bright_ti5": optional_float(row, "bright_ti5"),
        "frp": optional_float(row, "frp"),
        "confidence": str(row.get("confidence", "")),
        "daynight": str(row.get("daynight", "")),
        "raw": json.dumps(raw),
    }

def insert_hotspots(rows: list[dict]) -> int:
    if not rows:
        return 0

    sql = text("""
        INSERT INTO raw_hotspots (
            source, satellite, instrument, acquisition_time,
            latitude, longitude, brightness, bright_ti4, bright_ti5,
            frp, confidence, daynight, raw
        )
        VALUES (
            :source, :satellite, :instrument, :acquisition_time,
            :latitude, :longitude, :brightness, :bright_ti4, :bright_ti5,
            :frp, :confidence, :daynight, CAST(:raw AS jsonb)
        )
        ON CONFLICT (source, satellite, acquisition_time, latitude, longitude)
        DO NOTHING
    """)

    inserted = 0
    with engine.begin() as conn:
        for row in rows:
            result = conn.execute(sql, row)
            inserted += result.rowcount
    return inserted

def main():
    all_rows = []
    for source in SOURCES:
        df = fetch_firms_csv(source, day_range=1)
        if df.empty:
            continue
        all_rows.extend(normalize_row(row) for _, row in df.iterrows())

    count = insert_hotspots(all_rows)
    print(f"Inserted {count} new hotspots")

if __name__ == "__main__":
    main()
```

---

## 8. Filtrage géographique France

### 8.1 Pourquoi filtrer ?

La bounding box France inclut des zones étrangères. De plus, tous les hotspots ne sont pas des feux de forêt :

- torchères industrielles ;
- centrales / aciéries ;
- zones urbaines chaudes ;
- feux agricoles ;
- pixels mal localisés ;
- surfaces très chaudes.

### 8.2 Étapes

1. Filtrer par polygone France métropolitaine + Corse.
2. Calculer distance au masque forêt.
3. Garder :
   - points dans forêt ;
   - points à moins de 500 m / 1000 m d’une forêt ;
   - points en végétation naturelle ou zone agricole sèche selon ton choix.
4. Exclure :
   - zones urbaines denses ;
   - zones industrielles ;
   - ports ;
   - sites connus de faux positifs récurrents.

### 8.3 Requête PostGIS pour distance à la forêt

```sql
WITH recent AS (
    SELECT *
    FROM raw_hotspots
    WHERE acquisition_time > now() - interval '24 hours'
),
nearest_forest AS (
    SELECT
        h.id AS hotspot_id,
        MIN(ST_Distance(h.geom, f.geom::geography)) AS forest_distance_m,
        BOOL_OR(ST_Intersects(h.geom::geometry, f.geom)) AS in_forest
    FROM recent h
    JOIN forests f
      ON ST_DWithin(h.geom, f.geom::geography, 5000)
    GROUP BY h.id
)
SELECT *
FROM nearest_forest;
```

### 8.4 Classes forestières

Pour BD Forêt / Masque Forêt, tu peux commencer sans distinguer les essences. Ensuite, tu peux ajouter un risque selon type :

- résineux : risque potentiellement plus élevé ;
- maquis / garrigue : risque élevé en zone méditerranéenne ;
- feuillus humides : risque un peu moindre ;
- landes / broussailles : risque élevé ;
- zones agricoles proches forêt : risque important, car propagation possible.

---

## 9. Clustering spatio-temporel

### 9.1 Pourquoi clusteriser ?

Un même incendie peut générer plusieurs points. Il faut regrouper les hotspots proches dans l’espace et le temps en un seul “événement feu”.

### 9.2 Règle MVP simple

Deux hotspots appartiennent au même événement s’ils sont :

- à moins de 1500 m l’un de l’autre ;
- séparés de moins de 90 minutes ;
- issus d’un point non déjà archivé.

### 9.3 Version Python DBSCAN

```python
import numpy as np
from sklearn.cluster import DBSCAN

EARTH_RADIUS_M = 6371000
EPS_M = 1500
EPS_RAD = EPS_M / EARTH_RADIUS_M

coords = np.radians(df[["latitude", "longitude"]].to_numpy())

model = DBSCAN(
    eps=EPS_RAD,
    min_samples=1,
    metric="haversine"
)

df["cluster_id"] = model.fit_predict(coords)
```

### 9.4 Version avancée

La version avancée doit faire un clustering en 3D :

- latitude ;
- longitude ;
- temps.

Une distance possible :

```text
distance = distance_spatiale_m + alpha * distance_temporelle_minutes
```

Exemple :

```text
alpha = 20 m par minute
90 minutes = 1800 m équivalent
```

---

## 10. Scoring de priorité

### 10.1 Objectif

Le score ne doit pas dire “c’est sûr à 98 % que c’est un feu”. Il doit dire :

> “Ce signal mérite-t-il d’être remonté rapidement à un humain ou à une interface d’alerte ?”

### 10.2 Score MVP

```text
score = 0

Fraîcheur :
+30 si observation < 30 min
+20 si observation < 1 h
+10 si observation < 3 h

FRP :
+25 si frp >= 100
+15 si frp >= 30
+8  si frp >= 10

Confiance :
+20 si confidence = high
+10 si confidence = nominal
-15 si confidence = low

Forêt :
+25 si dans forêt
+15 si distance forêt < 500 m
+8  si distance forêt < 1500 m
-20 si distance forêt > 5000 m

Contexte météo :
+15 si vent fort et humidité basse
+10 si Météo des forêts = risque élevé/très élevé
+5  si température élevée

Cluster :
+15 si plusieurs hotspots dans le même événement
+10 si observé par plusieurs satellites/sources
+10 si croissance temporelle du cluster

Faux positif probable :
-30 si zone industrielle
-20 si zone urbaine dense
-15 si hotspot récurrent au même endroit sans événement connu
```

### 10.3 Classes de priorité

| Score | Priorité | Action |
|---:|---|---|
| < 30 | faible | stocker seulement |
| 30–55 | moyenne | afficher sur carte |
| 55–75 | haute | alerte discrète |
| > 75 | critique | alerte immédiate |

---

## 11. API backend

### 11.1 Endpoints MVP

```text
GET /health
GET /hotspots/recent?hours=24
GET /events/recent?hours=24&min_priority=medium
GET /events/{id}
GET /events.geojson?hours=24&min_priority=medium
POST /admin/ingest/firms
POST /admin/recompute-events
POST /admin/send-test-alert
```

### 11.2 Exemple FastAPI

```python
from fastapi import FastAPI, Query
from sqlalchemy import create_engine, text
import os

app = FastAPI(title="PyroVigil France API")
engine = create_engine(os.environ["DATABASE_URL"])

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/events/recent")
def recent_events(
    hours: int = Query(24, ge=1, le=168),
    min_priority: str = "medium"
):
    sql = text("""
        SELECT
            id,
            status,
            first_seen,
            last_seen,
            ST_Y(centroid::geometry) AS latitude,
            ST_X(centroid::geometry) AS longitude,
            hotspot_count,
            max_frp,
            sum_frp,
            forest_distance_m,
            in_forest,
            department_code,
            risk_score,
            priority
        FROM fire_events
        WHERE last_seen > now() - (:hours || ' hours')::interval
        ORDER BY risk_score DESC, last_seen DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"hours": hours}).mappings().all()
    return [dict(row) for row in rows]
```

### 11.3 GeoJSON endpoint

```python
@app.get("/events.geojson")
def events_geojson(hours: int = 24):
    sql = text("""
        SELECT jsonb_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(jsonb_agg(
                jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(centroid::geometry)::jsonb,
                    'properties', jsonb_build_object(
                        'id', id,
                        'priority', priority,
                        'risk_score', risk_score,
                        'last_seen', last_seen,
                        'hotspot_count', hotspot_count,
                        'max_frp', max_frp,
                        'in_forest', in_forest,
                        'forest_distance_m', forest_distance_m
                    )
                )
            ), '[]'::jsonb)
        ) AS geojson
        FROM fire_events
        WHERE last_seen > now() - (:hours || ' hours')::interval
    """)
    with engine.connect() as conn:
        result = conn.execute(sql, {"hours": hours}).scalar()
    return result or {"type": "FeatureCollection", "features": []}
```

---

## 12. Interface web

### 12.1 Objectif UX

L’interface doit répondre en 5 secondes à :

- Où y a-t-il des signaux récents ?
- Lesquels sont les plus préoccupants ?
- Est-ce en forêt ?
- Quelle est la fraîcheur de la détection ?
- Quelle est la source ?
- L’événement est-il nouveau, stable ou en croissance ?

### 12.2 Écran principal

Carte France avec :

- points bruts désactivables ;
- clusters / événements ;
- couleur selon priorité ;
- couche forêts ;
- couche départements ;
- couche vent météo ;
- filtre temporel : 1 h, 3 h, 6 h, 24 h, 7 jours ;
- panneau latéral d’événements triés par score.

### 12.3 Code MapLibre simplifié

```tsx
import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";

export default function FireMap() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;

    const map = new maplibregl.Map({
      container: ref.current,
      style: "https://demotiles.maplibre.org/style.json",
      center: [2.5, 46.5],
      zoom: 5,
    });

    map.on("load", async () => {
      const res = await fetch("/api/events.geojson?hours=24");
      const geojson = await res.json();

      map.addSource("events", {
        type: "geojson",
        data: geojson,
      });

      map.addLayer({
        id: "events-circle",
        type: "circle",
        source: "events",
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "risk_score"],
            0, 4,
            100, 16
          ],
          "circle-color": [
            "match",
            ["get", "priority"],
            "critical", "#d00000",
            "high", "#ff7b00",
            "medium", "#ffd000",
            "#999999"
          ],
          "circle-opacity": 0.8,
        },
      });
    });

    return () => map.remove();
  }, []);

  return <div ref={ref} style={{ width: "100%", height: "100vh" }} />;
}
```

---

## 13. Alerting

### 13.1 Règles d’alerte MVP

Envoyer une alerte si :

```text
priority in ('high', 'critical')
AND last_seen < 60 minutes
AND no alert sent for same event in last 2 hours
```

### 13.2 Payload Discord

```python
import requests

def send_discord_alert(webhook_url: str, event: dict):
    content = (
        f"🔥 **Signal feu potentiel - {event['priority'].upper()}**\n"
        f"Score : {event['risk_score']:.0f}/100\n"
        f"Hotspots : {event['hotspot_count']}\n"
        f"FRP max : {event.get('max_frp')}\n"
        f"Dernière détection : {event['last_seen']}\n"
        f"Position : {event['latitude']:.5f}, {event['longitude']:.5f}\n"
        f"En forêt : {event['in_forest']}\n"
        f"Distance forêt : {event.get('forest_distance_m')} m\n"
        f"https://maps.google.com/?q={event['latitude']},{event['longitude']}"
    )

    response = requests.post(webhook_url, json={"content": content}, timeout=10)
    response.raise_for_status()
```

### 13.3 Anti-spam

Table `alerts` :

- ne pas réalerter si même événement déjà alerté récemment ;
- réalerter si le score augmente de plus de 20 points ;
- réalerter si nouveau satellite confirme ;
- réalerter si le cluster grossit fortement.

---

## 14. Backtesting et validation

### 14.1 Objectif

Mesurer :

- délai entre première détection satellite et événement connu ;
- faux positifs ;
- faux négatifs ;
- score moyen des vrais feux ;
- performance par région et saison.

### 14.2 Sources de vérité

- BDIFF pour historique France ;
- EFFIS Fire History ;
- rapports SDIS / préfectures ;
- Copernicus EMS Rapid Mapping pour gros événements ;
- articles géolocalisés seulement comme source secondaire.

### 14.3 Métriques

| Métrique | Définition |
|---|---|
| Recall événement | % de feux historiques ayant au moins un hotspot proche |
| Precision alerte | % d’alertes correspondant à un vrai feu |
| Latence détection | temps entre acquisition satellite et ingestion |
| Latence événement | temps entre départ estimé et première alerte |
| Faux positifs par jour | nombre d’alertes non pertinentes |
| Score separation | séparation des scores vrais feux vs faux positifs |

### 14.4 Matching historique

Un hotspot correspond à un incendie BDIFF si :

- distance < 2 km du lieu déclaré ;
- date même jour ou ±1 jour ;
- éventuellement même commune/département.

Pseudo-SQL :

```sql
SELECT
    b.id AS bdiff_fire_id,
    h.id AS hotspot_id,
    ST_Distance(b.geom::geography, h.geom) AS distance_m,
    h.acquisition_time
FROM bdiff_fires b
JOIN raw_hotspots h
  ON ST_DWithin(b.geom::geography, h.geom, 2000)
WHERE h.acquisition_time BETWEEN b.fire_date - interval '1 day'
                             AND b.fire_date + interval '1 day';
```

---

## 15. Roadmap machine learning

### Phase 1 — règles expertes

Pas de ML au départ. Le plus important est d’avoir :

- ingestion fiable ;
- stockage propre ;
- clustering ;
- scoring lisible ;
- interface utile.

### Phase 2 — modèle supervisé tabulaire

Entraîner un modèle type LightGBM / XGBoost sur :

- frp ;
- brightness ;
- confidence ;
- source satellite ;
- heure ;
- distance forêt ;
- occupation du sol ;
- météo ;
- historique local ;
- densité urbaine ;
- distance routes ;
- récurrence hotspot ;
- département / région / saison.

Label :

```text
1 = vrai feu probable confirmé par BDIFF/EFFIS/périmètre connu
0 = faux positif probable ou hotspot hors forêt sans événement
```

### Phase 3 — modèle spatio-temporel

Utiliser des séquences d’observations :

- hotspots successifs ;
- évolution du FRP ;
- déplacement du centroïde ;
- vent ;
- direction de propagation ;
- météo locale.

Modèles possibles :

- Random Forest / LightGBM avec features temporelles ;
- HDBSCAN + scoring ;
- modèle bayésien ;
- ConvLSTM seulement si tu construis un vrai datacube raster.

### Phase 4 — données image

Ajouter :

- Sentinel-2 avant/après ;
- Sentinel-3 SLSTR ;
- MTG/FCI FRP ;
- Sentinel-5P/CAMS fumée ou aérosols ;
- images caméra Pyronear si partenariat/données ouvertes disponibles.

---

## 16. Limites scientifiques et opérationnelles

### 16.1 Faux négatifs

Un satellite peut manquer un feu si :

- le feu est trop petit ;
- il est sous nuages ;
- il est sous canopée ;
- il démarre entre deux passages ;
- la résolution est trop grossière ;
- le signal thermique est masqué.

### 16.2 Faux positifs

Causes fréquentes :

- sites industriels ;
- torchères ;
- feux agricoles ;
- surfaces minérales très chaudes ;
- reflets ou artefacts ;
- pixels mal géolocalisés.

### 16.3 Latence

La latence totale est :

```text
latence = attente du passage satellite
        + traitement fournisseur
        + disponibilité API
        + polling de ton système
        + scoring / alerting
```

Pour un système “le plus rapide possible”, les satellites géostationnaires type MTG/Meteosat deviennent très importants, car ils observent beaucoup plus souvent l’Europe que les satellites en orbite basse.

---

## 17. Déploiement Docker Compose

### 17.1 Arborescence

```text
pyrovigil-france/
├── apps/
│   ├── api/
│   │   ├── main.py
│   │   ├── ingestion/
│   │   │   ├── firms.py
│   │   │   ├── weather.py
│   │   │   └── forests.py
│   │   ├── scoring/
│   │   │   ├── cluster.py
│   │   │   └── score.py
│   │   └── requirements.txt
│   └── web/
│       ├── app/
│       ├── package.json
│       └── next.config.js
├── data/
│   ├── forests/
│   └── boundaries/
├── db/
│   ├── init.sql
│   └── migrations/
├── docker-compose.yml
├── .env.example
└── README.md
```

### 17.2 docker-compose.yml

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    environment:
      POSTGRES_USER: pyrovigil
      POSTGRES_PASSWORD: pyrovigil
      POSTGRES_DB: pyrovigil
    ports:
      - "5432:5432"
    volumes:
      - pyrovigil_pg:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql

  api:
    build: ./apps/api
    environment:
      DATABASE_URL: postgresql+psycopg://pyrovigil:pyrovigil@db:5432/pyrovigil
      FIRMS_MAP_KEY: ${FIRMS_MAP_KEY}
      DISCORD_WEBHOOK_URL: ${DISCORD_WEBHOOK_URL}
    ports:
      - "8000:8000"
    depends_on:
      - db

  worker:
    build: ./apps/api
    command: python -m ingestion.scheduler
    environment:
      DATABASE_URL: postgresql+psycopg://pyrovigil:pyrovigil@db:5432/pyrovigil
      FIRMS_MAP_KEY: ${FIRMS_MAP_KEY}
      DISCORD_WEBHOOK_URL: ${DISCORD_WEBHOOK_URL}
    depends_on:
      - db

  web:
    build: ./apps/web
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    ports:
      - "3000:3000"
    depends_on:
      - api

volumes:
  pyrovigil_pg:
```

### 17.3 .env.example

```bash
FIRMS_MAP_KEY=your_firms_map_key
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DATABASE_URL=postgresql+psycopg://pyrovigil:pyrovigil@localhost:5432/pyrovigil
```

---

## 18. Plan de réalisation A à Z

### Semaine 1 — prototype données

- Créer repo GitHub.
- Monter Docker Compose avec PostGIS.
- Créer tables `raw_hotspots`, `fire_events`, `event_hotspots`.
- Obtenir clé NASA FIRMS.
- Écrire ingestion FIRMS.
- Stocker points récents France.
- Exposer `/hotspots/recent`.

Livrable : API qui renvoie les hotspots récents.

### Semaine 2 — carte MVP

- Créer app Next.js.
- Ajouter MapLibre.
- Afficher GeoJSON des hotspots.
- Ajouter filtre 1 h / 6 h / 24 h.
- Ajouter couleur par source et FRP.

Livrable : carte des anomalies thermiques récentes.

### Semaine 3 — forêt et scoring

- Télécharger Masque Forêt IGN ou BD Forêt.
- Importer dans PostGIS.
- Calculer distance forêt.
- Créer score MVP.
- Créer table `fire_events`.
- Regrouper hotspots en événements.

Livrable : carte d’événements scorés, pas seulement des points bruts.

### Semaine 4 — alerting

- Ajouter Discord webhook.
- Ajouter anti-spam.
- Ajouter priorités.
- Ajouter page détail événement.
- Ajouter export GeoJSON/CSV.

Livrable : système qui t’alerte sur les signaux haute priorité.

### Semaine 5 — validation

- Importer historique BDIFF.
- Rejouer quelques périodes historiques.
- Comparer les hotspots aux feux connus.
- Ajuster seuils.
- Documenter faux positifs fréquents.

Livrable : rapport de performance initial.

### Semaine 6+ — avancé

- Ajouter EFFIS WMS ou données EFFIS accessibles.
- Ajouter Sentinel-3 FRP.
- Tester MTG/FCI FRP via EUMETSAT / LSA SAF.
- Ajouter météo horaire.
- Ajouter modèle LightGBM.
- Ajouter mode “France entière” + “département surveillé”.

---

## 19. Priorités techniques

### À faire absolument

1. PostGIS propre.
2. Ingestion FIRMS robuste.
3. Déduplication.
4. Filtre France.
5. Filtre forêt.
6. Clustering.
7. Score explicable.
8. Carte.
9. Alertes.
10. Backtesting.

### À ne pas faire au début

- entraîner un gros modèle deep learning ;
- télécharger toutes les images Sentinel-2 ;
- promettre de l’alerte officielle ;
- viser temps réel absolu ;
- faire une app mobile native ;
- gérer le monde entier.

---

## 20. Risques projet

| Risque | Gravité | Mitigation |
|---|---:|---|
| Trop de faux positifs | élevée | forêt + zones industrielles + récurrence + météo |
| Latence satellite trop haute | élevée | intégrer MTG/Meteosat ensuite |
| API FIRMS indisponible ou limitée | moyenne | cache, retries, backoff |
| Données forêt lourdes | moyenne | commencer par Masque Forêt, simplifier géométries |
| Alertes inutiles | élevée | seuil haut au début, digest, anti-spam |
| Validation difficile | moyenne | BDIFF + EFFIS + quelques grands cas connus |

---

## 21. Choix recommandé pour ton premier commit

Je te conseille de créer un projet minimal comme ça :

```text
pyrovigil-france/
├── api/
│   ├── main.py
│   ├── firms_ingest.py
│   ├── scoring.py
│   └── db.py
├── web/
│   └── carte Next.js
├── db/
│   └── init.sql
├── docker-compose.yml
└── README.md
```

Et ton README doit promettre seulement :

> PyroVigil France est un prototype open-source d’aide à la détection rapide de signaux de feux de forêt en France à partir de données satellite ouvertes. Il ingère les anomalies thermiques NASA FIRMS, les filtre avec des données forestières françaises et génère des événements scorés visualisables sur une carte.

---

## 22. Références utiles

### Données feux actifs

- NASA FIRMS : https://firms.modaps.eosdis.nasa.gov/
- NASA FIRMS API : https://firms.modaps.eosdis.nasa.gov/api/
- NASA FIRMS Area API : https://firms.modaps.eosdis.nasa.gov/api/area/
- NASA Earthdata FIRMS : https://www.earthdata.nasa.gov/data/tools/firms
- EFFIS : https://effis.emergency.copernicus.eu/
- EFFIS applications : https://forest-fire.emergency.copernicus.eu/applications
- EFFIS data/services : https://forest-fire.emergency.copernicus.eu/applications/data-and-services
- Copernicus EMS data access : https://emergency.copernicus.eu/data/
- Sentinel-3 SLSTR FRP : https://user.eumetsat.int/catalogue/EO%3AEUM%3ADAT%3A0417
- MTG Fire Radiative Power : https://user.eumetsat.int/catalogue/EO%3AEUM%3ADAT%3A1156
- LSA SAF : https://lsa-saf.eumetsat.int/en/

### Données France

- Météo des forêts : https://meteofrance.com/meteo-des-forets
- Explication Météo des forêts : https://meteofrance.com/comprendre-la-vigilance/meteo-des-forets-informer-sensibiliser-le-public-au-danger-incendie
- BDIFF : https://bdiff.agriculture.gouv.fr/
- BD Forêt IGN : https://www.data.gouv.fr/datasets/bd-foret-r/
- Masque Forêt IGN : https://www.data.gouv.fr/datasets/masque-foret/
- CORINE Land Cover France : https://www.data.gouv.fr/datasets/corine-land-cover-occupation-des-sols-en-france/
- Forêts publiques ONF : https://www.data.gouv.fr/datasets/forets-publiques-diffusion-publique/

### Projets proches

- Pyronear : https://pyronear.org/
- Pyronear GitHub pyro-vision : https://github.com/pyronear/pyro-vision
- Pyronear data.gouv.fr : https://www.data.gouv.fr/organizations/pyronear/
- Images de départs de feux SDIS 2024 : https://www.data.gouv.fr/datasets/images-de-departs-de-feux-sdis-2024
- Feux de Forêt : https://feuxdeforet.fr/cartes/feux/
- Feux.net : https://feux.net/incendies/carte-feux-france/
- OpenFireMap : https://openfiremap.org/
- OpenFireMap Wiki : https://wiki.openstreetmap.org/wiki/OpenFireMap

### Recherche et datasets

- PyroNear-2024 dataset : https://arxiv.org/abs/2402.05349
- Multimodal satellite wildfire identification in Europe : https://arxiv.org/abs/2308.02508
- FCI-FireDyn / MTG fire behavior monitoring : https://arxiv.org/abs/2510.26677
- MTG-FCI Fire Event Tracker : https://arxiv.org/abs/2606.06016
- Sentinel-3 NRT FRP monitoring : https://metis.eumetsat.int/frp/
- Open-Meteo : https://open-meteo.com/

---

## 23. Décision finale

Le projet est intéressant parce qu’il ne cherche pas seulement à afficher des points rouges sur une carte. Les cartes existent déjà. Le vrai produit utile serait :

- une **pipeline open-source reproductible** ;
- une **API France-first** ;
- un **score explicable** ;
- un **filtrage anti-faux-positifs** ;
- un **système d’alertes configurables** ;
- un **dataset historique** pour améliorer la détection.

La première version doit donc être simple :

> FIRMS + PostGIS + Masque Forêt IGN + score + MapLibre + Discord.

Ensuite seulement, tu ajoutes :

> EFFIS + Sentinel-3 FRP + MTG/FCI + météo + ML.
