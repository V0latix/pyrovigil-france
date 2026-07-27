"""Ingestion des anomalies thermiques NASA FIRMS.

API : https://firms.modaps.eosdis.nasa.gov/api/area/
Clé gratuite (immédiate, par email) : https://firms.modaps.eosdis.nasa.gov/api/map_key

ponytail: urllib + csv de la stdlib. Le CSV renvoyé fait quelques centaines de lignes, pandas serait un
marteau-pilon.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import logging
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("pyrovigil.firms")

API_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# France métropolitaine + Corse, avec des marges sur les pays voisins (filtrées ensuite par geo.py).
BBOX_FRANCE = "-5.5,41.0,10.0,51.5"

SOURCES = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT",
]


class FirmsError(RuntimeError):
    pass


@contextlib.contextmanager
def ipv4_only():
    """Force la résolution DNS en IPv4 le temps d'une requête.

    ponytail: les runners GitHub Actions n'ont pas toujours de route IPv6. Quand la résolution renvoie
    une adresse AAAA, urllib échoue en « Network is unreachable » (errno 101) sans se rabattre sur IPv4 —
    un tiers de nos exécutions planifiées tombaient là-dessus. FIRMS est joignable en IPv4 partout, donc
    on tranche. Le patch est limité à la durée de l'appel pour ne pas contaminer le reste du processus.
    """
    original = socket.getaddrinfo

    def ipv4(host, port, family=0, *args, **kwargs):
        return original(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = ipv4
    try:
        yield
    finally:
        socket.getaddrinfo = original


def _get(url: str, timeout: int = 60, attempts: int = 3) -> str:
    """GET avec retry et backoff exponentiel — FIRMS renvoie régulièrement des 429/503."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with ipv4_only(), urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(2**attempt)
    raise FirmsError(f"FIRMS injoignable après {attempts} tentatives : {last}")


def fetch_csv(source: str, map_key: str, day_range: int = 1, bbox: str = BBOX_FRANCE) -> str:
    return _get(f"{API_BASE}/{map_key}/{source}/{bbox}/{day_range}")


def _confidence(value: str) -> str:
    """Normalise la confiance : VIIRS renvoie l/n/h, MODIS un pourcentage."""
    value = (value or "").strip().lower()
    if value in ("l", "low"):
        return "low"
    if value in ("n", "nominal"):
        return "nominal"
    if value in ("h", "high"):
        return "high"
    if value.isdigit():  # MODIS : 0-100
        percent = int(value)
        return "high" if percent >= 80 else "nominal" if percent >= 30 else "low"
    return "unknown"


