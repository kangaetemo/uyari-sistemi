"""Serving-for-the-set rule: a heavy prematch favourite is a break down and the opponent serves for the set."""
import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.monitor import Monitor
from app.core.results import break_outcome, build_report, format_report, rule_outcome, serve_report, set_outcome
from app.core.parsers import PrematchOdds
from app.core.serve_for_set import outcome_update, serving_for_set
from app.db.repository import Repository
from app.notifications.telegram import format_alert
from app.web import queries
from app.web.server import create_app
from tests.test_live_stream import connected_stream
from tests.test_third_set import CONFIG, FakeAPI, live_payload

CONFIG_ON = SimpleNamespace(**vars(CONFIG), serve_for_set_enabled=True, serve_for_set_favorite_max=Decimal("1.35"))
AUTH = ("admin", "secret-pass")


def games(number, first, second):
    return {"set_number": f"Set {number}", "number_game": str(first + second), "player_served": "First Player"}


def fixture(set_games=(4, 5), set_number=2, serve="Second Player", points="0 - 0", last_served=None, last_game=None,
            status=None, earlier=(("6", "4"),)):
    """Event 77; the previous game of the current set was served by `last_served` (the other player by default)."""
    scores = [{"score_set": str(i + 1), "score_first": a, "score_second": b} for i, (a, b) in enumerate(earlier)]
    scores.append({"score_set": str(set_number), "score_first": str(set_games[0]), "score_second": str(set_games[1])})
    played = sum(set_games)
    last_served = last_served or ("First Player" if serve == "Second Player" else "Second Player")
    return {"event_key": "77", "event_status": status or f"Set {set_number}", "event_game_result": points,
            "event_serve": serve, "event_first_player": "Fav", "event_second_player": "Dog",
            "scores": scores,
            "pointbypoint": [{"set_number": f"Set {set_number}", "number_game": str(last_game or played),
                              "player_served": last_served}]}


def favorite_match(repo, key="77", home="1.20", away="4.50"):
    side = "Home" if Decimal(home) < Decimal(away) else "Away"
    repo.upsert_prematch(key, {"event_first_player": "Fav", "event_second_player": "Dog", "tournament_name": "ATP Test",
                               "event_type_type": "Itf Men Singles"},
                         PrematchOdds(key, Decimal(home), Decimal(away)), side, True)


def run_scan(repo, stream, api=None):
    api = api or FakeAPI(payload=live_payload(home="2.60", away="1.50", status="Set 2"))
    asyncio.run(Monitor(CONFIG_ON, api, repo, None, stream).live_scan())
    return api


def serve_alerts(repo):
    return repo.conn.execute("SELECT * FROM alerts WHERE rule_type='SERVE_FOR_SET' ORDER BY set_number").fetchall()


@pytest.mark.parametrize("score,server", [((3, 5), "Away"), ((4, 5), "Away"), ((5, 6), "Away"),
                                          ((5, 3), "Home"), ((5, 4), "Home"), ((6, 5), "Home")])
def test_break_up_and_serving_for_the_set(score, server):
    serve = "First Player" if server == "Home" else "Second Player"
    point = serving_for_set(fixture(score, serve=serve))
    assert (point.set_number, point.server) == (2, server)
    assert (point.server_games, point.receiver_games) == (max(score), min(score))


@pytest.mark.parametrize("score", [(4, 4), (2, 5), (4, 6), (6, 6), (5, 5), (0, 0)])
def test_other_scores_are_not_serving_for_the_set(score):
    assert serving_for_set(fixture(score)) is None


def test_receiver_at_set_point_score_is_not_flagged():
    # 3-5 with the favourite serving: it must hold first, nobody is serving for the set yet.
    assert serving_for_set(fixture((3, 5), serve="First Player")) is None


def test_stale_event_serve_is_overruled_by_point_by_point():
    # The underdog just held to 5-3, event_serve still names it; point-by-point says the favourite serves next.
    stale = fixture((3, 5), serve="Second Player", last_served="Second Player")
    assert serving_for_set(stale) is None


def test_game_in_progress_in_point_by_point():
    current = fixture((4, 5), points="15 - 0", last_served="Second Player", last_game=10)
    assert serving_for_set(current).server == "Away"


def test_without_point_by_point_event_serve_counts_once_a_point_is_played():
    bare = {**fixture((4, 5)), "pointbypoint": []}
    assert serving_for_set(bare) is None
    assert serving_for_set({**bare, "event_game_result": "0 - 15"}).server == "Away"


def test_finished_match_is_ignored():
    assert serving_for_set(fixture((4, 5), status="Finished")) is None


