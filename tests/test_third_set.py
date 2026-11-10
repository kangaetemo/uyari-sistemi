import asyncio
import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core.monitor import Monitor
from app.core.parsers import PrematchOdds, parse_live_odds
from app.core.third_set import FeedClock, opening_score, process_third_set
from app.db.repository import Repository
from app.notifications.telegram import format_alert

UPDATED = "2026-10-04 20:00:00"
CONFIG = SimpleNamespace(third_set_enabled=True,third_set_min=Decimal("1.30"),
    third_set_max=Decimal("1.60"),third_set_trigger=Decimal("2.00"),
    third_set_odds_max_age_seconds=30,live_trigger=Decimal("2.00"),timezone=ZoneInfo("Europe/Istanbul"))


def score_fixture(points="0 - 0", games=("0","0"), status="Set 3"):
    return {"event_key":"77","event_status":status,"event_game_result":points,
        "event_first_player":"Player A","event_second_player":"Player B","tournament_name":"ATP Test",
        "first_player_key":"10","second_player_key":"20","event_final_result":"1 - 1",
        "scores":[{"score_set":"1","score_first":"6","score_second":"4"},
                  {"score_set":"2","score_first":"3","score_second":"6"},
                  {"score_set":"3","score_first":games[0],"score_second":games[1]}]}


def live_payload(home="1.40",away="3.00",status="Set 3",points="0 - 0",updated=UPDATED,suspended="No",feed_updated=UPDATED):
    """Event 77 under test plus another live event that stands for the rest of the feed's clock."""
    return {"success":1,"result":{"77":{"event_key":77,"event_status":status,"event_game_result":points,
        "live_odds":[{"odd_name":"To Win","type":side,"value":value,"upd":updated,"suspended":suspended}
                     for side,value in (("Home",home),("Away",away))]},
        "88":{"event_key":88,"event_status":"Set 1","event_game_result":"15 - 0",
        "live_odds":[{"odd_name":"To Win","type":side,"value":"1.90","upd":feed_updated,"suspended":"No"}
                     for side in ("Home","Away")]}}}


class FakeAPI:
    def __init__(self, fixture=None, payload=None):
        self.fixture = fixture or score_fixture()
        self.payload = payload or live_payload()
        self.score_calls = 0
        self.live_calls = 0

    async def livescore(self):
        self.score_calls += 1
        return {"success":1,"result":[self.fixture]}

    async def live_odds(self):
        self.live_calls += 1
        return self.payload


class FakeMonotonic:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def clock_for(repo):
    # One feed clock per database, like one Monitor per process.
    if not hasattr(repo, "_test_clock"):
        repo._test_clock = FeedClock(FakeMonotonic())
    return repo._test_clock


def process(repo,payload=None,fixture=None):
    api = FakeAPI(fixture)
    asyncio.run(process_third_set(CONFIG,api,repo,parse_live_odds(payload or live_payload()),clock=clock_for(repo)))
    return api


@pytest.mark.parametrize("side,initial", [("Home","1.30"),("Home","1.60"),("Away","1.30"),("Away","1.60")])
def test_opening_boundaries_right_side_strict_threshold_and_one_alert(tmp_path,side,initial):
    repo = Repository(tmp_path / "a.db")
    process(repo,live_payload(home=initial if side == "Home" else "3",away=initial if side == "Away" else "3"))
    watches = repo.third_set_watched()
    assert len(watches) == 1 and watches[0]["favorite_side"] == side
    for odd in ["1.80","2.00","2.01","2.20"]:
        process(repo,live_payload(home=odd if side == "Home" else "9",away=odd if side == "Away" else "9"))
        if odd == "2.00":
            assert not repo.pending_alerts()
    alerts = repo.pending_alerts()
    assert len(alerts) == 1 and alerts[0]["rule_type"] == "THIRD_SET"
    assert alerts[0]["favorite_side"] == side
    assert alerts[0]["reference_odds"] == initial
    assert alerts[0]["previous_live_odds"] == "2.00"
    assert alerts[0]["trigger_live_odds"] == "2.01"
    text = format_alert(alerts[0],CONFIG.timezone)
    assert "3. set başlangıcı" in text and "Maç önü" not in text
    assert f"{initial}" in text
    repo.close()


