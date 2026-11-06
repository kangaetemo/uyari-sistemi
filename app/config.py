import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
    match_expiry_hours: int = 8
    live_stale_minutes: int = 45
    result_poll_seconds: int = 1800
    websocket_enabled: bool = True
    serve_for_set_enabled: bool = True
    serve_for_set_favorite_max: Decimal = Decimal("1.35")


def parse_decimal(name, value):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} geçerli bir ondalık sayı olmalı (ör. 2.00), verilen: {value!r}") from None
    if not number.is_finite():
        raise ValueError(f"{name} sonlu bir sayı olmalı, verilen: {value!r}")
    return number


def load_config(*, test_telegram: bool = False, require_api: bool = True) -> Config:
    load_dotenv()
    def decimal(name, default):
        return parse_decimal(name, os.getenv(name, default))
    def integer(name, default):
        value = os.getenv(name, default)
        try:
            return int(str(value).strip())
        except ValueError:
            raise ValueError(f"{name} tam sayı olmalı, verilen: {value!r}") from None
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
        live_poll_seconds=integer("LIVE_POLL_SECONDS", "3"),
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
        match_expiry_hours=integer("MATCH_EXPIRY_HOURS", "8"),
        live_stale_minutes=integer("LIVE_STALE_MINUTES", "45"),
        result_poll_seconds=integer("RESULT_POLL_SECONDS", "1800"),
        websocket_enabled=os.getenv("WEBSOCKET_ENABLED", "true").lower() in {"1", "true", "yes"},
        serve_for_set_enabled=os.getenv("SERVE_FOR_SET_ENABLED", "true").lower() in {"1", "true", "yes"},
        serve_for_set_favorite_max=decimal("SERVE_FOR_SET_FAVORITE_MAX", "1.35"),
    )
    if require_api and not test_telegram and not config.api_key:
        raise ValueError("API_TENNIS_KEY .env içinde gerekli")
    if (not dry_run or test_telegram) and not (config.telegram_token and config.telegram_chat_id):
        raise ValueError("Telegram için TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID gerekli")
    if not (Decimal("1") < config.prematch_min <= config.prematch_max):
        raise ValueError("PREMATCH_MIN > 1 ve PREMATCH_MIN <= PREMATCH_MAX olmalı")
    validate_live_trigger(config.live_trigger, config.prematch_max)
    if not (Decimal("1") < config.third_set_min <= config.third_set_max):
        raise ValueError("THIRD_SET_MIN > 1 ve THIRD_SET_MIN <= THIRD_SET_MAX olmalı")
    if config.third_set_trigger <= config.third_set_max:
        raise ValueError("THIRD_SET_TRIGGER > THIRD_SET_MAX olmalı")
    if config.third_set_odds_max_age_seconds < 1:
        raise ValueError("THIRD_SET_ODDS_MAX_AGE_SECONDS >= 1 olmalı")
    if config.serve_for_set_favorite_max <= Decimal("1"):
        raise ValueError("SERVE_FOR_SET_FAVORITE_MAX > 1 olmalı")
    if config.prematch_poll_seconds < 30 or config.live_poll_seconds < 2 or config.status_poll_seconds < 30:
        raise ValueError("Polling aralıkları: prematch >=30, live >=2, status >=30 saniye")
    if config.match_expiry_hours < 1 or config.live_stale_minutes < 5 or config.result_poll_seconds < 300:
        raise ValueError("MATCH_EXPIRY_HOURS >= 1, LIVE_STALE_MINUTES >= 5, RESULT_POLL_SECONDS >= 300 olmalı")
    return config


def validate_live_trigger(value, prematch_max):
    if value <= prematch_max:
        # Otherwise every eligible favourite would alert as soon as it goes live.
        raise ValueError("LIVE_TRIGGER, PREMATCH_MAX değerinden büyük olmalı")