def test_alert_once_per_set_with_live_odds_and_message(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    stream = connected_stream(fixture((4, 5)))
    api = run_scan(repo, stream)
    run_scan(repo, stream)
    rows = serve_alerts(repo)
    assert [(r["set_number"], r["favorite_side"], r["trigger_live_odds"], r["reference_odds"]) for r in rows] == \
        [(2, "Home", "2.60", "1.20")]
    assert api.score_calls == 0
    stored = repo.conn.execute("SELECT * FROM serve_for_set").fetchone()
    assert (stored["favorite_games"], stored["opponent_games"], stored["game_result"]) == (4, 5, "0 - 0")
    text = format_alert(next(a for a in repo.pending_alerts() if a["rule_type"] == "SERVE_FOR_SET"), CONFIG.timezone)
    assert "SET İÇİN SERVİS" in text and "2. set: 4-5 geride" in text and "servis: Dog" in text
    assert "Maç önü: 1.20" in text and "Canlı oran: 2.60" in text
    # Another set of the same match alerts again.
    stream.handle(json.dumps(fixture((5, 6), set_number=3, earlier=(("6", "4"), ("6", "7")))))
    run_scan(repo, stream, FakeAPI(payload=live_payload(home="3.10", away="1.40", status="Set 3")))
    assert [r["set_number"] for r in serve_alerts(repo)] == [2, 3]
    repo.close()


def test_away_favorite(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo, home="5.00", away="1.15")
    run_scan(repo, connected_stream(fixture((5, 3), serve="First Player")),
             FakeAPI(payload=live_payload(home="1.40", away="2.80", status="Set 2")))
    assert [(r["favorite_side"], r["trigger_live_odds"]) for r in serve_alerts(repo)] == [("Away", "2.80")]
    repo.close()


@pytest.mark.parametrize("home,away,score,serve", [
    ("1.20", "4.50", (5, 4), "First Player"),   # The favourite itself serves for the set.
    ("1.50", "2.60", (4, 5), "Second Player"),  # Not a heavy favourite.
])
def test_no_alert(tmp_path, home, away, score, serve):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo, home=home, away=away)
    run_scan(repo, connected_stream(fixture(score, serve=serve)))
    assert serve_alerts(repo) == []
    repo.close()


def test_match_without_prematch_reference_is_skipped(tmp_path):
    repo = Repository(tmp_path / "a.db")
    run_scan(repo, connected_stream(fixture((4, 5))))
    assert serve_alerts(repo) == []
    repo.close()