def _float(row: dict, key: str) -> float | None:
    value = (row.get(key) or "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def _acquisition_time(row: dict) -> datetime:
    """acq_date='2026-07-24' + acq_time='934' (HHMM, parfois sans zéro initial) -> datetime UTC."""
    return datetime.strptime(
        row["acq_date"].strip() + row["acq_time"].strip().zfill(4), "%Y-%m-%d%H%M"
    ).replace(tzinfo=timezone.utc)


def parse_csv(text: str, firms_source: str) -> list[dict]:
    """Transforme la réponse FIRMS en lignes prêtes à insérer.

    Renvoie une liste vide si la réponse ne contient pas de données (FIRMS répond en texte libre du type
    "No fire alerts found..." ou un message d'erreur si la clé est invalide).
    """
    if "latitude" not in text[:200]:
        return []

    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if not (row.get("latitude") and row.get("longitude")):
            continue
        rows.append(
            {
                "source": "FIRMS",
                "firms_source": firms_source,
                "satellite": (row.get("satellite") or "").strip(),
                "instrument": (row.get("instrument") or "").strip(),
                "acquisition_time": _acquisition_time(row),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                # MODIS renvoie `brightness`/`bright_t31`, VIIRS `bright_ti4`/`bright_ti5`
                "brightness": _float(row, "brightness"),
                "bright_ti4": _float(row, "bright_ti4"),
                "bright_ti5": _float(row, "bright_ti5"),
                "frp": _float(row, "frp"),
                "confidence": _confidence(row.get("confidence", "")),
                "daynight": (row.get("daynight") or "").strip(),
                "raw": json.dumps(row, ensure_ascii=False),
            }
        )
    return rows


def _cell_row(row: dict) -> dict:
    """Ligne `hot_cells` correspondant à un hotspot.

    `daynight` vient de FIRMS ('D'/'N') ; MTG ne le fournit pas et laisse les deux drapeaux à 0,
    donc le critère « vue de jour comme de nuit » ne se déclenche jamais sur MTG seul.
    """
    from .db import cell

    lat_cell, lon_cell = cell(row["latitude"], row["longitude"])
    daynight = (row.get("daynight") or "").strip().upper()
    return {
        "lat_cell": lat_cell,
        "lon_cell": lon_cell,
        "day": row["acquisition_time"].strftime("%Y-%m-%d"),
        "max_frp": row["frp"],
        "night": 1 if daynight == "N" else 0,
        "day_pass": 1 if daynight == "D" else 0,
    }


def insert_hotspots(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insère en ignorant les doublons (même satellite, même instant, même position).

    Renvoie le nombre de lignes réellement nouvelles.
    """
    if not rows:
        return 0

    from .db import to_sql

    before = conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0]
    with conn:
        # Mémoire des sites récurrents, avant l'insertion des hotspots : elle enregistre ce qui a été
        # *vu*, pas ce qui est nouveau. Un site déjà connu doit continuer de compter ses jours, sinon
        # la déduplication l'effacerait de sa propre histoire. L'upsert est idempotent.
        conn.executemany(
            """
            INSERT INTO hot_cells (lat_cell, lon_cell, day, max_frp, night, day_pass)
            VALUES (:lat_cell, :lon_cell, :day, :max_frp, :night, :day_pass)
            ON CONFLICT (lat_cell, lon_cell, day) DO UPDATE SET
                max_frp  = max(coalesce(hot_cells.max_frp, 0), coalesce(excluded.max_frp, 0)),
                night    = max(hot_cells.night, excluded.night),
                day_pass = max(hot_cells.day_pass, excluded.day_pass)
            """,
            [_cell_row(row) for row in rows],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO raw_hotspots (
                source, firms_source, satellite, instrument, acquisition_time,
                latitude, longitude, brightness, bright_ti4, bright_ti5,
                frp, confidence, daynight, raw
            ) VALUES (
                :source, :firms_source, :satellite, :instrument, :acquisition_time,
                :latitude, :longitude, :brightness, :bright_ti4, :bright_ti5,
                :frp, :confidence, :daynight, :raw
            )
            """,
            [{**row, "acquisition_time": to_sql(row["acquisition_time"])} for row in rows],
        )
    return conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0] - before


def ingest(conn: sqlite3.Connection, map_key: str, day_range: int = 1) -> int:
    """Interroge toutes les sources FIRMS et stocke les nouveaux hotspots.

    Une source injoignable ne fait pas échouer les autres : FIRMS a des indisponibilités passagères et
    limite le débit par clé. Les trois quarts des données valent mieux que rien, d'autant que les sources
    se recouvrent largement. En revanche, si *toutes* échouent, on lève : un job planifié doit échouer
    bruyamment plutôt que de laisser croire qu'il n'y a pas de feu.
    """
    rows, failures = [], []
    for source in SOURCES:
        try:
            rows.extend(parse_csv(fetch_csv(source, map_key, day_range), source))
        except FirmsError as exc:
            failures.append(source)
            logger.warning("source FIRMS %s indisponible : %s", source, exc)

    if len(failures) == len(SOURCES):
        raise FirmsError(f"toutes les sources FIRMS ont échoué : {', '.join(failures)}")
    return insert_hotspots(conn, rows)


def ingest_fixture(conn: sqlite3.Connection, path: Path, rebase: bool = True) -> int:
    """Ingère un CSV FIRMS local, pour travailler sans clé API.

    ponytail: `rebase` décale les horodatages pour que la détection la plus récente tombe à l'instant
    présent. Sans ça, une fixture figée serait toujours « vieille », tous les scores de fraîcheur
    seraient nuls et la démo ne montrerait rien. Le décalage est arrondi au bloc de 10 minutes pour
    rester déterministe : relancer l'ingestion n'insère pas de doublons.
    """
    rows = parse_csv(path.read_text(encoding="utf-8"), "FIXTURE")
    if rows and rebase:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        anchor = now - timedelta(minutes=now.minute % 10)
        shift = anchor - max(row["acquisition_time"] for row in rows)
        for row in rows:
            row["acquisition_time"] += shift
    return insert_hotspots(conn, rows)
