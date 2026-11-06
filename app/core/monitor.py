import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from app.api.live_stream import is_final
from app.core.parsers import parse_live_odds, parse_prematch_odds
from app.core.results import parse_result
from app.core.rules import eligible
from app.core.third_set import FeedClock, is_third_set, opening_score, process_third_set
from app.notifications.telegram import format_alert

log = logging.getLogger(__name__)
RESULT_LOOKBACK_DAYS = 7
HEARTBEAT_SECONDS = 30


def event_state(fixture):
    status = str(fixture.get("event_status") or "").lower()
    if any(word in status for word in ("cancel", "postpon", "walkover", "walk over", "retired", "abandon")):
        return "CANCELLED"
    if "finish" in status or "ended" in status:
        return "FINISHED"
    if str(fixture.get("event_live")) == "1" or status.startswith("set "):
        return "LIVE"
    return "PREMATCH"


def favorite_side(odds, minimum, maximum):
    """The side the prematch rule follows: an eligible side first, otherwise the lower price."""
    if eligible(odds.home, minimum, maximum):
        return "Home"
    if eligible(odds.away, minimum, maximum):
        return "Away"
    if odds.home is not None and odds.away is not None and odds.home != odds.away:
        return "Home" if odds.home < odds.away else "Away"
    return None


