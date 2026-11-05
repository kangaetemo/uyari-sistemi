"""Third-set opening rule uses full-match To Win odds, never set-winner markets."""
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.rules import eligible

log = logging.getLogger(__name__)
PROVIDER_TIMEZONE = ZoneInfo("Europe/Istanbul")


def is_third_set(status):
    return status.strip().lower() == "set 3"


def third_set_ended(status):
    value = status.strip().lower()
    number = re.fullmatch(r"set (\d+)", value)
    return bool((number and int(number[1]) > 3) or
                any(word in value for word in ("finish", "ended", "cancel", "postpon", "retired", "walkover", "abandon")))


def zero_score(value):
    return isinstance(value, str) and value.replace(" ", "") == "0-0"


def opening_score(fixture):
    """Return True at the opening, False after play, None with insufficient data."""
    if not is_third_set(str(fixture.get("event_status") or "")):
        return None
    scores = fixture.get("scores")
    if not isinstance(scores, list):
        return None
    third = [s for s in scores if isinstance(s, dict) and str(s.get("score_set")) == "3"]
    if len(third) != 1:
        return None
    score = third[0]
    if score.get("score_first") is None or score.get("score_second") is None:
        return None
    if str(score["score_first"]) != "0" or str(score["score_second"]) != "0":
        return False
    points = fixture.get("event_game_result")
    if not isinstance(points, str) or points.strip() in {"", "-"}:
        return None
    return zero_score(points)


def fresh_update(updated, current, max_age):
    try:
        stamp = datetime.strptime(updated, "%Y-%m-%d %H:%M:%S").replace(tzinfo=PROVIDER_TIMEZONE)
    except (TypeError, ValueError):
        return False
    return -5 <= (current - stamp).total_seconds() <= max_age


async def process_third_set(config, api, repo, live, *, current=None):
    current = current or datetime.now(timezone.utc)
    candidates = {key:item for key,item in live.items()
                  if is_third_set(item.status) and not repo.third_set_decided(key)}
    if candidates:
        payload = await api.livescore()
        result = (payload or {}).get("result")
        fixtures = {str(row["event_key"]):row for row in result
                    if isinstance(row,dict) and row.get("event_key") is not None} if isinstance(result,list) else {}
        for key, item in candidates.items():
            fixture = fixtures.get(key)
            if not fixture:
                continue
            opening = opening_score(fixture)
            if opening is False or (opening is True and item.game_score and not zero_score(item.game_score)):
                repo.decide_third_set(key, fixture, "MISSED")
                log.info("3. SET | event=%s | başlangıç kaçırıldı; takip açılmadı", key)
                continue
            if opening is not True:
                continue
            valid = {}
            for side, odd, updated in (("Home",item.home,item.home_updated_at),("Away",item.away,item.away_updated_at)):
                if odd is not None and fresh_update(updated,current,config.third_set_odds_max_age_seconds):
                    valid[side] = (odd,updated)
            if not valid:
                continue  # Suspended/stale opening odds may become valid before the first point.
            watches = {side:values for side,values in valid.items()
                       if eligible(values[0],config.third_set_min,config.third_set_max)}
            repo.decide_third_set(key,fixture,"CAPTURED",watches)
            for side,(odd,_) in watches.items():
                log.info("3. SET TAKİP | event=%s | taraf=%s | başlangıç=%s",key,side,odd)

    for watch in repo.third_set_watched():
        item = live.get(watch["event_key"])
        if item is None:
            continue
        if not is_third_set(item.status):
            # Empty/unknown/interrupted statuses do not prove that the set ended.
            if third_set_ended(item.status):
                repo.close_third_set(watch["event_key"])
            continue
        side = watch["favorite_side"]
        odd, updated = (item.home,item.home_updated_at) if side == "Home" else (item.away,item.away_updated_at)
        if odd is None or not fresh_update(updated,current,config.third_set_odds_max_age_seconds):
            continue
        if repo.record_third_set_live(watch,odd,updated,config.third_set_trigger):
            log.warning("3. SET ALARMI | event=%s | taraf=%s | %s -> %s",
                        watch["event_key"],side,watch["opening_odds"],odd)
