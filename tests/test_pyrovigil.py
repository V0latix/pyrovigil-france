"""Vérifications de PyroVigil.

ponytail: un seul fichier, des `assert` nus. Tourne avec `python tests/test_pyrovigil.py` comme avec
`pytest`. Chaque test couvre une logique qui pourrait casser en silence : parsing, déduplication,
géographie, clustering, scoring, anti-spam.
"""

from __future__ import annotations

import contextlib
import json
import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyrovigil import alerts, db, events, firms, geo, lsasaf, scoring  # noqa: E402

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


def _carre_foret() -> dict:
    """Carré de forêt fictif : lon 6.30->6.40, lat 43.10->43.20."""
    return {
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


def test_distance_a_la_foret():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / geo.FORESTS_FILE).write_text(json.dumps(_carre_foret()))
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
        index = geo.GeoIndex(Path(tmp))  # hors ligne par défaut : aucun appel réseau dans les tests
        assert index.forest(43.15, 6.35) == (None, None)
        assert index.locate(43.15, 6.35)["in_forest"] is None


@contextlib.contextmanager
def _faux_masque_foret(reponse=_carre_foret):
    """Remplace l'appel au WFS IGN et enregistre les tuiles demandées."""
    appels: list[tuple[int, int]] = []

    def faux_wfs(tile_lat, tile_lon):
        appels.append((tile_lat, tile_lon))
        return reponse()

    original = geo._fetch_forest_tile
    geo._fetch_forest_tile = faux_wfs
    try:
        yield appels
    finally:
        geo._fetch_forest_tile = original


def test_masque_foret_ign_par_tuile():
    with _faux_masque_foret() as appels, tempfile.TemporaryDirectory() as tmp:
        index = geo.GeoIndex(Path(tmp), online=True)
        assert index.has_forests

        assert index.forest(43.15, 6.35) == (True, 0.0), "point au cœur de la forêt"
        assert index.forest(43.15, 6.39)[0] is True, "même point de vue de la tuile"
        assert appels == [(431, 63)], "une seule requête pour une même tuile de 0,1°"

        # tuile voisine : une requête de plus, distance plafonnée
        assert index.forest(43.15, 6.60) == (False, geo.MAX_FOREST_SEARCH_M)
        assert appels == [(431, 63), (431, 65)]


def test_masque_foret_injoignable_reste_neutre():
    with _faux_masque_foret(reponse=lambda: None) as appels, tempfile.TemporaryDirectory() as tmp:
        index = geo.GeoIndex(Path(tmp), online=True)
        assert index.forest(43.15, 6.35) == (None, None), "panne IGN : critère neutre, pas pénalisant"
        index.forest(43.16, 6.36)
        assert len(appels) == 1, "un échec ne se rejoue pas pour chaque point de la tuile"


def test_pas_de_requete_foret_hors_de_France():
    with _faux_masque_foret() as appels:
        index = geo.GeoIndex(DATA_DIR, online=True)
        if not index.has_departments:
            return
        assert index.locate(41.3874, 2.1686)["in_forest"] is None, "Barcelone : le masque IGN ne dit rien"
        assert appels == []
        assert index.locate(43.15, 6.35)["in_forest"] == 1
        assert appels == [(431, 63)]


def test_resolution_ipv4_restauree_meme_en_cas_d_erreur():
    import socket

    original = socket.getaddrinfo

    with firms.ipv4_only():
        familles = {info[0] for info in socket.getaddrinfo("localhost", 80)}
        assert familles == {socket.AF_INET}, f"IPv6 non filtré : {familles}"

    assert socket.getaddrinfo is original, "la résolution doit être restaurée"

    # même si la requête lève, le patch ne doit pas fuir sur le reste du processus
    try:
        with firms.ipv4_only():
            raise RuntimeError("boum")
    except RuntimeError:
        pass
    assert socket.getaddrinfo is original


