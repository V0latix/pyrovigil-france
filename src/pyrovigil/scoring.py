"""Score de priorité d'un événement (§10 du briefing).

Le score ne dit pas « c'est un feu à 98 % ». Il répond à : *ce signal mérite-t-il d'être remonté vite à un
humain ?* Il est donc volontairement lisible et auditable — chaque point attribué garde sa raison, stockée
en JSON avec l'événement et affichée sur la carte.

Un critère dont la donnée manque ne compte pas : sans couche forêt, le critère forêt vaut 0 plutôt que de
pénaliser à l'aveugle.
"""

from __future__ import annotations

from datetime import datetime

from .db import from_sql, now_utc

# §10.3 — seuils de priorité
THRESHOLDS = [(75, "critical"), (55, "high"), (30, "medium"), (float("-inf"), "low")]


def _freshness(age_minutes: float) -> list[tuple[str, int]]:
    if age_minutes < 30:
        return [("détection de moins de 30 min", 30)]
    if age_minutes < 60:
        return [("détection de moins d'1 h", 20)]
    if age_minutes < 180:
        return [("détection de moins de 3 h", 10)]
    return []


def _frp(frp: float | None) -> list[tuple[str, int]]:
    if frp is None:
        return []
    if frp >= 100:
        return [(f"puissance radiative très élevée ({frp:.0f} MW)", 25)]
    if frp >= 30:
        return [(f"puissance radiative élevée ({frp:.0f} MW)", 15)]
    if frp >= 10:
        return [(f"puissance radiative modérée ({frp:.0f} MW)", 8)]
    return []


def _confidence(confidence: str | None) -> list[tuple[str, int]]:
    return {
        "high": [("confiance satellite haute", 20)],
        "nominal": [("confiance satellite nominale", 10)],
        "low": [("confiance satellite basse", -15)],
    }.get(confidence or "", [])


def _forest(in_forest: int | None, distance_m: float | None) -> list[tuple[str, int]]:
    if in_forest is None and distance_m is None:
        return []  # couche forêt absente : critère neutre, pas pénalisant
    if in_forest:
        return [("dans une zone forestière", 25)]
    if distance_m is None:
        return []
    if distance_m < 500:
        return [(f"à {distance_m:.0f} m d'une forêt", 15)]
    if distance_m < 1500:
        return [(f"à {distance_m:.0f} m d'une forêt", 8)]
    if distance_m >= 5000:
        return [("à plus de 5 km de toute forêt", -20)]
    return []


def _cluster(pixel_count: int, source_count: int) -> list[tuple[str, int]]:
    """`pixel_count` compte les positions distinctes, pas les lignes.

    Une source géostationnaire réobserve le même pixel toutes les 10 min : compter les lignes
    accorderait le bonus de regroupement à un foyer unique vu six fois.
    """
    reasons = []
    if pixel_count > 1:
        reasons.append((f"{pixel_count} pixels chauds groupés", 15))
    if source_count > 1:
        reasons.append((f"confirmé par {source_count} satellites", 10))
    return reasons


def score_event(event: dict, confidence: str | None = None, now: datetime | None = None) -> dict:
    """Calcule score, priorité et justification d'un événement.

    `event` accepte une ligne de fire_events (sqlite3.Row convertie en dict) ou un résumé de
    `events.summarize`. `confidence` est la meilleure confiance des hotspots du groupe.
    """
    now = now or now_utc()
    last_seen = event["last_seen"]
    if isinstance(last_seen, str):
        last_seen = from_sql(last_seen)
    age_minutes = (now - last_seen).total_seconds() / 60

    reasons: list[tuple[str, int]] = []
    reasons += _freshness(age_minutes)
    reasons += _frp(event.get("max_frp"))
    reasons += _confidence(confidence or event.get("confidence"))
    reasons += _forest(event.get("in_forest"), event.get("forest_distance_m"))
    # repli sur hotspot_count pour les lignes fire_events déjà en base, qui n'ont pas de pixel_count
    reasons += _cluster(
        event.get("pixel_count") or event.get("hotspot_count") or 1, event.get("source_count") or 1
    )
    # ponytail: critères météo (vent, humidité, Météo des forêts) et détection des zones industrielles
    # non implémentés en v1 — ils s'ajoutent ici sans toucher au reste.

    total = sum(points for _, points in reasons)
    return {
        "risk_score": max(0.0, min(100.0, float(total))),
        "priority": next(name for threshold, name in THRESHOLDS if total >= threshold),
        "reasons": reasons,
    }
