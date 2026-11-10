# Tenis canlı oran alarmı

Geliştirme geçmişi, mevcut durum ve sonraki işler: [Proje durumu ve yol haritası](PROJE_DURUMU_VE_YOL_HARITASI.md).

API-Tennis maç önü `Home/Away` oranlarının bookmaker medyanını izler. Maç öncesi son geçerli oranı varsayılan olarak 1.01–1.35 olan tarafın canlı `To Win` oranı 2.00 veya üzerine çıktığında bir kez Telegram bildirimi hazırlar. Bahis işlemi yapmaz.

Ek kural: üçüncü setin başında maç kazanma (`To Win`) oranı **1.30–1.60 dahil** olan taraf ayrıca izlenir. Aynı üçüncü sette oranı **2.00'nin üstüne** çıkarsa Telegram alarmı üretilir. 2.00 bu kuralı tetiklemez. Önceden prematch favorisi olması gerekmez. Her kural/taraf için ayrı bir alarm tutulur; prematch alarmı üçüncü set alarmını engellemez.

Başlangıç, skor yanıtındaki `Set 3` ve üçüncü setin **ilk oyununun henüz bitmemiş** olmasıyla (oyunlar 0–0) doğrulanır. İlk oyunun içindeki puan (15-0, 30-15 gibi) artık önemli değil: skor ve oran akışları birbirinden birkaç saniye geride kaldığı için puanın tam 0–0 olduğu an ile kullanılabilir oran neredeyse hiç aynı taramaya denk gelmiyordu ve bütün 3. setler "kaçırıldı" sayılıyordu. Başlangıç oranı askıya alınmamış olmalı ve en fazla `THIRD_SET_OPENING_MAX_AGE_SECONDS` (300) saniye eski olabilir: set arasında (120 sn) oran çoğu zaman hiç değişmez, bu yüzden eskiden kullanılan 30 saniyelik sınır başlangıcı her seferinde ilk sayıdan sonraya itiyordu. Oran akışının tamamı 30 saniyeden uzun süre ilerlemezse akış durmuş sayılır ve beklenir. İki taraftan biri askıda veya eskiyse karar bir sonraki taramaya bırakılır. İlk uygun başlangıç gözlemindeki oran sabitlenir; sonradan aralığa giren taraf eklenmez. Her kararın skoru ve oranları `third_set_sessions.detail` kolonuna yazılır. Program setin ortasında açılırsa veya başlangıç sırasında veri alınamazsa o maç için bu kural atlanır. API'nin gecikmesi ve polling aralığı nedeniyle her set başlangıcını yakalamak garanti değildir. Dördüncü sete geçen, biten veya iptal edilen maçta üçüncü set takibi sona erer.

`THIRD_SET_ENABLED=true` varsayılandır; `.env` değişikliği yapmadan yeni kural etkinleşir. `THIRD_SET_MIN`, `THIRD_SET_MAX`, `THIRD_SET_TRIGGER` ve `THIRD_SET_ODDS_MAX_AGE_SECONDS` ile ayarlanır. Bu kural açıkken prematch watchlist boş olsa da bütün canlı oranlar her canlı polling aralığında sorgulanır. Henüz başlangıç kararı verilmemiş üçüncü set event'leri varsa ayrıca **tek toplu** canlı skor çağrısı yapılır. `THIRD_SET_ENABLED=false` ile önceki polling davranışına dönülür.

Mevcut SQLite kayıtları korunarak alarm tablosu otomatik güncellenir. Eski şemadan ilk geçişte `data/*before-third-set-*.db` yedeği oluşturulur. Yeni başlangıçlar, takip geçmişi ve alarm durumları SQLite'da saklanır; yeniden başlatmak aynı alarmı tekrar oluşturmaz.

## Set için servis alarmı

Maç önü favori oranı `SERVE_FOR_SET_FAVORITE_MAX` (varsayılan 1.35) veya altında olan oyuncu herhangi bir sette **bir break geride** kalır ve rakip **set için servis eder** ise (favori açısından 3-5, 4-5 veya 5-6, servis rakipte) Telegram alarmı gider. Servis sırası dönüşümlü olduğundan önde olan oyuncu 5-3, 5-4 veya 6-5'te servis ediyorsa tam bir break öndedir; favori bu oyunu kırmazsa seti kaybeder. Her sette en fazla bir alarm üretilir; aynı maçın başka bir setinde aynı durum yeniden alarm verir. ITF dahil bütün maçlara uygulanır.