def test_une_source_firms_en_panne_ne_bloque_pas_les_autres():
    conn = db.connect(":memory:")
    csv_texte = FIXTURE.read_text()
    appels = []

    def fetch_capricieux(source, map_key, day_range, bbox=firms.BBOX_FRANCE):
        appels.append(source)
        if source in ("VIIRS_NOAA20_NRT", "MODIS_NRT"):
            raise firms.FirmsError("simulation d'indisponibilité")
        return csv_texte

    original = firms.fetch_csv
    firms.fetch_csv = fetch_capricieux
    try:
        assert firms.ingest(conn, "cle-factice") == 8, "les sources valides doivent être ingérées"
        assert len(appels) == 4, "toutes les sources sont tentées"

        # si tout échoue en revanche, l'ingestion doit lever : un job planifié doit échouer bruyamment
        firms.fetch_csv = lambda *a, **k: (_ for _ in ()).throw(firms.FirmsError("tout est tombé"))
        try:
            firms.ingest(db.connect(":memory:"), "cle-factice")
            raise AssertionError("une panne totale doit lever FirmsError")
        except firms.FirmsError:
            pass
    finally:
        firms.fetch_csv = original


def _hotspot(lat, lon, minutes_ago=0, satellite="N", frp=50.0, confidence="high", **extra):
    """Hotspot minimal pour les tests de clustering et de scoring."""
    return {
        "id": extra.pop("id", None),
        "latitude": lat,
        "longitude": lon,
        "time": db.now_utc().replace(microsecond=0) - timedelta(minutes=minutes_ago),
        "satellite": satellite,
        "confidence": confidence,
        "frp": frp,
        "brightness": None,
        "bright_ti4": 350.0,
        "in_france": 1,
        "department_code": "83",
        "in_forest": extra.pop("in_forest", None),
        "forest_distance_m": extra.pop("forest_distance_m", None),
        **extra,
    }


def test_haversine():
    # 1 degré de latitude ≈ 111 km
    assert 110_000 < events.haversine_m(43.0, 6.0, 44.0, 6.0) < 112_000
    assert events.haversine_m(43.0, 6.0, 43.0, 6.0) == 0.0


def test_cluster_regroupe_les_hotspots_proches():
    # 3 pixels dans un rayon de ~500 m, sur 30 minutes -> un seul feu
    group = [
        _hotspot(43.1521, 6.3487, minutes_ago=30),
        _hotspot(43.1563, 6.3512, minutes_ago=30),
        _hotspot(43.1498, 6.3441, minutes_ago=0),
    ]
    assert len(events.cluster(group)) == 1

    # le même trio plus un point à 50 km -> deux feux distincts
    clusters = events.cluster(group + [_hotspot(43.6, 6.3, minutes_ago=10)])
    assert sorted(len(c) for c in clusters) == [1, 3]


def test_cluster_separe_dans_le_temps():
    # même position, mais 160 minutes d'écart (> 90) : deux événements
    same_place = [_hotspot(43.15, 6.35, minutes_ago=0), _hotspot(43.15, 6.35, minutes_ago=160)]
    assert len(events.cluster(same_place)) == 2

    # un passage intermédiaire fait le pont : la relation est transitive, le feu qui dure reste un seul
    # événement même si ses extrémités sont éloignées dans le temps
    bridged = same_place + [_hotspot(43.15, 6.35, minutes_ago=80)]
    assert len(events.cluster(bridged)) == 1


def test_cluster_regroupe_les_pixels_mtg_voisins():
    """La grille MTG espace ses pixels de ~1050 et ~1710 m : à 1500 m un front se fragmentait."""
    front = [_hotspot(44.877 + 0.015 * i, -0.890, satellite="MTG-I1") for i in range(4)]
    ecarts = [events.haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
              for a, b in zip(front, front[1:])]
    assert all(1650 < e < 1750 for e in ecarts), ecarts
    assert len(events.cluster(front)) == 1, "un front MTG contigu est un seul événement"

    # deux foyers réellement distincts ne doivent pas fusionner pour autant
    assert len(events.cluster([_hotspot(44.877, -0.890), _hotspot(44.920, -0.890)])) == 2


