"""Filtrage géographique : France, département, forêt.

La bounding box FIRMS déborde sur l'Espagne, l'Italie, la Suisse, l'Allemagne et la Belgique. On ne
supprime pas ces points — le briefing veut conserver le brut pour rejouer les détections — on les marque
`in_france = 0`.

ponytail: pas de PostGIS. Les 96 départements tiennent dans 1 Mo de GeoJSON, Shapely les indexe en mémoire
au démarrage et répond en microsecondes. La couche forêt suit le même chemin quand elle est présente.
"""

from __future__ import annotations

import json
import math
import sqlite3
import urllib.request
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import transform
from shapely.strtree import STRtree

# 96 départements de France métropolitaine + Corse, ~1 Mo.
DEPARTMENTS_URL = "https://france-geojson.gregoiredavid.fr/repo/departements.geojson"
DEPARTMENTS_FILE = "departements.geojson"
FORESTS_FILE = "forests.geojson"

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


def _load_features(path: Path) -> tuple[list, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data["features"] if data.get("type") == "FeatureCollection" else [data]
    geoms, props = [], []
    for feature in features:
        if feature.get("geometry"):
            geoms.append(shape(feature["geometry"]))
            props.append(feature.get("properties") or {})
    return geoms, props


class GeoIndex:
    """Index spatial des départements et, si disponible, des forêts."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.departments: list = []
        self.department_props: list[dict] = []
        self._dep_tree: STRtree | None = None
        self.forests: list = []
        self._forest_tree: STRtree | None = None

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
        return self._forest_tree is not None

    def department(self, lat: float, lon: float) -> str | None:
        """Code du département contenant le point, None s'il est hors de France."""
        if self._dep_tree is None:
            return None
        point = Point(lon, lat)
        for index in self._dep_tree.query(point):
            if self.departments[index].contains(point):
                return self.department_props[index].get("code")
        return None

    def forest(self, lat: float, lon: float) -> tuple[bool | None, float | None]:
        """(dans une forêt, distance en mètres à la forêt la plus proche).

        (None, None) si aucune couche forêt n'est chargée. La distance est plafonnée à
        MAX_FOREST_SEARCH_M : au-delà, seule compte l'information « loin de tout ».
        """
        if self._forest_tree is None:
            return None, None

        point = Point(lon, lat)
        # fenêtre de recherche en degrés, généreuse par rapport au rayon métrique visé
        margin = MAX_FOREST_SEARCH_M / (_DEG_LON_M * math.cos(math.radians(lat)))
        box = point.buffer(margin, quad_segs=1)

        origin = Point(0, 0)
        nearest = MAX_FOREST_SEARCH_M
        for index in self._forest_tree.query(box):
            forest = self.forests[index]
            if forest.contains(point):
                return True, 0.0
            distance = _local_meters(forest, lat, lon).distance(origin)
            nearest = min(nearest, distance)
        return False, nearest

    def locate(self, lat: float, lon: float) -> dict:
        """Enrichissement complet d'un point."""
        department = self.department(lat, lon)
        in_forest, forest_distance = self.forest(lat, lon)
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

    where = "" if force else "WHERE in_france IS NULL OR (in_forest IS NULL AND :has_forests)"
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