Skor, oyun ve o an servis atan oyuncu WebSocket'ten ve `get_livescore`'dan okunur. Sunucuda WebSocket her sayıda değil çok daha seyrek mesaj gönderdiği için (bütün maçlar için dakikada birkaç mesaj) alarm bir dakikaya kadar gecikebiliyordu. Bu yüzden canlı bir ağır favori (ya da sonucu henüz belli olmayan bir alarm) varken her taramada tek toplu `get_livescore` çağrısı yapılır ve iki kaynaktan hangisi maçta daha ilerideyse (set, oyun, puan) o kullanılır. `event_serve` bir oyun bittikten hemen sonra bir süre eski servisçiyi gösterebildiği için servis atan oyuncu `pointbypoint` listesindeki son oyunla doğrulanır; `pointbypoint` yoksa yeni oyunun ilk sayısı oynandıktan sonra karar verilir. Alarm mesajı set skorunu, oyun içi puanı, maç önü oranı ve favorinin o anki canlı `To Win` oranını içerir. Favorinin canlı oranı yoksa (askıda) bir sonraki taramada tekrar denenir. Maç önü oranı hiç yayınlanmayan maçlar için aşağıdaki canlı referans kuralına bakın.

Alarmdan sonra bot sonucu da kaydeder: favori o oyunu kırdı mı (**break yaptı** / **rakip servisini aldı**), seti aldı mı ve maçı kazandı mı. Bu alarmlar maç sonu istatistiklerinden ayrı tutulur: panelde ve `--report` çıktısında kendi bölümleri vardır (break oranı, set alma oranı, break sonrası set alma oranı, ayrıca aynı alarmların maç sonucu) ve maç sonu toplamına katılmaz. Alarm listesinde ve canlı alarmlarda bu kuralın sonucu "break yaptı / yapamadı" olarak gösterilir. `SERVE_FOR_SET_ENABLED=false` kuralı kapatır. İlk açılışta alarm tablosu set numarası için güncellenir ve önce `data/*before-serve-for-set-*.db` yedeği alınır.

## Maç önü oranı olmayan maçlar (canlı referans)

Sağlayıcı bazı maçlara hiç maç önü oranı girmez; örneğin yarı finaller bittikten sonra programa eklenen bir final. Böyle bir maç 1. sette canlıya düştüğünde ve en fazla bir oyun oynanmışken bot iki oyuncunun canlı `To Win` oranını maç önü oranı yerine kaydeder. Favori ve uygunluk normal maç önü kuralıyla belirlenir; maç sonu ve set için servis kuralları bu maça da uygulanır. Kaynak `matches.prematch_source = 'LIVE'` olarak işaretlenir, Telegram alarmında "maç önü oranı yoktu; 1. set başındaki canlı oran" notu, panelde "(canlı)" etiketi görünür. 1. sette iki oyun oynandıysa referans alınmaz. `LIVE_REFERENCE_ENABLED=false` bu davranışı kapatır.

## Canlı skor WebSocket'i

`WEBSOCKET_ENABLED=true` (varsayılan) iken bot API-Tennis'in canlı skor WebSocket'ine bağlanır. Gelen skorla üçüncü setin 0–0 başladığı an görülünce bot bir sonraki taramayı beklemeden o anın oranını çeker. Sunucuda WebSocket her sayıda mesaj göndermiyor ve bazı maçları hiç taşımıyor; bu yüzden kuralların taze skora ihtiyaç duyduğu maçlar (3. set adayı, canlı ağır favori, açık set kırma alarmı) için `get_livescore` de çağrılır ve hangisi daha ilerideyse o kullanılır. WebSocket oran göndermez, oranlar `get_live_odds` ile `LIVE_POLL_SECONDS` (3) saniyede bir çekilmeye devam eder. Bağlantı koparsa bot 1–60 saniye aralıklarla yeniden bağlanır ve bu sırada `get_livescore` ile çalışır. Bağlantıyı denemek için:

```powershell
python main.py --test-websocket
```

## Sonuçlar ve rapor

Takip edilen maçların sonucu (`get_fixtures` içindeki `event_winner`, yoksa set skoru) `match_results` tablosuna yazılır. Her alarm, takip edilen tarafa göre **Kazandı**, **Kaybetti**, **Bekliyor** veya **Geçersiz** (iptal/walkover) olarak sonuçlanır. Maçtan çekilmeyle biten sonuçlar raporda ayrıca belirtilir.

