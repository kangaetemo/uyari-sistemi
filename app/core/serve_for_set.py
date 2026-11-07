"""Serving-for-the-set rule: the prematch favourite is a break down and the opponent serves for the set.

Serve alternates every game, so when the player leading 5-3, 5-4 or 6-5 is the
one serving, that player is exactly one break up and holding wins the set. The
favourite then has to break to stay in the set. The rule needs nothing beyond
the livescore feed (set games, current server, point-by-point) and the
prematch price the bot already stored, so it costs no extra API calls while
the WebSocket is connected.
"""
import logging
import re
from dataclasses import dataclass

from app.api.live_stream import is_final

log = logging.getLogger(__name__)
SIDE_BY_PLAYER = {"first player": "Home", "second player": "Away"}
OTHER = {"Home": "Away", "Away": "Home"}
# (server games, receiver games) in the current set.
SERVING_FOR_SET = {(5, 3), (5, 4), (6, 5)}


@dataclass(frozen=True)
class SetPoint:
    set_number: int
    server: str          # "Home" or "Away": the player serving for the set
    server_games: int
    receiver_games: int
    points: str | None   # Score inside the game, e.g. "0 - 0"


def leading_int(value):
    match = re.match(r"\s*(\d+)", str(value)) if value is not None else None
    return int(match[1]) if match else None


def current_set(fixture):
    match = re.fullmatch(r"\s*set\s+(\d+)\s*", str(fixture.get("event_status") or ""), re.IGNORECASE)
    return int(match[1]) if match else None


def set_games(fixture, number):
    """(first player games, second player games) of set `number`, or None."""
    scores = fixture.get("scores")
    if not isinstance(scores, list):
        return None
    rows = [s for s in scores if isinstance(s, dict) and leading_int(s.get("score_set")) == number]
    if len(rows) != 1:
        return None
    first, second = leading_int(rows[0].get("score_first")), leading_int(rows[0].get("score_second"))
    return None if first is None or second is None else (first, second)


POINT_ORDER = {"0": 0, "15": 1, "30": 2, "40": 3, "A": 4, "AD": 4}


def progress(fixture):
    """How far a score row has got: (set, games in that set, points in the game), for comparing two feeds.

    A finished match counts as furthest along. Tie-break points are plain numbers.
    """
    if is_final(fixture):
        return (99, 0, 0)
    number = current_set(fixture) or 0
    games = set_games(fixture, number) if number else None
    points = 0
    for part in str(fixture.get("event_game_result") or "").split("-"):
        part = part.strip().upper()
        points += POINT_ORDER.get(part, int(part) if part.isdigit() else 0)
    return (number, sum(games) if games else 0, points)


def player_side(value):
    return SIDE_BY_PLAYER.get(str(value or "").strip().lower())


def current_server(fixture, number, played):
    """Who serves the game now being played, or None when the feed does not say for sure.

    `event_serve` can still name the previous server for a moment after a game
    ends, which at 5-3 would wrongly flag the receiver as serving for the set.
    The point-by-point list settles it: its last game in this set is either the
    game in progress (same server) or the game just finished (the other one).
    Without point-by-point, `event_serve` is trusted only once a point of the
    new game has been played.
    """
    from_points = None
    games = [g for g in fixture.get("pointbypoint") or []
             if isinstance(g, dict) and leading_int(re.sub(r"\D+", " ", str(g.get("set_number") or ""))) == number
             and leading_int(g.get("number_game")) is not None]
    if games:
        last = max(games, key=lambda g: leading_int(g["number_game"]))
        served, game_number = player_side(last.get("player_served")), leading_int(last["number_game"])
        if served and game_number == played + 1:
            from_points = served
        elif served and game_number == played:
            from_points = OTHER[served]
    from_serve = player_side(fixture.get("event_serve"))
    if from_points and from_serve and from_points != from_serve:
        return None
    if from_points:
        return from_points
    points = str(fixture.get("event_game_result") or "").replace(" ", "")
    if from_serve and points not in {"", "-", "0-0"}:
        return from_serve
    return None


