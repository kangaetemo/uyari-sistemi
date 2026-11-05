from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.core.parsers import PrematchOdds
from app.db.repository import Repository
from app.web import queries
from app.web.server import create_app

TZ = ZoneInfo("Europe/Istanbul")
AUTH = ("admin", "secret-pass")


def add_match(repo, key, side, path, winner=None):
    repo.upsert_prematch(key, {"event_first_player": f"{key}A", "event_second_player": f"{key}B",
                               "event_type_type": "Itf Men Singles", "tournament_name": "M25"},
                         PrematchOdds(key, Decimal("1.2"), Decimal("1.2")), side, True)
    for odd in path:
        repo.record_live(repo.get_match(key), Decimal(odd), None, Decimal("2"))
    if winner:
        repo.save_result(key, winner, "2 - 0", "Finished", "FINISHED")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    add_match(repo, "1", "Home", ["1.5", "1.9", "2.1", "2.6"], winner="Home")   # alert 2.10, won
    add_match(repo, "2", "Home", ["1.5", "2.0", "1.7"], winner="Away")          # alert 2.00, lost
    add_match(repo, "3", "Home", ["1.4", "1.85", "1.6"], winner="Home")         # no alert, would win at 1.80
    add_match(repo, "4", "Away", ["2.3"])                                       # alert, pending
    repo.set_state("last_live_scan", datetime.now(timezone.utc).isoformat())
    repo.close()
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db, *AUTH, TZ))


def test_login_is_required(client):
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("admin", "wrong")).status_code == 401
    assert client.get("/health").json()["ok"] is True


@pytest.mark.parametrize("path", ["/", "/?days=0", "/alerts", "/alerts?rule=PREMATCH&result=WON", "/active",
                                  "/match/1", "/analysis", "/analysis?rule=THIRD_SET", "/analysis?pmin=1.1&pmax=abc"])
def test_pages_render(client, path):
    response = client.get(path, auth=AUTH)
    assert response.status_code == 200, response.text
    assert "çalışıyor" in response.text


def test_overview_numbers(client):
    text = client.get("/?days=0", auth=AUTH).text
    assert "%50.0" in text            # 1 won, 1 lost
    assert "Itf Men Singles" in text


def test_alert_filter(client):
    text = client.get("/alerts?result=LOST", auth=AUTH).text
    assert "/match/2" in text and "/match/1" not in text


def test_unknown_match_is_404(client):
    assert client.get("/match/999", auth=AUTH).status_code == 404


def test_threshold_analysis_replays_first_crossing(db):
    conn = queries.connect(db)
    rows = {row["threshold"]: row for row in queries.threshold_analysis(conn, "PREMATCH", 0)["rows"]}
    assert (rows[Decimal("2.00")]["won"], rows[Decimal("2.00")]["lost"]) == (1, 1)
    assert rows[Decimal("2.00")]["profit"] == Decimal("0.10")      # +1.10 -1
    assert (rows[Decimal("1.80")]["won"], rows[Decimal("1.80")]["lost"]) == (2, 1)
    assert (rows[Decimal("2.60")]["won"], rows[Decimal("2.60")]["lost"]) == (1, 0)
    assert queries.threshold_analysis(conn, "PREMATCH", 0)["pending"] == 1
    conn.close()


def test_missing_database_is_503(tmp_path):
    client = TestClient(create_app(tmp_path / "none.db", *AUTH, TZ))
    assert client.get("/", auth=AUTH).status_code == 503
