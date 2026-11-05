# Tenis oran takip sistemi — durum ve yol haritası

Son güncelleme: **5 Kasım 2026**

Bu dosya, bu görevde yapılan geliştirmeler, mevcut kaynak kod ve kullanıcının paylaştığı diğer sohbet üzerinden hazırlanmıştır. Tamamlanan uygulama özellikleri ile sonraki aşamalar aşağıda ayrı işaretlenmiştir. Yeni bir bilgisayarda veya yeni bir sohbette devam ederken önce bu dosya ve `README.md` okunmalıdır.

## 1. Amaç ve mevcut kurallar

Sistem tenis maçlarının oranlarını toplar, geçmişini SQLite'a kaydeder ve koşullar oluşunca Telegram alarmı gönderir. Otomatik bahis işlemi yoktur. Telegram bildirim kanalı olarak devam edecek; planlanan web paneli oran geçmişini ve istatistikleri gösterecek.

İki kural da **maç kazanma oranını** izler. Üçüncü set kuralında set kazananı marketi kullanılmaz.

| Kural | Referans oran | Alarm koşulu | Takip kapsamı |
| --- | --- | --- | --- |
| Prematch favorisi | Maç başlamadan önce görülen son geçerli bookmaker medyanı: 1.01–1.35, sınırlar dahil | Aynı tarafın canlı `To Win` oranı **≥ 2.00** | Prematch kriterini karşılayan maçlar |
| Üçüncü set başlangıcı | Üçüncü set başındaki ilk geçerli maç kazanma oranı: 1.30–1.60, sınırlar dahil | Aynı üçüncü sette aynı tarafın oranı **> 2.00** | Prematch listesinde olmayan maçlar da dahil |

Üçüncü set başlangıcı, canlı skor yanıtındaki `Set 3`, üçüncü set oyunlarının 0–0 olması ve mevcut oyunun puanlarının 0–0 olmasıyla doğrulanır. Başlangıç snapshot'ının provider zamanı varsayılan olarak en fazla 30 saniye eski olabilir. Set başlangıcı kaçırılmışsa maç sonradan bu kurala alınmaz. Veri veya polling gecikmesi nedeniyle her başlangıcı yakalamak garanti değildir.

Eşleştirme `event_key` ile yapılır. Oyuncu isimleri gösterim içindir. Home/Away tarafı ilgili kuralın başlangıcında sabitlenir. Prematch ve üçüncü set alarmı birbirinden bağımsızdır; üçüncü set takibi dördüncü sete geçildiği veya maçın bittiği/iptal edildiği anlaşıldığında kapanır.

## 2. Tamamlanan işler

### İzleme motoru

- [x] Python uygulaması, modüler klasör yapısı ve `python main.py` başlangıç komutu.
- [x] Merkezi `.env` konfigürasyonu, örnek ayarlar ve polling doğrulaması.
- [x] API-Tennis istemcisi: fixtures, prematch odds, live odds ve livescore.
- [x] Timeout, sınırlı retry/backoff ve HTTP 429 bekleme davranışı.
- [x] `Home/Away` prematch parser'ı ve geçerli bookmaker oranlarının medyanı.
- [x] Decimal tabanlı oran işlemleri ve sınır kontrolleri.
- [x] İlk ve son prematch referansını saklama; kriterden çıkma ve tekrar girme.
- [x] Toplu `get_live_odds` çağrısı ve `event_key` üzerinden eşleştirme.
- [x] Canlı `To Win` Home/Away parser'ı; askıdaki oranları atlama, aynı taraf için en yeni `upd` snapshot'ını seçme.
- [x] Prematch favorisinin doğru canlı tarafını izleme.
- [x] Üçüncü set başlangıcı kuralı, ayrı takip durumu ve geçmişi.

### Kayıt, bildirim ve teşhis

