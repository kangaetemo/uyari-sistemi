from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .rules import median_odds, parse_decimal_odd


@dataclass(frozen=True)
class PrematchOdds:
    event_key: str
    home: Decimal | None
    away: Decimal | None


@dataclass(frozen=True)
class LiveOdds:
    event_key: str
    home: Decimal | None
    away: Decimal | None
    home_updated_at: str | None
    away_updated_at: str | None
    status: str
    score: str | None
    game_score: str | None = None


def parse_prematch_odds(payload: Any) -> dict[str, PrematchOdds]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return {}
    parsed = {}
    for key, record in result.items():
        market = record.get("Home/Away") if isinstance(record, dict) else None
        if not isinstance(market, dict):
            continue
        home, away = median_odds(market.get("Home")), median_odds(market.get("Away"))
        if home is not None or away is not None:
            parsed[str(key)] = PrematchOdds(str(key), home, away)
    return parsed


def parse_live_odds(payload: Any) -> dict[str, LiveOdds]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return {}
    parsed = {}
    for key, record in result.items():
        if not isinstance(record, dict):
            continue
        odds = {"Home": None, "Away": None}
        updates = {"Home": None, "Away": None}
        # API-Tennis documents live_odds as a list of odd_name/type/value/upd rows.
        rows = record.get("live_odds")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("odd_name") != "To Win":
                continue
            side = row.get("type")
            if side not in odds or str(row.get("suspended", "No")).lower() in {"yes", "true", "1"}:
                continue
            odd = parse_decimal_odd(row.get("value"))
            if odd is not None and (updates[side] is None or str(row.get("upd") or "") >= updates[side]):
                odds[side], updates[side] = odd, str(row.get("upd") or "")
        event_key = str(record.get("event_key") or key)
        parsed[event_key] = LiveOdds(event_key, odds["Home"], odds["Away"], updates["Home"], updates["Away"], str(record.get("event_status") or ""), record.get("event_final_result"), record.get("event_game_result"))
    return parsed
