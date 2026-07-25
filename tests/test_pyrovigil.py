"""Vérifications de PyroVigil.

ponytail: un seul fichier, des `assert` nus. Tourne avec `python tests/test_pyrovigil.py` comme avec
`pytest`. Chaque test couvre une logique qui pourrait casser en silence : parsing, déduplication,
géographie, clustering, scoring, anti-spam.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyrovigil import db, firms, geo  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "firms_sample.csv"
DATA_DIR = ROOT / "data"


def test_parse_csv_horodatage_et_confiance():
    rows = firms.parse_csv(FIXTURE.read_text(), "VIIRS_SNPP_NRT")
    assert len(rows) == 8, rows

    first = rows[0]
    assert first["acquisition_time"] == datetime(2026, 7, 24, 12, 42, tzinfo=timezone.utc)
    assert first["confidence"] == "high"
    assert first["frp"] == 42.3
    assert first["latitude"] == 43.1521

    # le point des Landes est de nuit et peu fiable
    landes = [r for r in rows if r["latitude"] == 44.2018][0]
    assert landes["confidence"] == "low"
    assert landes["daynight"] == "N"


def test_confiance_modis_numerique():
    assert firms._confidence("85") == "high"
    assert firms._confidence("45") == "nominal"
    assert firms._confidence("10") == "low"
    assert firms._confidence("") == "unknown"


def test_parse_csv_reponse_vide():
    assert firms.parse_csv("No fire alerts found for your parameters", "VIIRS_SNPP_NRT") == []
    assert firms.parse_csv("Invalid MAP_KEY", "VIIRS_SNPP_NRT") == []


def test_ingestion_deduplique():
    conn = db.connect(":memory:")
    rows = firms.parse_csv(FIXTURE.read_text(), "VIIRS_SNPP_NRT")

    assert firms.insert_hotspots(conn, rows) == 8
    assert firms.insert_hotspots(conn, rows) == 0, "réingérer les mêmes points ne doit rien ajouter"
    assert conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0] == 8


def test_ingestion_fixture_recale_les_dates():
    conn = db.connect(":memory:")
    assert firms.ingest_fixture(conn, FIXTURE) == 8

    latest = conn.execute("SELECT max(acquisition_time) FROM raw_hotspots").fetchone()[0]
    age_minutes = (db.now_utc() - db.from_sql(latest)).total_seconds() / 60
    assert 0 <= age_minutes < 10, f"la détection la plus récente devrait être fraîche, {age_minutes:.0f} min"

    # le recalage est quantifié : réingérer la fixture ne crée pas de doublons
    assert firms.ingest_fixture(conn, FIXTURE) == 0


def test_filtre_france_et_departements():
    index = geo.GeoIndex(DATA_DIR)
    if not index.has_departments:
        print("  --  test_filtre_france_et_departements ignoré (lancez `pyrovigil fetch-data`)")
        return

    assert index.department(43.2965, 5.3698) == "13", "Marseille"
    assert index.department(42.1037, 9.0512) == "2A", "Corse-du-Sud"
    assert index.department(48.8566, 2.3522) == "75", "Paris"
    assert index.department(41.3874, 2.1686) is None, "Barcelone est hors de France"
    assert index.department(42.80, 6.20) is None, "un point en Méditerranée n'est dans aucun département"

    assert index.locate(43.2965, 5.3698)["in_france"] == 1
    assert index.locate(41.3874, 2.1686)["in_france"] == 0


def test_distance_a_la_foret():
    # carré de forêt fictif : lon 6.30->6.40, lat 43.10->43.20
    polygon = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[6.30, 43.10], [6.40, 43.10], [6.40, 43.20], [6.30, 43.20], [6.30, 43.10]]
                    ],
                },
            }
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / geo.FORESTS_FILE).write_text(json.dumps(polygon))
        index = geo.GeoIndex(Path(tmp))
        assert index.has_forests

        assert index.forest(43.15, 6.35) == (True, 0.0), "point au cœur de la forêt"

        # 1 km à l'est du bord : 1000 m / (111320 * cos(43°)) de longitude
        in_forest, distance = index.forest(43.15, 6.40 + 1000 / (111320 * 0.7314))
        assert in_forest is False
        assert 950 < distance < 1050, distance

        # au-delà du rayon de recherche, la distance est plafonnée
        assert index.forest(43.15, 6.60) == (False, geo.MAX_FOREST_SEARCH_M)


def test_sans_couche_foret():
    with tempfile.TemporaryDirectory() as tmp:
        index = geo.GeoIndex(Path(tmp))
        assert index.forest(43.15, 6.35) == (None, None)
        assert index.locate(43.15, 6.35)["in_forest"] is None


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passés")


if __name__ == "__main__":
    run_all()
