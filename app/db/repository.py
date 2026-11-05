import sqlite3
from datetime import datetime, timezone
from pathlib import Path


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
"""


class Repository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate_alerts(path)

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

    def close(self):
        self.conn.close()

    def watched(self):
        return {row["event_key"]: row for row in self.conn.execute("SELECT * FROM matches WHERE is_watched=1 AND alerted=0")}

    def get_match(self, event_key):
        return self.conn.execute("SELECT * FROM matches WHERE event_key=?", (event_key,)).fetchone()

    def upsert_prematch(self, event_key, fixture, odds, side, eligible):
        old = self.get_match(event_key)
        if old and old["status"] in {"LIVE", "ALERTED", "FINISHED", "CANCELLED"}:
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
                match_start_time,status,is_watched,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                first_player_name=excluded.first_player_name,second_player_name=excluded.second_player_name,
                tournament_name=excluded.tournament_name,favorite_player_key=excluded.favorite_player_key,
                favorite_player_name=excluded.favorite_player_name,favorite_side=excluded.favorite_side,
                latest_prematch_odds=excluded.latest_prematch_odds,prematch_updated_at=excluded.prematch_updated_at,
                status=excluded.status,is_watched=excluded.is_watched,updated_at=excluded.updated_at""",
                (event_key,str(first_key or ""),str(second_key or ""),first_name,second_name,
                 fixture.get("tournament_name"),str(fixture.get("tournament_key") or ""),str(player_key or ""),player_name,side,
                 str(value),str(value),stamp,stamp,
                 f"{fixture.get('event_date','')}T{fixture.get('event_time','')}",status,int(eligible),stamp,stamp))
            if not old or old["latest_prematch_odds"] != str(value):
                self.conn.execute("INSERT INTO prematch_odds_history(event_key,home_median,away_median,favorite_side,favorite_odds,observed_at) VALUES (?,?,?,?,?,?)",
                                  (event_key,str(odds.home) if odds.home else None,str(odds.away) if odds.away else None,side,str(value),stamp))

    def set_status(self, event_key, status):
        with self.conn:
            self.conn.execute("UPDATE matches SET status=?,is_watched=?,updated_at=? WHERE event_key=?", (status, 0 if status in {"FINISHED","CANCELLED"} else 1, now(),event_key))
            if status in {"FINISHED", "CANCELLED"}:
                self.conn.execute("UPDATE third_set_watches SET status='CLOSED' WHERE event_key=? AND status='WATCHING'", (event_key,))

    def record_live(self, match, odd, provider_updated_at, threshold):
        from decimal import Decimal
        event_key = match["event_key"]
        previous = self.conn.execute("SELECT live_odds FROM live_odds_history WHERE event_key=? ORDER BY id DESC LIMIT 1", (event_key,)).fetchone()
        stamp = now()
        with self.conn:
            if not previous or previous["live_odds"] != str(odd):
                self.conn.execute("INSERT INTO live_odds_history(event_key,favorite_side,live_odds,provider_updated_at,observed_at) VALUES (?,?,?,?,?)",
                                  (event_key,match["favorite_side"],str(odd),provider_updated_at,stamp))
            self.conn.execute("UPDATE matches SET status='LIVE',updated_at=? WHERE event_key=? AND status != 'ALERTED'", (stamp,event_key))
            if Decimal(str(odd)) >= threshold and not match["alerted"]:
                self.conn.execute("INSERT OR IGNORE INTO alerts(event_key,favorite_player_name,prematch_odds,previous_live_odds,trigger_live_odds,threshold,created_at,favorite_side,reference_odds) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (event_key,match["favorite_player_name"],match["latest_prematch_odds"],previous["live_odds"] if previous else None,str(odd),str(threshold),stamp,match["favorite_side"],match["latest_prematch_odds"]))
                self.conn.execute("UPDATE matches SET alerted=1,is_watched=0,status='ALERTED',updated_at=? WHERE event_key=?", (stamp,event_key))
                return True
        return False

    def pending_alerts(self):
        return self.conn.execute("""SELECT a.*,m.first_player_name,m.second_player_name,m.tournament_name
            FROM alerts a JOIN matches m USING(event_key)
            WHERE a.telegram_sent=0 AND a.attempts<5 AND (a.next_attempt_at IS NULL OR a.next_attempt_at<=?)""", (now(),)).fetchall()

    def mark_sent(self, alert_id, message_id):
        with self.conn:
            self.conn.execute("UPDATE alerts SET telegram_sent=1,telegram_message_id=? WHERE id=?", (str(message_id or ""),alert_id))

    def mark_failed(self, alert_id, delay):
        from datetime import timedelta
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        with self.conn:
            self.conn.execute("UPDATE alerts SET attempts=attempts+1,next_attempt_at=? WHERE id=?", (next_at,alert_id))

    def count_today_alerts(self):
        return self.conn.execute("SELECT COUNT(*) FROM alerts WHERE created_at>=?", (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0]

    def third_set_decided(self, event_key):
        return self.conn.execute("SELECT 1 FROM third_set_sessions WHERE event_key=?", (event_key,)).fetchone() is not None

    def third_set_watched(self):
        return self.conn.execute("SELECT * FROM third_set_watches WHERE status='WATCHING'").fetchall()

    def decide_third_set(self, event_key, fixture, decision, watches=None):
        stamp = now()
        with self.conn:
            self.conn.execute("""INSERT INTO matches(event_key,first_player_key,second_player_key,
                first_player_name,second_player_name,tournament_name,tournament_key,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'LIVE',?,?) ON CONFLICT(event_key) DO UPDATE SET
                first_player_name=COALESCE(excluded.first_player_name,matches.first_player_name),
                second_player_name=COALESCE(excluded.second_player_name,matches.second_player_name),
                tournament_name=COALESCE(excluded.tournament_name,matches.tournament_name)""",
                (event_key,str(fixture.get("first_player_key") or ""),str(fixture.get("second_player_key") or ""),
                 fixture.get("event_first_player"),fixture.get("event_second_player"),fixture.get("tournament_name"),
                 str(fixture.get("tournament_key") or ""),stamp,stamp))
            inserted = self.conn.execute("INSERT OR IGNORE INTO third_set_sessions(event_key,decision,observed_at) VALUES (?,?,?)",(event_key,decision,stamp))
            if inserted.rowcount != 1:
                return
            for side,(odd,updated) in (watches or {}).items():
                name = fixture.get("event_first_player") if side == "Home" else fixture.get("event_second_player")
                self.conn.execute("INSERT INTO third_set_watches(event_key,favorite_side,favorite_player_name,opening_odds,opening_provider_updated_at,observed_at) VALUES (?,?,?,?,?,?)",(event_key,side,name,str(odd),updated,stamp))
                self.conn.execute("INSERT INTO third_set_odds_history(event_key,favorite_side,live_odds,provider_updated_at,observed_at) VALUES (?,?,?,?,?)",(event_key,side,str(odd),updated,stamp))

    def close_third_set(self, event_key):
        with self.conn:
            self.conn.execute("UPDATE third_set_watches SET status='CLOSED' WHERE event_key=? AND status='WATCHING'", (event_key,))

    def record_third_set_live(self, watch, odd, updated, threshold):
        from decimal import Decimal
        key, side = watch["event_key"], watch["favorite_side"]
        stamp = now()
        with self.conn:
            current = self.conn.execute("SELECT status FROM third_set_watches WHERE event_key=? AND favorite_side=?",(key,side)).fetchone()
            if not current or current["status"] != "WATCHING":
                return False
            previous = self.conn.execute("SELECT live_odds,provider_updated_at FROM third_set_odds_history WHERE event_key=? AND favorite_side=? ORDER BY id DESC LIMIT 1",(key,side)).fetchone()
            if previous and previous["provider_updated_at"] and updated and updated < previous["provider_updated_at"]:
                return False
            if not previous or Decimal(previous["live_odds"]) != odd:
                self.conn.execute("INSERT INTO third_set_odds_history(event_key,favorite_side,live_odds,provider_updated_at,observed_at) VALUES (?,?,?,?,?)",(key,side,str(odd),updated,stamp))
            # Strictly above 2.00, as requested; the prematch rule remains >= its threshold.
            if odd <= threshold:
                return False
            inserted = self.conn.execute("""INSERT OR IGNORE INTO alerts(event_key,favorite_player_name,
                previous_live_odds,trigger_live_odds,threshold,created_at,rule_type,favorite_side,reference_odds)
                VALUES (?,?,?,?,?,?,'THIRD_SET',?,?)""",
                (key,watch["favorite_player_name"],previous["live_odds"] if previous else None,str(odd),str(threshold),stamp,side,watch["opening_odds"]))
            self.conn.execute("UPDATE third_set_watches SET status='ALERTED' WHERE event_key=? AND favorite_side=?", (key,side))
            return inserted.rowcount == 1