def test_cluster_confirme_par_deux_satellites():
    """Fragmenté, un feu vu par FIRMS et MTG perdait le bonus de confirmation croisée."""
    groupes = events.cluster([
        _hotspot(43.4900, 6.0550, satellite="N21"),
        _hotspot(43.4760, 6.0520, satellite="MTG-I1", minutes_ago=5),
    ])
    assert len(groupes) == 1
    assert events.summarize(groupes[0])["source_count"] == 2


def test_emprise_au_sol():
    """L'emprise est une surface : disque pour un pixel isolé, contour pour un front."""
    anneau = events.footprint([_hotspot(43.15, 6.35)])
    assert anneau[0] == anneau[-1], "un anneau GeoJSON est fermé"

    lons = [p[0] for p in anneau]
    lats = [p[1] for p in anneau]
    # ~700 m de rayon, et un cercle à l'écran : même largeur au sol en longitude qu'en latitude
    largeur_m = (max(lons) - min(lons)) * events.DEGREE_M * math.cos(math.radians(43.15))
    hauteur_m = (max(lats) - min(lats)) * events.DEGREE_M
    assert 1200 < hauteur_m < 1500, hauteur_m
    assert abs(largeur_m - hauteur_m) < 60, (largeur_m, hauteur_m)

    # un front de 3 pixels donne une emprise nettement plus longue que large
    front = events.footprint([_hotspot(43.15 + 0.015 * i, 6.35) for i in range(3)])
    etendue = (max(p[1] for p in front) - min(p[1] for p in front)) * events.DEGREE_M
    assert etendue > 4000, etendue


def test_summarize_agrege_le_groupe():
    summary = events.summarize(
        [
            _hotspot(43.10, 6.30, minutes_ago=60, satellite="N", frp=20.0, confidence="nominal"),
            _hotspot(43.20, 6.40, minutes_ago=10, satellite="N20", frp=100.0, confidence="high"),
        ]
    )
    assert summary["hotspot_count"] == 2
    assert summary["source_count"] == 2, "deux satellites distincts"
    assert summary["max_frp"] == 100.0
    assert summary["sum_frp"] == 120.0
    assert summary["confidence"] == "high"
    assert abs(summary["latitude"] - 43.15) < 1e-9
    assert db.from_sql(summary["last_seen"]) > db.from_sql(summary["first_seen"])


def test_rebuild_events_est_idempotent_et_stable():
    conn = db.connect(":memory:")
    firms.ingest_fixture(conn, FIXTURE)
    index = geo.GeoIndex(DATA_DIR)
    if index.has_departments:
        geo.enrich_hotspots(conn, index)

    first = events.rebuild_events(conn)
    assert first["created"] == first["events"] > 0
    ids = [r["id"] for r in conn.execute("SELECT id FROM fire_events ORDER BY id")]

    # relancer ne crée rien et conserve les identifiants (les alertes en dépendent)
    second = events.rebuild_events(conn)
    assert second["created"] == 0
    assert second["updated"] == second["events"] == first["events"]
    assert [r["id"] for r in conn.execute("SELECT id FROM fire_events ORDER BY id")] == ids

    # le massif des Maures : 4 hotspots proches, 2 satellites, un seul événement
    maures = conn.execute(
        "SELECT * FROM fire_events ORDER BY hotspot_count DESC LIMIT 1"
    ).fetchone()
    assert maures["hotspot_count"] == 4
    assert maures["source_count"] == 2
    assert maures["max_frp"] == 118.6


def _event(**overrides):
    base = {
        "last_seen": db.now_utc() - timedelta(minutes=10),
        "max_frp": 150.0,
        "hotspot_count": 4,
        "source_count": 2,
        "in_forest": 1,
        "forest_distance_m": 0.0,
    }
    return {**base, **overrides}