- [x] SQLite tablolarının otomatik oluşturulması ve foreign key kullanımı.
- [x] Değişen oranlar için zaman damgalı history; değişmeyen oranların tekrar yazılmaması.
- [x] Başlangıç/referans oranı, önceki canlı oran ve alarm oranını kaydetme.
- [x] Restart sonrasında takip ve bekleyen alarm durumunun korunması.
- [x] Kural/taraf bazında duplicate alarm engeli.
- [x] Önce alarmı kaydetme, ardından Telegram gönderimi ve sonucunu işleme.
- [x] Başarısız Telegram gönderimleri için en fazla beş deneme ve artan bekleme.
- [x] Eşzamanlı gönderim döngülerinin aynı alarmı iki kez göndermesini engelleyen kilit.
- [x] Türkçe Telegram mesajları; üçüncü set mesajında kural ve başlangıç oranı.
- [x] Dry-run, Telegram testi, tek tarama ve manuel scan komutları.
- [x] Ayrıntılı, salt okunur `--scan-live` teşhisi: event/market/watchlist/eşleşme sayıları, doğru tarafın oranı ve ilk beş örnek event.
- [x] HTTPX/HTTPCore INFO URL kayıtlarının kapatılması; güvenli method/status/süre logları.
- [x] Önceki log dosyasında saptanan API anahtarı izinin maskelenmesi.
- [x] Üçüncü set öncesi alarm şemasından otomatik geçiş; mevcut veriyi koruma ve yedek oluşturma.
- [x] Türkçe kurulum ve kullanım README'si.

### Doğrulama durumu

- En son kod değişikliğinde **33 test geçti**. Bu belge yazılırken testler yeniden çalıştırılmadı; bu sayı son geliştirme çalışmasının sonucudur.
- Testler parser, medyan, sınırlar, doğru taraf seçimi, duplicate alarm, restart, üçüncü set başlangıcının kaçırılması, askıdaki/eski oranlar, şema geçişi ve eşzamanlı bildirim gönderimini kapsıyor.
- Mevcut anahtarla gerçek live odds ve livescore yanıtları incelendi. Canlı odds yanıtında oyunların set içi skoru yok; livescore yanıtında `scores` alanı var.
- Gerçek `--scan-live` çağrısında HTTP 200, kullanılabilir `To Win` marketleri ve watchlist karşılaştırması doğrulandı. Örnek taramalardaki sayılar o anlık sonuçlardır, kalıcı istatistik değildir.
- Yeni üçüncü set akışı gerçek API ile dry-run olarak da çalıştırıldı. Pozitif üçüncü set alarmı otomatik testlerde doğrulandı; gerçek bir set başlangıcından Telegram alarmına kadar olan pozitif senaryonun saha doğrulaması henüz kaydedilmedi.
- Diğer sohbette iki gerçek alarmın çalıştığı aktarılıyor. Bu bilgi sohbetten alınmıştır; maçların kazandı/kaybetti sonuçları bu belge kapsamında doğrulanmadı.

## 3. Dosyalar ve veri yapısı

| Dosya | Görevi |
| --- | --- |
| `main.py`, `app/main.py` | Başlatma, komutlar ve log kurulumu |
| `app/config.py` | Ortam ayarları ve doğrulama |
| `app/api/tennis.py` | API-Tennis istemcisi ve güvenli istek logları |
| `app/core/parsers.py` | Prematch ve canlı response parser'ları |
| `app/core/rules.py` | Oran parse, medyan ve temel koşullar |
| `app/core/monitor.py` | Tarama döngüleri, teşhis ve bildirim kuyruğu |
| `app/core/third_set.py` | Üçüncü set başlangıç doğrulaması ve kuralı |
| `app/db/repository.py` | SQLite şeması, geçiş ve veri işlemleri |
| `app/notifications/telegram.py` | Telegram gönderimi ve mesaj biçimi |
| `tests/` | Otomatik testler ve örnek API fixture'ları |
| `.env.example`, `.gitignore`, `requirements.txt` | Örnek ayarlar, gizlenecek dosyalar ve bağımlılıklar |
| `README.md` | Kurulum ve kullanım |

Varsayılan veritabanı: `data/tennis_alert.db`. Gerçek yol `DATABASE_PATH` ile değişebilir.

Mevcut tablolar:

- `matches`
- `prematch_odds_history`
- `live_odds_history`
- `alerts`
- `third_set_sessions`
- `third_set_watches`
- `third_set_odds_history`

