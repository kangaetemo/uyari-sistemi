"""Backing the opponent at each alert: the opponent's live price is stored and replayed against the result."""
import asyncio
import sqlite3
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.monitor import Monitor
from app.core.parsers import PrematchOdds
from app.core.results import build_report, estimate_opponent_odds, format_report, opponent_odds
from app.db.repository import Repository
from app.web.server import create_app
from tests.test_live_stream import connected_stream
from tests.test_serve_for_set import favorite_match, fixture, run_scan, serve_alerts
from tests.test_third_set import CONFIG, FakeAPI, live_payload, process
from tests.test_web import AUTH, TZ


def row(odd, side="Home", winner="Away", outcome_type="FINISHED", opponent=None, rule="PREMATCH"):
    return {"rule_type": rule, "favorite_side": side, "trigger_live_odds": odd, "opponent_odds": opponent,
            "winner_side": winner, "outcome_type": outcome_type}


@pytest.mark.parametrize("favorite,expected", [("2.00", "1.82"), ("2.50", "1.54"), ("3.00", "1.40")])
def test_estimate_assumes_a_five_percent_margin(favorite, expected):
    assert estimate_opponent_odds(Decimal(favorite)) == Decimal(expected)


def test_stored_price_wins_over_the_estimate():
    assert opponent_odds(row("2.00", opponent="1.75")) == (Decimal("1.75"), False)
    assert opponent_odds(row("2.00")) == (Decimal("1.82"), True)
    # A dict built before the column existed (or an old database row) has no opponent_odds key.
    old = row("2.00")
    old.pop("opponent_odds")
    assert opponent_odds(old) == (Decimal("1.82"), True)


def test_report_replays_the_opposite_bet():
    rows = [row("2.10", winner="Away", opponent="1.70"),               # favourite lost: opponent +0.70
            row("2.20", winner="Home", opponent="1.65"),               # favourite won: opponent -1
            row("2.00", winner="Away"),                                # estimated 1.82: +0.82
            row("2.30", winner=None, outcome_type="WALKOVER", opponent="1.60"),  # void
            row("2.40", winner=None, outcome_type=None, opponent="1.55")]        # pending
    item = build_report(rows)["PREMATCH"]
    assert item["profit"] == Decimal("-0.80")  # Backing the favourite: -1 +1.20 -1.
    assert (item["opp_settled"], item["opp_estimated"]) == (3, 1)
    assert item["opp_profit"] == Decimal("0.52")
    assert item["opp_odds_sum"] == Decimal("5.17")
    text = format_report(rows, 7)
    assert "Rakibe oynasaydık (alarm anındaki rakip oranından 1 birim): +0.52 birim, %+17.3" in text
    assert "1 oran tahmini" in text and "%5 bahis marjı" in text


def test_prematch_alert_stores_the_opponent_price(tmp_path):
    repo = Repository(tmp_path / "a.db")
    repo.upsert_prematch("77", {"event_first_player": "Fav", "event_second_player": "Dog"},
                         PrematchOdds("77", Decimal("1.20"), Decimal("4.50")), "Home", True)
    api = FakeAPI(payload=live_payload(home="2.10", away="1.70", status="Set 2"))
    asyncio.run(Monitor(CONFIG, api, repo, None).live_scan())
    alert = repo.conn.execute("SELECT * FROM alerts WHERE rule_type='PREMATCH'").fetchone()
    assert (alert["trigger_live_odds"], alert["opponent_odds"]) == ("2.10", "1.70")
    repo.close()


def test_third_set_alert_stores_the_opponent_price(tmp_path):
    repo = Repository(tmp_path / "a.db")
    process(repo, live_payload(home="1.40", away="3.00"))
    process(repo, live_payload(home="2.10", away="1.65"))
    alert = repo.conn.execute("SELECT * FROM alerts WHERE rule_type='THIRD_SET'").fetchone()
    assert (alert["favorite_side"], alert["trigger_live_odds"], alert["opponent_odds"]) == ("Home", "2.10", "1.65")
    repo.close()


def test_serve_for_set_alert_stores_the_opponent_price(tmp_path):
    repo = Repository(tmp_path / "a.db")
    favorite_match(repo)
    run_scan(repo, connected_stream(fixture((4, 5))))
    assert [(r["trigger_live_odds"], r["opponent_odds"]) for r in serve_alerts(repo)] == [("2.60", "1.50")]
    repo.close()


def test_suspended_opponent_price_is_left_empty(tmp_path):
    repo = Repository(tmp_path / "a.db")
    repo.upsert_prematch("77", {"event_first_player": "Fav", "event_second_player": "Dog"},
                         PrematchOdds("77", Decimal("1.20"), Decimal("4.50")), "Home", True)
    repo.record_live(repo.get_match("77"), Decimal("2.10"), None, Decimal("2"), None)
    assert repo.conn.execute("SELECT opponent_odds FROM alerts").fetchone()[0] is None
    repo.close()


def test_existing_database_gets_the_column(tmp_path):
    path = tmp_path / "a.db"
    Repository(path).close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE alerts DROP COLUMN opponent_odds")
    conn.commit()
    conn.close()
    repo = Repository(path)
    assert "opponent_odds" in {r["name"] for r in repo.conn.execute("PRAGMA table_info(alerts)")}
    repo.close()


def test_panel_shows_the_opposite_bet(tmp_path):
    path = tmp_path / "a.db"
    repo = Repository(path)
    for key, winner, opponent in (("1", "Away", "1.70"), ("2", "Home", None)):
        repo.upsert_prematch(key, {"event_first_player": f"{key}A", "event_second_player": f"{key}B",
                                   "event_type_type": "Atp Singles"},
                             PrematchOdds(key, Decimal("1.2"), Decimal("4")), "Home", True)
        repo.record_live(repo.get_match(key), Decimal("2.10"), None, Decimal("2"), opponent and Decimal(opponent))
        repo.save_result(key, winner, "2 - 0", "Finished", "FINISHED")
    repo.close()
    client = TestClient(create_app(path, *AUTH, TZ))
    text = client.get("/?days=0", auth=AUTH).text
    # 1.70 won (+0.70), estimated 1.74 lost (-1): -0.30 units over 2 alerts.
    assert "Rakibe oynasaydık" in text and "-0.30 birim" in text
    assert "1 alarmda rakip oranı kayıtlı değil" in text and "%5 bahis marjı" in text
    assert "Rakip oranı (alarm anı)" in client.get("/match/1", auth=AUTH).text
