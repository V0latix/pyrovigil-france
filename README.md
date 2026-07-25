# PyroVigil France

Prototype open-source d'aide à la détection rapide de signaux de feux de forêt en France à partir de données
satellite ouvertes. Il ingère les anomalies thermiques NASA FIRMS, les filtre avec des données géographiques
françaises, les regroupe en événements scorés, les affiche sur une carte et alerte sur les signaux forts.

> **Ce n'est pas un système d'alerte officiel.** Il ne remplace ni les pompiers, ni le SDIS, ni la préfecture,
> et ne confirme pas juridiquement un incendie. En cas de feu, appelez le **18** ou le **112**.

```
FIRMS ──▶ raw_hotspots ──▶ filtre France / forêt ──▶ clustering ──▶ score ──┬──▶ API + carte
                                                                            └──▶ alerte Discord
```

## Démarrage

Aucune clé n'est nécessaire pour voir le système tourner : une fixture FIRMS locale sert de jeu de démonstration.

```bash
uv sync
uv run pyrovigil fetch-data          # télécharge les 96 départements (~1 Mo)
uv run pyrovigil ingest --fixture    # ingère le CSV de démonstration, score, alerte
uv run pyrovigil serve               # carte et API sur http://127.0.0.1:8000/
```

Puis, avec une vraie clé (gratuite et immédiate sur
[firms.modaps.eosdis.nasa.gov/api/map_key](https://firms.modaps.eosdis.nasa.gov/api/map_key)) :

```bash
cp .env.example .env      # renseigner FIRMS_MAP_KEY
export $(grep -v '^#' .env | xargs)
uv run pyrovigil ingest   # données réelles des dernières 24 h
```

Pour une surveillance continue, une ligne de cron suffit — la commande est idempotente :

```cron
*/10 * * * * cd /chemin/vers/pyrovigil && FIRMS_MAP_KEY=… .venv/bin/pyrovigil ingest >> pyrovigil.log 2>&1
```

## Commandes

| Commande | Rôle |
|---|---|
| `pyrovigil fetch-data` | télécharge les couches géographiques |
| `pyrovigil ingest` | récupère FIRMS, localise, clusterise, score, alerte |
| `pyrovigil ingest --fixture` | idem sur le CSV local, sans clé API |
| `pyrovigil ingest --loop 600` | répète toutes les 10 minutes |
| `pyrovigil ingest --no-alerts` | ingère sans rien envoyer |
| `pyrovigil serve` | API REST et carte |

## API

| Endpoint | Description |
|---|---|
| `GET /` | carte MapLibre |
| `GET /health` | état, volume de données, couches chargées |
| `GET /hotspots/recent?hours=24` | anomalies thermiques brutes |
| `GET /events/recent?hours=24&min_priority=high` | événements triés par score |
| `GET /events/{id}` | détail, hotspots rattachés, alertes envoyées |
| `GET /events.geojson`, `GET /hotspots.geojson` | exports cartographiques |
| `POST /admin/ingest/firms` | ingestion à la demande |
| `POST /admin/recompute-events` | recalcul clustering et scores |
| `POST /admin/send-alerts` | rejoue la règle d'alerte |

Les routes `/admin/*` déclenchent des appels réseau sortants. Elles exigent l'en-tête `X-Admin-Token` et
restent **désactivées** tant que `PYROVIGIL_ADMIN_TOKEN` n'est pas défini. Documentation interactive sur `/docs`.

## Le score

Le score ne prétend pas dire « c'est un feu ». Il répond à : *ce signal mérite-t-il d'être regardé vite ?*
Chaque point attribué garde sa raison, stockée avec l'événement et affichée dans le popup de la carte.

| Critère | Points |
|---|---|
| Fraîcheur | +30 (< 30 min), +20 (< 1 h), +10 (< 3 h) |
| Puissance radiative (FRP) | +25 (≥ 100 MW), +15 (≥ 30), +8 (≥ 10) |
| Confiance satellite | +20 haute, +10 nominale, −15 basse |
| Forêt | +25 dedans, +15 (< 500 m), +8 (< 1500 m), −20 (> 5 km) |
| Regroupement | +15 plusieurs pixels, +10 plusieurs satellites |

Priorités : `low` < 30, `medium` 30–55, `high` 55–75, `critical` > 75. Une alerte part en `high` ou `critical`
si la détection a moins d'une heure et qu'aucune alerte n'a été envoyée sur le même événement depuis deux heures.

**Un critère dont la donnée manque vaut 0**, il ne pénalise pas à l'aveugle. C'est notamment le cas du critère
forêt tant que la couche n'est pas installée.

## Ajouter la couche forêt

Sans elle, le critère forêt est neutre et les faux positifs industriels ne sont pas filtrés — sur le jeu de
démonstration, la torchère de Fos-sur-Mer ressort en `high`, ce qui illustre exactement le problème.

Déposez un GeoJSON de polygones forestiers en `data/forests.geojson` (EPSG:4326), puis relancez
`pyrovigil ingest` : les hotspots existants sont relocalisés automatiquement. Sources possibles :
[BD Forêt IGN](https://www.data.gouv.fr/datasets/bd-foret-r/),
[Masque Forêt IGN](https://www.data.gouv.fr/datasets/masque-foret/),
[CORINE Land Cover](https://www.data.gouv.fr/datasets/corine-land-cover-occupation-des-sols-en-france/).
Ces jeux pèsent plusieurs gigaoctets : simplifiez les géométries (`mapshaper -simplify 5%`) avant import, tout
est chargé en mémoire.

## Choix techniques

Le [briefing](docs/pyrovigil_france_detection_feux_satellite_open_source.md) prévoit PostgreSQL + PostGIS,
Docker Compose et Next.js. Cette implémentation vise le même modèle de données avec beaucoup moins de pièces :

| Briefing | Ici | Pourquoi |
|---|---|---|
| PostGIS + Docker | SQLite + Shapely | La France produit 10 à 200 hotspots/jour, tout tient en mémoire |
| pandas, SQLAlchemy, requests, sklearn | `csv`, `sqlite3`, `urllib` de la stdlib | Le CSV fait quelques centaines de lignes |
| DBSCAN | union-find naïf, O(n²) | 30 lignes, aucune dépendance |
| Next.js + MapLibre | un fichier HTML + MapLibre | Aucun build, aucun npm |
| APScheduler + worker | CLI idempotente + cron | cron sait déjà faire |

Dépendances : `fastapi`, `uvicorn`, `shapely`. La migration vers PostGIS ne toucherait que `db.py` et les
requêtes de `api.py`.

## Vérification

```bash
uv run python tests/test_pyrovigil.py    # tourne aussi sous pytest
```

23 vérifications : parsing FIRMS, déduplication, filtre France, distance à la forêt, clustering,
stabilité des identifiants d'événements, barème de score, anti-spam.

## Limites

**Faux négatifs** — un feu peut échapper au satellite s'il est trop petit, sous les nuages, sous la canopée,
ou s'il démarre entre deux passages orbitaux.

**Faux positifs** — torchères, sites industriels, aciéries, feux agricoles, surfaces minérales très chaudes,
pixels mal géolocalisés. C'est le principal ennemi du projet, d'où le filtrage forêt et le score.

**Latence** — passage satellite + traitement NASA + disponibilité API + intervalle de polling. Compter de
quelques dizaines de minutes à plusieurs heures. Les satellites géostationnaires (MTG/Meteosat) sont la piste
pour descendre nettement.

## Suite

Météo dans le score (Open-Meteo, Météo des forêts) · détection des zones industrielles récurrentes ·
masque forêt IGN · import BDIFF et backtesting · EFFIS · Sentinel-3 FRP · MTG/FCI · modèle LightGBM.

## Données

NASA FIRMS · [contours des départements](https://france-geojson.gregoiredavid.fr/) · fonds de carte
© OpenStreetMap.
