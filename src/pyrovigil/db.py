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

-- Mémoire longue des lieux qui s'allument : un agrégat d'une ligne par case de ~1 km et par jour.
-- C'est ce qui distingue une torchère d'un feu — la première revient, le second non. Volontairement
-- séparé de raw_hotspots, qui est purgé à 30 jours : la valeur du critère est justement dans la
-- durée. Quelques centaines de lignes par jour pour toute la France.
CREATE TABLE IF NOT EXISTS hot_cells (
    lat_cell  INTEGER NOT NULL,          -- latitude / 0.01, soit ~1,1 km
    lon_cell  INTEGER NOT NULL,          -- longitude / 0.01, ~0,8 km à 45°N
    day       TEXT NOT NULL,             -- 'YYYY-MM-DD' de l'acquisition
    max_frp   REAL,
    night     INTEGER NOT NULL DEFAULT 0,  -- vue à un passage de nuit
    day_pass  INTEGER NOT NULL DEFAULT 0,  -- vue à un passage de jour
    PRIMARY KEY (lat_cell, lon_cell, day)
);

CREATE INDEX IF NOT EXISTS hot_cells_day_idx ON hot_cells (day);
"""

CELL_DEGREES = 0.01


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Ouvre la base et applique le schéma (idempotent).

    `check_same_thread=False` : FastAPI exécute les dépendances et les endpoints synchrones dans des
    threads différents de son pool. Chaque requête reçoit sa propre connexion, fermée à la fin, donc
    aucune connexion n'est partagée entre deux threads simultanément.
    """
    conn = sqlite3.connect(str(path or DEFAULT_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _backfill_hot_cells(conn)
    return conn


def _backfill_hot_cells(conn: sqlite3.Connection) -> None:
    """Reconstruit la mémoire des sites depuis les hotspots déjà en base, une seule fois.

    Sans ce rattrapage, le critère de récurrence démarrerait aveugle sur une base existante et
    n'aurait rien à dire avant plusieurs semaines — alors que l'historique dont il a besoin est
    déjà là. Le garde-fou sur la table vide fait que ça ne tourne qu'au tout premier accès.

    ponytail: `round()` de SQLite arrondit le demi vers le haut là où celui de Python arrondit au
    pair. Une case rattrapée peut donc différer d'une unité d'une case ingérée, sans conséquence :
    `events.recurrence` interroge de toute façon la case et ses 8 voisines.
    """
    if conn.execute("SELECT EXISTS (SELECT 1 FROM hot_cells)").fetchone()[0]:
        return
    with conn:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO hot_cells (lat_cell, lon_cell, day, max_frp, night, day_pass)
            SELECT CAST(round(latitude / {CELL_DEGREES}) AS INTEGER),
                   CAST(round(longitude / {CELL_DEGREES}) AS INTEGER),
                   date(acquisition_time), max(frp),
                   max(daynight = 'N'), max(daynight = 'D')
              FROM raw_hotspots
             GROUP BY 1, 2, 3
            """
        )


def cell(latitude: float, longitude: float) -> tuple[int, int]:
    """Case de ~1 km servant de clé à `hot_cells`.

    Assez large pour qu'un même site industriel retombe dans la même case d'un passage à l'autre
    malgré la géolocalisation approximative des pixels, assez fine pour ne pas absorber un feu voisin.
    """
    return round(latitude / CELL_DEGREES), round(longitude / CELL_DEGREES)


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