@pytest.mark.parametrize("home", ["1.29","1.61"])
def test_opening_outside_range_cannot_enter_later(tmp_path,home):
    repo = Repository(tmp_path / "a.db")
    process(repo,live_payload(home=home))
    process(repo,live_payload(home="1.40"))
    assert not repo.third_set_watched() and repo.third_set_decided("77")
    repo.close()


@pytest.mark.parametrize("fixture", [score_fixture(games=("1","0")),score_fixture(games=("4","3"))])
def test_late_start_cannot_be_captured(tmp_path,fixture):
    repo = Repository(tmp_path / "a.db")
    process(repo,fixture=fixture)
    process(repo)  # Regressing provider scores must not re-open a missed start.
    assert not repo.third_set_watched()
    row = repo.conn.execute("SELECT decision,detail FROM third_set_sessions").fetchone()
    assert row["decision"] == "MISSED" and row["detail"].startswith(f"3. set {fixture['scores'][2]['score_first']}-")
    repo.close()


@pytest.mark.parametrize("points", ["15 - 0","40 - 30","-"])
def test_first_game_in_progress_is_still_the_opening(tmp_path,points):
    """The score and odds feeds lag each other; a 0-0 point snapshot practically never lined up."""
    repo = Repository(tmp_path / "a.db")
    process(repo,live_payload(points=points),fixture=score_fixture(points=points))
    watches = repo.third_set_watched()
    assert len(watches) == 1 and watches[0]["opening_odds"] == "1.40"
    detail = repo.conn.execute("SELECT detail FROM third_set_sessions").fetchone()[0]
    assert detail == f"3. set 0-0, puan {points}, oran 1.40/3.00"
    repo.close()


def test_set_three_without_its_own_score_row_is_the_opening(tmp_path):
    repo = Repository(tmp_path / "a.db")
    fixture = score_fixture()
    fixture["scores"] = fixture["scores"][:2]
    process(repo,fixture=fixture)
    assert len(repo.third_set_watched()) == 1
    repo.close()


def test_price_unchanged_through_the_set_break_is_the_opening(tmp_path):
    """The To Win price often does not move during the 120 s set break; its upd stays old."""
    repo = Repository(tmp_path / "a.db")
    process(repo,live_payload(updated="2026-10-04 19:57:30"))
    assert len(repo.third_set_watched()) == 1
    repo.close()


def test_stalled_feed_does_not_capture(tmp_path):
    repo = Repository(tmp_path / "a.db")
    clock = clock_for(repo)
    clock.observe(parse_live_odds(live_payload()))
    clock._monotonic.now += 120  # Nothing in the whole feed moved for two minutes.
    process(repo)
    assert not repo.third_set_decided("77")
    repo.close()


@pytest.mark.parametrize("payload", [live_payload(suspended="Yes"),live_payload(updated="2026-10-04 19:54:00"),live_payload(updated="bad")])
def test_invalid_opening_odds_not_captured(tmp_path,payload):
    repo = Repository(tmp_path / "a.db")
    process(repo,payload)
    assert not repo.third_set_watched() and not repo.third_set_decided("77")
    process(repo)  # Recover while the score still proves the opening.
    assert len(repo.third_set_watched()) == 1
    repo.close()


def test_missing_scores_are_not_assumed_zero(tmp_path):
    repo = Repository(tmp_path / "a.db")
    fixture = score_fixture()
    fixture.pop("scores")
    assert opening_score(fixture) is None
    process(repo,fixture=fixture)
    assert not repo.third_set_decided("77")
    repo.close()