def test_score_feu_serieux_en_foret():
    result = scoring.score_event(_event(), confidence="high")
    # 30 fraîcheur + 25 FRP + 20 confiance + 25 forêt + 15 cluster + 10 multi-satellite = 125 -> plafonné
    assert result["priority"] == "critical"
    assert result["risk_score"] == 100.0
    assert any("forestière" in reason for reason, _points in result["reasons"])


def test_score_signal_faible_et_isole():
    result = scoring.score_event(
        _event(
            last_seen=db.now_utc() - timedelta(hours=8),
            max_frp=3.0,
            hotspot_count=1,
            source_count=1,
            in_forest=0,
            forest_distance_m=9000.0,
        ),
        confidence="low",
    )
    # ancien, faible, peu fiable, loin de toute forêt
    assert result["priority"] == "low"
    assert result["risk_score"] == 0.0


def test_score_sans_couche_foret_ne_penalise_pas():
    sans_foret = scoring.score_event(_event(in_forest=None, forest_distance_m=None), confidence="nominal")
    hors_foret = scoring.score_event(_event(in_forest=0, forest_distance_m=9000.0), confidence="nominal")
    assert sans_foret["risk_score"] > hors_foret["risk_score"]
    assert not any("forêt" in reason for reason, _points in sans_foret["reasons"])


def test_score_explicable():
    result = scoring.score_event(_event(), confidence="high")
    assert all(isinstance(reason, str) and isinstance(points, int) for reason, points in result["reasons"])
    assert len(result["reasons"]) == 6, result["reasons"]


def test_priorites_sur_la_fixture():
    conn = db.connect(":memory:")
    firms.ingest_fixture(conn, FIXTURE)
    index = geo.GeoIndex(DATA_DIR)
    if not index.has_departments:
        print("  --  test_priorites_sur_la_fixture ignoré (lancez `pyrovigil fetch-data`)")
        return
    geo.enrich_hotspots(conn, index)
    events.rebuild_events(conn)

    maures = dict(conn.execute("SELECT * FROM fire_events WHERE department_code = '83'").fetchone())
    landes = dict(conn.execute("SELECT * FROM fire_events WHERE department_code = '40'").fetchone())

    # gros foyer récent, confirmé par deux satellites, contre un pixel isolé, ancien et peu fiable
    assert maures["priority"] in ("high", "critical"), maures
    assert maures["risk_score"] > landes["risk_score"]
    assert landes["priority"] == "low", landes
    assert json.loads(maures["score_detail"]), "la justification du score est stockée"


def _base_avec_evenement(priority="critical", risk_score=90.0, minutes_ago=5):
    """Base en mémoire contenant un unique événement, sans passer par l'ingestion."""
    conn = db.connect(":memory:")
    conn.execute(
        """
        INSERT INTO fire_events (first_seen, last_seen, latitude, longitude, hotspot_count,
                                 source_count, max_frp, in_forest, department_code, risk_score, priority)
        VALUES (?, ?, 43.15, 6.35, 4, 2, 118.6, NULL, '83', ?, ?)
        """,
        (
            db.to_sql(db.now_utc() - timedelta(minutes=minutes_ago + 40)),
            db.to_sql(db.now_utc() - timedelta(minutes=minutes_ago)),
            risk_score,
            priority,
        ),
    )
    conn.commit()
    return conn


def test_alerte_uniquement_les_priorites_hautes():
    for priority, attendu in [("critical", 1), ("high", 1), ("medium", 0), ("low", 0)]:
        conn = _base_avec_evenement(priority=priority)
        assert len(alerts.pending_events(conn)) == attendu, priority


def test_pas_d_alerte_sur_un_evenement_ancien():
    conn = _base_avec_evenement(minutes_ago=180)
    assert alerts.pending_events(conn) == [], "au-delà d'une heure, l'événement n'est plus une alerte"


