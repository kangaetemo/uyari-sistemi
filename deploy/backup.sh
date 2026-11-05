#!/usr/bin/env bash
# Çalışan uygulamayı durdurmadan tutarlı bir SQLite yedeği alır ve son 14 yedeği tutar.
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${DATABASE_PATH:-data/tennis_alert.db}"
DEST="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP:-14}"
mkdir -p "$DEST"
[ -f "$DB" ] || { echo "Veritabanı yok: $DB"; exit 0; }
STAMP="$(date -u +%Y%m%d-%H%M%S)"
.venv/bin/python - "$DB" "$DEST/tennis_alert-$STAMP.db" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close(); dst.close()
PY
gzip "$DEST/tennis_alert-$STAMP.db"
ls -1t "$DEST"/tennis_alert-*.db.gz | tail -n +"$((KEEP + 1))" | xargs -r rm --
echo "Yedek alındı: $DEST/tennis_alert-$STAMP.db.gz"