class Monitor:
    def __init__(self, config, api, repository, telegram, stream=None):
        self.config, self.api, self.repo, self.telegram = config, api, repository, telegram
        self.stream = stream
        self._scan_now = asyncio.Event()
        if stream is not None:
            stream.listeners.append(self._on_stream_update)
            stream.listeners.append(self._store_alerted_scores)
        self._alerted = set()      # Matches whose live score the panel shows; refreshed on every live scan.
        self._last_score = {}
        self._final_from_socket = set()  # The socket's final score is kept; the odds feed must not blank its sets.
        self.started = datetime.now().astimezone()
        self.clock = FeedClock()
        self._delivery_lock = asyncio.Lock()
        self._alerts_ready = asyncio.Event()
        self._last_heartbeat = None

    def _store_result(self, key, fixture):
        result = parse_result(fixture)
        if result and self.repo.save_result(key, result.winner_side, result.final_result,
                                            result.provider_status, result.outcome_type):
            log.info("SONUÇ | event=%s | %s | kazanan=%s | %s", key, result.outcome_type,
                     result.winner_side or "-", result.final_result or "-")

    async def prematch_scan(self):
        local_date = datetime.now(self.config.timezone).date()
        stop = local_date + timedelta(days=1)
        fixtures, odds_data = await asyncio.gather(
            self.api.fixtures(local_date.isoformat(),stop.isoformat()),
            self.api.prematch_odds(local_date.isoformat(),stop.isoformat()))
        if not fixtures or not odds_data:
            log.warning("Prematch taraması eksik API cevabı nedeniyle atlandı")
            return []
        fixture_map = {str(f["event_key"]):f for f in fixtures.get("result",[]) if isinstance(f,dict) and f.get("event_key") is not None}
        odds_map = parse_prematch_odds(odds_data)
        found = []
        for key, fixture in fixture_map.items():
            state = event_state(fixture)
            if state in {"FINISHED", "CANCELLED"} and self.repo.get_match(key):
                self.repo.set_status(key,state)
                self._store_result(key,fixture)
        for key, odds in odds_map.items():
            fixture = fixture_map.get(key)
            if not fixture or event_state(fixture) != "PREMATCH":
                continue  # Never replace the last prematch reference with in-play prices.
            # Recomputed on every scan: the favourite can change before the match starts.
            side = favorite_side(odds,self.config.prematch_min,self.config.prematch_max)
            if not side:
                continue
            value = odds.home if side == "Home" else odds.away
            is_eligible = eligible(value,self.config.prematch_min,self.config.prematch_max)
            self.repo.upsert_prematch(key,fixture,odds,side,is_eligible)
            if is_eligible:
                found.append((key,fixture,side,value))
        log.info("Prematch: %s fixture, %s uygun favori",len(fixture_map),len(found))
        return found

    async def live_scan(self):
        watched = self.repo.watched()
        third_set_enabled = self.config.third_set_enabled
        if not watched and not third_set_enabled:
            return []
        payload = await self.api.live_odds()
        if not payload:
            return []
        live = parse_live_odds(payload)
        self._heartbeat(len(live))
        self._alerted = self.repo.recently_alerted()
        seen = []
        alerted = False
        for key, match in watched.items():
            item = live.get(key)
            if not item:
                continue
            state = event_state({"event_status":item.status,"event_live":"1"})
            if state in {"FINISHED","CANCELLED"}:
                self.repo.set_status(key,state)
                continue
            odd = item.home if match["favorite_side"] == "Home" else item.away
            if odd is None:
                continue
            updated = item.home_updated_at if match["favorite_side"] == "Home" else item.away_updated_at
            log.debug("LIVE | event=%s | %s | %s",key,match["favorite_player_name"],odd)
            if self.repo.record_live(match,odd,updated,self.config.live_trigger):
                alerted = True
                log.warning("ALARM | %s | %s -> %s",match["favorite_player_name"],match["latest_prematch_odds"],odd)
            seen.append((key,match["favorite_player_name"],odd))
        if third_set_enabled:
            scores = self.stream.matches if self.stream_ready() else None
            if await process_third_set(self.config,self.api,self.repo,live,clock=self.clock,scores=scores):
                alerted = True
        if alerted:
            self._alerts_ready.set()  # Delivery runs in its own loop so Telegram never stalls polling.
            self._alerted = self.repo.recently_alerted()
        try:
            self._scores_from_odds(live)
        except Exception:
            log.exception("Canlı skor kaydedilemedi")
        return seen

    def _save_score(self, key, source, **score):
        signature = tuple(score.values())
        if self._last_score.get(key) == signature:
            return
        self._last_score[key] = signature
        self.repo.save_live_score(key, source=source, **score)

    def _store_alerted_scores(self, rows):
        """Keep the panel's live score of alerted matches current; uses socket data only, no API call."""
        for row in rows:
            key = str(row["event_key"])
            if key not in self._alerted:
                continue
            sets = sorted(([str(s.get("score_set")), str(s.get("score_first")), str(s.get("score_second"))]
                           for s in row.get("scores") or [] if isinstance(s, dict) and s.get("score_set") is not None),
                          key=lambda item: int(item[0]) if item[0].isdigit() else 99)
            if is_final(row):
                self._final_from_socket.add(key)
            try:
                self._save_score(key, "ws", status=str(row.get("event_status") or ""),
                                 final_result=row.get("event_final_result"), game_result=row.get("event_game_result"),
                                 serve=row.get("event_serve"), sets=json.dumps(sets) if sets else None,
                                 winner=row.get("event_winner"))
            except Exception:
                log.exception("Canlı skor kaydedilemedi: event=%s", key)

    def _scores_from_odds(self, live):
        """Without socket data for an alerted match, show the odds feed's status and set score instead."""
        for key in set(self._last_score) - self._alerted:
            self._last_score.pop(key, None)
            self._final_from_socket.discard(key)
        socket = self.stream.matches if self.stream_ready() else {}
        for key in self._alerted - socket.keys() - self._final_from_socket:
            item = live.get(key)
            if item is not None:
                self._save_score(key, "odds", status=item.status, final_result=item.score,
                                 game_result=item.game_score, serve=None, sets=None, winner=None)

    def stream_ready(self):
        """Use WebSocket scores only while the socket is connected; otherwise fall back to get_livescore."""
        return self.stream is not None and self.stream.connected

    def _on_stream_update(self, rows):
        """A third set just reached 0-0: scan now instead of waiting for the next poll."""
        if not self.config.third_set_enabled:
            return
        for row in rows:
            key = str(row["event_key"])
            if is_third_set(str(row.get("event_status") or "")) and opening_score(row) and not self.repo.third_set_decided(key):
                log.info("3. SET | event=%s | WebSocket: set 0-0 başladı; hemen taranıyor", key)
                self._scan_now.set()
                return

    async def live_loop(self, seconds):
        while True:
            try:
                await self.scheduled_live_scan()
            except Exception:
                log.exception("Tarama hatası; döngü devam edecek")
            try:
                await asyncio.wait_for(self._scan_now.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass
            self._scan_now.clear()

    def _heartbeat(self, live_events):
        """Let the panel show that the bot is alive without a write on every 5 s poll."""
        moment = time.monotonic()
        if self._last_heartbeat is not None and moment - self._last_heartbeat < HEARTBEAT_SECONDS:
            return
        self._last_heartbeat = moment
        self.repo.set_state("last_live_scan", datetime.now(timezone.utc).isoformat())
        self.repo.set_state("live_events", live_events)
        if self.stream is not None:
            self.repo.set_state("stream_connected", int(self.stream.connected))
            self.repo.set_state("stream_messages", self.stream.messages)

    async def diagnose_live(self):
        """Read-only API/watchlist inspection, including when watchlist is empty."""
        watched = self.repo.watched()
        log.info("Aktif watchlist: %s maç", len(watched))
        third_watches = self.repo.third_set_watched()
        log.info("Aktif 3. set takibi: %s taraf", len(third_watches))
        if not watched:
            log.info("Aktif watchlist boş; karşılaştırılacak maç yok.")
        payload = await self.api.live_odds()
        if payload is None:
            log.warning("Live API verisi alınamadı veya success != 1.")
            return
        result = payload.get("result")
        if not isinstance(result, dict):
            log.warning("Live API result beklenen nesne yapısında değil.")
            return
        live = parse_live_odds(payload)
        log.info("Live API: %s event alındı", len(result))
        usable = sum(item.home is not None or item.away is not None for item in live.values())
        log.info("Kullanılabilir To Win marketi: %s", usable)
        if not result:
            log.info("API şu anda live event döndürmedi.")
        matched = watched.keys() & live.keys()
        log.info("Watchlist ile eşleşen live event: %s", len(matched))
        if result and watched and not matched:
            log.info("Live maçlar mevcut ancak aktif watchlist ile event_key eşleşmesi bulunamadı.")
        for key in sorted(matched):
            match, item = watched[key], live[key]
            odd = item.home if match["favorite_side"] == "Home" else item.away
            if odd is None:
                log.info("%s | Live event bulundu ancak kullanılabilir To Win marketi bulunamadı", key)
            else:
                log.info("%s | %s vs %s | Favori: %s | Prematch: %s | Live: %s",
                         key, match["first_player_name"] or "?", match["second_player_name"] or "?",
                         match["favorite_side"], match["latest_prematch_odds"], odd)
        for watch in third_watches:
            item = live.get(watch["event_key"])
            if item:
                odd = item.home if watch["favorite_side"] == "Home" else item.away
                log.info("3. SET | event=%s | taraf=%s | başlangıç=%s | canlı=%s | durum=%s",
                         watch["event_key"],watch["favorite_side"],watch["opening_odds"],odd,item.status)
        for key, record in list(result.items())[:5]:
            if not isinstance(record, dict):
                continue
            event_key = str(record.get("event_key") or key)
            item = live.get(event_key)
            if item is None:
                continue
            first = record.get("event_first_player") or (f"Oyuncu #{record['first_player_key']}" if record.get("first_player_key") else "?")
            second = record.get("event_second_player") or (f"Oyuncu #{record['second_player_key']}" if record.get("second_player_key") else "?")
            log.info("%s | %s vs %s | Status: %s | To Win Home: %s | Away: %s | upd: %s",
                     event_key, first, second,
                     item.status or "?", item.home if item.home is not None else "yok",
                     item.away if item.away is not None else "yok", item.home_updated_at or "yok")

    async def scheduled_live_scan(self):
        if self.config.third_set_enabled:
            await self.live_scan()
            return
        watched = self.repo.watched()
        if not watched:
            return
        current = datetime.now(self.config.timezone)
        for match in watched.values():
            if match["status"] in {"LIVE", "ALERTED"}:
                await self.live_scan()
                return
            try:
                start = datetime.fromisoformat(match["match_start_time"]).replace(tzinfo=self.config.timezone)
            except (TypeError,ValueError):
                await self.live_scan()
                return
            if start <= current + timedelta(hours=2):
                await self.live_scan()
                return

    async def deliver_alerts(self):
        async with self._delivery_lock:
            for alert in self.repo.pending_alerts():
                if not self.repo.claim_alert(alert["id"]):
                    continue  # Another process is sending it.
                try:
                    message_id = await self.telegram.send(format_alert(alert,self.config.timezone))
                except Exception:
                    log.exception("Telegram alarmı hazırlanamadı/gönderilemedi: event=%s", alert["event_key"])
                    message_id = None
                if message_id is not None:
                    self.repo.mark_sent(alert["id"],message_id)
                    log.info("Telegram bildirimi gönderildi: event=%s",alert["event_key"])
                else:
                    self.repo.mark_failed(alert["id"],min(30 * 2 ** alert["attempts"],900))

    async def delivery_loop(self, fallback_seconds=30):
        while True:
            try:
                await self.deliver_alerts()
            except Exception:
                log.exception("Bildirim döngüsü hatası; döngü devam edecek")
            try:
                await asyncio.wait_for(self._alerts_ready.wait(), timeout=fallback_seconds)
            except asyncio.TimeoutError:
                pass
            self._alerts_ready.clear()

    def _result_dates(self, rows):
        days = []
        for row in rows:
            try:
                days.append(datetime.fromisoformat(row["match_start_time"]).date())
            except (TypeError, ValueError):
                try:
                    days.append(datetime.fromisoformat(row["created_at"]).astimezone(self.config.timezone).date())
                except (TypeError, ValueError):
                    continue
        today = datetime.now(self.config.timezone).date()
        earliest = max(min(days, default=today), today - timedelta(days=RESULT_LOOKBACK_DAYS))
        return earliest, today

    async def collect_results(self):
        """Settle started matches so every alert ends as WON, LOST or VOID."""
        rows = self.repo.matches_without_result(RESULT_LOOKBACK_DAYS)
        if not rows:
            return 0
        start, stop = self._result_dates(rows)
        payload = await self.api.fixtures(start.isoformat(), stop.isoformat())
        if not payload:
            return 0
        fixtures = {str(f["event_key"]):f for f in payload.get("result",[]) if isinstance(f,dict) and f.get("event_key") is not None}
        saved = 0
        for row in rows:
            fixture = fixtures.get(row["event_key"])
            if not fixture or parse_result(fixture) is None:
                continue
            state = event_state(fixture)
            if state in {"FINISHED", "CANCELLED"} and row["is_watched"]:
                self.repo.set_status(row["event_key"], state)
            self._store_result(row["event_key"], fixture)
            saved += 1
        return saved

    async def maintenance(self):
        expired = self.repo.expire_stale(self.config.timezone, self.config.match_expiry_hours,
                                         self.config.live_stale_minutes)
        if expired:
            log.info("Süresi geçen %s takip kapatıldı: %s", len(expired), ", ".join(expired[:10]))
        await self.collect_results()
        self.repo.set_state("last_maintenance", datetime.now(timezone.utc).isoformat())

    async def loop(self, operation, seconds):
        while True:
            try:
                await operation()
            except Exception:
                log.exception("Tarama hatası; döngü devam edecek")
            await asyncio.sleep(seconds)

    async def status_loop(self):
        while True:
            await asyncio.sleep(self.config.status_poll_seconds)
            try:
                count = len(self.repo.watched())
                stream = (f"bağlı, {self.stream.messages} mesaj" if self.stream.connected else "kopuk") if self.stream else "kapalı"
                log.info("STATUS | uptime=%s | watchlist=%s | bugün alarm=%s | API requests=%s | WebSocket=%s | son başarılı=%s",
                         datetime.now().astimezone()-self.started,count,self.repo.count_today_alerts(self.config.timezone),
                         dict(self.api.counts),stream,self.api.last_success)
            except Exception:
                log.exception("Durum kaydı yazılamadı")
