# PyroVigil France

Prototype open-source d'aide à la détection rapide de signaux de feux de forêt en France à partir de données
satellite ouvertes. Il ingère les anomalies thermiques NASA FIRMS et MTG, les filtre avec des données
géographiques françaises, les regroupe en événements scorés, les affiche sur une carte et alerte sur les
signaux forts.

> **Ce n'est pas un système d'alerte officiel.** Il ne remplace ni les pompiers, ni le SDIS, ni la préfecture,
> et ne confirme pas juridiquement un incendie. En cas de feu, appelez le **18** ou le **112**.

```
FIRMS  (polaire, 375 m, ~4 h)  ─┐
                                ├─▶ raw_hotspots ──▶ filtre France / forêt ──▶ clustering ──▶ score ──┬──▶ API + carte
MTG    (géostat., 1 km, ~30 min) ┘                                                                     └──▶ alerte Discord
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

Ajouter [des identifiants LSA SAF](https://mokey.lsasvcs.ipma.pt/auth/signup) (gratuits aussi) dans le même
`.env` active la source MTG et fait tomber la latence de 4-5 h à une trentaine de minutes — voir
[§ La source MTG](#la-source-mtg). Sans eux, `ingest` se rabat sur FIRMS seul et le dit.

Pour une surveillance continue, une ligne de cron suffit — la commande est idempotente :

```cron
# MTG toutes les 10 min (sa cadence de publication), FIRMS une fois par heure (sa latence réelle)
*/10 * * * * cd /chemin/vers/pyrovigil && set -a && . ./.env && .venv/bin/pyrovigil ingest --source mtg >> pyrovigil.log 2>&1
17  *  * * * cd /chemin/vers/pyrovigil && set -a && . ./.env && .venv/bin/pyrovigil ingest >> pyrovigil.log 2>&1
```

## Commandes

| Commande | Rôle |
|---|---|
| `pyrovigil fetch-data` | télécharge les contours des départements |
| `pyrovigil ingest` | récupère toutes les sources, localise, clusterise, score, alerte |
| `pyrovigil ingest --source mtg` | MTG seul — pour une boucle à 10 minutes |
| `pyrovigil ingest --fixture` | idem sur le CSV local, sans clé API |
| `pyrovigil ingest --loop 600` | répète toutes les 10 minutes |
| `pyrovigil ingest --no-alerts` | ingère sans rien envoyer |
| `pyrovigil export` | génère `dist/` : carte et données en fichiers statiques |
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

## Déploiement gratuit

Le dépôt s'héberge tout seul, sans serveur ni base managée. Deux workflows se partagent le travail :

[`ingest.yml`](.github/workflows/ingest.yml), **toutes les heures** — le pipeline complet :

```
récupère pyrovigil.db depuis la Release « data »
  └─ pyrovigil ingest        (FIRMS + MTG → localisation → clustering → score → alertes)
      └─ pyrovigil export    (events.geojson + hotspots.geojson + index.html)
          ├─ renvoie pyrovigil.db dans la Release
          └─ déploie dist/ sur Vercel
