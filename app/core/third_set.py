"""Third-set opening rule uses full-match To Win odds, never set-winner markets."""
import logging
import re
import time
from datetime import datetime

from app.core.rules import eligible

log = logging.getLogger(__name__)
UPD_FORMAT = "%Y-%m-%d %H:%M:%S"


def is_third_set(status):
    return status.strip().lower() == "set 3"


def third_set_ended(status):
    value = status.strip().lower()
    number = re.fullmatch(r"set (\d+)", value)
    return bool((number and int(number[1]) > 3) or
                any(word in value for word in ("finish", "ended", "cancel", "postpon", "retired", "walkover", "walk over", "abandon")))


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


def parse_upd(value):
    try:
        return datetime.strptime(value, UPD_FORMAT)
    except (TypeError, ValueError):
        return None


class FeedClock:
    """Judges snapshot freshness against the provider's own clock.

    API-Tennis returns `upd` in a provider timezone that does not follow the
    requested `timezone` parameter (observed UTC+2 in September 2026 while
    Europe/Istanbul was requested). Converting `upd` with a fixed timezone made
    every snapshot look an hour or more old, so the third-set rule never
    captured. Comparing `upd` values with the newest `upd` in the same payload
    needs no timezone, and tracking when that newest value last advanced on our
    own monotonic clock detects a stalled feed.
    """

    def __init__(self, monotonic=time.monotonic):
        self._monotonic = monotonic
        self.newest = None
        self.advanced_at = None

    def observe(self, live):
        stamps = [stamp for item in live.values()
                  for stamp in (parse_upd(item.home_updated_at), parse_upd(item.away_updated_at)) if stamp]
        if not stamps:
            return
        newest = max(stamps)
        if self.newest is None or newest > self.newest:
            self.newest, self.advanced_at = newest, self._monotonic()

    def fresh(self, updated, max_age):
        stamp = parse_upd(updated)
        if stamp is None or self.newest is None:
            return False
        if self._monotonic() - self.advanced_at > max_age:
            return False  # The whole feed stopped moving.
        return (self.newest - stamp).total_seconds() <= max_age


async def process_third_set(config, api, repo, live, *, clock, scores=None):
    """Capture new third-set openings and follow open watches; returns the number of new alerts.

    `scores` is the WebSocket's latest fixture per event_key; without it the
    scores come from one `get_livescore` call.
    """
    clock.observe(live)
    created = 0
    max_age = config.third_set_odds_max_age_seconds
    candidates = {key:item for key,item in live.items()
                  if is_third_set(item.status) and not repo.third_set_decided(key)}
    if candidates:
        if scores is not None:
            fixtures = scores
        else:
            payload = await api.livescore()
            result = (payload or {}).get("result")
            fixtures = {str(row["event_key"]):row for row in result
                        if isinstance(row,dict) and row.get("event_key") is not None} if isinstance(result,list) else {}
        for key, item in candidates.items():
            fixture = fixtures.get(key)
            if not fixture:
                continue
            opening = opening_score(fixture)
            if opening is False:
                repo.decide_third_set(key, fixture, "MISSED")
                log.info("3. SET | event=%s | başlangıç kaçırıldı; takip açılmadı", key)
                continue
            if opening is not True:
                continue
            if item.game_score and not zero_score(item.game_score):
                # The two feeds disagree; the next poll settles which one lagged.
                log.info("3. SET | event=%s | skor ve oran akışı uyuşmuyor; bekleniyor", key)
                continue
            sides = (("Home",item.home,item.home_updated_at),("Away",item.away,item.away_updated_at))
            stale = [side for side,odd,updated in sides if odd is None or not clock.fresh(updated,max_age)]
            if stale:
                # Deciding with one side stale or suspended would drop it for good.
                log.info("3. SET | event=%s | başlangıç oranı güncel değil (%s); bekleniyor", key, ",".join(stale))
                continue
            watches = {side:(odd,updated) for side,odd,updated in sides
                       if eligible(odd,config.third_set_min,config.third_set_max)}
            repo.decide_third_set(key,fixture,"CAPTURED",watches)
            for side,(odd,_) in watches.items():
                log.info("3. SET TAKİP | event=%s | taraf=%s | başlangıç=%s",key,side,odd)

    for watch in repo.third_set_tracked():
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
        if odd is None or not clock.fresh(updated,max_age):
            continue
        opponent = item.away if side == "Home" else item.home
        if repo.record_third_set_live(watch,odd,updated,config.third_set_trigger,opponent):
            created += 1
            log.warning("3. SET ALARMI | event=%s | taraf=%s | %s -> %s",
                        watch["event_key"],side,watch["opening_odds"],odd)
    return created
