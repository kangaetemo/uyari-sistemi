from decimal import Decimal
from app.core.parsers import PrematchOdds
from app.db.repository import Repository


def test_restart_transition_and_one_alert(tmp_path):
    path = tmp_path / "test.db"
    fixture = {"event_first_player":"Player A","event_second_player":"Player B","event_date":"2026-09-25","event_time":"19:00"}
    repo = Repository(path)
    repo.upsert_prematch("1",fixture,PrematchOdds("1",Decimal("1.30"),Decimal("3")),"Home",True)
    repo.close()
    repo = Repository(path)
    assert "1" in repo.watched()
    repo.upsert_prematch("1",fixture,PrematchOdds("1",Decimal("1.42"),Decimal("2.8")),"Home",False)
    assert "1" not in repo.watched()
    repo.upsert_prematch("1",fixture,PrematchOdds("1",Decimal("1.34"),Decimal("3")),"Home",True)
    for odd in ["1.70","1.95","2.01","2.15","2.30"]:
        repo.record_live(repo.get_match("1"),Decimal(odd),None,Decimal("2"))
    assert repo.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    assert repo.conn.execute("SELECT previous_live_odds FROM alerts").fetchone()[0] == "1.95"
    repo.close()


def test_unchanged_result_is_not_saved_again(tmp_path):
    repo = Repository(tmp_path / "test.db")
    fixture = {"event_first_player":"Player A","event_second_player":"Player B","event_date":"2026-11-07","event_time":"18:00"}
    repo.upsert_prematch("1",fixture,PrematchOdds("1",Decimal("1.30"),Decimal("3")),"Home",True)
    assert repo.save_result("1","Home","2 - 0","Finished","FINISHED")
    assert not repo.save_result("1","Home","2 - 0","Finished","FINISHED")
    assert repo.save_result("1","Away","1 - 2","Finished","FINISHED")
    assert repo.conn.execute("SELECT winner_side FROM match_results").fetchone()[0] == "Away"
    repo.close()