Log yolu: `logs/tennis-alert.log`. Secret değerleri bu belgeye, kaynak koda veya Git'e yazılmamalı.

## 4. Diğer sohbetten alınan hedefler

- Ev ve iş bilgisayarında aynı proje üzerinden devam edebilmek için private GitHub repository.
- Uzun vadede Proxmox/LXC üzerinde 7/24 çalışma.
- Telegram alarmına ek olarak canlı oran grafikleri ve istatistik paneli.
- Bugün hangi maçlara alarm verildiği, hangi takip edilen oyuncuların kazandığı/kaybettiği ve başarı yüzdesi.
- Günlük ve son 30 günlük sonuçlar; kural bazında karşılaştırma.
- API maliyetini düşürmek; tekrar tekrar trial hesabı açmaya dayanmayan sürdürülebilir kullanım.
- Gerekirse daha uygun fiyatlı sağlayıcıyı gerçek veriyle karşılaştırma.
- REST polling, cache ve mümkünse WebSocket seçeneklerini değerlendirme.

Diğer sohbette API-Tennis, BetsAPI, Odds Data API ve Live Tennis API fiyatları/paketleri tartışıldı. **Bu çalışmada güncel fiyatlar, paket kapsamı ve mevcut denemenin bitiş tarihi tekrar doğrulanmadı.** Sohbetteki “bir hafta kaldı” ifadesinden takvim tarihi çıkarılmamalı. 20–30 USD/ay bandı diğer sohbette önerilen bütçe hedefidir; kesin bütçe henüz belirlenmedi.

## 5. Sohbetteki önerilere ilişkin düzeltmeler

1. **Bookmaker medyanı kaldırılmadı.** Mevcut prematch kuralı hâlâ medyanı kullanıyor. Tek bookmaker veya farklı piyasa fiyatı sağlayan bir API'ye geçiş, aynı stratejinin birebir devamı olarak kabul edilmeden önce karşılaştırılmalı.
2. **Maç bazlı sorgu istek sayısını otomatik azaltmaz.** Mevcut sağlayıcı bütün canlı odds'u tek response ile döndürüyor. Aynı aralıkta 15 maça ayrı istek göndermek, bir toplu isteğe kıyasla 15 istek olabilir. Filtreli çağrı response boyutunu azaltabilir; kota/fiyat kazancı sağlayıcının ücretlendirme modeline bağlıdır.
3. **Yalnızca prematch favorilerini sorgulamak üçüncü set kuralını eksiltir.** Bu kuralın adayları prematch watchlist dışında da bulunabilir. Keşif ve izleme tasarımında iki kural birlikte ele alınmalı.
4. **Mevcut kodun varsayılan canlı aralığı 5 saniye.** Diğer sohbetteki 15 saniye örneği uygulanmış bir değişiklik değildir. Gerçek ayar `.env` ile farklı olabilir.
5. **Grafik için history mevcut, ama alarmdan sonra takip duruyor.** Prematch `alerted` ve üçüncü set `ALERTED` durumları ilgili kuralın history toplamasını da sonlandırıyor. Alarm sonrası maç sonuna kadar grafik isteniyorsa veri toplama durumu ile bildirim durumu ayrılmalı.
6. **Kazanan/kaybeden istatistiği henüz yok.** Maçın bitiş durumu işlense de kazanan oyuncuyu ve nihai skoru kalıcı kaydedip alarmlarla sonuçlandıran bir servis henüz yazılmadı.
7. Web paneli önceki ilk sürüm kapsamı dışındaydı; artık sonraki aşama hedefi olarak kaydedildi. Teknoloji seçimi kesinleşmedi.

## 6. Sonraki işler — önerilen sıra

### A. Projeyi farklı bilgisayarlarda sürdürülebilir hale getirme

- [ ] Yerel Git repository oluşturma. Bu belge hazırlanırken çalışma klasöründe `.git` bulunmuyor.
- [ ] `.env`, SQLite dosyaları/yedekleri, loglar ve `.venv` için ignore kontrolü; ilk commit öncesi secret taraması.
- [ ] Private GitHub repository oluşturma ve ilk push.
- [ ] Ev/iş bilgisayarı için clone, sanal ortam ve ayrı `.env` kurulumunu belgeleme.
- [ ] SQLite geçmişini Git'ten ayrı yedekleme; başka bilgisayara geçerken kod ile geçmiş veriyi ayrı taşıma.