```powershell
python main.py --report --days 30
```

Rapor kural bazında kazanma oranını, ortalama alarm oranını, o orandaki başabaş kazanma oranını (1/oran) ve her alarma 1 birim yatırılmış gibi hesaplanan teorik getiriyi gösterir. Teorik getiri, alarm anındaki oranın gerçekten alınabildiğini varsayar. 100'den az sonuç olduğunda rapor bunu uyarı olarak belirtir.

Her bölümde ayrıca **"Rakibe oynasaydık"** satırı vardır: aynı alarmlarda 1 birim diğer oyuncuya, alarm anındaki rakip oranından yatırılsaydı teorik getiri, ROI, ortalama rakip oranı ve başabaş oranı. Bot her alarmda rakibin canlı oranını da kaydeder (`alerts.opponent_odds`, ilk açılışta kolon otomatik eklenir). Bu özellikten önceki alarmlarda veya rakip oranı askıdaysa oran, favorinin alarm oranından %5 bahis marjı varsayılarak tahmin edilir ve rapor kaç oranın tahmini olduğunu yazar. Set kırma alarmlarında da rakip bahsi maç sonucuna göre hesaplanır.

### Günlük teşhis

"Bugün neden alarm gelmedi?" sorusu için yalnızca veritabanını okuyan bir özet vardır (API çağrısı yapmaz, servis çalışırken de kullanılabilir):

```bash
cd /opt/uyari-sistemi && sudo -u tennis .venv/bin/python main.py --teshis          # bugün
cd /opt/uyari-sistemi && sudo -u tennis .venv/bin/python main.py --teshis --gun 2026-11-09
```

Çıktıda botun son canlı taramasının ne kadar önce olduğu, günün alarmları ve Telegram'a gidip gitmedikleri, maç önü uygun favoriler ve canlıda ulaştıkları en yüksek oran (eşiğe ne kadar yaklaştıkları), 3. set kararları (`CAPTURED`/`MISSED`, karar anındaki skor ve oranlar) ve set için servis alarm anları bulunur.

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
python main.py --test-websocket
python main.py --report --days 30
pytest
```

`--test-telegram`, `DRY_RUN=true` olsa da gerçek bir test mesajı yollar. `--once` birer maç önü ve canlı tarama yapar. Dry-run API sorgularını ve SQLite kayıtlarını çalıştırır, Telegram mesajını yalnızca loglar.

`--scan-live` salt okunur teşhis komutudur: watchlist boş olsa bile API'yi sorgular; HTTP durumu, `success` hatası, canlı event sayısı, kullanılabilir `To Win` sayısı, `event_key` eşleşmeleri ve en fazla beş örnek event gösterir. Eşleşme yoksa bu normal bir durum olarak raporlanır. Canlı yanıtta oyuncu adı yoksa mevcut oyuncu anahtarları gösterilir.

Veriler `data/tennis_alert.db`, dönen loglar `logs/tennis-alert.log` içinde tutulur. Uygulama yeniden başladığında SQLite watchlist ve gönderilmeyi bekleyen alarmları yükler. Başarısız Telegram gönderimleri en fazla beş kez artan aralıklarla tekrar denenir; beşinci denemeden sonra vazgeçilen alarm loga hata olarak yazılır. Her alarm gönderilmeden önce veritabanında rezerve edildiği için aynı anda iki süreç (ör. servis ve elle çalıştırılan `--once`) aynı alarmı iki kez göndermez. Telegram gönderimi canlı taramadan ayrı bir döngüde çalışır; Telegram yavaşlasa da oran taraması beklemez. SQLite ile Telegram arasında tam dağıtık işlem garantisi olmadığından, gönderim başarılı olup DB güncellemesi gerçekleşmeden süreç aniden kapanırsa nadiren tekrar bildirim olabilir.

API-Tennis resmi belgelerinde fixture alanları `event_first_player`, `event_second_player`, `event_status`, `event_live`; canlı odds ise `live_odds` satırlarında `odd_name`, `type`, `value`, `upd` olarak gösterilir. [API-Tennis dokümantasyonu](https://api-tennis.com/documentation). Canlı `To Win` Home/Away örneği erişilebilir belgede tam gösterilmediğinden, parser kullanıcı tarafından sağlanan gözleme dayanır. Gerçek hesapla ilk taramada bu eşlemeyi doğrulayın. Canlı oran güncelleme zamanı `upd` kaynak metni olarak saklanır.
