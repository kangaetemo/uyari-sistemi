"""`python main.py --teshis`: why a day produced (or did not produce) alerts, read from the database only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.core.rules import eligible

TOP_MATCHES = 12


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _age(value, current):
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "hiç yok"
    seconds = int((current - stamp).total_seconds())
    if seconds < 120:
        return f"{seconds} sn önce"
    if seconds < 7200:
        return f"{seconds // 60} dk önce"
    return f"{seconds // 3600} saat önce"


def daily_report(repo, config, day=None):
    tz = config.timezone
    current = datetime.now(timezone.utc)
    day = day or datetime.now(tz).date()
    start = datetime.combine(day, datetime.min.time(), tz).astimezone(timezone.utc).isoformat()
    stop = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).astimezone(timezone.utc).isoformat()
    conn = repo.conn
    lines = [f"TEŞHİS | {day.isoformat()} ({tz.key})", ""]

    state = {row["key"]: row for row in conn.execute("SELECT * FROM bot_state")}

    def value(key):
        row = state.get(key)
        return row["value"] if row else None

    lines.append("Bot durumu")
    lines.append(f"  son canlı tarama: {_age(value('last_live_scan'), current)} | canlı event: {value('live_events') or '-'}")
    lines.append(f"  WebSocket bağlı: {value('stream_connected') or '-'} | skor kapsamı: {value('stream_coverage') or '-'}")
    lines.append(f"  son bakım/sonuç toplama: {_age(value('last_maintenance'), current)}")
    if value("last_live_scan") is None or (current - datetime.fromisoformat(value("last_live_scan"))).total_seconds() > 300:
        lines.append("  !! Canlı tarama 5 dakikadan uzun süredir çalışmıyor: servis durmuş ya da API cevap vermiyor olabilir.")
    lines.append("")

    alerts = conn.execute("""SELECT a.*,m.first_player_name,m.second_player_name FROM alerts a
        JOIN matches m USING(event_key) WHERE a.created_at>=? AND a.created_at<? ORDER BY a.created_at""",
                          (start, stop)).fetchall()
    lines.append(f"Alarmlar: {len(alerts)}")
    for alert in alerts:
        sent = ("gönderildi" if alert["telegram_sent"] else "gönderim bekliyor" if not alert["attempts"]
                else f"GÖNDERİLEMEDİ ({alert['attempts']} deneme)")
        lines.append(f"  {alert['created_at'][11:16]} UTC | {alert['rule_type']} | {alert['first_player_name']} - "
                     f"{alert['second_player_name']} | {alert['reference_odds']} -> {alert['trigger_live_odds']} | {sent}")
    lines.append("")

    matches = conn.execute("SELECT * FROM matches WHERE match_start_time LIKE ?", (f"{day.isoformat()}T%",)).fetchall()
    favorites = [m for m in matches if m["prematch_source"] is None
                 and eligible(_decimal(m["latest_prematch_odds"]), config.prematch_min, config.prematch_max)]
    statuses = {}
    for match in favorites:
        statuses[match["status"]] = statuses.get(match["status"], 0) + 1
    lines.append(f"Maç önü kuralı ({config.prematch_min}-{config.prematch_max} favori, canlı >= {config.live_trigger})")
    lines.append(f"  bugün başlayan maç (DB'de): {len(matches)} | uygun favori: {len(favorites)} | "
                 + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())))
    peaks = conn.execute("""SELECT h.event_key,MAX(CAST(h.live_odds AS REAL)) AS peak,COUNT(*) AS changes
        FROM live_odds_history h WHERE h.observed_at>=? AND h.observed_at<? GROUP BY h.event_key""", (start, stop)).fetchall()
    by_key = {m["event_key"]: m for m in favorites}
    followed = sorted((row for row in peaks if row["event_key"] in by_key), key=lambda row: -row["peak"])
    lines.append(f"  canlı oranı izlenen uygun favori: {len(followed)}")
    for row in followed[:TOP_MATCHES]:
        match = by_key[row["event_key"]]
        mark = "ALARM" if match["alerted"] else ""
        lines.append(f"    {match['favorite_player_name']} | maç önü {match['latest_prematch_odds']} | en yüksek canlı {row['peak']:.2f} "
                     f"| {row['changes']} değişim | {match['status']} {mark}".rstrip())
    unseen = [m for m in favorites if m["event_key"] not in {row["event_key"] for row in followed}
              and m["status"] not in {"PREMATCH_ELIGIBLE"}]
    if unseen:
        lines.append(f"  canlıya geçtiği/kapandığı halde hiç canlı oranı kaydedilmeyen uygun favori: {len(unseen)}")
        for match in unseen[:TOP_MATCHES]:
            lines.append(f"    {match['favorite_player_name']} | maç önü {match['latest_prematch_odds']} | {match['status']}")
    lines.append("")

    sessions = conn.execute("""SELECT s.*,m.first_player_name,m.second_player_name FROM third_set_sessions s
        JOIN matches m USING(event_key) WHERE s.observed_at>=? AND s.observed_at<? ORDER BY s.observed_at""",
                            (start, stop)).fetchall()
    decisions = {}
    for row in sessions:
        decisions[row["decision"]] = decisions.get(row["decision"], 0) + 1
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(third_set_sessions)")}
    lines.append(f"3. set kuralı ({config.third_set_min}-{config.third_set_max} başlangıç, > {config.third_set_trigger})")
    lines.append(f"  karar verilen 3. set: {len(sessions)} | " + ", ".join(f"{k}={v}" for k, v in sorted(decisions.items())))
    watches = conn.execute("""SELECT w.* FROM third_set_watches w WHERE w.observed_at>=? AND w.observed_at<?""",
                           (start, stop)).fetchall()
    lines.append(f"  takibe alınan taraf: {len(watches)}")
    for watch in watches[:TOP_MATCHES]:
        peak = conn.execute("SELECT MAX(CAST(live_odds AS REAL)) FROM third_set_odds_history WHERE event_key=? AND favorite_side=?",
                            (watch["event_key"], watch["favorite_side"])).fetchone()[0]
        lines.append(f"    {watch['favorite_player_name']} | başlangıç {watch['opening_odds']} | en yüksek {peak or 0:.2f} | {watch['status']}")
    if "detail" in columns:
        for row in [s for s in sessions if s["detail"]][-TOP_MATCHES:]:
            lines.append(f"    {row['observed_at'][11:16]} UTC | {row['decision']} | {row['first_player_name']} - "
                         f"{row['second_player_name']} | {row['detail']}")
    lines.append("")

    serve = conn.execute("SELECT COUNT(*) FROM serve_for_set WHERE created_at>=? AND created_at<?", (start, stop)).fetchone()[0]
    lines.append(f"Set için servis kuralı: bugün {serve} alarm anı kaydedildi")
    return "\n".join(lines)
