"""API REST PyroVigil (§11 du briefing) et service de la carte.

ponytail: la couche web sert directement les lignes SQLite. Pas de modèles Pydantic en double du schéma,
pas d'ORM — les colonnes de la base *sont* le contrat de l'API.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse

from . import alerts, db, events, firms, geo

DATA_DIR = Path(os.environ.get("PYROVIGIL_DATA", "data"))
STATIC_DIR = Path(__file__).parent / "static"

PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Fenêtre des exports statiques. La carte charge ce volume une fois et filtre côté navigateur.
EXPORT_HOURS = 168

app = FastAPI(
    title="PyroVigil France API",
    description=(
        "Signaux de feux potentiels en France à partir des anomalies thermiques NASA FIRMS. "
        "Outil d'aide à la détection — ne remplace ni les pompiers, ni le SDIS, ni la préfecture."
    ),
    version="0.1.0",
)


def get_db():
    conn = db.connect(os.environ.get("PYROVIGIL_DB"))
    try:
        yield conn
    finally:
        conn.close()


def require_admin(x_admin_token: str = Header(default="")) -> None:
    """Les routes /admin déclenchent des appels réseau sortants : elles restent fermées par défaut."""
    expected = os.environ.get("PYROVIGIL_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "PYROVIGIL_ADMIN_TOKEN non configuré, routes admin désactivées")
    if x_admin_token != expected:
        raise HTTPException(403, "jeton admin invalide")


def _rows(conn: sqlite3.Connection, sql: str, params: dict) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _event_payload(row: dict) -> dict:
    row["score_detail"] = json.loads(row["score_detail"]) if row.get("score_detail") else []
    row["in_forest"] = None if row["in_forest"] is None else bool(row["in_forest"])
    return row


@app.get("/health")
def health(conn: sqlite3.Connection = Depends(get_db)):
    hotspots = conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0]
    latest = conn.execute("SELECT max(acquisition_time) FROM raw_hotspots").fetchone()[0]
    index = geo.GeoIndex(DATA_DIR)
    return {
        "status": "ok",
        "hotspots": hotspots,
        "latest_acquisition": latest,
        "departments_loaded": index.has_departments,
        "forests_loaded": index.has_forests,
    }


@app.get("/hotspots/recent")
def recent_hotspots(
    hours: int = Query(24, ge=1, le=720),
    france_only: bool = True,
    conn: sqlite3.Connection = Depends(get_db),
):
    return _rows(
        conn,
        f"""
        SELECT id, firms_source, satellite, acquisition_time, latitude, longitude,
               frp, brightness, bright_ti4, confidence, daynight,
               in_france, department_code, in_forest, forest_distance_m
          FROM raw_hotspots
         WHERE acquisition_time > datetime('now', :window)
           {"AND (in_france = 1 OR in_france IS NULL)" if france_only else ""}
         ORDER BY acquisition_time DESC
        """,
        {"window": f"-{hours} hours"},
    )


@app.get("/events/recent")
def recent_events(
    hours: int = Query(24, ge=1, le=720),
    min_priority: str = Query("low", pattern="^(low|medium|high|critical)$"),
    conn: sqlite3.Connection = Depends(get_db),
):
    rows = _rows(
        conn,
        """
        SELECT * FROM fire_events
         WHERE last_seen > datetime('now', :window)
         ORDER BY risk_score DESC, last_seen DESC
        """,
        {"window": f"-{hours} hours"},
    )
    floor = PRIORITY_ORDER[min_priority]
    return [_event_payload(row) for row in rows if PRIORITY_ORDER.get(row["priority"], 0) >= floor]


@app.get("/events/{event_id}")
def event_detail(event_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT * FROM fire_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "événement inconnu")
    payload = _event_payload(dict(row))
    payload["hotspots"] = _rows(
        conn,
        """
        SELECT h.id, h.firms_source, h.satellite, h.acquisition_time, h.latitude, h.longitude,
               h.frp, h.confidence, h.daynight
          FROM event_hotspots e JOIN raw_hotspots h ON h.id = e.hotspot_id
         WHERE e.event_id = :id
         ORDER BY h.acquisition_time
        """,
        {"id": event_id},
    )
    payload["alerts"] = _rows(
        conn,
        "SELECT id, channel, sent_at, delivery_status, risk_score_at_send FROM alerts"
        " WHERE event_id = :id ORDER BY sent_at DESC",
        {"id": event_id},
    )
    return payload


@app.get("/events.geojson")
def events_geojson(
    hours: int = Query(24, ge=1, le=720),
    min_priority: str = Query("low", pattern="^(low|medium|high|critical)$"),
    conn: sqlite3.Connection = Depends(get_db),
):
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [event["longitude"], event["latitude"]]},
            "properties": {
                key: event[key]
                for key in (
                    "id", "priority", "risk_score", "first_seen", "last_seen", "hotspot_count",
                    "source_count", "max_frp", "sum_frp", "in_forest", "forest_distance_m",
                    "department_code", "score_detail",
                )
            },
        }
        for event in recent_events(hours=hours, min_priority=min_priority, conn=conn)
    ]
    return {"type": "FeatureCollection", "features": features}


@app.get("/hotspots.geojson")
def hotspots_geojson(hours: int = Query(24, ge=1, le=720), conn: sqlite3.Connection = Depends(get_db)):
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [h["longitude"], h["latitude"]]},
            "properties": {
                key: h[key]
                for key in ("id", "satellite", "acquisition_time", "frp", "confidence", "in_france")
            },
        }
        for h in recent_hotspots(hours=hours, france_only=False, conn=conn)
    ]
    return {"type": "FeatureCollection", "features": features}


@app.post("/admin/ingest/firms", dependencies=[Depends(require_admin)])
def admin_ingest(days: int = Query(1, ge=1, le=10), conn: sqlite3.Connection = Depends(get_db)):
    map_key = os.environ.get("FIRMS_MAP_KEY")
    if not map_key:
        raise HTTPException(503, "FIRMS_MAP_KEY non configurée")
    inserted = firms.ingest(conn, map_key, day_range=days)
    index = geo.GeoIndex(DATA_DIR)
    enriched = geo.enrich_hotspots(conn, index)
    stats = events.rebuild_events(conn)
    sent = alerts.send_alerts(conn, os.environ.get("DISCORD_WEBHOOK_URL"))
    return {"inserted": inserted, "enriched": enriched, **stats, "alerts": sent}


@app.post("/admin/send-alerts", dependencies=[Depends(require_admin)])
def admin_send_alerts(conn: sqlite3.Connection = Depends(get_db)):
    """Rejoue la règle d'alerte immédiatement. L'anti-spam reste actif."""
    return {"alerts": alerts.send_alerts(conn, os.environ.get("DISCORD_WEBHOOK_URL"))}


@app.get("/", include_in_schema=False)
def carte():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/admin/recompute-events", dependencies=[Depends(require_admin)])
def admin_recompute(
    window_hours: int = Query(events.DEFAULT_WINDOW_HOURS, ge=1, le=720),
    conn: sqlite3.Connection = Depends(get_db),
):
    enriched = geo.enrich_hotspots(conn, geo.GeoIndex(DATA_DIR))
    return {"enriched": enriched, **events.rebuild_events(conn, window_hours=window_hours)}


