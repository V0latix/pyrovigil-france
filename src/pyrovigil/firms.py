"""Ingestion des anomalies thermiques NASA FIRMS.

API : https://firms.modaps.eosdis.nasa.gov/api/area/
Clé gratuite (immédiate, par email) : https://firms.modaps.eosdis.nasa.gov/api/map_key

ponytail: urllib + csv de la stdlib. Le CSV renvoyé fait quelques centaines de lignes, pandas serait un
marteau-pilon.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _get(url: str, timeout: int = 30, attempts: int = 3) -> str:
    """GET avec retry et backoff exponentiel — FIRMS renvoie régulièrement des 429/503."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
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


def insert_hotspots(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insère en ignorant les doublons (même satellite, même instant, même position).

    Renvoie le nombre de lignes réellement nouvelles.
    """
    if not rows:
        return 0

    from .db import to_sql

    before = conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0]
    with conn:
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
    """Interroge toutes les sources FIRMS et stocke les nouveaux hotspots."""
    rows = []
    for source in SOURCES:
        rows.extend(parse_csv(fetch_csv(source, map_key, day_range), source))
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
