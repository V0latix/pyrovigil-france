"""Regroupement des hotspots en événements feu.

Un même incendie génère plusieurs pixels chauds, vus par plusieurs satellites à quelques minutes
d'intervalle. La règle du briefing (§9.2) : deux hotspots appartiennent au même événement s'ils sont à
moins de 1500 m et à moins de 90 minutes l'un de l'autre. La relation est transitive, donc un feu qui
continue de brûler agrège les passages successifs.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter

from .db import from_sql, to_sql

CLUSTER_DISTANCE_M = 1500
CLUSTER_MINUTES = 90

# Fenêtre de recalcul. Plus large que 24 h pour qu'un feu qui dure reste un seul événement.
DEFAULT_WINDOW_HOURS = 48

EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def cluster(hotspots: list[dict]) -> list[list[dict]]:
    """Regroupe des hotspots par proximité spatio-temporelle (union-find).

    ponytail: comparaison naïve de toutes les paires, O(n²). La France produit quelques centaines de
    hotspots par fenêtre de 48 h, soit un temps de calcul négligeable. Passer à un index spatial
    (Shapely STRtree, déjà présent dans geo.py) au-delà de ~2000 points par passe.
    """
    parent = list(range(len(hotspots)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(len(hotspots)):
        a = hotspots[i]
        for j in range(i + 1, len(hotspots)):
            b = hotspots[j]
            minutes = abs((a["time"] - b["time"]).total_seconds()) / 60
            if minutes > CLUSTER_MINUTES:
                continue
            if haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"]) <= CLUSTER_DISTANCE_M:
                union(i, j)

    groups: dict[int, list[dict]] = {}
    for i, hotspot in enumerate(hotspots):
        groups.setdefault(find(i), []).append(hotspot)
    return list(groups.values())


def summarize(group: list[dict]) -> dict:
    """Agrège un groupe de hotspots en une ligne fire_events."""
    times = [h["time"] for h in group]
    brightnesses = [b for h in group for b in (h["brightness"], h["bright_ti4"]) if b is not None]
    frps = [h["frp"] for h in group if h["frp"] is not None]
    departments = [h["department_code"] for h in group if h["department_code"]]
    forest_flags = [h["in_forest"] for h in group if h["in_forest"] is not None]
    forest_distances = [h["forest_distance_m"] for h in group if h["forest_distance_m"] is not None]

    return {
        "first_seen": to_sql(min(times)),
        "last_seen": to_sql(max(times)),
        "latitude": sum(h["latitude"] for h in group) / len(group),
        "longitude": sum(h["longitude"] for h in group) / len(group),
        "hotspot_count": len(group),
        "source_count": len({h["satellite"] for h in group}),
        "max_frp": max(frps) if frps else None,
        "sum_frp": sum(frps) if frps else None,
        "max_brightness": max(brightnesses) if brightnesses else None,
        "in_france": 1 if any(h["in_france"] for h in group) else 0,
        "department_code": Counter(departments).most_common(1)[0][0] if departments else None,
        "in_forest": max(forest_flags) if forest_flags else None,
        "forest_distance_m": min(forest_distances) if forest_distances else None,
        "confidence": _best_confidence(group),
    }


_CONFIDENCE_RANK = {"low": 0, "unknown": 1, "nominal": 2, "high": 3}


def _best_confidence(group: list[dict]) -> str:
    """La meilleure confiance du groupe — un pixel « high » suffit à crédibiliser l'événement."""
    return max((h["confidence"] or "unknown" for h in group), key=lambda c: _CONFIDENCE_RANK.get(c, 1))


_HOTSPOT_COLUMNS = """
    h.id, h.latitude, h.longitude, h.acquisition_time, h.satellite, h.confidence,
    h.frp, h.brightness, h.bright_ti4, h.in_france, h.department_code,
    h.in_forest, h.forest_distance_m
"""


def _as_hotspots(rows) -> list[dict]:
    return [{**dict(row), "time": from_sql(row["acquisition_time"])} for row in rows]