def test_rule_off(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    off = SimpleNamespace(**{**vars(CONFIG_ON), "serve_for_set_enabled": False})
    asyncio.run(Monitor(off, FakeAPI(payload=live_payload(home="2.60", status="Set 2")), repo, None,
                        connected_stream(fixture((4, 5)))).live_scan())
    assert serve_alerts(repo) == []
    repo.close()


def test_disconnected_socket_falls_back_to_one_livescore_call(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    stream = connected_stream()
    stream.connected = False
    api = FakeAPI(fixture=fixture((4, 5)), payload=live_payload(home="2.60", status="Set 2"))
    run_scan(repo, stream, api)
    assert api.score_calls == 1 and len(serve_alerts(repo)) == 1
    repo.close()


def outcome_row(**changes):
    return {"set_number": 2, "favorite_side": "Home", "favorite_games": 4, "opponent_games": 5,
            "game_outcome": None, "set_outcome": None, **changes}


@pytest.mark.parametrize("row,later,expected", [
    (outcome_row(), fixture((4, 6), set_number=2, status="Set 3"), ("HELD", "LOST")),
    (outcome_row(), fixture((5, 5)), ("BROKE", None)),
    (outcome_row(), fixture((4, 5), points="15 - 30"), None),
    (outcome_row(game_outcome="BROKE"), fixture((7, 5), status="Set 3"), ("BROKE", "WON")),
    (outcome_row(game_outcome="BROKE"), fixture((6, 7), status="Set 3"), ("BROKE", "LOST")),
    (outcome_row(game_outcome="BROKE"), fixture((6, 5)), None),
    (outcome_row(favorite_side="Away"), fixture((6, 4), status="Set 3"), ("HELD", "LOST")),
])
def test_outcome_update(row, later, expected):
    assert outcome_update(row, later) == expected


def test_socket_follows_the_outcome(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    stream = connected_stream(fixture((4, 5)))
    api = FakeAPI(payload=live_payload(home="2.60", status="Set 2"))
    asyncio.run(Monitor(CONFIG_ON, api, repo, None, stream).live_scan())
    stream.handle(json.dumps(fixture((5, 5), serve="First Player")))
    stored = lambda: tuple(repo.conn.execute("SELECT game_outcome,set_outcome FROM serve_for_set").fetchone())
    assert stored() == ("BROKE", None)
    stream.handle(json.dumps({**fixture((7, 5), status="Set 3"), "event_game_result": "0 - 0"}))
    assert stored() == ("BROKE", "WON")
    repo.close()


def test_match_result_settles_the_deciding_set(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    stream = connected_stream(fixture((4, 5), set_number=3, earlier=(("6", "4"), ("3", "6"))))
    monitor = Monitor(CONFIG_ON, FakeAPI(payload=live_payload(home="2.60", status="Set 3")), repo, None, stream)
    asyncio.run(monitor.live_scan())
    monitor._store_result("77", {"event_status": "Finished", "event_winner": "First Player", "event_final_result": "2 - 1"})
    row = repo.conn.execute("SELECT game_outcome,set_outcome FROM serve_for_set").fetchone()
    assert tuple(row) == ("BROKE", "WON")
    repo.close()


def test_old_alerts_table_is_migrated(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    favorite_match(repo)
    repo.record_live(repo.get_match("77"), Decimal("2.10"), None, Decimal("2"))
    repo.close()
    conn = sqlite3.connect(path)
    conn.executescript("""ALTER TABLE alerts RENAME TO new_alerts;
        CREATE TABLE alerts (id INTEGER PRIMARY KEY, event_key TEXT NOT NULL REFERENCES matches(event_key),
         favorite_player_name TEXT, prematch_odds TEXT, previous_live_odds TEXT, trigger_live_odds TEXT, threshold TEXT,
         telegram_sent INTEGER NOT NULL DEFAULT 0, telegram_message_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
         next_attempt_at TEXT, created_at TEXT NOT NULL, rule_type TEXT NOT NULL DEFAULT 'PREMATCH',
         favorite_side TEXT, reference_odds TEXT, UNIQUE(event_key,rule_type,favorite_side));
        INSERT INTO alerts SELECT id,event_key,favorite_player_name,prematch_odds,previous_live_odds,trigger_live_odds,
         threshold,telegram_sent,telegram_message_id,attempts,next_attempt_at,created_at,rule_type,favorite_side,reference_odds
         FROM new_alerts;
        DROP TABLE new_alerts;""")
    conn.commit()
    conn.close()
    repo = Repository(path)
    assert [(r["rule_type"], r["set_number"], r["trigger_live_odds"]) for r in repo.conn.execute("SELECT * FROM alerts")] == \
        [("PREMATCH", 0, "2.10")]
    assert list(tmp_path.glob("a.before-serve-for-set-*.db"))
    point = serving_for_set(fixture((4, 5)))
    match = repo.serve_for_set_favorites(["77"], Decimal("1.35"))["77"]
    assert repo.record_serve_for_set(match, point, Decimal("2.6"), Decimal("1.35"))
    assert repo.record_serve_for_set(match, replace(point, set_number=3), Decimal("2.9"), Decimal("1.35"))
    assert len(serve_alerts(repo)) == 2
    repo.close()


def test_panel_shows_the_rule(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    favorite_match(repo)
    stream = connected_stream(fixture((4, 5)))
    monitor = Monitor(CONFIG_ON, FakeAPI(payload=live_payload(home="2.60", status="Set 2")), repo, None, stream)
    asyncio.run(monitor.live_scan())
    stream.handle(json.dumps(fixture((5, 5), serve="First Player")))
    repo.close()
    client = TestClient(create_app(path, *AUTH, CONFIG.timezone))
    overview = client.get("/", auth=AUTH).text
    assert "Maç sonu alarmları" in overview and "Set kırma alarmları (set için servis)" in overview
    assert "Break (o oyun)" in overview and "Aynı alarmlarda maç sonucu" in overview
    listing = client.get("/alerts?rule=SERVE_FOR_SET", auth=AUTH).text
    assert "2. set 4-5" in listing and "Break yaptı" in listing and "maç: bekliyor" in listing
    # The break is the rule's own result, so the filter follows it.
    assert "2. set 4-5" in client.get("/alerts?rule=SERVE_FOR_SET&result=WON", auth=AUTH).text
    assert "2. set 4-5" not in client.get("/alerts?rule=SERVE_FOR_SET&result=PENDING", auth=AUTH).text
    detail = client.get("/match/77", auth=AUTH).text
    assert "4-5 geride" in detail and "break yaptı" in detail
    assert client.get("/live", auth=AUTH).status_code == 200
    assert client.get("/analysis?rule=SERVE_FOR_SET", auth=AUTH).status_code == 200


def test_socket_wakes_the_live_loop_only_for_a_favorite(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    stream = connected_stream()
    monitor = Monitor(CONFIG_ON, FakeAPI(), repo, None, stream)
    stream.handle(json.dumps(fixture((5, 4), serve="First Player")))  # The favourite serves for the set.
    assert not monitor._scan_now.is_set()
    stream.handle(json.dumps(fixture((4, 5))))
    assert monitor._scan_now.is_set()
    repo.close()


def test_match_missing_from_socket_is_judged_from_livescore(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    stream = connected_stream({"event_key": "99", "event_status": "Set 1"})  # Connected, but without match 77.
    api = FakeAPI(fixture=fixture((4, 5)), payload=live_payload(home="2.60", status="Set 2"))
    monitor = Monitor(CONFIG_ON, api, repo, None, stream)
    asyncio.run(monitor.live_scan())
    assert api.score_calls == 1 and [r["set_number"] for r in serve_alerts(repo)] == [2]
    # Its outcome is followed from get_livescore as well.
    api.fixture = fixture((5, 5), serve="First Player")
    asyncio.run(monitor.live_scan())
    row = repo.conn.execute("SELECT game_outcome,set_outcome FROM serve_for_set").fetchone()
    assert tuple(row) == ("BROKE", None)
    repo.close()


def test_no_livescore_call_without_a_favorite_missing_from_socket(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo, home="1.50", away="2.60")  # Not a heavy favourite.
    api = FakeAPI(fixture=fixture((4, 5)), payload=live_payload(home="2.60", status="Set 2"))
    asyncio.run(Monitor(CONFIG_ON, api, repo, None, connected_stream()).live_scan())
    assert api.score_calls == 0 and serve_alerts(repo) == []
    repo.close()


def test_set_break_stats_are_kept_apart_from_match_stats(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    favorite_match(repo)
    stream = connected_stream(fixture((4, 5)))
    asyncio.run(Monitor(CONFIG_ON, FakeAPI(payload=live_payload(home="2.60", status="Set 2")), repo, None, stream).live_scan())
    stream.handle(json.dumps(fixture((5, 5), serve="First Player")))
    stream.handle(json.dumps({**fixture((7, 5), status="Set 3"), "event_game_result": "0 - 0"}))
    repo.save_result("77", "Away", "1 - 2", "Finished", "FINISHED")  # Broke back, took the set, lost the match.
    rows = repo.alerts_with_results(datetime.now(timezone.utc) - timedelta(days=1))
    assert sorted(r["rule_type"] for r in rows) == ["PREMATCH", "SERVE_FOR_SET"]

    stats = build_report(rows)
    assert stats["ALL"]["total"] == 1 and stats["ALL"]["LOST"] == 1  # Only the prematch alert.
    assert stats["SERVE_FOR_SET"]["LOST"] == 1
    serve = serve_report(rows)
    assert (serve["break"]["WON"], serve["break"]["rate"]) == (1, 100)
    assert (serve["set"]["WON"], serve["set_after_break"]["settled"]) == (1, 1)
    assert serve["match"]["LOST"] == 1

    text = format_report(rows, 30)
    match_part, serve_part = text.split("== Set kırma alarmları (set için servis) ==")
    assert "Set için servis" not in match_part and "Maç sonu toplamı" in match_part
    assert "Break yaptı: 1 / 1 (%100.0)" in serve_part and "Seti aldı: 1 / 1 (%100.0)" in serve_part
    assert "Ayrıca maç sonucu: Kazandı: 0 | Kaybetti: 1" in serve_part
    repo.close()

    conn = queries.connect(path)
    data = queries.overview(conn, 30)
    assert [r["rule_type"] for r in data["match_rows"]] == ["PREMATCH"]
    assert [(name, s["total"]) for name, s in data["by_type"]] == [("Itf Men Singles", 1)]
    assert [(name, t["break"]["WON"]) for name, t in data["serve_by_type"]] == [("Itf Men Singles", 1)]
    conn.close()


@pytest.mark.parametrize("game,set_result,outcome_type,expected", [
    ("BROKE", None, None, ("WON", "PENDING")),
    ("HELD", "LOST", None, ("LOST", "LOST")),
    (None, None, None, ("PENDING", "PENDING")),
    (None, None, "RETIRED", ("VOID", "VOID")),
])
def test_break_and_set_outcomes(game, set_result, outcome_type, expected):
    row = {"rule_type": "SERVE_FOR_SET", "game_outcome": game, "set_outcome": set_result, "outcome_type": outcome_type}
    assert (break_outcome(row), set_outcome(row)) == expected
    assert rule_outcome(row) == expected[0]
