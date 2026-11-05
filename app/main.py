import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.api.live_stream import LiveScoreStream
from app.api.tennis import TennisAPI
from app.config import load_config, parse_decimal, validate_live_trigger
from app.core.results import format_report
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
    config = load_config(test_telegram=args.test_telegram, require_api=not args.report)
    setup_logging(config.log_level)
    if args.live_trigger is not None:
        from dataclasses import replace
        value = parse_decimal("--live-trigger", args.live_trigger)
        validate_live_trigger(value, config.prematch_max)
        config = replace(config,live_trigger=value)
    if args.report:
        repo = Repository(config.database_path)
        try:
            since = datetime.now(timezone.utc) - timedelta(days=args.days)
            print(format_report(repo.alerts_with_results(since), args.days))
        finally:
            repo.close()
        return
    api = TennisAPI(config.api_key, config.timezone.key) if not args.test_telegram else None
    repo = Repository(config.database_path)
    telegram = Telegram(config.telegram_token,config.telegram_chat_id,config.dry_run,config.timezone)
    stream = LiveScoreStream(config.api_key, config.timezone.key) if api and config.websocket_enabled else None
    monitor = Monitor(config,api,repo,telegram,stream) if api else None
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
        elif args.collect_results:
            await monitor.maintenance()
        elif args.test_websocket:
            await test_websocket(LiveScoreStream(config.api_key, config.timezone.key))
        else:
            logging.info("Tennis Alert başladı | dry_run=%s | live=%ss | 3. set=%s | WebSocket=%s",
                         config.dry_run, config.live_poll_seconds, config.third_set_enabled, bool(stream))
            await asyncio.gather(
                *([stream.run()] if stream else []),
                monitor.loop(monitor.prematch_scan,config.prematch_poll_seconds),
                monitor.live_loop(config.live_poll_seconds),
                monitor.delivery_loop(),
                monitor.loop(monitor.maintenance,config.result_poll_seconds),
                monitor.status_loop())
    finally:
        logging.info("Tennis Alert güvenli şekilde kapatılıyor...")
        await telegram.close()
        if api:
            await api.close()
        repo.close()


async def test_websocket(stream, seconds=30):
    """Listen for a while and summarise what the socket sends; nothing is written."""
    statuses = {}

    def count(rows):
        for row in rows:
            statuses[str(row.get("event_status") or "?")] = statuses.get(str(row.get("event_status") or "?"), 0) + 1
    stream.listeners.append(count)
    task = asyncio.create_task(stream.run())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if not stream.messages:
        raise RuntimeError(f"{seconds} saniyede WebSocket mesajı gelmedi; paket WebSocket içermiyor olabilir veya şu an canlı maç yok")
    logging.info("WebSocket çalışıyor: %s saniyede %s mesaj, %s canlı maç", seconds, stream.messages, len(stream.matches))
    for status, total in sorted(statuses.items(), key=lambda item: -item[1])[:8]:
        logging.info("  %s: %s güncelleme", status, total)
    third = [m for m in stream.matches.values() if str(m.get("event_status")).strip().lower() == "set 3"]
    for match in third[:5]:
        sets = {str(s.get("score_set")): f"{s.get('score_first')}-{s.get('score_second')}" for s in match.get("scores") or [] if isinstance(s, dict)}
        logging.info("  3. set: %s vs %s | setler %s | oyun %s", match.get("event_first_player"),
                     match.get("event_second_player"), sets, match.get("event_game_result"))


def main():
    parser = argparse.ArgumentParser(description="Tenis canlı oran ve Telegram alarm sistemi")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once",action="store_true")
    modes.add_argument("--scan-prematch",action="store_true")
    modes.add_argument("--scan-live",action="store_true")
    modes.add_argument("--test-telegram",action="store_true")
    modes.add_argument("--test-websocket",action="store_true",help="WebSocket bağlantısını 30 sn dinle ve özetle")
    modes.add_argument("--collect-results",action="store_true",help="Eski takipleri kapat ve maç sonuçlarını topla")
    modes.add_argument("--report",action="store_true",help="Alarm sonuçları ve kazanma oranı raporu")
    parser.add_argument("--days",type=int,default=30,help="--report için gün sayısı (varsayılan 30)")
    parser.add_argument("--live-trigger",help="Canlı alarm eşiği")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except (ValueError,RuntimeError) as exc:
        parser.exit(2,f"Hata: {exc}\n")
    except Exception:
        logging.exception("Beklenmeyen hata; uygulama kapanıyor")
        parser.exit(1,"Beklenmeyen hata; ayrıntı logs/tennis-alert.log içinde.\n")
