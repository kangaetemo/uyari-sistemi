"""Regression tests for the review fixes: feed clock, side recompute, expiry, results, delivery, config, API."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.api.tennis import TennisAPI
from app.config import load_config
from app.core.monitor import Monitor
from app.core.parsers import LiveOdds, PrematchOdds
from app.core.results import alert_outcome, format_report, parse_result
from app.core.third_set import FeedClock
from app.db.repository import Repository
from tests.test_third_set import CONFIG, live_payload, process

TZ = ZoneInfo("Europe/Istanbul")


def item(home_upd, away_upd=None):
    return LiveOdds("1", Decimal("1.5"), Decimal("2.5"), home_upd, away_upd or home_upd, "Set 3", None)


# --- Feed clock ---------------------------------------------------------------

def test_real_provider_pair_is_fresh_whatever_the_timezone():
    # Real pair from the database: upd 11:25:06 (UTC+2) observed at 09:25:10 UTC.
    # The old code read upd as UTC+3 and computed an age of 3604 s.
    clock = FeedClock(lambda: 0.0)
    clock.observe({"1": item("2026-09-26 11:25:06"), "2": item("2026-09-26 11:25:10")})
    assert clock.fresh("2026-09-26 11:25:06", 30)
    assert not clock.fresh("2026-09-26 11:24:30", 30)


def test_stalled_feed_is_not_fresh():
    now = [0.0]
    clock = FeedClock(lambda: now[0])
    clock.observe({"1": item("2026-09-26 11:25:06")})
    now[0] = 31.0
    clock.observe({"1": item("2026-09-26 11:25:06")})  # Nothing moved for 31 s.
    assert not clock.fresh("2026-09-26 11:25:06", 30)
    clock.observe({"1": item("2026-09-26 11:25:40")})
    assert clock.fresh("2026-09-26 11:25:40", 30)


def test_one_stale_side_delays_the_decision_instead_of_dropping_it(tmp_path):
    repo = Repository(tmp_path / "a.db")
    payload = live_payload(home="2.80", away="1.45")
    payload["result"]["77"]["live_odds"][1]["upd"] = "2026-10-04 19:59:00"  # Away 60 s behind the feed.
    process(repo, payload)
    assert not repo.third_set_decided("77")
    process(repo, live_payload(home="2.80", away="1.45", updated="2026-10-04 20:00:05", feed_updated="2026-10-04 20:00:05"))
    watches = repo.third_set_watched()
    assert [w["favorite_side"] for w in watches] == ["Away"]
    repo.close()


def test_feed_disagreement_waits_instead_of_missing(tmp_path):
    repo = Repository(tmp_path / "a.db")
    process(repo, live_payload(points="15 - 0"))  # Livescore still says 0-0.
    assert not repo.third_set_decided("77")
    process(repo)
    assert len(repo.third_set_watched()) == 1
    repo.close()


def test_third_set_history_continues_after_alert_until_set_ends(tmp_path):
    repo = Repository(tmp_path / "a.db")
    process(repo)
    for odd in ("2.10", "2.40", "1.90"):
        process(repo, live_payload(home=odd))
    history = [r[0] for r in repo.conn.execute("SELECT live_odds FROM third_set_odds_history ORDER BY id")]
    assert history == ["1.40", "2.10", "2.40", "1.90"]
    assert len(repo.pending_alerts()) == 1
    process(repo, live_payload(status="Set 4"))
    assert repo.conn.execute("SELECT status FROM third_set_watches").fetchone()[0] == "ALERTED_CLOSED"
    repo.close()


# --- Prematch -----------------------------------------------------------------

class PrematchAPI:
    def __init__(self):
        self.home, self.away = "1.30", "3.50"

    async def fixtures(self, start, stop):
        return {"success": 1, "result": [{"event_key": "5", "event_status": "", "event_live": "0",
                "event_first_player": "A", "event_second_player": "B", "event_date": "2026-11-05", "event_time": "20:00"}]}

    async def prematch_odds(self, start, stop):
        return {"success": 1, "result": {"5": {"Home/Away": {"Home": {"bk": self.home}, "Away": {"bk": self.away}}}}}


def test_prematch_favourite_is_recomputed_until_live(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = PrematchAPI()
    config = SimpleNamespace(timezone=TZ, prematch_min=Decimal("1.01"), prematch_max=Decimal("1.35"))
    monitor = Monitor(config, api, repo, None)
    asyncio.run(monitor.prematch_scan())
    assert repo.get_match("5")["favorite_side"] == "Home"
    api.home, api.away = "3.40", "1.30"
    asyncio.run(monitor.prematch_scan())
    match = repo.get_match("5")
    assert match["favorite_side"] == "Away" and match["favorite_player_name"] == "B" and match["is_watched"] == 1
    repo.close()


def test_prematch_history_continues_after_alert(tmp_path):
    repo = Repository(tmp_path / "a.db")
    repo.upsert_prematch("1", {"event_first_player": "A"}, PrematchOdds("1", Decimal("1.2"), Decimal("4")), "Home", True)
    for odd in ("1.8", "2.1", "2.5", "1.6"):
        repo.record_live(repo.get_match("1"), Decimal(odd), None, Decimal("2"))
    assert repo.conn.execute("SELECT COUNT(*) FROM live_odds_history").fetchone()[0] == 4
    assert repo.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    assert "1" in repo.watched()
    repo.set_status("1", "FINISHED")
    assert "1" not in repo.watched()
    repo.close()


# --- Expiry -------------------------------------------------------------------

def test_expire_stale_closes_unseen_and_long_past_matches(tmp_path):
    repo = Repository(tmp_path / "a.db")
    old_start = (datetime.now(TZ) - timedelta(hours=10)).strftime("%Y-%m-%d")
    for key, when in (("old", old_start), ("new", (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d"))):
        repo.upsert_prematch(key, {"event_date": when, "event_time": "00:00"},
                             PrematchOdds(key, Decimal("1.2"), Decimal("4")), "Home", True)
    repo.upsert_prematch("live", {}, PrematchOdds("live", Decimal("1.2"), Decimal("4")), "Home", True)
    repo.record_live(repo.get_match("live"), Decimal("1.5"), None, Decimal("2"))
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    repo.conn.execute("UPDATE matches SET updated_at=? WHERE event_key='live'", (stale,))
    expired = repo.expire_stale(TZ, 8, 45)
    assert sorted(expired) == ["live", "old"]
    assert set(repo.watched()) == {"new"}
    repo.close()


# --- Results and report -------------------------------------------------------

@pytest.mark.parametrize("fixture,expected", [
    ({"event_status": "Finished", "event_winner": "First Player", "event_final_result": "2 - 1"}, ("Home", "FINISHED")),
    ({"event_status": "Finished", "event_final_result": "0 - 2"}, ("Away", "FINISHED")),
    ({"event_status": "Retired", "event_winner": "Second Player"}, ("Away", "RETIRED")),
    ({"event_status": "Cancelled", "event_winner": "First Player"}, (None, "CANCELLED")),
    ({"event_status": "Set 2"}, None),
    ({"event_status": "Postponed"}, None),
])
def test_parse_result(fixture, expected):
    result = parse_result(fixture)
    assert (result and (result.winner_side, result.outcome_type)) == expected


class ResultsAPI:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def fixtures(self, start, stop):
        self.calls.append((start, stop))
        return {"success": 1, "result": self.rows}


def test_collect_results_and_report(tmp_path):
    repo = Repository(tmp_path / "a.db")
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    for key, side in (("1", "Home"), ("2", "Away"), ("3", "Home")):
        repo.upsert_prematch(key, {"event_date": today, "event_time": "10:00"},
                             PrematchOdds(key, Decimal("1.2"), Decimal("1.2")), side, True)
        repo.record_live(repo.get_match(key), Decimal("2.00"), None, Decimal("2"))
    api = ResultsAPI([
        {"event_key": "1", "event_status": "Finished", "event_winner": "First Player", "event_final_result": "2 - 1"},
        {"event_key": "2", "event_status": "Finished", "event_winner": "First Player", "event_final_result": "2 - 0"},
        {"event_key": "3", "event_status": "Set 3"},
    ])
    monitor = Monitor(SimpleNamespace(timezone=TZ, match_expiry_hours=8, live_stale_minutes=45), api, repo, None)
    asyncio.run(monitor.maintenance())
    assert api.calls == [(today, today)]
    rows = repo.alerts_with_results(datetime.now(timezone.utc) - timedelta(days=1))
    assert [alert_outcome(r) for r in rows] == ["WON", "LOST", "PENDING"]
    assert "1" not in repo.watched()
    text = format_report(rows, 30)
    assert "Kazandı: 1 | Kaybetti: 1 | Bekliyor: 1" in text
    assert "Kazanma oranı: %50.0" in text and "Başabaş kazanma oranı: %50.0" in text
    assert "+0.00 birim" in text
    repo.close()


# --- Delivery -----------------------------------------------------------------

def make_alerts(repo, count):
    for key in map(str, range(count)):
        repo.upsert_prematch(key, {}, PrematchOdds(key, Decimal("1.2"), Decimal("4")), "Home", True)
        repo.record_live(repo.get_match(key), Decimal("2.1"), None, Decimal("2"))


def test_failing_alert_does_not_block_the_queue(tmp_path, caplog):
    class FlakyTelegram:
        calls = 0

        async def send(self, message):
            self.calls += 1
            if self.calls == 1:
                raise AttributeError("unexpected response")
            return "1"
    repo = Repository(tmp_path / "a.db")
    make_alerts(repo, 2)
    telegram = FlakyTelegram()
    with caplog.at_level(logging.ERROR):
        asyncio.run(Monitor(CONFIG, None, repo, telegram).deliver_alerts())
    rows = repo.conn.execute("SELECT telegram_sent,attempts FROM alerts ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(0, 1), (1, 0)]
    repo.close()


def test_two_processes_cannot_send_the_same_alert(tmp_path):
    path = tmp_path / "a.db"
    first, second = Repository(path), Repository(path)
    make_alerts(first, 1)
    alert_id = first.pending_alerts()[0]["id"]
    assert first.claim_alert(alert_id)
    assert not second.claim_alert(alert_id)
    first.close(), second.close()


def test_exhausted_alert_is_logged(tmp_path, caplog):
    repo = Repository(tmp_path / "a.db")
    make_alerts(repo, 1)
    alert_id = repo.pending_alerts()[0]["id"]
    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            repo.mark_failed(alert_id, 0)
    assert "artık denenmeyecek" in caplog.text
    assert not repo.pending_alerts()
    repo.close()


# --- Config -------------------------------------------------------------------

@pytest.mark.parametrize("name,value,message", [
    ("LIVE_TRIGGER", "abc", "LIVE_TRIGGER geçerli bir ondalık sayı"),
    ("PREMATCH_MAX", "NaN", "PREMATCH_MAX sonlu"),
    ("LIVE_TRIGGER", "1.30", "LIVE_TRIGGER, PREMATCH_MAX"),
    ("LIVE_POLL_SECONDS", "x", "LIVE_POLL_SECONDS tam sayı"),
])
def test_config_errors_are_readable(monkeypatch, name, value, message):
    monkeypatch.setenv("API_TENNIS_KEY", "k")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        load_config()


def test_report_needs_no_api_key(monkeypatch):
    monkeypatch.setenv("API_TENNIS_KEY", "")
    monkeypatch.setenv("DRY_RUN", "true")
    assert load_config(require_api=False).api_key == ""


# --- API client ---------------------------------------------------------------

def api_with(responses, monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return responses[min(len(calls) - 1, len(responses) - 1)]
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr("app.api.tennis.asyncio.sleep", fake_sleep)
    api = TennisAPI("key")
    asyncio.run(api.client.aclose())
    api.client = httpx.AsyncClient(base_url="https://api.api-tennis.com/tennis/", transport=httpx.MockTransport(respond))
    return api, calls, sleeps


def test_rate_limit_waits_once_briefly(monkeypatch):
    api, calls, sleeps = api_with([httpx.Response(429, headers={"Retry-After": "600"})], monkeypatch)
    assert asyncio.run(api.live_odds()) is None
    assert len(calls) == 1 and sleeps == [60]
    asyncio.run(api.close())


def test_auth_error_is_not_retried(monkeypatch, caplog):
    api, calls, sleeps = api_with([httpx.Response(401)], monkeypatch)
    with caplog.at_level(logging.ERROR):
        assert asyncio.run(api.live_odds()) is None
    assert len(calls) == 1 and "paket süresi" in caplog.text
    asyncio.run(api.close())


def test_server_error_is_retried(monkeypatch):
    api, calls, sleeps = api_with([httpx.Response(500), httpx.Response(200, json={"success": 1, "result": {}})], monkeypatch)
    assert asyncio.run(api.live_odds()) == {"success": 1, "result": {}}
    assert len(calls) == 2
    asyncio.run(api.close())