Tamamlanma ölçütü: ikinci bilgisayarda kod alınabiliyor, bağımlılıklar kuruluyor, kendi yerel ayarlarıyla testler ve teşhis komutları çalışıyor.

### B. API süresi, maliyet ve kota kararı

- [ ] Hesaptan gerçek trial bitiş tarihini, gerekli canlı odds paketini ve kotayı doğrulama.
- [ ] Kabul edilebilir aylık bütçeyi belirleme.
- [ ] Mevcut gerçek günlük istek sayısını ölçme; prematch/live/livescore dağılımını ayırma. Şimdiki sayaç süreç içidir, kalıcı günlük `api_usage` tablosu yoktur.
- [ ] API-Tennis ile aday sağlayıcıların güncel fiyatlarını, kapsamını ve kullanım koşullarını resmi kaynaklardan doğrulama.
- [ ] Deneme erişimiyle aynı maçlarda prematch/live oranlarını, gecikmeyi, set başlangıcını ve sonuçları karşılaştırma.
- [ ] Her sağlayıcıda prematch ve live tarafının aynı maç kimliğini kullanabildiğini doğrulama. Sağlayıcılar arası ID'lerin aynı olduğunu varsaymama.
- [ ] Uygun aday seçilirse sağlayıcı adaptörü ekleme; ortak veri modellerini koruma.

Karşılaştırma başlıkları: ATP/WTA/Challenger/ITF ve ihtiyaç duyulan diğer maçların kapsamı, tam maç kazananı marketi, 3. set oyun/puan skoru, provider timestamp'i, askıdaki oranlar, maç sonucu ve toplu çağrı desteği.

Tamamlanma ölçütü: iki kuralı da destekleyen, test edilmiş ve bütçesi kabul edilmiş bir erişim planı. Henüz sağlayıcı değişimi yapılmadı; API-Tennis mevcut kaynak olarak devam ediyor.

### C. Polling ve canlı keşif optimizasyonu

- [ ] Toplu ve maç bazlı çağrıların gerçek kota/veri boyutu maliyetini karşılaştırma.
- [ ] Prematch için başlama saati, gecikmiş maçlar ve bitiş durumlarına göre gereksiz sorguları azaltma.
- [ ] Üçüncü set adaylarını watchlist dışından da keşfedebilecek yaklaşımı koruma.
- [ ] 5, 10 ve 15 saniyelik polling'in alarm gecikmesi ve 0–0 set başlangıcını yakalama oranına etkisini ölçme.
- [ ] Livescore çağrılarını cache, aday filtreleme veya sağlayıcının push desteğiyle azaltma.
- [ ] WebSocket'i ancak paket erişimi ve maliyet avantajı doğrulanırsa değerlendirme.

Tamamlanma ölçütü: daha az kaynak tüketimi; her iki kuralın adaylarını ve set başlangıcını kaçırma etkisi ölçülmüş.

### D. Sonuç toplama ve istatistik verisi

- [ ] Maç bitişi sonrası sonuç sorgulama; canlı listeden kaybolan event'leri de sonuçlandırma.
- [ ] Kazanan taraf/oyuncu anahtarı, nihai skor, bitiş zamanı ve sonuç kontrol zamanını SQLite'a yazma.
- [ ] Her alarmı takip edilen taraf üzerinden `WON`, `LOST`, `PENDING` veya `VOID` gibi açık bir sonuç durumuyla ilişkilendirme.
- [ ] Retirement, walkover, iptal ve ertelenme için nasıl raporlanacağını kararlaştırma; mevcut genel `CANCELLED` durumunu sonuç türleri için genişletme.
- [ ] Bildirim durumundan bağımsız history toplama; alarm sonrası maç sonuna kadar grafik verisi.
- [ ] Günlük sınırları `Europe/Istanbul` ile hesaplama; veritabanında UTC saklamayı sürdürme.
- [ ] Ayrı kural alarm sayısı ile benzersiz maç sayısını ayrı raporlama. Aynı maç iki kurala da alarm verebilir.

