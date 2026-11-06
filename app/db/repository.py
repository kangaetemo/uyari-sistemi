import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)
MAX_ALERT_ATTEMPTS = 5
CLOSED_STATUSES = {"FINISHED", "CANCELLED", "EXPIRED"}


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS matches (
 event_key TEXT PRIMARY KEY, first_player_key TEXT, second_player_key TEXT,
 first_player_name TEXT, second_player_name TEXT, tournament_name TEXT, tournament_key TEXT,
 favorite_player_key TEXT, favorite_player_name TEXT, favorite_side TEXT CHECK(favorite_side IN ('Home','Away')),
 first_seen_prematch_odds TEXT, latest_prematch_odds TEXT, first_seen_at TEXT,
 prematch_updated_at TEXT, match_start_time TEXT, status TEXT NOT NULL,
 is_watched INTEGER NOT NULL DEFAULT 0, alerted INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prematch_odds_history (
 id INTEGER PRIMARY KEY, event_key TEXT NOT NULL REFERENCES matches(event_key),
 home_median TEXT, away_median TEXT, favorite_side TEXT, favorite_odds TEXT, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_odds_history (
 id INTEGER PRIMARY KEY, event_key TEXT NOT NULL REFERENCES matches(event_key),
 favorite_side TEXT NOT NULL, live_odds TEXT NOT NULL, provider_updated_at TEXT, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
 id INTEGER PRIMARY KEY, event_key TEXT NOT NULL REFERENCES matches(event_key),
 favorite_player_name TEXT, prematch_odds TEXT, previous_live_odds TEXT,
 trigger_live_odds TEXT, threshold TEXT, telegram_sent INTEGER NOT NULL DEFAULT 0,
 telegram_message_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
 created_at TEXT NOT NULL, rule_type TEXT NOT NULL DEFAULT 'PREMATCH',
 favorite_side TEXT, reference_odds TEXT,
 UNIQUE(event_key,rule_type,favorite_side)
);
CREATE TABLE IF NOT EXISTS third_set_sessions (
 event_key TEXT PRIMARY KEY REFERENCES matches(event_key), decision TEXT NOT NULL,
 observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS third_set_watches (
 event_key TEXT NOT NULL REFERENCES matches(event_key), favorite_side TEXT NOT NULL,
 favorite_player_name TEXT, opening_odds TEXT NOT NULL, opening_provider_updated_at TEXT,
 observed_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'WATCHING',
 PRIMARY KEY(event_key,favorite_side)
);
CREATE TABLE IF NOT EXISTS third_set_odds_history (
 id INTEGER PRIMARY KEY, event_key TEXT NOT NULL, favorite_side TEXT NOT NULL,
 live_odds TEXT NOT NULL, provider_updated_at TEXT, observed_at TEXT NOT NULL,
 FOREIGN KEY(event_key,favorite_side) REFERENCES third_set_watches(event_key,favorite_side)
);
CREATE TABLE IF NOT EXISTS bot_state (
 key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_results (
 event_key TEXT PRIMARY KEY REFERENCES matches(event_key), winner_side TEXT CHECK(winner_side IN ('Home','Away')),
 final_result TEXT, provider_status TEXT, outcome_type TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_scores (
 event_key TEXT PRIMARY KEY, status TEXT, final_result TEXT, game_result TEXT, serve TEXT,
 sets TEXT, winner TEXT, source TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # A generous timeout lets a second process (a CLI scan, the future panel) wait instead of failing.
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate_alerts(path)
        self._add_missing_columns()

    def _migrate_alerts(self, path):
        if "rule_type" in {row["name"] for row in self.conn.execute("PRAGMA table_info(alerts)")}:
            return
        # The old UNIQUE(event_key) would suppress an independent third-set alert.
        backup_path = path.with_name(f"{path.stem}.before-third-set-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.db")
        backup = sqlite3.connect(backup_path)
        try:
            self.conn.backup(backup)
        finally:
            backup.close()
        create_alerts = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS alerts"):SCHEMA.index("CREATE TABLE IF NOT EXISTS third_set_sessions")]
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("ALTER TABLE alerts RENAME TO alerts_legacy")
            self.conn.execute(create_alerts)
            self.conn.execute("""INSERT INTO alerts(id,event_key,favorite_player_name,prematch_odds,
                previous_live_odds,trigger_live_odds,threshold,telegram_sent,telegram_message_id,
                attempts,next_attempt_at,created_at,favorite_side,reference_odds)
                SELECT a.id,a.event_key,a.favorite_player_name,a.prematch_odds,a.previous_live_odds,
                a.trigger_live_odds,a.threshold,a.telegram_sent,a.telegram_message_id,a.attempts,
                a.next_attempt_at,a.created_at,m.favorite_side,a.prematch_odds
                FROM alerts_legacy a JOIN matches m USING(event_key)""")
            self.conn.execute("DROP TABLE alerts_legacy")

    def _add_missing_columns(self):
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(matches)")}
        if "event_type" not in columns:
            # e.g. "Atp Singles", "Itf Men Singles"; lets the panel split statistics by level.
            with self.conn:
                self.conn.execute("ALTER TABLE matches ADD COLUMN event_type TEXT")

    def close(self):
        self.conn.close()

    def set_state(self, key, value):
        with self.conn:
            self.conn.execute("""INSERT INTO bot_state(key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""", (key,str(value),now()))

    def watched(self):
        """Matches whose live odds are collected; alerted ones keep collecting until they close."""
        return {row["event_key"]: row for row in self.conn.execute("SELECT * FROM matches WHERE is_watched=1")}

    def get_match(self, event_key):
        return self.conn.execute("SELECT * FROM matches WHERE event_key=?", (event_key,)).fetchone()

    def upsert_prematch(self, event_key, fixture, odds, side, eligible):
        old = self.get_match(event_key)
        if old and old["status"] in {"LIVE", "ALERTED"} | CLOSED_STATUSES:
            return
        if old and not side:
            side = old["favorite_side"]
        value = odds.home if side == "Home" else odds.away if side == "Away" else None
        if value is None:
            return
        stamp = now()
        first_key = fixture.get("first_player_key")
        second_key = fixture.get("second_player_key")
        first_name = fixture.get("event_first_player")
        second_name = fixture.get("event_second_player")
        player_key = first_key if side == "Home" else second_key
        player_name = first_name if side == "Home" else second_name
        status = "PREMATCH_ELIGIBLE" if eligible else "PREMATCH_INELIGIBLE"
        with self.conn:
            self.conn.execute("""INSERT INTO matches (
                event_key,first_player_key,second_player_key,first_player_name,second_player_name,
                tournament_name,tournament_key,favorite_player_key,favorite_player_name,favorite_side,
                first_seen_prematch_odds,latest_prematch_odds,first_seen_at,prematch_updated_at,
                match_start_time,status,is_watched,created_at,updated_at,event_type)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                first_player_name=excluded.first_player_name,second_player_name=excluded.second_player_name,
                tournament_name=excluded.tournament_name,favorite_player_key=excluded.favorite_player_key,
                favorite_player_name=excluded.favorite_player_name,favorite_side=excluded.favorite_side,
                latest_prematch_odds=excluded.latest_prematch_odds,prematch_updated_at=excluded.prematch_updated_at,
                match_start_time=excluded.match_start_time,
                event_type=COALESCE(excluded.event_type,matches.event_type),
                status=excluded.status,is_watched=excluded.is_watched,updated_at=excluded.updated_at""",
                (event_key,str(first_key or ""),str(second_key or ""),first_name,second_name,
                 fixture.get("tournament_name"),str(fixture.get("tournament_key") or ""),str(player_key or ""),player_name,side,
                 str(value),str(value),stamp,stamp,
                 f"{fixture.get('event_date','')}T{fixture.get('event_time','')}",status,int(eligible),stamp,stamp,
                 fixture.get("event_type_type")))
            if not old or old["latest_prematch_odds"] != str(value):
                self.conn.execute("INSERT INTO prematch_odds_history(event_key,home_median,away_median,favorite_side,favorite_odds,observed_at) VALUES (?,?,?,?,?,?)",
                                  (event_key,str(odds.home) if odds.home else None,str(odds.away) if odds.away else None,side,str(value),stamp))

    def set_status(self, event_key, status):
        closed = status in CLOSED_STATUSES
        with self.conn:
            self.conn.execute("UPDATE matches SET status=?,is_watched=?,updated_at=? WHERE event_key=?", (status, 0 if closed else 1, now(),event_key))
            if closed:
                self._close_third_set(event_key)

    def record_live(self, match, odd, provider_updated_at, threshold):
        """Store a changed live price; create the prematch alert once. History continues after the alert."""
        event_key = match["event_key"]
        stamp = now()
        with self.conn:
            previous = self.conn.execute("SELECT live_odds FROM live_odds_history WHERE event_key=? ORDER BY id DESC LIMIT 1", (event_key,)).fetchone()
            if not previous or previous["live_odds"] != str(odd):
                self.conn.execute("INSERT INTO live_odds_history(event_key,favorite_side,live_odds,provider_updated_at,observed_at) VALUES (?,?,?,?,?)",
                                  (event_key,match["favorite_side"],str(odd),provider_updated_at,stamp))
            self.conn.execute("UPDATE matches SET status=CASE WHEN status='ALERTED' THEN status ELSE 'LIVE' END,updated_at=? WHERE event_key=?", (stamp,event_key))
            if match["alerted"] or Decimal(str(odd)) < threshold:
                return False
            inserted = self.conn.execute("INSERT OR IGNORE INTO alerts(event_key,favorite_player_name,prematch_odds,previous_live_odds,trigger_live_odds,threshold,created_at,favorite_side,reference_odds) VALUES (?,?,?,?,?,?,?,?,?)",
                              (event_key,match["favorite_player_name"],match["latest_prematch_odds"],previous["live_odds"] if previous else None,str(odd),str(threshold),stamp,match["favorite_side"],match["latest_prematch_odds"]))
            self.conn.execute("UPDATE matches SET alerted=1,status='ALERTED',updated_at=? WHERE event_key=?", (stamp,event_key))
            return inserted.rowcount == 1

    def pending_alerts(self):
        return self.conn.execute("""SELECT a.*,m.first_player_name,m.second_player_name,m.tournament_name
            FROM alerts a JOIN matches m USING(event_key)
            WHERE a.telegram_sent=0 AND a.attempts<? AND (a.next_attempt_at IS NULL OR a.next_attempt_at<=?)""", (MAX_ALERT_ATTEMPTS,now())).fetchall()

    def claim_alert(self, alert_id, lease_seconds=120):
        """Atomically reserve an alert so that a second process cannot send it at the same time."""
        stamp = datetime.now(timezone.utc)
        with self.conn:
            claimed = self.conn.execute("""UPDATE alerts SET next_attempt_at=? WHERE id=? AND telegram_sent=0 AND attempts<?
                AND (next_attempt_at IS NULL OR next_attempt_at<=?)""",
                ((stamp + timedelta(seconds=lease_seconds)).isoformat(),alert_id,MAX_ALERT_ATTEMPTS,stamp.isoformat()))
        return claimed.rowcount == 1

    def mark_sent(self, alert_id, message_id):
        with self.conn:
            self.conn.execute("UPDATE alerts SET telegram_sent=1,telegram_message_id=?,next_attempt_at=NULL WHERE id=?", (str(message_id or ""),alert_id))

    def mark_failed(self, alert_id, delay):
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        with self.conn:
            self.conn.execute("UPDATE alerts SET attempts=attempts+1,next_attempt_at=? WHERE id=?", (next_at,alert_id))
            row = self.conn.execute("SELECT attempts,event_key FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if row and row["attempts"] >= MAX_ALERT_ATTEMPTS:
            log.error("Alarm %s denemede gönderilemedi, artık denenmeyecek: id=%s event=%s",
                      MAX_ALERT_ATTEMPTS, alert_id, row["event_key"])

    def count_today_alerts(self, tz=timezone.utc):
        midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.conn.execute("SELECT COUNT(*) FROM alerts WHERE created_at>=?", (midnight.astimezone(timezone.utc).isoformat(),)).fetchone()[0]

    def expire_stale(self, tz, start_grace_hours, live_stale_minutes, third_set_max_hours=4):
        """Close tracking that the feeds never closed: unseen live matches and long-past prematch entries."""
        current = datetime.now(timezone.utc)
        expired = []
        live_cutoff = (current - timedelta(minutes=live_stale_minutes)).isoformat()
        for row in self.conn.execute("SELECT event_key,status,match_start_time,updated_at FROM matches WHERE is_watched=1").fetchall():
            if row["status"] in {"LIVE", "ALERTED"}:
                if row["updated_at"] < live_cutoff:
                    expired.append(row["event_key"])
                continue
            try:
                start = datetime.fromisoformat(row["match_start_time"]).replace(tzinfo=tz)
            except (TypeError, ValueError):
                start = None
            if start and start + timedelta(hours=start_grace_hours) < current:
                expired.append(row["event_key"])
        for key in expired:
            self.set_status(key, "EXPIRED")
        third_cutoff = (current - timedelta(hours=third_set_max_hours)).isoformat()
        with self.conn:
            stale_watches = self.conn.execute("""SELECT DISTINCT event_key FROM third_set_watches
                WHERE status IN ('WATCHING','ALERTED') AND observed_at<?""", (third_cutoff,)).fetchall()
            for row in stale_watches:
                self._close_third_set(row["event_key"])
        return expired

    def third_set_decided(self, event_key):
        return self.conn.execute("SELECT 1 FROM third_set_sessions WHERE event_key=?", (event_key,)).fetchone() is not None

    def third_set_watched(self):
        """Watches that can still alert."""
        return self.conn.execute("SELECT * FROM third_set_watches WHERE status='WATCHING'").fetchall()

    def third_set_tracked(self):
        """Watches whose odds history is collected: still watching, or alerted but the set is not over."""
        return self.conn.execute("SELECT * FROM third_set_watches WHERE status IN ('WATCHING','ALERTED')").fetchall()

    def decide_third_set(self, event_key, fixture, decision, watches=None):
        stamp = now()
        with self.conn:
            self.conn.execute("""INSERT INTO matches(event_key,first_player_key,second_player_key,
                first_player_name,second_player_name,tournament_name,tournament_key,status,created_at,updated_at,event_type)
                VALUES (?,?,?,?,?,?,?,'LIVE',?,?,?) ON CONFLICT(event_key) DO UPDATE SET
                event_type=COALESCE(excluded.event_type,matches.event_type),
                first_player_name=COALESCE(excluded.first_player_name,matches.first_player_name),
                second_player_name=COALESCE(excluded.second_player_name,matches.second_player_name),
                tournament_name=COALESCE(excluded.tournament_name,matches.tournament_name)""",
                (event_key,str(fixture.get("first_player_key") or ""),str(fixture.get("second_player_key") or ""),
                 fixture.get("event_first_player"),fixture.get("event_second_player"),fixture.get("tournament_name"),
                 str(fixture.get("tournament_key") or ""),stamp,stamp,fixture.get("event_type_type")))
            inserted = self.conn.execute("INSERT OR IGNORE INTO third_set_sessions(event_key,decision,observed_at) VALUES (?,?,?)",(event_key,decision,stamp))
            if inserted.rowcount != 1:
                return
            for side,(odd,updated) in (watches or {}).items():
                name = fixture.get("event_first_player") if side == "Home" else fixture.get("event_second_player")
                self.conn.execute("INSERT INTO third_set_watches(event_key,favorite_side,favorite_player_name,opening_odds,opening_provider_updated_at,observed_at) VALUES (?,?,?,?,?,?)",(event_key,side,name,str(odd),updated,stamp))
                self.conn.execute("INSERT INTO third_set_odds_history(event_key,favorite_side,live_odds,provider_updated_at,observed_at) VALUES (?,?,?,?,?)",(event_key,side,str(odd),updated,stamp))

    def _close_third_set(self, event_key):
        self.conn.execute("""UPDATE third_set_watches SET status=CASE status WHEN 'ALERTED' THEN 'ALERTED_CLOSED' ELSE 'CLOSED' END
            WHERE event_key=? AND status IN ('WATCHING','ALERTED')""", (event_key,))

    def close_third_set(self, event_key):
        with self.conn:
            self._close_third_set(event_key)

    def record_third_set_live(self, watch, odd, updated, threshold):
        key, side = watch["event_key"], watch["favorite_side"]
        stamp = now()
        with self.conn:
            current = self.conn.execute("SELECT status FROM third_set_watches WHERE event_key=? AND favorite_side=?",(key,side)).fetchone()
            if not current or current["status"] not in {"WATCHING", "ALERTED"}:
                return False
            previous = self.conn.execute("SELECT live_odds,provider_updated_at FROM third_set_odds_history WHERE event_key=? AND favorite_side=? ORDER BY id DESC LIMIT 1",(key,side)).fetchone()
            if previous and previous["provider_updated_at"] and updated and updated < previous["provider_updated_at"]:
                return False
            if not previous or Decimal(previous["live_odds"]) != odd:
                self.conn.execute("INSERT INTO third_set_odds_history(event_key,favorite_side,live_odds,provider_updated_at,observed_at) VALUES (?,?,?,?,?)",(key,side,str(odd),updated,stamp))
            # Strictly above 2.00, as requested; the prematch rule remains >= its threshold.
            if current["status"] == "ALERTED" or odd <= threshold:
                return False
            inserted = self.conn.execute("""INSERT OR IGNORE INTO alerts(event_key,favorite_player_name,
                previous_live_odds,trigger_live_odds,threshold,created_at,rule_type,favorite_side,reference_odds)
                VALUES (?,?,?,?,?,?,'THIRD_SET',?,?)""",
                (key,watch["favorite_player_name"],previous["live_odds"] if previous else None,str(odd),str(threshold),stamp,side,watch["opening_odds"]))
            self.conn.execute("UPDATE third_set_watches SET status='ALERTED' WHERE event_key=? AND favorite_side=?", (key,side))
            return inserted.rowcount == 1

    # --- Live score of alerted matches (for the panel) -------------------------

    def recently_alerted(self, hours=24):
        """Event keys alerted in the last `hours`; the bot stores their live score for the panel."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return {row[0] for row in self.conn.execute("SELECT DISTINCT event_key FROM alerts WHERE created_at>=?", (cutoff,))}

    def save_live_score(self, event_key, *, status, final_result, game_result, serve, sets, winner, source):
        """`sets` is a JSON list of [set, first, second]; `source` is 'ws' (socket) or 'odds' (odds feed fallback)."""
        with self.conn:
            self.conn.execute("""INSERT INTO live_scores(event_key,status,final_result,game_result,serve,sets,winner,source,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET status=excluded.status,
                final_result=excluded.final_result,game_result=excluded.game_result,serve=excluded.serve,
                sets=excluded.sets,winner=COALESCE(excluded.winner,live_scores.winner),
                source=excluded.source,updated_at=excluded.updated_at""",
                (event_key,status,final_result,game_result,serve,sets,winner,source,now()))

    # --- Results -------------------------------------------------------------

    def save_result(self, event_key, winner_side, final_result, provider_status, outcome_type):
        if not self.get_match(event_key):
            return False
        with self.conn:
            self.conn.execute("""INSERT INTO match_results(event_key,winner_side,final_result,provider_status,outcome_type,recorded_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET winner_side=excluded.winner_side,
                final_result=excluded.final_result,provider_status=excluded.provider_status,
                outcome_type=excluded.outcome_type,recorded_at=excluded.recorded_at""",
                (event_key,winner_side,final_result,provider_status,outcome_type,now()))
        return True

    def matches_without_result(self, days):
        """Started matches worth settling (alerted, tracked live or third-set decided) that have no result yet."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return self.conn.execute("""SELECT m.* FROM matches m LEFT JOIN match_results r USING(event_key)
            WHERE r.event_key IS NULL AND m.created_at>=?
            AND (m.status IN ('LIVE','ALERTED','FINISHED','EXPIRED')
                 OR EXISTS (SELECT 1 FROM alerts a WHERE a.event_key=m.event_key))""", (cutoff,)).fetchall()

    def alerts_with_results(self, since_utc):
        return self.conn.execute("""SELECT a.*,m.first_player_name,m.second_player_name,m.tournament_name,
            r.winner_side,r.final_result,r.outcome_type
            FROM alerts a JOIN matches m USING(event_key) LEFT JOIN match_results r USING(event_key)
            WHERE a.created_at>=? ORDER BY a.created_at""", (since_utc.isoformat(),)).fetchall()
