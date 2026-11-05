#!/usr/bin/env bash
# Debian/Ubuntu (Proxmox LXC dahil) için kurulum veya güncelleme. root olarak çalıştırın:
#   bash deploy/install.sh
# Repo /opt/uyari-sistemi altında değilse oraya klonlanır; varsa güncellenir.
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/kangaetemo/uyari-sistemi.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/uyari-sistemi
APP_USER=tennis

if [ "$(id -u)" -ne 0 ]; then echo "root olarak çalıştırın (sudo bash deploy/install.sh)"; exit 1; fi

apt-get update -qq
apt-get install -y -qq git python3 python3-venv ca-certificates tzdata >/dev/null

id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" pull --quiet --ff-only origin "$BRANCH"
else
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
git config --global --add safe.directory "$APP_DIR"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo ">>> $APP_DIR/.env oluşturuldu. API_TENNIS_KEY ve Telegram bilgilerini yazın, sonra:"
  echo ">>>   systemctl restart tennis-alert"
fi
chmod 600 "$APP_DIR/.env"
mkdir -p "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/backups"
chmod +x "$APP_DIR/deploy/backup.sh"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

install -m 644 "$APP_DIR/deploy/tennis-alert.service" /etc/systemd/system/tennis-alert.service
install -m 644 "$APP_DIR/deploy/tennis-alert-backup.service" /etc/systemd/system/tennis-alert-backup.service
install -m 644 "$APP_DIR/deploy/tennis-alert-backup.timer" /etc/systemd/system/tennis-alert-backup.timer
systemctl daemon-reload
systemctl enable --quiet tennis-alert.service tennis-alert-backup.timer
systemctl start tennis-alert-backup.timer

if grep -qE '^API_TENNIS_KEY=.+' "$APP_DIR/.env"; then
  systemctl restart tennis-alert.service
  echo "Servis yeniden başlatıldı. Durum: systemctl status tennis-alert"
else
  echo "API_TENNIS_KEY boş olduğu için servis başlatılmadı."
fi
