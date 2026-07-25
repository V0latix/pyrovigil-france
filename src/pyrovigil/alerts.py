"""Alertes sur les événements prioritaires (§13 du briefing).

Règle : priorité high ou critical, détection de moins d'une heure, et pas d'alerte déjà envoyée pour le
même événement depuis deux heures. Une réalerte est autorisée avant ce délai si le score a nettement
progressé — un feu qui grossit vaut un second message.

L'anti-spam est la partie qui compte. Un système qui crie trop finit ignoré, ce qui est pire que pas
d'alerte du tout.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.error
import urllib.request

logger = logging.getLogger("pyrovigil.alerts")

ALERT_PRIORITIES = ("high", "critical")
MAX_AGE_MINUTES = 60
COOLDOWN_HOURS = 2
RESCORE_DELTA = 20  # points de progression qui justifient une réalerte avant la fin du cooldown


def pending_events(conn: sqlite3.Connection) -> list[dict]:
    """Événements qui méritent une alerte maintenant."""
    rows = conn.execute(
        f"""
        SELECT e.*,
               (SELECT max(a.sent_at) FROM alerts a WHERE a.event_id = e.id) AS last_alert_at,
               (SELECT a.risk_score_at_send FROM alerts a
                 WHERE a.event_id = e.id ORDER BY a.sent_at DESC LIMIT 1) AS last_alert_score
          FROM fire_events e
         WHERE e.priority IN ({','.join('?' * len(ALERT_PRIORITIES))})
           AND e.last_seen > datetime('now', ?)
        """,
        (*ALERT_PRIORITIES, f"-{MAX_AGE_MINUTES} minutes"),
    ).fetchall()

    ready = []
    for row in rows:
        event = dict(row)
        if event["last_alert_at"] is None:
            ready.append(event)
            continue
        recent = conn.execute(
            "SELECT datetime(?) > datetime('now', ?)", (event["last_alert_at"], f"-{COOLDOWN_HOURS} hours")
        ).fetchone()[0]
        progressed = event["risk_score"] - (event["last_alert_score"] or 0) > RESCORE_DELTA
        if not recent or progressed:
            ready.append(event)
    return ready


def format_message(event: dict) -> str:
    forest = (
        "donnée absente"
        if event.get("in_forest") is None
        else "oui"
        if event["in_forest"]
        else f"non, à {event['forest_distance_m']:.0f} m" if event.get("forest_distance_m") else "non"
    )
    return (
        f"🔥 **Signal feu potentiel — {event['priority'].upper()}**\n"
        f"Score : {event['risk_score']:.0f}/100\n"
        f"Département : {event.get('department_code') or 'inconnu'}\n"
        f"Pixels chauds : {event['hotspot_count']} ({event['source_count']} satellite(s))\n"
        f"FRP max : {event.get('max_frp') or '—'} MW\n"
        f"Dernière détection : {event['last_seen']} UTC\n"
        f"Position : {event['latitude']:.5f}, {event['longitude']:.5f}\n"
        f"En forêt : {forest}\n"
        f"https://www.openstreetmap.org/?mlat={event['latitude']}&mlon={event['longitude']}#map=14/"
        f"{event['latitude']}/{event['longitude']}\n"
        f"_Signal satellite non vérifié — ne remplace pas les secours._"
    )


def _post_discord(webhook_url: str, content: str) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"content": content}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def send_alerts(conn: sqlite3.Connection, webhook_url: str | None = None) -> list[dict]:
    """Envoie les alertes dues et les trace en base.

    Sans webhook, le message est seulement journalisé et enregistré avec le statut `logged` : le
    déclenchement reste vérifiable en développement sans dépendre de Discord.
    """
    sent = []
    for event in pending_events(conn):
        content = format_message(event)
        status = "sent"

        if webhook_url:
            try:
                _post_discord(webhook_url, content)
            except (urllib.error.URLError, TimeoutError) as exc:
                status = "failed"
                logger.warning("échec de l'envoi Discord pour l'événement %s : %s", event["id"], exc)
        else:
            status = "logged"
            logger.info("alerte (aucun webhook configuré) :\n%s", content)

        with conn:
            conn.execute(
                """
                INSERT INTO alerts (event_id, channel, payload, risk_score_at_send, delivery_status)
                VALUES (:event_id, :channel, :payload, :risk_score, :status)
                """,
                {
                    "event_id": event["id"],
                    "channel": "discord" if webhook_url else "log",
                    "payload": json.dumps({"content": content}, ensure_ascii=False),
                    "risk_score": event["risk_score"],
                    "status": status,
                },
            )
        sent.append({"event_id": event["id"], "priority": event["priority"], "status": status})
    return sent
