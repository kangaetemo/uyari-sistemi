"""Live score of alerted matches: written by the bot from data it already has, shown on the panel's /live page."""
import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.core.parsers import PrematchOdds
from app.core.monitor import Monitor
from app.db.repository import Repository
from app.web import queries
from app.web.server import create_app, match_status
from tests.test_live_stream import connected_stream
from tests.test_third_set import CONFIG, FakeAPI, live_payload, score_fixture

TZ = ZoneInfo("Europe/Istanbul")
AUTH = ("admin", "secret-pass")


def alerted_match(repo, key="77", side="Home"):
    repo.upsert_prematch(key, {"event_first_player": "Player A", "event_second_player": "Player B",
                               "tournament_name": "ATP Test"},
                         PrematchOdds(key, Decimal("1.2"), Decimal("4")), side, True)
    repo.record_live(repo.get_match(key), Decimal("2.10"), None, Decimal("2"))


def score(repo, key="77"):
    return repo.conn.execute("SELECT * FROM live_scores WHERE event_key=?", (key,)).fetchone()


def test_socket_scores_are_stored_only_for_alerted_matches(tmp_path):
    repo = Repository(tmp_path / "a.db")
    alerted_match(repo)
    api = FakeAPI(payload=live_payload(status="Set 2"))
    stream = connected_stream()
    monitor = Monitor(CONFIG, api, repo, None, stream)
    asyncio.run(monitor.live_scan())                     # Loads the alerted set.
    stream.handle(json.dumps({**score_fixture(points="30 - 15", games=("2", "1")), "event_serve": "First Player"}))
    stream.handle(json.dumps({**score_fixture(), "event_key": "88"}))
    row = score(repo)
    assert (row["status"], row["game_result"], row["serve"], row["source"]) == ("Set 3", "30 - 15", "First Player", "ws")
    assert json.loads(row["sets"]) == [["1", "6", "4"], ["2", "3", "6"], ["3", "2", "1"]]
    assert score(repo, "88") is None
    assert api.score_calls == 0
    repo.close()


def test_final_socket_score_is_not_blanked_by_the_odds_feed(tmp_path):
    repo = Repository(tmp_path / "a.db")
    alerted_match(repo)
    api = FakeAPI(payload=live_payload(status="Set 3"))
    stream = connected_stream()
    monitor = Monitor(CONFIG, api, repo, None, stream)
    asyncio.run(monitor.live_scan())
    stream.handle(json.dumps({**score_fixture(points="-", games=("6", "2")), "event_status": "Finished",
                              "event_final_result": "2 - 1", "event_winner": "First Player"}))
    api.payload = live_payload(status="Finished")
    asyncio.run(monitor.live_scan())
    row = score(repo)
    assert (row["status"], row["source"], row["winner"]) == ("Finished", "ws", "First Player")
    assert json.loads(row["sets"])[-1] == ["3", "6", "2"]
    repo.close()


def test_without_socket_the_odds_feed_status_is_used(tmp_path):
    repo = Repository(tmp_path / "a.db")
    alerted_match(repo)
    stream = connected_stream()
    stream.connected = False
    asyncio.run(Monitor(CONFIG, FakeAPI(payload=live_payload(status="Set 2", points="40 - 30")), repo, None, stream).live_scan())
    row = score(repo)
    assert (row["status"], row["game_result"], row["sets"], row["source"]) == ("Set 2", "40 - 30", None, "odds")
    repo.close()


def test_live_page_shows_score_odds_and_provisional_result(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    alerted_match(repo, "77")
    repo.record_live(repo.get_match("77"), Decimal("2.45"), None, Decimal("2"))
    repo.save_live_score("77", status="Set 3", final_result="1 - 1", game_result="30 - 15", serve="Second Player",
                         sets=json.dumps([["1", "6", "4"], ["2", "6.5", "7.7"], ["3", "2", "1"]]), winner=None, source="ws")
    alerted_match(repo, "78", side="Away")
    repo.save_live_score("78", status="Finished", final_result="2 - 0", game_result="-", serve=None,
                         sets=json.dumps([["1", "6", "3"], ["2", "6", "4"]]), winner="First Player", source="ws")
    repo.set_state("last_live_scan", datetime.now(timezone.utc).isoformat())
    repo.close()

    conn = queries.connect(path)
    rows, counts = queries.live_alerts(conn, 24)
    conn.close()
    assert [r["event_key"] for r in rows] == ["77", "78"]          # Live first.
    assert rows[0]["current_odds"] == "2.45" and rows[0]["points"] == ["30", "15"]
    assert (rows[1]["outcome"], rows[1]["provisional"]) == ("LOST", True)   # Away followed, Home won.
    assert (counts["live"], counts["final"], counts["LOST"]) == (1, 1, 1)

    client = TestClient(create_app(path, *AUTH, TZ))
    page = client.get("/live", auth=AUTH)
    assert page.status_code == 200 and "Canlı alarmlar" in page.text and "setInterval" in page.text
    part = client.get("/live?partial=1&hours=12", auth=AUTH)
    assert part.status_code == 200 and "<nav>" not in part.text
    assert "3. set" in part.text and "2.45" in part.text and "6<sup>5</sup>" in part.text
    assert "ön sonuç" in part.text and "Kaybetti" in part.text
    assert client.get("/live").status_code == 401


def test_live_page_works_before_the_bot_creates_the_table(tmp_path):
    path = tmp_path / "old.db"
    repo = Repository(path)
    alerted_match(repo)
    repo.conn.execute("DROP TABLE live_scores")
    repo.close()
    assert TestClient(create_app(path, *AUTH, TZ)).get("/live", auth=AUTH).status_code == 200


def test_status_names():
    assert match_status("Set 2") == "2. set"
    assert match_status("Finished") == "Bitti"
    assert match_status("Retired") == "Çekilme"
