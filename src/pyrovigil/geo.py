"""Filtrage géographique : France, département, forêt.

La bounding box FIRMS déborde sur l'Espagne, l'Italie, la Suisse, l'Allemagne et la Belgique. On ne
supprime pas ces points — le briefing veut conserver le brut pour rejouer les détections — on les marque
`in_france = 0`.

ponytail: pas de PostGIS. Les 96 départements tiennent dans 1 Mo de GeoJSON, Shapely les indexe en mémoire
au démarrage et répond en microsecondes.

La forêt, elle, ne tient pas dans un fichier : le Masque Forêt IGN compte 1,26 million de polygones. Comme
la France produit 10 à 200 hotspots par jour, groupés géographiquement, on interroge le WFS de la
Géoplateforme à la demande, tuile par tuile, au lieu de télécharger le pays entier. Un `forests.geojson`
déposé dans le dossier de données reste prioritaire : c'est le mode hors-ligne et celui des tests.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from .firms import ipv4_only

logger = logging.getLogger(__name__)

# 96 départements de France métropolitaine + Corse, ~1 Mo.
DEPARTMENTS_URL = "https://france-geojson.gregoiredavid.fr/repo/departements.geojson"
DEPARTMENTS_FILE = "departements.geojson"
FORESTS_FILE = "forests.geojson"

# Masque Forêt IGN 2021-2023, France métropolitaine, sans clé ni quota déclaré.
FOREST_WFS_URL = "https://data.geopf.fr/wfs/ows"
FOREST_LAYER = "IGNF_MASQUE-FORET.2021-2023:masque_foret"
# ~11 km de côté : quelques centaines de polygones, moins d'une seconde par requête.
FOREST_TILE_DEG = 0.1

# Au-delà, la distance à la forêt n'influence plus le score (cf. §10.2 : -20 si > 5 km).
MAX_FOREST_SEARCH_M = 5000

_DEG_LAT_M = 110_540.0
_DEG_LON_M = 111_320.0


def _local_meters(geom, lat0: float, lon0: float):
    """Projette une géométrie dans un repère métrique local centré sur (lat0, lon0).

    Approximation équirectangulaire : moins de 0,5 % d'erreur dans un rayon de quelques kilomètres, ce qui
    suffit largement pour des seuils à 500 m / 1500 m sur des pixels satellite de 375 m.
    """
    scale_x = _DEG_LON_M * math.cos(math.radians(lat0))
    return transform(lambda x, y: ((x - lon0) * scale_x, (y - lat0) * _DEG_LAT_M), geom)


def _features(data: dict) -> tuple[list, list[dict]]:
    features = data["features"] if data.get("type") == "FeatureCollection" else [data]
    geoms, props = [], []
    for feature in features:
        if feature.get("geometry"):
            geoms.append(shape(feature["geometry"]))
            props.append(feature.get("properties") or {})
    return geoms, props


def _load_features(path: Path) -> tuple[list, list[dict]]:
    return _features(json.loads(path.read_text(encoding="utf-8")))


def _nearest_forest(geoms: list, tree: STRtree, lat: float, lon: float) -> tuple[bool, float]:
    """(dans une forêt, distance en mètres), plafonnée à MAX_FOREST_SEARCH_M."""
    point = Point(lon, lat)
    # fenêtre de recherche en degrés, généreuse par rapport au rayon métrique visé
    margin = MAX_FOREST_SEARCH_M / (_DEG_LON_M * math.cos(math.radians(lat)))
    box = point.buffer(margin, quad_segs=1)

    origin = Point(0, 0)
    nearest = float(MAX_FOREST_SEARCH_M)
    for index in tree.query(box):
        forest = geoms[index]
        if forest.contains(point):
            return True, 0.0
        nearest = min(nearest, _local_meters(forest, lat, lon).distance(origin))
    return False, nearest


def _fetch_forest_tile(tile_lat: int, tile_lon: int) -> dict | None:
    """GeoJSON des forêts de la tuile, élargie de MAX_FOREST_SEARCH_M, ou None si l'IGN ne répond pas.

    L'appelant traite None comme « donnée absente » : le hotspot garde `in_forest NULL`, le score reste
    neutre et l'ingestion suivante réessaie.
    """
    lat0, lon0 = tile_lat * FOREST_TILE_DEG, tile_lon * FOREST_TILE_DEG
    lat1, lon1 = lat0 + FOREST_TILE_DEG, lon0 + FOREST_TILE_DEG
    margin_lat = MAX_FOREST_SEARCH_M / _DEG_LAT_M
    # la marge en longitude est la plus large au bord le plus proche du pôle
    margin_lon = MAX_FOREST_SEARCH_M / (
        _DEG_LON_M * math.cos(math.radians(max(abs(lat0), abs(lat1))))
    )
    query = urllib.parse.urlencode(
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": FOREST_LAYER,
            "SRSNAME": "EPSG:4326",  # la réponse GeoJSON sort en lon/lat
            "outputFormat": "application/json",
            # ponytail: jamais atteint (479 polygones au maximum mesuré) ; paginer si numberMatched déborde
            "count": 5000,
            # l'URN impose l'ordre lat/lon dans la bbox, contrairement à la réponse
            "BBOX": (
                f"{lat0 - margin_lat},{lon0 - margin_lon},{lat1 + margin_lat},{lon1 + margin_lon},"
                "urn:ogc:def:crs:EPSG::4326"
            ),
        }
    )
    try:
        with ipv4_only(), urllib.request.urlopen(f"{FOREST_WFS_URL}?{query}", timeout=60) as response:
            return json.loads(response.read())
    except (OSError, ValueError) as exc:
        logger.warning("masque forêt IGN injoignable pour la tuile %s/%s : %s", tile_lat, tile_lon, exc)
        return None


class GeoIndex:
    """Index spatial des départements et, si disponible, des forêts.

    `online` autorise l'interrogation du masque forêt IGN pour les tuiles manquantes. Il reste faux par
    défaut : ni les tests ni un import de bibliothèque ne doivent partir en réseau sans le demander.
    """

    def __init__(self, data_dir: Path, online: bool = False):
        self.data_dir = Path(data_dir)
        self.online = online
        self.departments: list = []
        self.department_props: list[dict] = []
        self._dep_tree: STRtree | None = None
        self.forests: list = []
        self._forest_tree: STRtree | None = None
        # tuiles forêt déjà interrogées ; None mémorise un échec pour ne pas le rejouer 200 fois
        self._tiles: dict[tuple[int, int], tuple[list, STRtree] | None] = {}

        dep_path = self.data_dir / DEPARTMENTS_FILE
        if dep_path.exists():
            self.departments, self.department_props = _load_features(dep_path)
            self._dep_tree = STRtree(self.departments)

        forest_path = self.data_dir / FORESTS_FILE
        if forest_path.exists():
            self.forests, _ = _load_features(forest_path)
            self._forest_tree = STRtree(self.forests)

    @property
    def has_departments(self) -> bool:
        return self._dep_tree is not None

    @property
    def has_forests(self) -> bool:
        return self._forest_tree is not None or self.online

    @property
    def forest_source(self) -> str | None:
        if self._forest_tree is not None:
            return f"fichier {FORESTS_FILE}"
        return "masque forêt IGN (WFS)" if self.online else None

    def department(self, lat: float, lon: float) -> str | None:
        """Code du département contenant le point, None s'il est hors de France."""
        if self._dep_tree is None:
            return None
        point = Point(lon, lat)
        for index in self._dep_tree.query(point):
            if self.departments[index].contains(point):
                return self.department_props[index].get("code")
        return None

    def _tile(self, lat: float, lon: float) -> tuple[list, STRtree] | None:
        """Polygones forestiers de la tuile contenant le point, téléchargés une seule fois par process."""
        key = (math.floor(lat / FOREST_TILE_DEG), math.floor(lon / FOREST_TILE_DEG))
        if key not in self._tiles:
            data = _fetch_forest_tile(*key)
            geoms = None if data is None else _features(data)[0]
            self._tiles[key] = None if geoms is None else (geoms, STRtree(geoms))
        return self._tiles[key]

    def forest(self, lat: float, lon: float) -> tuple[bool | None, float | None]:
        """(dans une forêt, distance en mètres à la forêt la plus proche).

        (None, None) si aucune couche forêt n'est disponible. La distance est plafonnée à
        MAX_FOREST_SEARCH_M : au-delà, seule compte l'information « loin de tout ».
        """
        if self._forest_tree is not None:
            return _nearest_forest(self.forests, self._forest_tree, lat, lon)
        if self.online:
            tile = self._tile(lat, lon)
            if tile is not None:
                return _nearest_forest(*tile, lat, lon)
        return None, None

    def locate(self, lat: float, lon: float) -> dict:
        """Enrichissement complet d'un point."""
        department = self.department(lat, lon)
        # hors de France, le masque IGN n'a rien à dire : ne pas le solliciter, et surtout ne pas
        # conclure « loin de toute forêt » sur une absence de donnée
        in_forest, forest_distance = self.forest(lat, lon) if department else (None, None)
        return {
            "in_france": None if not self.has_departments else int(department is not None),
            "department_code": department,
            "in_forest": None if in_forest is None else int(in_forest),
            "forest_distance_m": forest_distance,
        }


