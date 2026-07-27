"""Ingestion du produit MTG FRP-PIXEL (LSA-509), EUMETSAT / LSA SAF.

Là où FIRMS dépend du passage d'un satellite en orbite polaire — 4 à 5 h de latence mesurée —
MTG est géostationnaire : une observation toutes les 10 minutes, à 1 km, publiée 20 à 45 min
après l'acquisition. C'est la piste que le README nomme en § Suite pour descendre nettement.

En contrepartie, 1 km voit moins bien les petits feux que les 375 m de VIIRS. Les deux sources
sont complémentaires et non concurrentes : MTG voit vite et gros, FIRMS voit fin et tard.
`events.summarize` compte les satellites distincts, donc un feu vu par les deux gagne
automatiquement le « confirmé par plusieurs satellites » du barème.

Inscription gratuite (immédiate) : https://mokey.lsasvcs.ipma.pt/auth/signup
Le produit est en statut « démonstration » : sa disponibilité n'est pas garantie, un créneau
manquant est donc un cas normal et non une panne.

ponytail: le même répertoire publie chaque créneau en `.csv.gz` et en `.nc`. Le CSV se lit avec
`urllib`, `gzip` et `csv` de la stdlib ; le NetCDF imposerait `netCDF4` pour la même information.
"""

from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from . import firms
from .db import now_utc

logger = logging.getLogger("pyrovigil.lsasaf")

PRODUCT_URL = (
    "https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MTG/MTFRPPixel/NATIVE"
    "/{slot:%Y/%m/%d}/LSA-509_MTG_MTFRPPIXEL-ListProduct_MTG-FD_{slot:%Y%m%d%H%M}.csv.gz"
)

SLOT_MINUTES = 10
SLOTS_PER_HOUR = 6

# Le fichier couvre tout le disque Meteosat : l'Afrique en saison de brûlis en fait l'essentiel.
# On filtre sur la même emprise que FIRMS, avant de construire quoi que ce soit.
WEST, SOUTH, EAST, NORTH = (float(value) for value in firms.BBOX_FRANCE.split(","))

# Colonnes du « ListProduct », vérifiées sur un fichier réel le 27/07/2026. Le produit en publie
# 28 ; les autres décrivent la fenêtre de fond, la géométrie de visée et le détail des termes
# d'incertitude. Le contrôle d'en-tête de `parse_list` reste : le produit est en démonstration,
# et il vaut mieux échouer en nommant ce qu'on a reçu que d'insérer des lignes vides en silence.
LATITUDE, LONGITUDE, FRP = "LATITUDE", "LONGITUDE", "FRP"
FIRE_CONFIDENCE = "FIRE_CONFIDENCE"
REQUIRED_COLUMNS = (LATITUDE, LONGITUDE, FRP)


class LsaSafError(RuntimeError):
    """Indisponibilité passagère : on dégrade, l'ingestion suivante réessaie."""


class LsaSafAuthError(RuntimeError):
    """Identifiants absents ou refusés.

    Volontairement *pas* une sous-classe de LsaSafError : une erreur de configuration ne doit
    pas être avalée comme une panne réseau. Sinon un job planifié annoncerait tranquillement
    « aucun feu » à chaque exécution.
    """


def _confidence(value: float | None) -> str:
    """FIRE_CONFIDENCE (0 à 1) vers les classes du barème.

    ponytail: mêmes seuils que la confiance MODIS en pourcentage, déjà traitée dans
    `firms._confidence` — 80 % et 30 %. C'est un précédent du projet, pas une calibration : les
    deux échelles ne mesurent pas rigoureusement la même chose. À revoir sur une vraie saison,
    avec le reste du barème MTG.
    """
    if value is None:
        return "unknown"
    if value >= 0.8:
        return "high"
    if value >= 0.3:
        return "nominal"
    return "low"


def slots(now: datetime | None = None, count: int = SLOTS_PER_HOUR) -> list[datetime]:
    """Les `count` derniers créneaux de 10 min, du plus récent au plus ancien.

    Le créneau le plus récent n'est en général pas encore publié — c'est la latence de
    production. `fetch_slot` renverra None et on passera au suivant. Six créneaux couvrent
    l'heure : le job horaire rattrape ce qu'un job plus fréquent aurait manqué.
    """
    now = (now or now_utc()).replace(second=0, microsecond=0)
    latest = now - timedelta(minutes=now.minute % SLOT_MINUTES)
    return [latest - timedelta(minutes=SLOT_MINUTES * step) for step in range(count)]


