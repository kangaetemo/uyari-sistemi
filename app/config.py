import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    api_key: str
    telegram_token: str
    telegram_chat_id: str
    prematch_min: Decimal
    prematch_max: Decimal
    live_trigger: Decimal
    prematch_poll_seconds: int
    live_poll_seconds: int
    status_poll_seconds: int
    timezone: ZoneInfo
    dry_run: bool
    database_path: Path
    log_level: str
    third_set_enabled: bool = True
    third_set_min: Decimal = Decimal("1.30")
    third_set_max: Decimal = Decimal("1.60")
    third_set_trigger: Decimal = Decimal("2.00")
    third_set_odds_max_age_seconds: int = 30


def load_config(*, test_telegram: bool = False) -> Config:
    load_dotenv()
    def decimal(name, default):
        return Decimal(os.getenv(name, default))
    def integer(name, default):
        return int(os.getenv(name, default))
    dry_run = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes"}
    try:
        timezone = ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/Istanbul"))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("APP_TIMEZONE bulunamadı; tzdata paketini kurun veya geçerli bir zaman dilimi seçin") from exc
    config = Config(
        api_key=os.getenv("API_TENNIS_KEY", "").strip(),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        prematch_min=decimal("PREMATCH_MIN", "1.01"),
        prematch_max=decimal("PREMATCH_MAX", "1.35"),
        live_trigger=decimal("LIVE_TRIGGER", "2.00"),
        prematch_poll_seconds=integer("PREMATCH_POLL_SECONDS", "300"),
        live_poll_seconds=integer("LIVE_POLL_SECONDS", "5"),
        status_poll_seconds=integer("STATUS_POLL_SECONDS", "600"),
        timezone=timezone,
        dry_run=dry_run,
        database_path=Path(os.getenv("DATABASE_PATH", "data/tennis_alert.db")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        third_set_enabled=os.getenv("THIRD_SET_ENABLED", "true").lower() in {"1", "true", "yes"},
        third_set_min=decimal("THIRD_SET_MIN", "1.30"),
        third_set_max=decimal("THIRD_SET_MAX", "1.60"),
        third_set_trigger=decimal("THIRD_SET_TRIGGER", "2.00"),
        third_set_odds_max_age_seconds=integer("THIRD_SET_ODDS_MAX_AGE_SECONDS", "30"),
    )
    if not test_telegram and not config.api_key:
        raise ValueError("API_TENNIS_KEY .env içinde gerekli")
    if (not dry_run or test_telegram) and not (config.telegram_token and config.telegram_chat_id):
        raise ValueError("Telegram için TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID gerekli")
    if not (Decimal("1") < config.prematch_min <= config.prematch_max):
        raise ValueError("PREMATCH_MIN > 1 ve PREMATCH_MIN <= PREMATCH_MAX olmalı")
    if config.live_trigger <= 1:
        raise ValueError("LIVE_TRIGGER > 1 olmalı")
    if not (config.third_set_min.is_finite() and config.third_set_max.is_finite()
            and Decimal("1") < config.third_set_min <= config.third_set_max):
        raise ValueError("THIRD_SET_MIN > 1 ve THIRD_SET_MIN <= THIRD_SET_MAX olmalı")
    if not config.third_set_trigger.is_finite() or config.third_set_trigger <= config.third_set_max:
        raise ValueError("THIRD_SET_TRIGGER > THIRD_SET_MAX olmalı")
    if config.third_set_odds_max_age_seconds < 1:
        raise ValueError("THIRD_SET_ODDS_MAX_AGE_SECONDS >= 1 olmalı")
    if config.prematch_poll_seconds < 30 or config.live_poll_seconds < 2 or config.status_poll_seconds < 30:
        raise ValueError("Polling aralıkları: prematch >=30, live >=2, status >=30 saniye")
    return config