def test_anti_spam_une_seule_alerte():
    conn = _base_avec_evenement()

    assert len(alerts.send_alerts(conn)) == 1
    assert alerts.send_alerts(conn) == [], "le cooldown de 2 h bloque la seconde alerte"
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 1

    # sans webhook, l'alerte est tracée comme journalisée plutôt qu'envoyée
    assert conn.execute("SELECT delivery_status FROM alerts").fetchone()[0] == "logged"


def test_realerte_si_le_score_progresse():
    conn = _base_avec_evenement(risk_score=60.0)
    assert len(alerts.send_alerts(conn)) == 1

    conn.execute("UPDATE fire_events SET risk_score = 75")  # +15, sous le seuil
    conn.commit()
    assert alerts.send_alerts(conn) == []

    conn.execute("UPDATE fire_events SET risk_score = 95")  # +35 depuis la dernière alerte
    conn.commit()
    assert len(alerts.send_alerts(conn)) == 1, "un feu qui grossit nettement justifie un second message"
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 2


def test_alerte_discord_envoie_un_user_agent():
    """Discord répond 403 au `Python-urllib/x.y` par défaut : sans User-Agent, rien ne part jamais."""
    import urllib.request

    vus = {}

    class FausseReponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def capture(request, timeout=None):
        vus["headers"] = {k.lower(): v for k, v in request.header_items()}
        vus["url"] = request.full_url
        return FausseReponse()

    original = urllib.request.urlopen
    urllib.request.urlopen = capture
    try:
        conn = _base_avec_evenement()
        envoyees = alerts.send_alerts(conn, "https://discord.com/api/webhooks/1/x")
    finally:
        urllib.request.urlopen = original

    assert envoyees[0]["status"] == "sent", envoyees
    assert "pyrovigil" in vus["headers"].get("user-agent", "").lower(), vus["headers"]


def test_alerte_en_echec_ne_declenche_pas_le_cooldown():
    """Une alerte jamais arrivée ne doit pas en bloquer une autre pendant 2 h."""
    conn = _base_avec_evenement()

    # simule un envoi qui a échoué, comme lors d'une panne Discord
    conn.execute(
        "INSERT INTO alerts (event_id, channel, payload, risk_score_at_send, delivery_status)"
        " VALUES (1, 'discord', '{}', 90.0, 'failed')"
    )
    conn.commit()
    assert len(alerts.pending_events(conn)) == 1, "l'événement doit rester à alerter"

    # une fois réellement envoyée en revanche, le cooldown s'applique
    conn.execute("UPDATE alerts SET delivery_status = 'sent'")
    conn.commit()
    assert alerts.pending_events(conn) == []


def test_message_alerte_contient_l_essentiel():
    conn = _base_avec_evenement()
    message = alerts.format_message(alerts.pending_events(conn)[0])
    assert "CRITICAL" in message
    assert "43.15000, 6.35000" in message
    assert "openstreetmap.org" in message
    assert "ne remplace pas les secours" in message
    assert "donnée absente" in message, "sans couche forêt, le message ne doit rien affirmer"


# --- MTG FRP-PIXEL (LSA SAF) ------------------------------------------------------------------
#
# Extrait d'un vrai ListProduct (27/07/2026), réduit aux colonnes que le parsing utilise : le
# produit en publie 28. La dernière ligne est zambienne — le fichier couvre tout le disque Meteosat.
MTG_LISTE = """FIRE_CONFIDENCE,PIXEL_SIZE,LONGITUDE,LATITUDE,FRP,FRP_UNCERTAINTY
0.8823470916748047,1.92,6.3487,43.1521,118.6,12.4
0.6012340916748047,1.66,6.3550,43.1602,41.2,8.1
0.0100000000000000,1.51,28.7100,-12.4400,315.0,30.0
"""