Başarı yüzdesi, sonuçlanmış alarmlar üzerinden `WON / (WON + LOST) × 100` olarak gösterilebilir. Bekleyen veya geçersiz sonuçlar paydaya dahil edilmemeli. Teorik ROI daha sonra, varsayımları açık bir simülasyon olarak değerlendirilecek; sistem gerçek bahis veya gerçekleşmiş getiri kaydetmiyor.

Tamamlanma ölçütü: bilinen örnek maçların sonuçları doğru tarafa bağlanıyor; günlük ve kural bazlı sayılar veritabanından yeniden üretilebiliyor.

### E. Web paneli

- [ ] İlk panelin kapsamını ve teknoloji seçimini netleştirme: FastAPI + basit HTML/JS/Chart.js veya FastAPI + Next.js.
- [ ] Botun topladığı SQLite verisini okuyan backend; panelin her yenilenmesinde dış odds API'sini sorgulamama.
- [ ] Aktif takipler: oyuncular, kural, referans oranı, güncel oran, alarm durumu ve veri güncelliği.
- [ ] Zaman–oran grafiği; eşik çizgisi ve alarm işareti. Prematch ve üçüncü set için referansları ayrı gösterme.
- [ ] Günlük/maç içi min–max oranları; mevcut eksik history ile tam maç min–max iddiası üretmeme.
- [ ] Bugün ve son 30 gün: alarm listesi, kazanan/kaybeden/bekleyen sayısı, başarı yüzdesi, ortalama alarm oranı.
- [ ] Maç sonucu, geçmiş alarmlar ve kural bazlı filtreler.
- [ ] Önce yerel ağda kullanım; uzaktan erişim istenirse Cloudflare Tunnel ve erişim kontrolü.

Tamamlanma ölçütü: panel güncel ve geçmiş veriyi grafikte gösteriyor, sonuçlarla tutarlı istatistik üretiyor; Telegram bildirim akışı devam ediyor.

### F. Proxmox/LXC üzerinde 7/24 işletim

- [ ] Sunucu/LXC ortamı ve kalıcı veri dizinlerini belirleme.
- [ ] Git üzerinden kurulum/güncelleme, ayrı secret dosyası ve bağımlılık kurulumu.
- [ ] Servis olarak başlatma, otomatik yeniden başlatma ve log yönetimi.
- [ ] Düzenli SQLite yedeği ve geri yükleme kontrolü.
- [ ] Son başarılı veri çağrısı, süreç durumu ve uzun süren veri kesintileri için health kontrolü.
- [ ] Panel eklendiğinde SQLite eşzamanlı okuma/yazma davranışını kontrol etme; ihtiyaç oluşursa veritabanı seçeneklerini değerlendirme.

Tamamlanma ölçütü: bilgisayar oturumuna bağlı olmadan servis çalışıyor, yeniden başlatma sonrası toparlanıyor ve veriler yedekten geri alınabiliyor.

## 7. Açık kararlar ve bilinen sınırlar

- Kesin aylık API bütçesi ve trial bitiş tarihi.
- Sağlayıcı seçiminde bookmaker medyanının korunup korunmayacağı.
- Panelin teknoloji seçimi, alan adı ve erişim kapsamı.
- Retirement/walkover gibi sonuçların istatistikte nasıl sınıflandırılacağı.
- Üçüncü set başlangıcındaki kaçırma oranı; gerçek pozitif alarm doğrulaması.
- Geçici API hatasında sonuç/takip durumunun tamamlanması ve canlı listeden kaybolan maçların kapanması.
- Telegram ile SQLite arasında tam atomik işlem yoktur: gönderim başarılı olup DB kaydı güncellenmeden süreç kapanırsa nadiren tekrar bildirim mümkündür.
- Retry sınırını tüketen alarmlar veritabanında kalır; yeniden deneme için bir yönetim akışı henüz yoktur.

## 8. Devam ederken kullanılacak komutlar

```powershell
python main.py                 # Sürekli izleme
python main.py --scan-prematch # Prematch kontrolü
python main.py --scan-live     # Salt okunur canlı teşhis
python main.py --once          # Bir prematch + bir live tarama
python main.py --test-telegram # Gerçek Telegram test mesajı
pytest                        # Otomatik testler
```