def serving_for_set(fixture):
    """The SetPoint when someone is a break up and serving for the current set, else None."""
    number = current_set(fixture)
    if number is None or is_final(fixture):
        return None
    games = set_games(fixture, number)
    if games is None:
        return None
    first, second = games
    if (first, second) in SERVING_FOR_SET:
        leader = "Home"
    elif (second, first) in SERVING_FOR_SET:
        leader = "Away"
    else:
        return None
    if current_server(fixture, number, first + second) != leader:
        return None
    server_games, receiver_games = (first, second) if leader == "Home" else (second, first)
    return SetPoint(number, leader, server_games, receiver_games, fixture.get("event_game_result"))


def outcome_update(row, fixture):
    """New (game_outcome, set_outcome) for a stored alert from a later score, or None if unchanged.

    game_outcome: BROKE (favourite won the game) or HELD (opponent held, set lost).
    set_outcome: WON or LOST for the favourite.
    """
    number = row["set_number"]
    games = set_games(fixture, number)
    if games is None:
        return None
    fav, opp = games if row["favorite_side"] == "Home" else games[::-1]
    game, result = row["game_outcome"], row["set_outcome"]
    if game is None:
        if opp > row["opponent_games"]:
            game, result = "HELD", "LOST"
        elif fav > row["favorite_games"]:
            game = "BROKE"
    if game == "BROKE" and result is None:
        playing = current_set(fixture)
        if ((playing is not None and playing > number) or is_final(fixture)) and fav != opp:
            result = "WON" if fav > opp else "LOST"
    if (game, result) == (row["game_outcome"], row["set_outcome"]):
        return None
    return game, result


def track_outcomes(repo, fixtures, open_keys):
    """Update the outcome of stored alerts from score rows; returns how many changed."""
    changed = 0
    for fixture in fixtures:
        key = str(fixture.get("event_key"))
        if key not in open_keys:
            continue
        for row in repo.serve_for_set_open(key):
            update = outcome_update(row, fixture)
            if update:
                repo.set_serve_for_set_outcome(key, row["set_number"], *update)
                changed += 1
                log.info("SET İÇİN SERVİS SONUÇ | event=%s | set=%s | oyun=%s | set=%s",
                         key, row["set_number"], update[0] or "-", update[1] or "-")
    return changed


async def livescore_fixtures(api):
    payload = await api.livescore()
    result = (payload or {}).get("result")
    if not isinstance(result, list):
        return {}
    return {str(row["event_key"]): row for row in result if isinstance(row, dict) and row.get("event_key") is not None}


async def process_serve_for_set(config, api, repo, live, *, scores=None, open_keys=frozenset()):
    """Alert when a heavy prematch favourite must break to stay in the set; returns new alerts.

    `scores` is the WebSocket's latest fixture per event_key; without it the
    scores come from one `get_livescore` call, and only when a candidate is live.
    """
    favorites = repo.serve_for_set_favorites(live.keys(), config.serve_for_set_favorite_max)
    if not favorites and not open_keys:
        return 0
    fixtures = await livescore_fixtures(api) if scores is None else scores
    created = 0
    for key, match in favorites.items():
        fixture = fixtures.get(key)
        point = serving_for_set(fixture) if fixture else None
        if point is None or point.server == match["favorite_side"]:
            continue
        if repo.serve_for_set_alerted(key, point.set_number):
            continue
        item = live[key]
        odd = item.home if match["favorite_side"] == "Home" else item.away
        if odd is None:
            log.info("SET İÇİN SERVİS | event=%s | set=%s | favorinin canlı oranı yok; bekleniyor", key, point.set_number)
            continue
        if repo.record_serve_for_set(match, point, odd, config.serve_for_set_favorite_max):
            created += 1
            log.warning("SET İÇİN SERVİS ALARMI | event=%s | %s | set=%s | %s-%s geride | maç önü=%s | canlı=%s",
                        key, match["favorite_player_name"], point.set_number, point.receiver_games,
                        point.server_games, match["latest_prematch_odds"], odd)
    # The socket listener follows matches it carries; this covers the ones only get_livescore has.
    # Running both on a match is harmless: an unchanged outcome is not written again.
    track_outcomes(repo, fixtures.values(), open_keys)
    return created
