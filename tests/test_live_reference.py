"""Matches without any prematch odds: the opening live price of set 1 stands in as the reference."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.monitor import Monitor
from app.db.repository import Repository
from app.notifications.telegram import format_alert
from app.web.server import create_app
from tests.test_third_set import CONFIG, FakeAPI, live_payload

CONFIG_REF = SimpleNamespace(**{**vars(CONFIG), "third_set_enabled": False}, prematch_min=Decimal("1.01"),
                             prematch_max=Decimal("1.35"), live_reference_enabled=True)


def set_one(games=("0", "0"), points="15 - 0"):
    return {"event_key": "77", "event_status": "Set 1", "event_game_result": points,
            "event_first_player": "B. Andreescu", "event_second_player": "L. Romero Gormaz",
            "first_player_key": "1", "second_player_key": "2", "tournament_name": "WTA Cali",
            "event_date": "2026-11-07", "event_time": "19:35", "event_type_type": "Challenger Women Singles",
            "scores": [{"score_set": "1", "score_first": games[0], "score_second": games[1]}]}


def monitor_for(repo, api, priced=("88",), ready=True, config=CONFIG_REF):
    monitor = Monitor(config, api, repo, None)
    monitor._priced, monitor._priced_ready = set(priced), ready
    return monitor


def test_first_live_price_becomes_the_reference_and_alerts(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI(fixture=set_one(), payload=live_payload(home="1.25", away="3.80", status="Set 1"))
    monitor = monitor_for(repo, api)
    asyncio.run(monitor.live_scan())
    match = repo.get_match("77")
    assert (match["favorite_side"], match["latest_prematch_odds"], match["prematch_source"], match["is_watched"]) == \
        ("Home", "1.25", "LIVE", 1)
    assert match["tournament_name"] == "WTA Cali" and match["match_start_time"] == "2026-11-07T19:35"
    calls = api.score_calls
    api.payload = live_payload(home="2.10", away="1.70", status="Set 2")
    asyncio.run(monitor.live_scan())
    assert api.score_calls == calls  # Judged once; no more livescore calls for it.
    alert = repo.pending_alerts()[0]
    text = format_alert(alert, CONFIG.timezone)
    assert "Maç önü: 1.25 (maç önü oranı yoktu; 1. set başındaki canlı oran)" in text
    repo.close()
    client = TestClient(create_app(tmp_path / "a.db", "admin", "secret-pass", CONFIG.timezone))
    assert "1. set başındaki canlı oran" in client.get("/match/77", auth=("admin", "secret-pass")).text


@pytest.mark.parametrize("games", [("1", "1"), ("2", "0")])
def test_too_late_in_set_one(tmp_path, games):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI(fixture=set_one(games), payload=live_payload(home="1.25", away="3.80", status="Set 1"))
    monitor = monitor_for(repo, api)
    asyncio.run(monitor.live_scan())
    asyncio.run(monitor.live_scan())
    assert repo.get_match("77") is None and api.score_calls == 1
    repo.close()


def test_unpriced_underdog_reference_is_stored_but_not_watched(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI(fixture=set_one(("1", "0")), payload=live_payload(home="1.60", away="2.30", status="Set 1"))
    asyncio.run(monitor_for(repo, api).live_scan())
    match = repo.get_match("77")
    assert (match["favorite_side"], match["latest_prematch_odds"], match["is_watched"]) == ("Home", "1.60", 0)
    repo.close()


@pytest.mark.parametrize("kwargs", [dict(priced=("77", "88")), dict(ready=False),
                                    dict(config=SimpleNamespace(**{**vars(CONFIG_REF), "live_reference_enabled": False}))])
def test_no_live_reference(tmp_path, kwargs):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI(fixture=set_one(), payload=live_payload(home="1.25", away="3.80", status="Set 1"))
    asyncio.run(monitor_for(repo, api, **kwargs).live_scan())
    assert repo.get_match("77") is None and api.score_calls == 0
    repo.close()


def test_known_match_is_left_alone(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI(fixture=set_one(), payload=live_payload(home="1.25", away="3.80", status="Set 1"))
    from app.core.parsers import PrematchOdds
    repo.upsert_prematch("77", set_one(), PrematchOdds("77", Decimal("1.50"), Decimal("2.50")), "Home", False)
    asyncio.run(monitor_for(repo, api).live_scan())
    assert repo.get_match("77")["prematch_source"] is None and api.score_calls == 0
    repo.close()
