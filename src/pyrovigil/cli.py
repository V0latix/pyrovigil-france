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

from . import db, firms

DATA_DIR = Path(os.environ.get("PYROVIGIL_DATA", "data"))
FIXTURE = DATA_DIR / "firms_sample.csv"


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

    total = conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0]
    print(f"{total} hotspots en base")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyrovigil", description=__doc__)
    parser.add_argument("--database", default=None, help="chemin de la base SQLite")
    sub = parser.add_subparsers(dest="command", required=True)

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