def _load_window(conn: sqlite3.Connection, window_hours: int) -> list[dict]:
    return _as_hotspots(
        conn.execute(
            f"""
            SELECT {_HOTSPOT_COLUMNS}
              FROM raw_hotspots h
             WHERE h.acquisition_time > datetime('now', ?)
               AND (h.in_france = 1 OR h.in_france IS NULL)
             ORDER BY h.acquisition_time
            """,
            (f"-{window_hours} hours",),
        ).fetchall()
    )


def load_event_hotspots(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    """Tous les hotspots rattachés à un événement, y compris hors fenêtre de recalcul."""
    return _as_hotspots(
        conn.execute(
            f"""
            SELECT {_HOTSPOT_COLUMNS}
              FROM event_hotspots e
              JOIN raw_hotspots h ON h.id = e.hotspot_id
             WHERE e.event_id = ?
             ORDER BY h.acquisition_time
            """,
            (event_id,),
        ).fetchall()
    )


def _existing_event_ids(conn: sqlite3.Connection, hotspot_ids: list[int]) -> list[int]:
    """Événements déjà rattachés à l'un de ces hotspots, du plus ancien au plus récent."""
    placeholders = ",".join("?" * len(hotspot_ids))
    rows = conn.execute(
        f"SELECT DISTINCT event_id FROM event_hotspots WHERE hotspot_id IN ({placeholders})"
        " ORDER BY event_id",
        hotspot_ids,
    ).fetchall()
    return [row["event_id"] for row in rows]


def rebuild_events(conn: sqlite3.Connection, window_hours: int = DEFAULT_WINDOW_HOURS) -> dict:
    """Recalcule les événements de la fenêtre récente.

    Les identifiants sont stables : un cluster qui partage au moins un hotspot avec un événement
    existant reprend son id. Si un cluster en recouvre plusieurs (deux foyers qui se rejoignent), le
    plus ancien survit et absorbe les autres — sinon les alertes déjà envoyées perdraient leur cible.
    """
    groups = cluster(_load_window(conn, window_hours))

    created = updated = merged = 0
    with conn:
        for group in groups:
            hotspot_ids = [h["id"] for h in group]

            known = _existing_event_ids(conn, hotspot_ids)
            if known:
                event_id = known[0]
                if len(known) > 1:
                    # les liens des événements absorbés disparaissent en cascade, on les recrée juste après
                    conn.execute(
                        f"DELETE FROM fire_events WHERE id IN ({','.join('?' * (len(known) - 1))})",
                        known[1:],
                    )
                    merged += len(known) - 1
                updated += 1
            else:
                summary = _storable(summarize(group))
                event_id = conn.execute(
                    f"INSERT INTO fire_events ({', '.join(summary)})"
                    f" VALUES ({', '.join(':' + column for column in summary)})",
                    summary,
                ).lastrowid
                created += 1

            conn.executemany(
                "INSERT OR IGNORE INTO event_hotspots (event_id, hotspot_id) VALUES (?, ?)",
                [(event_id, hotspot_id) for hotspot_id in hotspot_ids],
            )

            # recalcul sur *tous* les hotspots de l'événement : ceux sortis de la fenêtre comptent encore
            summary = _storable(summarize(load_event_hotspots(conn, event_id)))
            conn.execute(
                """
                UPDATE fire_events
                   SET first_seen = :first_seen, last_seen = :last_seen,
                       latitude = :latitude, longitude = :longitude,
                       hotspot_count = :hotspot_count, source_count = :source_count,
                       max_frp = :max_frp, sum_frp = :sum_frp, max_brightness = :max_brightness,
                       in_france = :in_france, department_code = :department_code,
                       in_forest = :in_forest, forest_distance_m = :forest_distance_m,
                       updated_at = datetime('now')
                 WHERE id = :id
                """,
                {**summary, "id": event_id},
            )

    return {"events": len(groups), "created": created, "updated": updated, "merged": merged}


def _storable(summary: dict) -> dict:
    """La confiance sert au scoring mais n'est pas une colonne de fire_events."""
    return {key: value for key, value in summary.items() if key != "confidence"}
