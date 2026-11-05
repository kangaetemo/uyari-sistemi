import json
from pathlib import Path
from decimal import Decimal
from app.core.parsers import parse_live_odds, parse_prematch_odds


def fixture(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))


def test_prematch_parser():
    result = parse_prematch_odds(fixture("prematch.json"))
    assert result["12165577"].home == Decimal("1.26")


def test_live_parser():
    result = parse_live_odds(fixture("live.json"))
    assert result["12165577"].home == Decimal("2.04")
    assert result["12165577"].home_updated_at == "2026-09-25 19:42:18"


def test_live_uses_newest_active_snapshot():
    rows = [
        {"odd_name":"To Win","type":"Home","value":"1.72","upd":"2026-09-25 18:10:11","suspended":"No"},
        {"odd_name":"To Win","type":"Home","value":"1.61","upd":"2026-09-25 18:10:01","suspended":"No"},
        {"odd_name":"To Win","type":"Home","value":"9.99","upd":"2026-09-25 18:10:12","suspended":"Yes"},
        {"odd_name":"To Win","type":"Away","value":"2.65","upd":"2026-09-25 18:10:11","suspended":"No"},
    ]
    item = parse_live_odds({"result":{"77":{"event_key":77,"live_odds":rows}}})["77"]
    assert item.home == Decimal("1.72")
    assert item.away == Decimal("2.65")


def test_empty_live_result():
    assert parse_live_odds({"success":1,"result":{}}) == {}
