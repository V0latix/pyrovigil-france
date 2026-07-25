"""Ligne de commande PyroVigil.

ponytail: pas d'APScheduler ni de worker dédié. `pyrovigil ingest` est idempotente, cron ou launchd
l'appellent toutes les 10 minutes. `--loop N` existe pour les cas où lancer un cron est plus pénible que
laisser un terminal ouvert.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import db, events, firms, geo

DATA_DIR = Path(os.environ.get("PYROVIGIL_DATA", "data"))
FIXTURE = DATA_DIR / "firms_sample.csv"


def cmd_fetch_data(args: argparse.Namespace) -> int:
    path = geo.download_departments(DATA_DIR)
    index = geo.GeoIndex(DATA_DIR)
    print(f"{len(index.departments)} départements téléchargés dans {path}")
    if not index.has_forests:
        print(
            f"Pas de couche forêt ({DATA_DIR / geo.FORESTS_FILE} absent) : le score ignorera le critère\n"
            "forêt. Voir le README pour importer la BD Forêt IGN."
        )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    conn = db.connect(args.database)

    if args.fixture:
        inserted = firms.ingest_fixture(conn, Path(args.fixture_path or FIXTURE))
        print(f"{inserted} nouveaux hotspots (fixture {args.fixture_path or FIXTURE})")
    else:
        map_key = os.environ.get("FIRMS_MAP_KEY")
        if not map_key:
            print(
                "FIRMS_MAP_KEY manquante. Clé gratuite et immédiate sur\n"
                "  https://firms.modaps.eosdis.nasa.gov/api/map_key\n"
                "En attendant : `pyrovigil ingest --fixture` travaille sur un jeu de données local.",
                file=sys.stderr,
            )
            return 1
        inserted = firms.ingest(conn, map_key, day_range=args.days)
        print(f"{inserted} nouveaux hotspots (FIRMS, {args.days} j)")

    index = geo.GeoIndex(DATA_DIR)
    if index.has_departments:
        enriched = geo.enrich_hotspots(conn, index)
        in_france = conn.execute(
            "SELECT count(*) FROM raw_hotspots WHERE in_france = 1"
        ).fetchone()[0]
        print(f"{enriched} hotspots localisés, {in_france} en France")
    else:
        print("Masque France absent : lancez `pyrovigil fetch-data`", file=sys.stderr)

    stats = events.rebuild_events(conn)
    print(
        f"{stats['events']} événements dans la fenêtre "
        f"({stats['created']} créés, {stats['updated']} mis à jour, {stats['merged']} fusionnés)"
    )

    total = conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0]
    print(f"{total} hotspots en base")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyrovigil", description=__doc__)
    parser.add_argument("--database", default=None, help="chemin de la base SQLite")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch-data", help="télécharge les couches géographiques (départements)")
    fetch.set_defaults(func=cmd_fetch_data)

    ingest = sub.add_parser("ingest", help="récupère les hotspots et met à jour les événements")
    ingest.add_argument("--fixture", action="store_true", help="utilise un CSV local au lieu de l'API")
    ingest.add_argument("--fixture-path", default=None)
    ingest.add_argument("--days", type=int, default=1, help="fenêtre FIRMS en jours (1 à 10)")
    ingest.add_argument("--loop", type=int, metavar="SECONDES", help="répète indéfiniment")
    ingest.set_defaults(func=cmd_ingest)

    args = parser.parse_args(argv)

    if getattr(args, "loop", None):
        while True:
            args.func(args)
            time.sleep(args.loop)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
