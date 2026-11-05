import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.api.tennis import TennisAPI
from app.config import load_config
from app.core.monitor import Monitor
from app.db.repository import Repository
from app.notifications.telegram import Telegram


def setup_logging(level):
    Path("logs").mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(), RotatingFileHandler("logs/tennis-alert.log",maxBytes=5_000_000,backupCount=3,encoding="utf-8")]
    logging.basicConfig(level=level,format="%(asctime)s %(levelname)s %(message)s",handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run(args):
    config = load_config(test_telegram=args.test_telegram)
    setup_logging(config.log_level)
    if args.live_trigger is not None:
        from dataclasses import replace
        from decimal import Decimal
        value = Decimal(args.live_trigger)
        if value <= 1:
            raise ValueError("--live-trigger > 1 olmalı")
        config = replace(config,live_trigger=value)
    api = TennisAPI(config.api_key) if not args.test_telegram else None
    repo = Repository(config.database_path)
    telegram = Telegram(config.telegram_token,config.telegram_chat_id,config.dry_run,config.timezone)
    monitor = Monitor(config,api,repo,telegram) if api else None
    try:
        if args.test_telegram:
            result = await telegram.send("🎾 Tennis Alert test bildirimi.\nTelegram bağlantısı çalışıyor.",force=True)
            if result is None:
                raise RuntimeError("Telegram test bildirimi gönderilemedi")
            logging.info("Telegram test bildirimi gönderildi")
        elif args.scan_prematch:
            found = await monitor.prematch_scan()
            for key,fixture,side,value in found:
                logging.info("%s | %s vs %s | Favori: %s | Medyan: %s",key,fixture.get("event_first_player"),fixture.get("event_second_player"),side,value)
            logging.info("Toplam: %s uygun maç",len(found))
        elif args.scan_live:
            await monitor.diagnose_live()
        elif args.once:
            await monitor.prematch_scan()
            await monitor.live_scan()
            await monitor.deliver_alerts()
        else:
            await asyncio.gather(
                monitor.loop(monitor.prematch_scan,config.prematch_poll_seconds),
                monitor.loop(monitor.scheduled_live_scan,config.live_poll_seconds),
                monitor.loop(monitor.deliver_alerts,30),
                monitor.status_loop())
    finally:
        logging.info("Tennis Alert güvenli şekilde kapatılıyor...")
        await telegram.close()
        if api:
            await api.close()
        repo.close()


def main():
    parser = argparse.ArgumentParser(description="Tenis canlı oran ve Telegram alarm sistemi")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once",action="store_true")
    modes.add_argument("--scan-prematch",action="store_true")
    modes.add_argument("--scan-live",action="store_true")
    modes.add_argument("--test-telegram",action="store_true")
    parser.add_argument("--live-trigger",help="Canlı alarm eşiği")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (ValueError,RuntimeError) as exc:
        parser.exit(2,f"Hata: {exc}\n")