def test_restart_and_independent_prematch_alert(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    repo.upsert_prematch("77",score_fixture(),PrematchOdds("77",Decimal("1.25"),Decimal("4")),"Home",True)
    repo.record_live(repo.get_match("77"),Decimal("2.10"),UPDATED,Decimal("2"))
    process(repo,live_payload(home="3",away="1.40"))
    repo.close()
    repo = Repository(path)
    process(repo,live_payload(home="1.5",away="2.10"))
    alerts = repo.pending_alerts()
    assert len(alerts) == 2
    assert {a["rule_type"] for a in alerts} == {"PREMATCH","THIRD_SET"}
    assert next(a for a in alerts if a["rule_type"] == "THIRD_SET")["favorite_side"] == "Away"
    repo.close()
    repo = Repository(path)
    process(repo,live_payload(home="1.5",away="2.50"))
    assert len(repo.pending_alerts()) == 2
    repo.close()


@pytest.mark.parametrize("status", ["Set 4","Finished","Cancelled","Retired"])
def test_only_third_set_can_trigger(tmp_path,status):
    repo = Repository(tmp_path / "a.db")
    process(repo)
    process(repo,live_payload(home="2.10",status=status))
    assert not repo.pending_alerts() and not repo.third_set_watched()
    repo.close()


def test_monitor_polls_without_prematch_watchlist(tmp_path):
    repo = Repository(tmp_path / "a.db")
    api = FakeAPI()
    # No third-set candidate avoids reliance on the real wall clock in this test.
    api.payload = live_payload(status="Set 2")
    telegram = SimpleNamespace()
    asyncio.run(Monitor(CONFIG,api,repo,telegram).scheduled_live_scan())
    assert api.live_calls == 1 and api.score_calls == 0
    repo.close()


def test_interruption_can_resume_same_watch(tmp_path):
    repo = Repository(tmp_path / "a.db")
    process(repo)
    process(repo,live_payload(status="Interrupted"))
    assert len(repo.third_set_watched()) == 1
    process(repo,live_payload(home="2.10"))
    assert len(repo.pending_alerts()) == 1
    repo.close()


def test_full_monitor_delivers_only_one_third_set_notification(tmp_path):
    class FakeTelegram:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(message)
            return "123"
    repo = Repository(tmp_path / "a.db")
    telegram = FakeTelegram()
    api = FakeAPI()
    monitor = Monitor(CONFIG,api,repo,telegram)
    updated = datetime.now(CONFIG.timezone).strftime("%Y-%m-%d %H:%M:%S")
    api.payload = live_payload(updated=updated,feed_updated=updated)
    asyncio.run(monitor.live_scan())
    api.payload = live_payload(home="2.10",updated=updated,feed_updated=updated)
    for _ in range(2):
        asyncio.run(monitor.live_scan())
        asyncio.run(monitor.deliver_alerts())
    assert len(telegram.messages) == 1 and "3. set başlangıcı" in telegram.messages[0]
    assert not repo.pending_alerts()
    assert repo.conn.execute("SELECT telegram_sent FROM alerts").fetchone()[0] == 1
    repo.close()


def test_legacy_migration_preserves_alerts_and_makes_backup(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    repo.upsert_prematch("77",score_fixture(),PrematchOdds("77",Decimal("1.25"),Decimal("4")),"Home",True)
    repo.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE alerts")
        conn.execute("""CREATE TABLE alerts(id INTEGER PRIMARY KEY,event_key TEXT UNIQUE,
            favorite_player_name TEXT,prematch_odds TEXT,previous_live_odds TEXT,trigger_live_odds TEXT,
            threshold TEXT,telegram_sent INTEGER,telegram_message_id TEXT,attempts INTEGER,
            next_attempt_at TEXT,created_at TEXT)""")
        conn.execute("INSERT INTO alerts VALUES (99,'77','Player A','1.25','1.90','2.10','2',1,'123',2,NULL,'2026-10-04T17:00:00+00:00')")
    repo = Repository(path)
    old = repo.conn.execute("SELECT * FROM alerts WHERE id=99").fetchone()
    assert old["telegram_sent"] == 1 and old["telegram_message_id"] == "123" and old["attempts"] == 2
    assert old["rule_type"] == "PREMATCH" and old["favorite_side"] == "Home"
    assert len(list(tmp_path.glob("*before-third-set-*.db"))) == 1
    process(repo)
    process(repo,live_payload(home="2.10"))
    assert repo.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2
    repo.close()


def test_simultaneous_outbox_calls_do_not_send_twice(tmp_path):
    class FakeTelegram:
        calls = 0

        async def send(self, message):
            self.calls += 1
            await asyncio.sleep(0)
            return "123"
    repo = Repository(tmp_path / "a.db")
    process(repo)
    process(repo,live_payload(home="2.10"))
    sender = FakeTelegram()
    monitor = Monitor(CONFIG,None,repo,sender)
    async def deliver():
        await asyncio.gather(monitor.deliver_alerts(),monitor.deliver_alerts())
    asyncio.run(deliver())
    assert sender.calls == 1
    repo.close()
