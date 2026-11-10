from datetime import datetime
from decimal import Decimal

from app.core.diagnostics import daily_report
from app.core.parsers import PrematchOdds
from app.db.repository import Repository
from tests.test_third_set import CONFIG, score_fixture

DIAG_CONFIG = type(CONFIG)(**vars(CONFIG), prematch_min=Decimal("1.01"), prematch_max=Decimal("1.35"))


def test_daily_report_lists_alerts_candidates_and_third_set_decisions(tmp_path):
    repo = Repository(tmp_path / "a.db")
    today = datetime.now(CONFIG.timezone).date().isoformat()
    fixture = {"event_first_player": "Player A", "event_second_player": "Player B", "event_date": today, "event_time": "12:00"}
    repo.upsert_prematch("1", fixture, PrematchOdds("1", Decimal("1.20"), Decimal("4")), "Home", True)
    match = repo.watched()["1"]
    repo.record_live(match, Decimal("1.60"), "x", Decimal("2.00"))
    repo.record_live(repo.watched()["1"], Decimal("2.10"), "x", Decimal("2.00"))
    repo.decide_third_set("77", score_fixture(games=("2", "1")), "MISSED", detail="3. set 2-1, puan 15 - 0")
    text = daily_report(repo, DIAG_CONFIG)
    assert "Alarmlar: 1" in text and "PREMATCH | Player A - Player B | 1.20 -> 2.10 | gönderim bekliyor" in text
    assert "uygun favori: 1" in text and "en yüksek canlı 2.10" in text
    assert "MISSED=1" in text and "3. set 2-1, puan 15 - 0" in text
    assert "Canlı tarama 5 dakikadan uzun" in text
    repo.close()
