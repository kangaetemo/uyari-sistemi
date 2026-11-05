# Tenis canlı oran alarmı

Geliştirme geçmişi, mevcut durum ve sonraki işler: [Proje durumu ve yol haritası](PROJE_DURUMU_VE_YOL_HARITASI.md).

API-Tennis maç önü `Home/Away` oranlarının bookmaker medyanını izler. Maç öncesi son geçerli oranı varsayılan olarak 1.01–1.35 olan tarafın canlı `To Win` oranı 2.00 veya üzerine çıktığında bir kez Telegram bildirimi hazırlar. Bahis işlemi yapmaz.

Ek kural: üçüncü setin başında maç kazanma (`To Win`) oranı **1.30–1.60 dahil** olan taraf ayrıca izlenir. Aynı üçüncü sette oranı **2.00'nin üstüne** çıkarsa Telegram alarmı üretilir. 2.00 bu kuralı tetiklemez. Önceden prematch favorisi olması gerekmez. Her kural/taraf için ayrı bir alarm tutulur; prematch alarmı üçüncü set alarmını engellemez.

Başlangıç, `get_livescore` yanıtındaki `Set 3`, üçüncü set oyunlarının 0–0 olması ve mevcut oyunun puanlarının 0–0 olmasıyla doğrulanır. Canlı oran da en fazla 30 saniyelik güncel, askıya alınmamış bir snapshot olmalıdır. Güncellik, sağlayıcının saat dilimine bağlı kalmamak için `upd` değerinin aynı yanıttaki en yeni `upd` ile karşılaştırılmasıyla ölçülür; yanıtların tamamı 30 saniyeden uzun süre ilerlemezse akış durmuş sayılır. İki taraftan biri askıda veya eskiyse karar bir sonraki taramaya bırakılır. İlk uygun başlangıç gözlemindeki oran sabitlenir; sonradan aralığa giren taraf eklenmez. Program setin ortasında açılırsa veya başlangıç sırasında veri alınamazsa o maç için bu kural atlanır. API'nin gecikmesi ve polling aralığı nedeniyle her set başlangıcını yakalamak garanti değildir. Dördüncü sete geçen, biten veya iptal edilen maçta üçüncü set takibi sona erer.

`THIRD_SET_ENABLED=true` varsayılandır; `.env` değişikliği yapmadan yeni kural etkinleşir. `THIRD_SET_MIN`, `THIRD_SET_MAX`, `THIRD_SET_TRIGGER` ve `THIRD_SET_ODDS_MAX_AGE_SECONDS` ile ayarlanır. Bu kural açıkken prematch watchlist boş olsa da bütün canlı oranlar her canlı polling aralığında sorgulanır. Henüz başlangıç kararı verilmemiş üçüncü set event'leri varsa ayrıca **tek toplu** canlı skor çağrısı yapılır. `THIRD_SET_ENABLED=false` ile önceki polling davranışına dönülür.

Mevcut SQLite kayıtları korunarak alarm tablosu otomatik güncellenir. Eski şemadan ilk geçişte `data/*before-third-set-*.db` yedeği oluşturulur. Yeni başlangıçlar, takip geçmişi ve alarm durumları SQLite'da saklanır; yeniden başlatmak aynı alarmı tekrar oluşturmaz.

## Sonuçlar ve rapor

Takip edilen maçların sonucu (`get_fixtures` içindeki `event_winner`, yoksa set skoru) `match_results` tablosuna yazılır. Her alarm, takip edilen tarafa göre **Kazandı**, **Kaybetti**, **Bekliyor** veya **Geçersiz** (iptal/walkover) olarak sonuçlanır. Maçtan çekilmeyle biten sonuçlar raporda ayrıca belirtilir.

```powershell
python main.py --report --days 30
```

Rapor kural bazında kazanma oranını, ortalama alarm oranını, o orandaki başabaş kazanma oranını (1/oran) ve her alarma 1 birim yatırılmış gibi hesaplanan teorik getiriyi gösterir. Teorik getiri, alarm anındaki oranın gerçekten alınabildiğini varsayar. 100'den az sonuç olduğunda rapor bunu uyarı olarak belirtir.

Alarmdan sonra da oran geçmişi toplanmaya devam eder: maç önü kuralında maç bitene kadar, üçüncü set kuralında set bitene kadar. Böylece grafik ve farklı eşik denemeleri için veri birikir.

