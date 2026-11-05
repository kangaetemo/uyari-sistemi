# Sunucu kurulumu (VDS / Proxmox LXC / Debian / Ubuntu)

Bu kurulum botu bilgisayar oturumundan bağımsız, 7/24 bir `systemd` servisi olarak çalıştırır. Servis çökerse 15 saniye sonra kendini yeniden başlatır ve her gece 04:30'da (UTC) SQLite yedeği alınır.

## 1. Sunucu

Kiralık bir VDS/VPS veya Proxmox LXC olabilir; adımlar aynıdır. Debian 12 veya Ubuntu 24.04 seçin. Bu bot için 1 çekirdek, 512 MB–1 GB RAM ve 10 GB disk yeterlidir. Bot dışarıdan bağlantı kabul etmez, yalnızca API-Tennis ve Telegram'a çıkar; bu yüzden SSH dışında port açmaya gerek yoktur.

Kiralık VDS için ilk güvenlik adımları (root olarak):

```bash
apt-get update && apt-get upgrade -y
apt-get install -y ufw unattended-upgrades
ufw allow OpenSSH && ufw --force enable   # Yalnızca SSH açık kalır
dpkg-reconfigure -plow unattended-upgrades   # Güvenlik güncellemeleri otomatik
```

Mümkünse SSH'a parola yerine anahtarla girin ve parola girişini kapatın (`/etc/ssh/sshd_config` içinde `PasswordAuthentication no`).

Proxmox LXC için arayüzde bir Debian 12 veya Ubuntu 24.04 şablonuyla kapsayıcı oluşturmanız yeterlidir. Kapsayıcının internete çıkabildiğini ve saatinin doğru olduğunu kontrol edin:

```bash
ping -c1 api.api-tennis.com
timedatectl
```

## 2. Kurulum

Kapsayıcıda root olarak:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/kangaetemo/uyari-sistemi.git /opt/uyari-sistemi
bash /opt/uyari-sistemi/deploy/install.sh
```

Betik şunları yapar: `tennis` adında yetkisiz bir sistem kullanıcısı açar, sanal ortamı kurar, `.env.example` dosyasından `.env` oluşturur, servis ve yedek zamanlayıcısını etkinleştirir.

## 3. Ayarlar

```bash
nano /opt/uyari-sistemi/.env
```

En az `API_TENNIS_KEY` gerekli. Gerçek Telegram mesajı için `DRY_RUN=false`, `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` doldurun. Sonra:

```bash
cd /opt/uyari-sistemi
sudo -u tennis .venv/bin/python main.py --test-telegram   # Telegram bağlantısını dener
sudo -u tennis .venv/bin/python main.py --scan-live       # API anahtarını ve canlı veriyi dener
systemctl restart tennis-alert
```

## 4. Günlük kullanım

| İş | Komut |
| --- | --- |
| Servis durumu | `systemctl status tennis-alert` |
| Canlı log | `journalctl -u tennis-alert -f` veya `tail -f /opt/uyari-sistemi/logs/tennis-alert.log` |
| Alarm sonuç raporu | `cd /opt/uyari-sistemi && sudo -u tennis .venv/bin/python main.py --report --days 30` |
| Güncelleme | `bash /opt/uyari-sistemi/deploy/install.sh` |
| Elle yedek | `sudo -u tennis /opt/uyari-sistemi/deploy/backup.sh` |
| Durdurma | `systemctl stop tennis-alert` |
| Panel durumu | `systemctl status tennis-panel` (kurulum ve erişim: [PANEL.md](PANEL.md)) |

Yedekler `/opt/uyari-sistemi/backups/` altında, son 14 tanesi tutulur. Geri yüklemek için servisi durdurun, yedeği açıp `data/tennis_alert.db` üzerine koyun ve servisi başlatın:

```bash
systemctl stop tennis-alert
gunzip -c backups/tennis_alert-YYYYMMDD-HHMMSS.db.gz > data/tennis_alert.db
rm -f data/tennis_alert.db-wal data/tennis_alert.db-shm
chown tennis:tennis data/tennis_alert.db
systemctl start tennis-alert
```

## 5. Eski bilgisayardaki veriyi taşıma

Eski `data/tennis_alert.db` dosyası Git'e girmez. Taşımak isterseniz servis dururken dosyayı `/opt/uyari-sistemi/data/` altına kopyalayın (`scp` veya Proxmox dosya yükleme) ve sahibini `tennis` yapın. Yeni şema alanları ilk açılışta otomatik eklenir.

## Notlar

- `.env` dosyası yalnızca `tennis` kullanıcısı tarafından okunabilir (`chmod 600`). Gizli bilgileri Git'e koymayın.
- API paketinin süresi dolarsa logda `yetki hatası (HTTP 401/403)` görünür; anahtarı `.env` içinde yenileyip servisi yeniden başlatın.
- `LIVE_POLL_SECONDS=5` günde yaklaşık 17.000 canlı oran çağrısı yapar. Paketinizin kotası düşükse 10–15 saniyeye çıkarın.
