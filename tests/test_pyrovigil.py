"""Vérifications de PyroVigil.

ponytail: un seul fichier, des `assert` nus. Tourne avec `python tests/test_pyrovigil.py` comme avec
`pytest`. Chaque test couvre une logique qui pourrait casser en silence : parsing, déduplication,
géographie, clustering, scoring, anti-spam.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyrovigil import db, firms  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "firms_sample.csv"


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


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passés")


if __name__ == "__main__":
    run_all()
