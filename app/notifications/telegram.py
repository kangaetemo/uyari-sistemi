import html
import logging

import httpx

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token, chat_id, dry_run, timezone):
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self.token, self.chat_id, self.dry_run, self.timezone = token, chat_id, dry_run, timezone
        self.client = httpx.AsyncClient(timeout=15)

    async def close(self):
        await self.client.aclose()

    async def send(self, message, *, force=False):
        if self.dry_run and not force:
            log.warning("DRY RUN Telegram alarmı:\n%s", message)
            return "dry-run"
        try:
            response = await self.client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                              json={"chat_id":self.chat_id,"text":message,"parse_mode":"HTML"})
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise ValueError("Telegram API ok=false")
            return data["result"]["message_id"]
        except Exception as exc:  # noqa: BLE001 - a bad response must never stop the alert queue
            # Only the type is logged: httpx messages contain the URL, which carries the bot token.
            log.error("Telegram gönderimi başarısız: %s", type(exc).__name__)
            return None


def format_alert(alert, timezone):
    from datetime import datetime
    clean = lambda value: html.escape(str(value or "Bilinmiyor"))
    local_time = datetime.fromisoformat(alert["created_at"]).astimezone(timezone).strftime("%H:%M:%S")
    if "rule_type" in alert.keys() and alert["rule_type"] == "SERVE_FOR_SET":
        return format_serve_for_set(alert, clean, local_time)
    third_set = "rule_type" in alert.keys() and alert["rule_type"] == "THIRD_SET"
    reference = (f"📉 3. set başlangıcı: {clean(alert['reference_odds'])}\n" if third_set
                 else f"📉 Maç önü: {clean(alert['prematch_odds'])}\n")
    rule = "📋 Kural: 3. set başlangıcı → canlı oran artışı\n" if third_set else ""
    return ("🚨 <b>TENİS ORAN ALARMI</b>\n\n"
            f"{rule}"
            f"🎾 {clean(alert['first_player_name'])} vs {clean(alert['second_player_name'])}\n"
            f"🏆 {clean(alert['tournament_name'])}\n\n"
            f"⭐ Takip edilen: {clean(alert['favorite_player_name'])}\n"
            f"📍 Taraf: {clean(alert['favorite_side'])}\n"
            f"{reference}"
            f"📈 Canlı oran: {clean(alert['trigger_live_odds'])}\n"
            f"🎯 Alarm seviyesi: {'>' if third_set else '≥'} {clean(alert['threshold'])}\n"
            f"⏱ {local_time}\n🆔 Event: {clean(alert['event_key'])}")


def format_serve_for_set(alert, clean, local_time):
    favorite_home = alert["favorite_side"] == "Home"
    opponent = alert["second_player_name"] if favorite_home else alert["first_player_name"]
    keys = alert.keys()
    games = (f"{alert['favorite_games']}-{alert['opponent_games']}"
             if "favorite_games" in keys and alert["favorite_games"] is not None else "?")
    points = alert["game_result"] if "game_result" in keys and alert["game_result"] not in (None, "", "-") else None
    return ("🚨 <b>SET İÇİN SERVİS ALARMI</b>\n\n"
            "📋 Kural: maç önü favorisi break geride, rakip set için servis ediyor\n"
            f"🎾 {clean(alert['first_player_name'])} vs {clean(alert['second_player_name'])}\n"
            f"🏆 {clean(alert['tournament_name'])}\n\n"
            f"⭐ Favori: {clean(alert['favorite_player_name'])}\n"
            f"🔢 {clean(alert['set_number'])}. set: {clean(games)} geride | servis: {clean(opponent)}"
            f"{f' | oyun: {clean(points)}' if points else ''}\n"
            "🎯 Seti kurtarmak için bu oyunu kırmalı\n"
            f"📉 Maç önü: {clean(alert['prematch_odds'])}\n"
            f"📈 Canlı oran: {clean(alert['trigger_live_odds'])}\n"
            f"⏱ {local_time}\n🆔 Event: {clean(alert['event_key'])}")
