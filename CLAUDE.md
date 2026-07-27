# PyroVigil

`README.md` fait foi pour les commandes, l'architecture, le déploiement et le barème
du score — le lire en premier. Ce fichier ne contient que ce qu'il ne dit pas.

## Vérification

```bash
uv run python tests/test_pyrovigil.py   # ou pytest, même fichier
```

Un seul fichier, des `assert` nus, pas de fixtures ni de `conftest.py`. Un nouveau
test = une fonction `test_*` dans ce fichier ; `run_all()` la ramasse automatiquement.

## Conventions

- **Tout est en français** : docstrings, commentaires, noms de tests, sorties CLI.
- Les commentaires `ponytail:` marquent un raccourci assumé et son chemin de sortie.
  Les garder à jour, ne pas les supprimer en passant.
- Aucun linter ni formateur configuré. Ne pas en ajouter sans demande.
- Dépendances : `fastapi`, `uvicorn`, `shapely`. Le reste vient de la stdlib — c'est
  un choix, pas un oubli (`README.md` § Choix techniques).

## Pièges

- **Dates** : UTC, format `'YYYY-MM-DD HH:MM:SS'` partout. Les fonctions date de
  SQLite en dépendent (`db.py`).
- **Appeler `api.*` hors FastAPI** exige de passer les valeurs par défaut
  explicitement — sinon on récupère des objets `Query` (cf. `cmd_export` dans `cli.py`).
- **`firms.insert_hotspots` écrit pour les deux sources**, MTG compris — `lsasaf.ingest`
  l'appelle. Le nom du module induit en erreur : toute modification du schéma
  d'insertion ou de `hot_cells` passe par là.
- **`hot_cells` survit à la purge** : 180 jours contre 30 pour `raw_hotspots`. La
  supprimer avec les hotspots détruirait le critère de récurrence sans que rien n'échoue.
- `pyrovigil.db` à la racine est gitignoré mais bien présent en local : c'est une
  vraie base, pas un artefact jetable.
- Sans `FIRMS_MAP_KEY`, utiliser `pyrovigil ingest --fixture`. Sans `LSASAF_USER` /
  `LSASAF_PASSWORD`, la source MTG est ignorée avec un avertissement — sauf si elle est
  demandée explicitement (`--source mtg`), auquel cas la commande échoue.
- **Discord répond 403 au `User-Agent` par défaut d'`urllib`.** `alerts.USER_AGENT` n'est
  pas décoratif : sans lui, aucune alerte ne part et le statut passe en `failed`.
- Pas de python-dotenv : `export $(grep -v '^#' .env | xargs)`.
