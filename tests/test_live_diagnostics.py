import asyncio
import logging
from decimal import Decimal

import httpx

from app.api.tennis import TennisAPI
from app.core.monitor import Monitor
from app.core.parsers import PrematchOdds
from app.db.repository import Repository


class FakeAPI:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def live_odds(self):
        self.calls += 1
        return self.payload


def make_match(repo, key, side):
    fixture = {"event_first_player":"Player A","event_second_player":"Player B"}
    odds = PrematchOdds(key,Decimal("1.28") if side == "Home" else Decimal("3"),
                        Decimal("1.28") if side == "Away" else Decimal("3"))
    repo.upsert_prematch(key,fixture,odds,side,True)


def live_record(key, home="1.72", away="2.65", rows=None):
    if rows is None:
        rows = [{"odd_name":"To Win","type":side,"value":value,"upd":"2026-09-25 18:10:11","suspended":"No"}
                for side,value in (("Home",home),("Away",away))]
    return {"event_key":key,"event_status":"Set 2","event_first_player":"Player A",
            "event_second_player":"Player B","live_odds":rows}


def diagnose(repo, payload, caplog):
    api = FakeAPI(payload)
    monitor = Monitor(None,api,repo,None)
    with caplog.at_level(logging.INFO):
        asyncio.run(monitor.diagnose_live())
    return api.calls


def test_empty_result_and_watchlist(tmp_path, caplog):
    repo = Repository(tmp_path / "a.db")
    assert diagnose(repo,{"success":1,"result":{}},caplog) == 1
    assert "Aktif watchlist boş" in caplog.text
    assert "API şu anda live event döndürmedi" in caplog.text
    repo.close()


def test_live_without_match(tmp_path, caplog):
    repo = Repository(tmp_path / "a.db")
    make_match(repo,"1","Home")
    diagnose(repo,{"success":1,"result":{"2":live_record("2")}},caplog)
    assert "Live API: 1 event alındı" in caplog.text
    assert "Kullanılabilir To Win marketi: 1" in caplog.text
    assert "Watchlist ile eşleşen live event: 0" in caplog.text
    assert "event_key eşleşmesi bulunamadı" in caplog.text
    repo.close()


def test_home_away_and_missing_market(tmp_path, caplog):
    repo = Repository(tmp_path / "a.db")
    for key,side in (("1","Home"),("2","Away"),("3","Home")):
        make_match(repo,key,side)
    payload = {"success":1,"result":{"1":live_record("1"),"2":live_record("2"),"3":live_record("3",rows=[])}}
    diagnose(repo,payload,caplog)
    assert "Watchlist ile eşleşen live event: 3" in caplog.text
    assert "1 | Player A vs Player B | Favori: Home | Prematch: 1.28 | Live: 1.72" in caplog.text
    assert "2 | Player A vs Player B | Favori: Away | Prematch: 1.28 | Live: 2.65" in caplog.text
    assert "3 | Live event bulundu ancak kullanılabilir To Win marketi bulunamadı" in caplog.text
    assert repo.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
    repo.close()


def test_api_success_failure_and_secret_logs(caplog):
    secret = "TEST_SECRET_123"
    def respond(request):
        return httpx.Response(200,json={"success":0,"error":f"invalid key {secret}","message":f"https://example.test/?APIkey={secret}"})
    api = TennisAPI(secret)
    asyncio.run(api.client.aclose())
    api.client = httpx.AsyncClient(base_url="https://api.api-tennis.com/tennis/",transport=httpx.MockTransport(respond))
    with caplog.at_level(logging.INFO):
        result = asyncio.run(api.live_odds())
    assert result is None
    assert "success=0" in caplog.text
    assert "method=get_live_odds | status=200" in caplog.text
    assert secret not in caplog.text
    assert "https://" not in caplog.text
    asyncio.run(api.close())
