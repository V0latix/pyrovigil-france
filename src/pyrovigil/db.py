"""Schéma et accès SQLite.

Le briefing prévoit PostgreSQL + PostGIS. On garde le même modèle de données, mais dans SQLite : la France
produit de l'ordre de 10 à 200 hotspots par jour, et les opérations géométriques (point dans polygone,
distance) sont faites en mémoire par Shapely dans `geo.py`. La colonne `geom` de PostGIS est remplacée par
le couple `latitude`/`longitude`.

ponytail: sqlite3 de la stdlib, pas de SQLAlchemy. Passer à PostGIS quand le volume ou la concurrence
l'exigent — seul ce module et les requêtes de `api.py` changent.

Toutes les dates sont stockées en UTC au format 'YYYY-MM-DD HH:MM:SS', celui que comprennent les fonctions
date de SQLite (`datetime('now')` est en UTC).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SQL_TIME = "%Y-%m-%d %H:%M:%S"

DEFAULT_DB_PATH = Path(os.environ.get("PYROVIGIL_DB", "pyrovigil.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_hotspots (
    id                INTEGER PRIMARY KEY,
    source            TEXT NOT NULL,           -- 'FIRMS'
    firms_source      TEXT,                    -- VIIRS_SNPP_NRT, MODIS_NRT, ...
    satellite         TEXT,
    instrument        TEXT,
    acquisition_time  TEXT NOT NULL,
    latitude          REAL NOT NULL,
    longitude         REAL NOT NULL,
    brightness        REAL,
    bright_ti4        REAL,
    bright_ti5        REAL,
    frp               REAL,
    confidence        TEXT,                    -- low | nominal | high
    daynight          TEXT,
    -- enrichissement géographique, calculé une fois à l'ingestion (voir geo.py)
    in_france         INTEGER,                 -- 1/0, NULL si masque indisponible
    department_code   TEXT,
    in_forest         INTEGER,                 -- 1/0, NULL si couche forêt absente
    forest_distance_m REAL,
    raw               TEXT NOT NULL,           -- ligne CSV d'origine, en JSON
    inserted_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, satellite, acquisition_time, latitude, longitude)
);

CREATE INDEX IF NOT EXISTS raw_hotspots_acq_idx ON raw_hotspots (acquisition_time DESC);
CREATE INDEX IF NOT EXISTS raw_hotspots_pos_idx ON raw_hotspots (latitude, longitude);

CREATE TABLE IF NOT EXISTS fire_events (
    id                INTEGER PRIMARY KEY,
    status            TEXT NOT NULL DEFAULT 'candidate',
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    latitude          REAL NOT NULL,           -- centroïde
    longitude         REAL NOT NULL,
    hotspot_count     INTEGER NOT NULL DEFAULT 0,
    source_count      INTEGER NOT NULL DEFAULT 0,   -- nb de satellites distincts
    max_frp           REAL,
    sum_frp           REAL,
    max_brightness    REAL,
    in_france         INTEGER,
    department_code   TEXT,
    in_forest         INTEGER,
    forest_distance_m REAL,
    risk_score        REAL NOT NULL DEFAULT 0,
    priority          TEXT NOT NULL DEFAULT 'low',
    score_detail      TEXT,                    -- JSON: [[raison, points], ...]
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS fire_events_last_seen_idx ON fire_events (last_seen DESC);
CREATE INDEX IF NOT EXISTS fire_events_score_idx ON fire_events (risk_score DESC);

CREATE TABLE IF NOT EXISTS event_hotspots (
    event_id   INTEGER NOT NULL REFERENCES fire_events(id) ON DELETE CASCADE,
    hotspot_id INTEGER NOT NULL REFERENCES raw_hotspots(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, hotspot_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id                 INTEGER PRIMARY KEY,
    event_id           INTEGER NOT NULL REFERENCES fire_events(id) ON DELETE CASCADE,
    channel            TEXT NOT NULL,
    payload            TEXT NOT NULL,
    risk_score_at_send REAL NOT NULL,
    sent_at            TEXT NOT NULL DEFAULT (datetime('now')),
    delivery_status    TEXT NOT NULL DEFAULT 'sent'
);

CREATE INDEX IF NOT EXISTS alerts_event_idx ON alerts (event_id, sent_at DESC);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Ouvre la base et applique le schéma (idempotent)."""
    conn = sqlite3.connect(str(path or DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def to_sql(dt: datetime) -> str:
    """datetime -> texte UTC stockable. Un datetime naïf est supposé déjà en UTC."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(SQL_TIME)


def from_sql(value: str) -> datetime:
    """Texte stocké -> datetime UTC conscient du fuseau."""
    return datetime.strptime(value, SQL_TIME).replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
