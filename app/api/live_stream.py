"""API-Tennis livescore WebSocket.

The socket pushes a match every time a point is played, so the bot learns that a
third set started at 0-0 the moment it happens instead of polling for it. It
carries scores only, never odds: odds still come from the REST API, but only
when something needs them.
"""
import asyncio
import json
import logging
import time
from urllib.parse import urlencode

log = logging.getLogger(__name__)
WS_URL = "wss://wss.api-tennis.com/live"
STALE_SECONDS = 30 * 60
FINAL_WORDS = ("finish", "ended", "cancel", "retired", "walk", "abandon")


def match_rows(message):
    """Normalise one socket message to a list of match dicts with an event_key.

    The documentation shows single match objects; a list, a {"result": ...}
    wrapper or a dict keyed by event_key are accepted as well.
    """
    if isinstance(message, list):
        rows = message
    elif isinstance(message, dict) and "event_key" in message:
        rows = [message]
    elif isinstance(message, dict) and isinstance(message.get("result"), (list, dict)):
        return match_rows(message["result"])
    elif isinstance(message, dict):
        rows = list(message.values())
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict) and row.get("event_key") is not None]


def is_final(fixture):
    status = str(fixture.get("event_status") or "").lower()
    return any(word in status for word in FINAL_WORDS)


class LiveScoreStream:
    def __init__(self, key, timezone_name, *, connect=None, monotonic=time.monotonic):
        self.key, self.timezone_name = key, timezone_name
        self._connect = connect
        self._monotonic = monotonic
        self.matches = {}        # event_key -> latest fixture dict
        self._seen_at = {}       # event_key -> monotonic time of the last update
        self.connected = False
        self.messages = 0
        self.last_message_at = None
        self.listeners = []

    def url(self):
        return f"{WS_URL}?{urlencode({'APIkey': self.key, 'timezone': self.timezone_name})}"

    def handle(self, raw):
        """Apply one raw message; returns the fixtures it updated."""
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("WebSocket: JSON olmayan mesaj atlandı")
            return []
        rows = match_rows(message)
        moment = self._monotonic()
        self.messages += 1
        self.last_message_at = moment
        for row in rows:
            key = str(row["event_key"])
            if is_final(row):
                self.matches.pop(key, None)
                self._seen_at.pop(key, None)
            else:
                self.matches[key] = row
                self._seen_at[key] = moment
        for key in [k for k, seen in self._seen_at.items() if moment - seen > STALE_SECONDS]:
            self.matches.pop(key, None)
            self._seen_at.pop(key, None)
        for listener in self.listeners:
            try:
                listener(rows)
            except Exception:
                log.exception("WebSocket dinleyici hatası")
        return rows

    async def _open(self):
        if self._connect is not None:
            return self._connect(self.url())
        from websockets.asyncio.client import connect
        return connect(self.url(), ping_interval=20, ping_timeout=20, open_timeout=20, max_size=8 * 2 ** 20)

    async def run(self):
        delay = 1
        while True:
            try:
                async with await self._open() as socket:
                    self.connected = True
                    delay = 1
                    log.info("WebSocket bağlandı (canlı skor)")
                    async for raw in socket:
                        self.handle(raw)
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as exc:
                # Only the type and status: the URL carries the API key.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                log.warning("WebSocket bağlantısı koptu: %s%s; %s sn sonra yeniden denenecek",
                            type(exc).__name__, f" (HTTP {status})" if status else "", delay)
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
