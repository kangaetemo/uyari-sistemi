import asyncio
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone

import httpx


log = logging.getLogger(__name__)


def safe_api_error(data, secret):
    """Show short provider error fields without echoing credentials or URLs."""
    fields = []
    for name in ("error", "message"):
        value = data.get(name)
        if isinstance(value, (str, int, float)):
            value = str(value).replace(secret, "[REDACTED]") if secret else str(value)
            value = re.sub(r"https?://\S+", "[URL]", value)
            value = re.sub(r"(?i)(APIkey|token)\s*[=:]\s*\S+", r"\1=[REDACTED]", value)
            fields.append(f"{name}={value[:160]}")
    return " | ".join(fields) if fields else "sağlayıcı hata ayrıntısı vermedi"


def retry_after_seconds(value, default=30, limit=60):
    try:
        return max(1, min(int(value), limit))
    except (TypeError, ValueError):
        return default


class TennisAPI:
    def __init__(self, key, timezone_name="Europe/Istanbul"):
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self.key = key
        self.timezone_name = timezone_name
        self.client = httpx.AsyncClient(base_url="https://api.api-tennis.com/tennis/", timeout=20)
        self.counts = Counter()
        self.last_success = None

    async def close(self):
        await self.client.aclose()

    async def request(self, method, **params):
        for attempt in range(4):
            try:
                self.counts[method] += 1
                started = time.monotonic()
                response = await self.client.get("", params={"method":method,"APIkey":self.key,**params})
                log.info("API request | method=%s | status=%s | elapsed=%.2fs", method, response.status_code, time.monotonic() - started)
                if response.status_code == 429:
                    # Wait once, briefly, and let the next poll try again instead of stalling every loop.
                    delay = retry_after_seconds(response.headers.get("Retry-After"))
                    log.warning("API rate limit (%s); %s saniye bekleniyor", method, delay)
                    await asyncio.sleep(delay)
                    return None
                if response.status_code in {401, 403}:
                    log.error("API %s yetki hatası (HTTP %s): anahtar geçersiz veya paket süresi dolmuş olabilir",
                              method, response.status_code)
                    return None
                if 400 <= response.status_code < 500:
                    log.warning("API %s istemci hatası (HTTP %s); tekrar denenmeyecek", method, response.status_code)
                    return None
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    log.warning("API %s geçersiz JSON yapısı", method)
                    return None
                if str(data.get("success")) != "1":
                    success = str(data.get("success"))
                    log.warning("API %s başarısız | success=%s | %s", method,
                                success if success in {"0", "False", "None"} else "beklenmeyen",
                                safe_api_error(data, self.key))
                    return None
                self.last_success = datetime.now(timezone.utc)
                return data
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("API %s hatası (deneme %s): %s", method, attempt + 1, type(exc).__name__)
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
        return None

    async def fixtures(self, start, stop):
        return await self.request("get_fixtures", date_start=start, date_stop=stop, timezone=self.timezone_name)

    async def prematch_odds(self, start, stop):
        return await self.request("get_odds", date_start=start, date_stop=stop, timezone=self.timezone_name)

    async def live_odds(self):
        return await self.request("get_live_odds", timezone=self.timezone_name)

    async def livescore(self):
        return await self.request("get_livescore", timezone=self.timezone_name)