`.env` içinde `DRY_RUN=true` API/veritabanı akışını çalıştırır, gerçek alarm mesajlarını loglar. `--test-telegram` bu ayardan bağımsız gerçek test mesajı gönderir. Kurulum ayrıntıları `README.md` içindedir.

## 8.1. 5 Kasım 2026 inceleme düzeltmeleri

- Üçüncü set kuralı gerçek API ile hiç tetiklenemiyordu: `upd` İstanbul saati sanılıyordu ama sağlayıcı UTC+2 (Eylül) dönüyordu, her oran ~1 saat eski görünüyordu. Güncellik artık aynı yanıttaki en yeni `upd` ile ölçülüyor.
- Bir tarafı eski/askıda olan üçüncü set başlangıcı artık bekletiliyor; uygun taraf kalıcı olarak düşmüyor. Skor ve oran akışı uyuşmazsa da bekleniyor.
- Maç önü favori tarafı maç başlayana kadar her taramada yeniden hesaplanıyor.
- Akıştan düşen veya başlangıç saati çok geçmiş takipler `EXPIRED` olarak kapanıyor.
- Maç sonuçları `match_results` tablosuna yazılıyor; `--report` kazanma oranını ve teorik getiriyi gösteriyor.
- Alarm sonrası oran geçmişi toplanmaya devam ediyor.
- Telegram kuyruğu: bozuk bir alarm kuyruğu kilitlemiyor, iki süreç aynı alarmı göndermiyor, gönderim taramadan ayrı.
- API: 429'da en fazla 60 saniye tek bekleme, 401/403 tekrar denenmiyor. Durum döngüsü hatada durmuyor.
- Ayar hataları okunur mesaj veriyor; `LIVE_TRIGGER > PREMATCH_MAX` kontrol ediliyor. "Bugün alarm" İstanbul gününe göre.
- SQLite WAL modu ve 30 sn kilit bekleme (panel/CLI ile eşzamanlı okuma için).
- `deploy/` altında systemd servisi, günlük yedek zamanlayıcısı ve kurulum betiği; ayrıntı `docs/SUNUCU_KURULUMU.md`.
- Kod GitHub'a taşındı: `kangaetemo/uyari-sistemi`.

## 8.2. 5 Kasım 2026: WebSocket

- Paket sınırı günde 2.000.000 istek (kullanıcının aktardığı bilgi); canlı tarama 3 saniyeye indirildi.
- Canlı skor WebSocket'i eklendi: 3. set 0–0 başlangıcı anında yakalanıyor, `get_livescore` sorgusu kalktı, bağlantı koparsa otomatik geri dönüş var.
- WebSocket oran göndermediği için oranlar REST ile çekilmeye devam ediyor.

## 9. Çalışma kaydı

| Aşama | Durum |
| --- | --- |
| İlk Python/SQLite/API-Tennis/Telegram izleme sistemi | Uygulandı; ilk testlerde 5 test geçti |
| Canlı teşhis ve secret log düzeltmesi | Uygulandı; o aşamada 11 test geçti; gerçek API kontrolü yapıldı |
| Üçüncü set başlangıcı kuralı ve alarm şeması geçişi | Uygulandı; son test koşusunda 33 test geçti |
| Diğer sohbeti mevcut kodla karşılaştırma ve bu yol haritası | 4 Ekim 2026'da tamamlandı |
| İnceleme düzeltmeleri, sonuç kaydı/rapor, GitHub ve LXC kurulum dosyaları | 5 Kasım 2026'da uygulandı; 59 test geçti |
| Sağlayıcı araştırması ve web paneli | Planlandı; uygulamaya başlanmadı |

Bu belge güncellenirken tamamlanan işlerin kutuları, test sonuçları, yeni kararlar ve sıradaki adım birlikte güncellenmelidir. Sıradaki önerilen çalışma: **Git ile taşınabilirliği kurmak ve API deneme bitişi/maliyetini doğrulamak; ardından sonuç kaydı ve panelin veri temelini tamamlamak.**