```

[`ingest-fast.yml`](.github/workflows/ingest-fast.yml), **toutes les 10 minutes** — MTG seul,
`ingest --source mtg`, puis sauvegarde de la base. **Ni export, ni déploiement** : c'est ce qui rend la
cadence tenable, le plan Vercel Hobby plafonnant à 100 déploiements par jour pour 144 créneaux. La carte
reste donc horaire ; c'est l'**alerte Discord** qui descend à une trentaine de minutes.

Les deux partagent le groupe `concurrency: ingest`, puisqu'ils écrivent la même base.

La base SQLite vit comme **asset d'une Release GitHub** : mutable, gratuit, et sans impact sur l'historique
git — un `.db` commité ajouterait plusieurs mégaoctets de binaire à chaque passage.

La carte exportée charge 7 jours de données en une fois et filtre dans le navigateur. Elle n'a donc besoin
d'aucun backend : les fichiers statiques suffisent, et la même page fonctionne servie par `pyrovigil serve`.

### Secrets à configurer

| Secret | Nécessaire ? | Rôle |
|---|---|---|
| `FIRMS_MAP_KEY` | **oui** | sans elle, `ingest.yml` n'a plus que MTG |
| `LSASAF_USER`, `LSASAF_PASSWORD` | pour `ingest-fast.yml` | sans eux, la source MTG est ignorée et la latence reste celle de FIRMS |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | non | sans eux, l'export part en artefact du workflow au lieu d'être déployé |
| `DISCORD_WEBHOOK_URL` | non | sans lui, les alertes sont seulement journalisées |

```bash
gh secret set FIRMS_MAP_KEY
gh secret set LSASAF_USER         # https://mokey.lsasvcs.ipma.pt/auth/signup
gh secret set LSASAF_PASSWORD
gh secret set VERCEL_TOKEN        # https://vercel.com/account/tokens
```

`VERCEL_ORG_ID` et `VERCEL_PROJECT_ID` se récupèrent dans `.vercel/project.json` après un `vercel link` local.

### Pourquoi pas le cron Vercel

Le plan Hobby limite les cron jobs à **une exécution par jour**, et une expression plus fréquente fait
échouer le déploiement. GitHub Actions descend à 5 minutes, gratuitement — c'est ce qui rend possible la
boucle MTG à 10 minutes.

### Quatre limites à connaître

**Le dépôt doit être public**, et ce n'est pas qu'une question de licence. Sur un dépôt **privé**, les
minutes Actions sont plafonnées (2 000/mois en plan Free) : `*/10` en consommerait à lui seul ~4 400. Et
surtout, GitHub y déprioritise fortement les tâches planifiées — mesuré sur ce dépôt avant sa bascule en
public, `*/10` n'a tiré que **10 fois en 18 heures**, avec un trou de 3 h 50, soit la cadence du workflow
horaire pour rien. Sur un dépôt public, les minutes sont illimitées et la planification bien plus fidèle.

**Les tâches planifiées restent retardées sous charge**, même en public : `*/10` veut dire « environ toutes
les 10 minutes », pas « à la minute ». Et GitHub ne garde **qu'une seule exécution en attente** par groupe de
concurrence : pendant que le job horaire tourne, un job rapproché peut être écarté. Sans gravité, le suivant
arrive dans 10 minutes et l'ingestion MTG reprend systématiquement la dernière heure de créneaux. En
pratique, compter 30 à 40 minutes de latence d'alerte plutôt que les 20 minutes du produit brut. Pour une
vraie cadence de 10 minutes, la boucle cron locale de la section [Démarrage](#démarrage) est plus fiable.

**La base grossit** — de l'ordre d'1 Mo par jour pour FIRMS seul, davantage avec MTG, qui réobserve un feu
persistant toutes les 10 minutes. Elle est téléchargée et renvoyée à chaque exécution, d'où la purge des
`raw_hotspots` au-delà de **30 jours** (`cli.PURGE_DAYS`), suivie d'un `VACUUM` : sans lui SQLite réutilise
les pages libérées mais ne rend jamais les octets. Les événements et les alertes, eux, sont conservés. Si le
volume redevient un problème, la bascule vers Postgres (Neon a une offre gratuite) ne toucherait que `db.py`
et les requêtes de `api.py`.

**Les workflows planifiés sont désactivés après 60 jours sans activité sur le dépôt.** Sur un projet
saisonnier, pensez à un commit de temps en temps hors saison.

## Le score

Le score ne prétend pas dire « c'est un feu ». Il répond à : *ce signal mérite-t-il d'être regardé vite ?*
Chaque point attribué garde sa raison, stockée avec l'événement et affichée dans le popup de la carte.

| Critère | Points |
|---|---|
| Fraîcheur | +30 (< 30 min), +20 (< 1 h), +10 (< 3 h) |
| Puissance radiative (FRP) | +25 (≥ 100 MW), +15 (≥ 30), +8 (≥ 10) |
| Confiance satellite | +20 haute, +10 nominale, −15 basse |
| Forêt | +25 dedans, +15 (< 500 m), +8 (< 1500 m), −20 (> 5 km) |
| Regroupement | +15 plusieurs **positions** distinctes, +10 plusieurs satellites |

Priorités : `low` < 30, `medium` 30–55, `high` 55–75, `critical` > 75. Une alerte part en `high` ou `critical`
si la détection a moins d'une heure et qu'aucune alerte n'a été envoyée sur le même événement depuis deux heures.

**Un critère dont la donnée manque vaut 0**, il ne pénalise pas à l'aveugle : c'est le cas du critère forêt
hors de France, ou quand le service IGN est indisponible.

Le regroupement compte les **positions distinctes**, pas les lignes en base. Une source géostationnaire
réobserve le même pixel toutes les 10 minutes : compter les lignes accorderait le bonus « plusieurs pixels
groupés » à un foyer unique vu six fois de suite.

## La source MTG

FIRMS dépend du passage d'un satellite en orbite polaire : deux à quatre survols par jour, et 4 à 5 heures
entre l'acquisition et la disponibilité. Le produit **MTG FRP-PIXEL** ([LSA-509](https://lsa-saf.eumetsat.int/en/data/products/fire-products/),
EUMETSAT / LSA SAF) est géostationnaire — il regarde l'Europe en permanence et publie **un fichier toutes les
10 minutes**, 20 à 45 minutes après l'acquisition.

Les deux sources ne se remplacent pas, elles se complètent :

| | FIRMS (VIIRS / MODIS) | MTG (FCI) |
|---|---|---|
| Orbite | polaire | géostationnaire |
| Résolution | 375 m (VIIRS) | 1 km au nadir, ~1,5 à 1,9 km² sur la France |
| Cadence | 2 à 4 passages / jour | 10 minutes |
| Latence | 4 à 5 h | 20 à 45 min |
| Voit bien | les petits feux | les feux qui démarrent |

MTG voit vite et gros, FIRMS voit fin et tard. Un feu vu par les deux gagne d'office le « confirmé par
plusieurs satellites » du barème, sans qu'aucune règle spécifique ait été ajoutée : `events.summarize` compte
déjà les satellites distincts.

**Ce qu'il faut savoir avant de s'y fier :**

- Le produit est en **statut « démonstration »** : sa disponibilité n'est pas garantie. Un créneau manquant
  est donc traité comme un cas normal — on journalise et on passe au suivant, jamais d'échec bruyant. Chaque
  ingestion reprend la dernière heure de créneaux, ce qui rattrape les trous tout seul.
- Le produit fournit un `FIRE_CONFIDENCE` **continu, de 0 à 1**, là où VIIRS donne trois classes. Il est
  ramené aux classes du barème avec les mêmes seuils que la confiance MODIS en pourcentage, déjà traitée
  dans `firms._confidence` : 0,8 et 0,3. C'est un précédent du projet, pas une calibration — les deux
  échelles ne mesurent pas rigoureusement la même chose, et c'est à revoir avec le reste du barème MTG.
- Le fichier couvre **tout le disque Meteosat**. Le filtre sur l'emprise France tombe avant toute autre
  chose, sans quoi l'Afrique en saison de brûlis constituerait l'essentiel de ce qu'on ingère.

Le « 1 km » est la résolution au nadir, sous le satellite. La France est vue avec un angle zénithal de
l'ordre de 53° depuis l'orbite à 0° de longitude : les pixels y font entre 1,5 et 1,9 km² (colonne
`PIXEL_SIZE` du produit). C'est une raison de plus de garder FIRMS pour les petits foyers.

Le même répertoire publie chaque créneau en `.csv.gz` et en `.nc`. On lit le CSV : `urllib`, `gzip` et `csv`
de la stdlib suffisent, là où le NetCDF imposerait `netCDF4` pour la même information.

## La couche forêt

Rien à installer : chaque hotspot situé en France est confronté au
[Masque Forêt IGN 2021-2023](https://www.data.gouv.fr/datasets/masque-foret/), interrogé en WFS sur la
Géoplateforme, sans clé. Le national pèse 1,26 million de polygones — trop pour un fichier — mais les
détections sont géographiquement groupées : on ne télécharge que les tuiles de 0,1° qui en contiennent,
une fois chacune, en mémoire pour la durée du processus. Compter une requête par tuile, moins d'une
seconde, quelques dizaines de mégaoctets jamais écrits sur disque.

Si l'IGN ne répond pas, `in_forest` reste `NULL` : le critère forêt vaut 0 et l'ingestion suivante réessaie.
Pour travailler hors ligne ou sur une autre source, déposez un GeoJSON de polygones (EPSG:4326) en
`data/forests.geojson` : il devient prioritaire et supprime tout appel réseau.

**Ce que la couche apporte, mesuré** — sur les 1 632 hotspots France de la base de démonstration, 84 %
tombent *dans* une forêt et aucun n'est à plus de 1,1 km d'un bois : en France, être près d'un massif ne
distingue presque rien, et la pénalité « à plus de 5 km de toute forêt » du barème ne se déclenche jamais.
La torchère de Fos-sur-Mer, elle, est bien classée hors forêt — à 550 m d'un bosquet, donc +8 au lieu de
+25. La couche cesse de créditer les sites industriels d'un bonus forestier, mais ne les élimine pas :
c'est la détection des zones industrielles récurrentes qui le fera.

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

38 vérifications : parsing FIRMS et MTG, déduplication, tolérance aux pannes de source, filtre France,
distance à la forêt, clustering, stabilité des identifiants d'événements, barème de score, purge, anti-spam.

## Limites

**Faux négatifs** — un feu peut échapper au satellite s'il est trop petit, sous les nuages, sous la canopée,
ou s'il démarre entre deux passages orbitaux.

**Faux positifs** — torchères, sites industriels, aciéries, feux agricoles, surfaces minérales très chaudes,
pixels mal géolocalisés. C'est le principal ennemi du projet, d'où le filtrage forêt et le score.

**Latence** — acquisition + traitement + disponibilité API + intervalle de polling. Une trentaine de minutes
par MTG, 4 à 5 heures par FIRMS. Descendre plus bas demanderait de sortir du satellite : les caméras au sol
(Pyronear) détectent en moins d'une minute, mais leur API est réservée aux SDIS partenaires.

**Barème à recalibrer sur MTG** — première mesure, une heure de données du 27/07/2026 : 12 détections dans
l'emprise, 4 en France, FRP de 10 à 25 MW, `FIRE_CONFIDENCE` étalé de 0,01 à 0,88. Une détection MTG fraîche
en forêt et de confiance nominale cumule 73 points (+30 fraîcheur, +25 forêt, +10 confiance, +8 FRP), soit
`high`, donc l'alerte ; en confiance haute elle passe `critical`. Autrement dit, **presque toute détection
MTG en forêt alertera**, et 84 % des hotspots France tombent en forêt. L'anti-spam borne les dégâts à une
alerte par événement toutes les deux heures, et c'est peut-être le bon comportement — mais si ça sature, ce
sont les seuils qu'il faut revoir, pas la source qu'il faut retirer.

## Suite

Recalibrage du barème sur les détections MTG · météo dans le score (Open-Meteo, Météo des forêts) ·
détection des zones industrielles récurrentes · recalibrage du barème forêt · import BDIFF et backtesting ·
EFFIS · Sentinel-3 FRP · modèle LightGBM.

## Données

NASA FIRMS · [MTG FRP-PIXEL, EUMETSAT / LSA SAF](https://lsa-saf.eumetsat.int/en/data/products/fire-products/)
(CC BY 4.0) · [contours des départements](https://france-geojson.gregoiredavid.fr/) ·
[Masque Forêt IGN](https://data.geopf.fr/) · fonds de carte © OpenStreetMap.