Feed'in kapatmadığı takipler otomatik kapanır: başlangıç saatinden `MATCH_EXPIRY_HOURS` (8) saat sonra canlıya geçmemiş maçlar ve canlı akışta `LIVE_STALE_MINUTES` (45) dakika görünmeyen maçlar `EXPIRED` olur. Bu iş ve sonuç toplama `RESULT_POLL_SECONDS` (1800) saniyede bir çalışır; elle `python main.py --collect-results` ile de tetiklenebilir.

## İstatistik paneli

`python -m app.web` botun veritabanını okuyan bir web paneli açar: kural bazında kazanma oranı ve teorik getiri, alarm listesi, maç başına oran grafiği, aktif takipler ve farklı eşiklerin geçmiş veriyle karşılaştırması. `.env` içinde `PANEL_PASSWORD` gerekir. Ayrıntı ve uzaktan erişim: [docs/PANEL.md](docs/PANEL.md).

## Sunucu (7/24)

Proxmox LXC veya herhangi bir Debian/Ubuntu sunucuda `systemd` servisi olarak kurulum: [docs/SUNUCU_KURULUMU.md](docs/SUNUCU_KURULUMU.md).

## Kurulum (Windows)

Python 3.11 veya üzeri kurulu olmalı. Proje klasöründe:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` içine `API_TENNIS_KEY` yazın. Gerçek Telegram gönderimi için `DRY_RUN=false` yapıp `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` doldurun. `.env` Git tarafından yok sayılır. Varsayılan eşikler ve tarama süreleri `.env.example` içinde bulunur. Canlı tarama en az 2, maç önü tarama en az 30 saniye olabilir.

## Çalıştırma

```powershell
python main.py
python main.py --once
python main.py --scan-prematch
python main.py --scan-live
python main.py --live-trigger 1.40
python main.py --test-telegram
python main.py --collect-results
python main.py --report --days 30
pytest
```

`--test-telegram`, `DRY_RUN=true` olsa da gerçek bir test mesajı yollar. `--once` birer maç önü ve canlı tarama yapar. Dry-run API sorgularını ve SQLite kayıtlarını çalıştırır, Telegram mesajını yalnızca loglar.

`--scan-live` salt okunur teşhis komutudur: watchlist boş olsa bile API'yi sorgular; HTTP durumu, `success` hatası, canlı event sayısı, kullanılabilir `To Win` sayısı, `event_key` eşleşmeleri ve en fazla beş örnek event gösterir. Eşleşme yoksa bu normal bir durum olarak raporlanır. Canlı yanıtta oyuncu adı yoksa mevcut oyuncu anahtarları gösterilir.

Veriler `data/tennis_alert.db`, dönen loglar `logs/tennis-alert.log` içinde tutulur. Uygulama yeniden başladığında SQLite watchlist ve gönderilmeyi bekleyen alarmları yükler. Başarısız Telegram gönderimleri en fazla beş kez artan aralıklarla tekrar denenir; beşinci denemeden sonra vazgeçilen alarm loga hata olarak yazılır. Her alarm gönderilmeden önce veritabanında rezerve edildiği için aynı anda iki süreç (ör. servis ve elle çalıştırılan `--once`) aynı alarmı iki kez göndermez. Telegram gönderimi canlı taramadan ayrı bir döngüde çalışır; Telegram yavaşlasa da oran taraması beklemez. SQLite ile Telegram arasında tam dağıtık işlem garantisi olmadığından, gönderim başarılı olup DB güncellemesi gerçekleşmeden süreç aniden kapanırsa nadiren tekrar bildirim olabilir.

API-Tennis resmi belgelerinde fixture alanları `event_first_player`, `event_second_player`, `event_status`, `event_live`; canlı odds ise `live_odds` satırlarında `odd_name`, `type`, `value`, `upd` olarak gösterilir. [API-Tennis dokümantasyonu](https://api-tennis.com/documentation). Canlı `To Win` Home/Away örneği erişilebilir belgede tam gösterilmediğinden, parser kullanıcı tarafından sağlanan gözleme dayanır. Gerçek hesapla ilk taramada bu eşlemeyi doğrulayın. Canlı oran güncelleme zamanı `upd` kaynak metni olarak saklanır.