def url_for(slot: datetime) -> str:
    """L'URL se déduit du créneau : aucun listage de répertoire n'est nécessaire."""
    return PRODUCT_URL.format(slot=slot)


def fetch_slot(
    slot: datetime, user: str, password: str, timeout: int = 60, attempts: int = 3
) -> str | None:
    """Télécharge et décompresse un créneau. None si le produit n'est pas (encore) publié."""
    request = urllib.request.Request(url_for(slot))
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            # ipv4_only : les runners GitHub n'ont pas toujours de route IPv6, cf. firms.py
            with firms.ipv4_only(), urllib.request.urlopen(request, timeout=timeout) as response:
                return gzip.decompress(response.read()).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # créneau pas encore publié, ou trou du produit de démonstration
            if exc.code in (401, 403):
                raise LsaSafAuthError(
                    f"identifiants LSA SAF refusés (HTTP {exc.code}). Vérifiez LSASAF_USER et "
                    "LSASAF_PASSWORD — compte gratuit sur https://mokey.lsasvcs.ipma.pt/auth/signup"
                ) from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(2**attempt)
    raise LsaSafError(f"créneau {slot:%Y-%m-%d %H:%M} injoignable après {attempts} tentatives : {last}")


def parse_list(text: str, slot: datetime) -> list[dict]:
    """Transforme un ListProduct en lignes prêtes pour `firms.insert_hotspots`.

    L'horodatage vient du **créneau**, pas d'une colonne : c'est lui qui entre dans la clé de
    déduplication `UNIQUE (source, satellite, acquisition_time, latitude, longitude)`, il doit
    donc être déterministe. Le nom de fichier porte l'heure de début d'acquisition de l'image.
    """
    reader = csv.DictReader(io.StringIO(text))
    header = [(name or "").strip().upper() for name in (reader.fieldnames or [])]
    absentes = [name for name in REQUIRED_COLUMNS if name not in header]
    if absentes:
        raise LsaSafError(
            f"colonnes {', '.join(absentes)} absentes du ListProduct — "
            f"en-tête reçu : {', '.join(header) or '(vide)'}"
        )

    rows = []
    for source_row in reader:
        row = {(key or "").strip().upper(): value for key, value in source_row.items()}
        latitude, longitude = firms._float(row, LATITUDE), firms._float(row, LONGITUDE)
        if latitude is None or longitude is None:
            continue
        if not (SOUTH <= latitude <= NORTH and WEST <= longitude <= EAST):
            continue
        rows.append(
            {
                "source": "MTG",
                "firms_source": None,
                "satellite": "MTG-I1",
                "instrument": "FCI",
                "acquisition_time": slot,
                "latitude": latitude,
                "longitude": longitude,
                # Les températures de brillance du produit ne sont pas celles de VIIRS/MODIS.
                # `summarize` en fait un max : mélanger deux grandeurs différentes le fausserait.
                "brightness": None,
                "bright_ti4": None,
                "bright_ti5": None,
                "frp": firms._float(row, FRP),
                "confidence": _confidence(firms._float(row, FIRE_CONFIDENCE)),
                "daynight": "",
                "raw": json.dumps(row, ensure_ascii=False),
            }
        )
    return rows


def ingest(
    conn: sqlite3.Connection,
    user: str,
    password: str,
    count: int = SLOTS_PER_HOUR,
    now: datetime | None = None,
) -> int:
    """Ingère les derniers créneaux publiés. Ne lève pas sur une source indisponible.

    Contrairement à `firms.ingest`, une récupération vide n'est pas une anomalie : à 10 min de
    cadence, le prochain passage est dans 10 min. Faire rougir un job planifié parce qu'un
    produit en démonstration a un trou n'apprendrait rien à personne.
    """
    if not (user and password):
        raise LsaSafAuthError(
            "LSASAF_USER et LSASAF_PASSWORD manquants. Compte gratuit et immédiat sur\n"
            "  https://mokey.lsasvcs.ipma.pt/auth/signup"
        )

    rows, echecs, publies = [], 0, 0
    wanted = slots(now, count)
    for slot in wanted:
        try:
            text = fetch_slot(slot, user, password)
        except LsaSafError as exc:
            echecs += 1
            logger.warning("créneau MTG %s indisponible : %s", f"{slot:%H:%M}", exc)
            continue
        if text is not None:
            publies += 1
            rows.extend(parse_list(text, slot))

    if echecs == len(wanted):
        logger.warning("aucun créneau MTG récupéré sur %d tentés", len(wanted))
    elif not publies:
        logger.info("aucun créneau MTG publié sur la fenêtre demandée")
    return firms.insert_hotspots(conn, rows)
