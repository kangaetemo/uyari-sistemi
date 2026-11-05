"""Run the statistics panel: python -m app.web"""
import ipaddress
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from dotenv import load_dotenv

from app.web.server import create_app


def main():
    load_dotenv()
    host = os.getenv("PANEL_HOST", "127.0.0.1").strip()
    port = int(os.getenv("PANEL_PORT", "8080"))
    username = os.getenv("PANEL_USER", "admin").strip()
    password = os.getenv("PANEL_PASSWORD", "").strip()
    if len(password) < 8:
        sys.exit("Hata: PANEL_PASSWORD .env içinde en az 8 karakter olmalı.")
    try:
        public = not ipaddress.ip_address(host).is_loopback
    except ValueError:
        public = host not in {"localhost"}
    if public:
        print(f"Uyarı: panel {host}:{port} üzerinde dışarıya açık. Bağlantı HTTPS değil; "
              "mümkünse SSH tüneli veya HTTPS ters vekil kullanın (docs/PANEL.md).", file=sys.stderr)
    app = create_app(Path(os.getenv("DATABASE_PATH", "data/tennis_alert.db")), username, password,
                     ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/Istanbul")))
    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=True)


if __name__ == "__main__":
    main()