def test_lsasaf_creneaux_deterministes():
    creneaux = lsasaf.slots(datetime(2026, 7, 26, 14, 37, 42, tzinfo=timezone.utc), count=3)
    assert creneaux[0] == datetime(2026, 7, 26, 14, 30, tzinfo=timezone.utc), "plancher à 10 min"
    assert creneaux[-1] == datetime(2026, 7, 26, 14, 10, tzinfo=timezone.utc), "du plus récent au plus ancien"
    assert lsasaf.url_for(creneaux[0]).endswith("/2026/07/26/LSA-509_MTG_MTFRPPIXEL-ListProduct_MTG-FD_202607261430.csv.gz")


def test_lsasaf_parse_liste():
    creneau = datetime(2026, 7, 26, 14, 30, tzinfo=timezone.utc)
    rows = lsasaf.parse_list(MTG_LISTE, creneau)

    # le point zambien du plein disque est hors emprise France
    assert len(rows) == 2, rows
    assert rows[0]["source"] == "MTG"
    assert rows[0]["satellite"] == "MTG-I1"
    assert rows[0]["frp"] == 118.6
    assert rows[0]["acquisition_time"] == creneau, "l'horodatage vient du créneau, pas d'une colonne"
    assert rows[0]["brightness"] is None and rows[0]["bright_ti4"] is None


def test_lsasaf_confiance():
    """FIRE_CONFIDENCE va de 0 à 1, aux mêmes seuils que la confiance MODIS en pourcentage."""
    assert lsasaf._confidence(0.88) == "high"
    assert lsasaf._confidence(0.60) == "nominal"
    assert lsasaf._confidence(0.01) == "low"
    assert lsasaf._confidence(None) == "unknown", "colonne absente : critère neutre, pas pénalisant"

    confiances = [r["confidence"] for r in lsasaf.parse_list(MTG_LISTE, db.now_utc())]
    assert confiances == ["high", "nominal"], confiances


def test_lsasaf_en_tete_inattendu_leve():
    """Le jour où le produit change de colonnes, il faut le savoir, pas insérer du vide."""
    try:
        lsasaf.parse_list("lat,lon,power\n43.1,6.3,10\n", db.now_utc())
        raise AssertionError("un en-tête inattendu doit lever")
    except lsasaf.LsaSafError as exc:
        assert "LATITUDE" in str(exc) and "LAT, LON, POWER" in str(exc), str(exc)


def test_lsasaf_deduplique():
    conn = db.connect(":memory:")
    creneau = datetime(2026, 7, 26, 14, 30, tzinfo=timezone.utc)
    rows = lsasaf.parse_list(MTG_LISTE, creneau)

    assert firms.insert_hotspots(conn, rows) == 2
    assert firms.insert_hotspots(conn, rows) == 0, "rejouer un créneau ne doit rien ajouter"


def test_lsasaf_creneau_absent_ou_en_panne_ne_leve_pas():
    """Produit en démonstration : un trou est normal, le prochain passage est dans 10 min."""
    conn = db.connect(":memory:")
    creneau = datetime(2026, 7, 26, 14, 30, tzinfo=timezone.utc)
    appels = []

    def fetch_capricieux(slot, user, password, **kwargs):
        appels.append(slot)
        if slot == creneau:
            return MTG_LISTE
        if slot.minute == 20:
            raise lsasaf.LsaSafError("simulation d'indisponibilité")
        return None  # pas encore publié

    original = lsasaf.fetch_slot
    lsasaf.fetch_slot = fetch_capricieux
    try:
        assert lsasaf.ingest(conn, "u", "p", count=3, now=creneau) == 2
        assert len(appels) == 3, "tous les créneaux sont tentés"

        # même quand tout échoue : on journalise, on n'échoue pas
        lsasaf.fetch_slot = lambda *a, **k: (_ for _ in ()).throw(lsasaf.LsaSafError("tout est tombé"))
        assert lsasaf.ingest(db.connect(":memory:"), "u", "p", count=3, now=creneau) == 0
    finally:
        lsasaf.fetch_slot = original


