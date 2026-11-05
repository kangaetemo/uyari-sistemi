"""Read-only queries for the panel. The panel never calls the odds API; it only reads what the bot stored."""
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.core.results import alert_outcome, build_report

RULES = ("PREMATCH", "THIRD_SET")
PREMATCH_THRESHOLDS = [Decimal(x) for x in ("1.60", "1.70", "1.80", "1.90", "2.00", "2.10", "2.20", "2.40", "2.60", "2.80", "3.00")]
THIRD_SET_THRESHOLDS = [Decimal(x) for x in ("1.70", "1.80", "1.90", "2.00", "2.10", "2.20", "2.40", "2.60", "2.80", "3.00")]


def connect(path: Path):
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def since(days):
    return datetime.now(timezone.utc) - timedelta(days=days) if days else datetime(1970, 1, 1, tzinfo=timezone.utc)


def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def has_column(conn, table, column):
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def alerts(conn, days):
    """Alerts with their match and settled result (columns missing in older databases come back NULL)."""
    event_type = "m.event_type" if has_column(conn, "matches", "event_type") else "NULL AS event_type"
    if has_table(conn, "match_results"):
        result_cols, join = "r.winner_side,r.final_result,r.outcome_type", "LEFT JOIN match_results r USING(event_key)"
    else:
        result_cols, join = "NULL AS winner_side,NULL AS final_result,NULL AS outcome_type", ""
    return conn.execute(f"""SELECT a.*,m.first_player_name,m.second_player_name,m.tournament_name,{event_type},{result_cols}
        FROM alerts a JOIN matches m USING(event_key) {join}
        WHERE a.created_at>=? ORDER BY a.created_at DESC""", (since(days).isoformat(),)).fetchall()


def summarize(stats):
    """Turn build_report counters into display numbers."""
    out = {}
    for rule, item in stats.items():
        settled = item["WON"] + item["LOST"]
        avg = item["odds_sum"] / item["total"] if item["total"] else None
        out[rule] = {
            **{k: item[k] for k in ("total", "WON", "LOST", "PENDING", "VOID", "retired")},
            "settled": settled,
            "win_rate": item["WON"] / settled * 100 if settled else None,
            "avg_odds": avg,
            "break_even": 100 / avg if avg else None,
            "profit": item["profit"],
            "roi": item["profit"] / settled * 100 if settled else None,
        }
    return out


def overview(conn, days):
    rows = alerts(conn, days)
    by_rule = summarize(build_report(rows))
    by_type = defaultdict(list)
    for row in rows:
        by_type[row["event_type"] or "Bilinmiyor"].append(row)
    types = sorted(((name, summarize(build_report(items))["ALL"]) for name, items in by_type.items()),
                   key=lambda pair: -pair[1]["total"])
    return {"rows": rows, "by_rule": by_rule, "by_type": types}


def daily_counts(rows, tz, days=30):
    """Alerts per local day for the last `days` days, split by outcome, oldest first."""
    today = datetime.now(tz).date()
    labels = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    counts = {label: {"WON": 0, "LOST": 0, "PENDING": 0, "VOID": 0} for label in labels}
    for row in rows:
        day = datetime.fromisoformat(row["created_at"]).astimezone(tz).date().isoformat()
        if day in counts:
            counts[day][alert_outcome(row)] += 1
    return labels, counts


def match_detail(conn, event_key):
    match = conn.execute("SELECT * FROM matches WHERE event_key=?", (event_key,)).fetchone()
    if match is None:
        return None
    result = conn.execute("SELECT * FROM match_results WHERE event_key=?", (event_key,)).fetchone() if has_table(conn, "match_results") else None
    return {
        "match": match,
        "result": result,
        "alerts": conn.execute("SELECT * FROM alerts WHERE event_key=? ORDER BY created_at", (event_key,)).fetchall(),
        "prematch": conn.execute("SELECT * FROM prematch_odds_history WHERE event_key=? ORDER BY id", (event_key,)).fetchall(),
        "live": conn.execute("SELECT * FROM live_odds_history WHERE event_key=? ORDER BY id", (event_key,)).fetchall(),
        "third_watches": conn.execute("SELECT * FROM third_set_watches WHERE event_key=?", (event_key,)).fetchall(),
        "third": conn.execute("SELECT * FROM third_set_odds_history WHERE event_key=? ORDER BY id", (event_key,)).fetchall(),
    }