def download_departments(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / DEPARTMENTS_FILE
    with urllib.request.urlopen(DEPARTMENTS_URL, timeout=120) as response:
        target.write_bytes(response.read())
    return target


def enrich_hotspots(conn: sqlite3.Connection, index: GeoIndex, force: bool = False) -> int:
    """Calcule France / département / forêt pour les hotspots qui ne l'ont pas encore.

    `force` recalcule tout — utile après avoir ajouté la couche forêt.
    """
    if not index.has_departments:
        return 0

    # `in_france = 1` dans la seconde branche : hors de France `in_forest` reste NULL par construction,
    # sans quoi les points étrangers seraient repris à chaque exécution
    where = (
        ""
        if force
        else "WHERE in_france IS NULL OR (in_forest IS NULL AND in_france = 1 AND :has_forests)"
    )
    rows = conn.execute(
        f"SELECT id, latitude, longitude FROM raw_hotspots {where}",
        {} if force else {"has_forests": int(index.has_forests)},
    ).fetchall()

    updates = [
        {"id": row["id"], **index.locate(row["latitude"], row["longitude"])} for row in rows
    ]
    if updates:
        with conn:
            conn.executemany(
                """
                UPDATE raw_hotspots
                   SET in_france = :in_france,
                       department_code = :department_code,
                       in_forest = :in_forest,
                       forest_distance_m = :forest_distance_m
                 WHERE id = :id
                """,
                updates,
            )
    return len(updates)