def test_lsasaf_identifiants_absents_sont_une_erreur_de_configuration():
    """Une clé oubliée ne doit pas ressembler à « aucun feu détecté »."""
    try:
        lsasaf.ingest(db.connect(":memory:"), "", "")
        raise AssertionError("des identifiants absents doivent lever")
    except lsasaf.LsaSafAuthError as exc:
        assert "mokey.lsasvcs.ipma.pt" in str(exc), "le message doit dire où s'inscrire"


def test_score_pixel_geostationnaire_repete_n_est_pas_un_groupe():
    """Six observations du même pixel à 10 min d'intervalle ne sont pas six foyers."""
    repete = [_hotspot(43.1521, 6.3487, minutes_ago=10 * i, satellite="MTG-I1") for i in range(6)]
    assert events.summarize(repete)["pixel_count"] == 1
    assert events.summarize(repete)["hotspot_count"] == 6, "le comptage brut reste disponible"

    groupes = [_hotspot(43.1521, 6.3487), _hotspot(43.1560, 6.3550)]
    assert events.summarize(groupes)["pixel_count"] == 2

    raisons = dict(scoring.score_event(events.summarize(repete), confidence="high")["reasons"])
    assert not any("pixels chauds groupés" in r for r in raisons), raisons
    raisons = dict(scoring.score_event(events.summarize(groupes), confidence="high")["reasons"])
    assert any("pixels chauds groupés" in r for r in raisons), raisons


def test_cli_une_source_en_panne_ne_tue_pas_l_autre():
    """Avec deux sources, une panne FIRMS ne doit plus emporter l'exécution entière."""
    import argparse
    import os

    from pyrovigil import cli

    conn = db.connect(":memory:")
    args = argparse.Namespace(source="all", days=1)
    creneau = datetime(2026, 7, 26, 14, 30, tzinfo=timezone.utc)

    originaux = (firms.ingest, lsasaf.ingest, os.environ.get("FIRMS_MAP_KEY"))
    firms.ingest = lambda *a, **k: (_ for _ in ()).throw(firms.FirmsError("tout est tombé"))
    lsasaf.ingest = lambda c, *a, **k: firms.insert_hotspots(c, lsasaf.parse_list(MTG_LISTE, creneau))
    os.environ["FIRMS_MAP_KEY"] = "cle-factice"
    try:
        assert cli._ingest_sources(conn, args) == 2, "MTG doit passer malgré la panne FIRMS"

        # les deux muettes en revanche : le job doit échouer, pas annoncer « aucun feu »
        lsasaf.ingest = lambda *a, **k: (_ for _ in ()).throw(lsasaf.LsaSafAuthError("pas d'identifiants"))
        assert cli._ingest_sources(db.connect(":memory:"), args) is None
    finally:
        firms.ingest, lsasaf.ingest, cle = originaux
        os.environ["FIRMS_MAP_KEY"] = cle or ""


def test_purge_conserve_evenements_et_alertes():
    from pyrovigil import cli

    conn = _base_avec_evenement()
    vieux = db.to_sql(db.now_utc() - timedelta(days=cli.PURGE_DAYS + 1))
    for acquisition in (vieux, db.to_sql(db.now_utc())):
        conn.execute(
            "INSERT INTO raw_hotspots (source, satellite, acquisition_time, latitude, longitude, raw)"
            " VALUES ('MTG', 'MTG-I1', ?, 43.15, 6.35, '{}')",
            (acquisition,),
        )
    conn.execute("INSERT INTO event_hotspots (event_id, hotspot_id) SELECT 1, id FROM raw_hotspots")
    conn.commit()
    alerts.send_alerts(conn)

    assert cli._purge(conn) == 1
    assert conn.execute("SELECT count(*) FROM raw_hotspots").fetchone()[0] == 1, "seul l'ancien part"
    assert conn.execute("SELECT count(*) FROM event_hotspots").fetchone()[0] == 1, "cascade"
    assert conn.execute("SELECT count(*) FROM fire_events").fetchone()[0] == 1, "l'événement survit"
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 1, "l'alerte survit"


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} tests passés")


if __name__ == "__main__":
    run_all()