def active(conn):
    matches = conn.execute("""SELECT m.*,
        (SELECT live_odds FROM live_odds_history h WHERE h.event_key=m.event_key ORDER BY id DESC LIMIT 1) AS last_live,
        (SELECT observed_at FROM live_odds_history h WHERE h.event_key=m.event_key ORDER BY id DESC LIMIT 1) AS last_live_at
        FROM matches m WHERE m.is_watched=1 ORDER BY m.status='PREMATCH_ELIGIBLE', m.match_start_time""").fetchall()
    third = conn.execute("""SELECT w.*,m.first_player_name,m.second_player_name,m.tournament_name,
        (SELECT live_odds FROM third_set_odds_history h WHERE h.event_key=w.event_key AND h.favorite_side=w.favorite_side ORDER BY id DESC LIMIT 1) AS last_live
        FROM third_set_watches w JOIN matches m USING(event_key) WHERE w.status IN ('WATCHING','ALERTED')
        ORDER BY w.observed_at DESC""").fetchall()
    return matches, third


def bot_state(conn):
    if not has_table(conn, "bot_state"):
        return {}
    return {row["key"]: row for row in conn.execute("SELECT * FROM bot_state")}


def _settle(winner, outcome_type, side):
    if outcome_type in {"CANCELLED", "WALKOVER"} or winner is None:
        return "VOID"
    return "WON" if winner == side else "LOST"


def _first_crossing(history, threshold, strict):
    for odd in history:
        if odd > threshold or (not strict and odd == threshold):
            return odd
    return None


def threshold_analysis(conn, rule, days, pmin=None, pmax=None):
    """What the rule would have produced at other thresholds, replayed on stored odds history.

    Each match counts once per threshold, at the first stored price that reaches it,
    exactly like a real alert. Only matches with a settled result are scored.
    """
    if not has_table(conn, "match_results"):
        return {"rows": [], "matches": 0, "pending": 0}
    cutoff = since(days).isoformat()
    if rule == "PREMATCH":
        population = conn.execute("""SELECT m.event_key,m.favorite_side AS side,m.latest_prematch_odds AS reference,
            r.winner_side,r.outcome_type FROM matches m LEFT JOIN match_results r USING(event_key)
            WHERE m.created_at>=? AND EXISTS (SELECT 1 FROM live_odds_history h WHERE h.event_key=m.event_key)""", (cutoff,)).fetchall()
        history_sql = "SELECT live_odds FROM live_odds_history WHERE event_key=? ORDER BY id"
        thresholds, strict = PREMATCH_THRESHOLDS, False
    else:
        population = conn.execute("""SELECT w.event_key,w.favorite_side AS side,w.opening_odds AS reference,
            r.winner_side,r.outcome_type FROM third_set_watches w LEFT JOIN match_results r USING(event_key)
            WHERE w.observed_at>=?""", (cutoff,)).fetchall()
        history_sql = "SELECT live_odds FROM third_set_odds_history WHERE event_key=? AND favorite_side=? ORDER BY id"
        thresholds, strict = THIRD_SET_THRESHOLDS, True
    scored = []
    pending = 0
    for row in population:
        reference = Decimal(row["reference"]) if row["reference"] else None
        if pmin is not None and (reference is None or reference < pmin):
            continue
        if pmax is not None and (reference is None or reference > pmax):
            continue
        if row["outcome_type"] is None:
            pending += 1
            continue
        params = (row["event_key"],) if rule == "PREMATCH" else (row["event_key"], row["side"])
        history = [Decimal(h[0]) for h in conn.execute(history_sql, params)]
        scored.append((history, _settle(row["winner_side"], row["outcome_type"], row["side"])))
    table = []
    for threshold in thresholds:
        won = lost = 0
        profit = Decimal(0)
        odds_sum = Decimal(0)
        for history, outcome in scored:
            odd = _first_crossing(history, threshold, strict)
            if odd is None or outcome == "VOID":
                continue
            odds_sum += odd
            if outcome == "WON":
                won += 1
                profit += odd - 1
            else:
                lost += 1
                profit -= 1
        settled = won + lost
        table.append({
            "threshold": threshold, "alerts": settled, "won": won, "lost": lost,
            "win_rate": won / settled * 100 if settled else None,
            "break_even": 100 / (odds_sum / settled) if settled else None,
            "profit": profit, "roi": profit / settled * 100 if settled else None,
        })
    return {"rows": table, "matches": len(scored), "pending": pending, "strict": strict}
