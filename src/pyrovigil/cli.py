"""Ligne de commande PyroVigil.

ponytail: pas d'APScheduler ni de worker dédié. `pyrovigil ingest` est idempotente, cron ou launchd
l'appellent toutes les 10 minutes. `--loop N` existe pour les cas où lancer un cron est plus pénible que
laisser un terminal ouvert.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from . import alerts, db, events, firms, geo

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

    by_priority = conn.execute(
        """
        SELECT priority, count(*) AS n FROM fire_events
         WHERE last_seen > datetime('now', '-24 hours')
         GROUP BY priority
        """
    ).fetchall()
    if by_priority:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        summary = ", ".join(
            f"{row['n']} {row['priority']}"
            for row in sorted(by_priority, key=lambda r: order.get(r["priority"], 9))
        )
        print(f"Priorités sur 24 h : {summary}")

    if not args.no_alerts:
        for alert in alerts.send_alerts(conn, os.environ.get("DISCORD_WEBHOOK_URL")):
            print(f"Alerte {alert['status']} — événement {alert['event_id']} ({alert['priority']})")

    total = conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0]
    print(f"{total} hotspots en base")
    conn.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Écrit la carte et ses données en fichiers statiques, prêts pour n'importe quel hébergeur.

    ponytail: réutilise directement les fonctions de l'API. La page charge toute la fenêtre disponible et
    filtre dans le navigateur, donc un seul fichier par couche suffit — pas un par plage horaire.
    """
    import json
    import shutil

    from . import api

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    conn = db.connect(args.database)

    for name, payload in [
        # min_priority explicite : appelées hors FastAPI, les valeurs par défaut restent des objets Query
        ("events.geojson", api.events_geojson(hours=api.EXPORT_HOURS, min_priority="low", conn=conn)),
        ("hotspots.geojson", api.hotspots_geojson(hours=api.EXPORT_HOURS, conn=conn)),
    ]:
        (out / name).write_text(json.dumps(payload), encoding="utf-8")
        print(f"{name} : {len(payload['features'])} entités")

    shutil.copy(api.STATIC_DIR / "index.html", out / "index.html")
    conn.close()
    print(f"Export statique dans {out}/")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    if args.database:
        os.environ["PYROVIGIL_DB"] = args.database
    print(f"Carte et API sur http://{args.host}:{args.port}/")
    uvicorn.run("pyrovigil.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
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
    ingest.add_argument("--no-alerts", action="store_true", help="n'envoie aucune alerte")
    ingest.set_defaults(func=cmd_ingest)

    export = sub.add_parser("export", help="génère la carte en fichiers statiques")
    export.add_argument("--out", default="dist", help="dossier de sortie (défaut : dist)")
    export.set_defaults(func=cmd_export)

    serve = sub.add_parser("serve", help="lance l'API et la carte")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)

    if getattr(args, "loop", None):
        while True:
            args.func(args)
            time.sleep(args.loop)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
