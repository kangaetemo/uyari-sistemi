# İstatistik paneli

Panel, botun SQLite veritabanını salt okunur açan küçük bir web sitesidir. Oran API'sini hiç çağırmaz; yalnızca botun kaydettiğini gösterir. Bot ile panel ayrı servislerdir; panel kapansa bile bot çalışmaya devam eder.

## Sayfalar

| Sayfa | Ne gösterir |
| --- | --- |
| Genel bakış | Kural bazında kazanma oranı, başabaş oranı (1 / ortalama alarm oranı), teorik getiri; son 30 günün günlük alarmları; maç türüne (ATP, WTA, Challenger, ITF) göre kırılım; set için servis alarmlarında favorinin o oyunu kırma ve seti alma oranı |
| Canlı alarmlar | Son 6/12/24/48 saatte alarm verilen maçlar: set skorları, oyun puanı, servis, takip edilen tarafın alarm oranı ve güncel oranı, kazandı/kaybetti. 5 saniyede bir kendini yeniler |
| Alarmlar | Tüm alarmlar; kurala, döneme ve sonuca göre filtre |
| Maç sayfası | Maç önü oran geçmişi, canlı oran grafiği, eşik ve referans çizgisi, alarm noktası, sonuç |
| Aktif takip | Şu an izlenen maçlar ve 3. set takipleri, son canlı oranlar |
| Eşik analizi | Kayıtlı geçmişle "eşik 1.80 / 2.20 / 3.00 olsaydı" sonuçları; referans oran aralığına göre filtre |

Üst satırdaki "Bot: çalışıyor" bilgisi botun son canlı taramasından gelir. 5 dakikadan uzun süre tarama yoksa kırmızı görünür. `/health` adresi giriş istemeden aynı bilgiyi JSON olarak verir; dış bir izleme servisi bağlanabilir.

## Canlı alarmlar ve API kullanımı

Canlı alarmlar sekmesi ek API çağrısı yapmaz. Bot, zaten açık tuttuğu canlı skor WebSocket'inden gelen skoru yalnızca son 24 saatte alarm verilen maçlar için `live_scores` tablosuna yazar; güncel oran, botun 3 saniyede bir çektiği canlı oran sorgusundan gelir. WebSocket koptuğunda skor oran akışındaki durumdan (set sayısı ve oyun puanı) gösterilir, set skorları bağlantı gelince geri gelir.

Maç bittiğinde WebSocket'in son skoru kazanan tarafı hemen gösterir ("ön sonuç"). Resmi sonuç, istatistiklere giren kayıt olarak, botun düzenli sonuç taramasında `match_results` tablosuna yazılır.

## Ayarlar

`.env` içinde:

```
PANEL_USER=admin
PANEL_PASSWORD=uzun-bir-sifre
PANEL_HOST=127.0.0.1
PANEL_PORT=8080
```

Sunucuda `bash deploy/install.sh` çalıştırınca `PANEL_PASSWORD` doluysa `tennis-panel` servisi de kurulur. Yerelde denemek için `python -m app.web` yeterlidir, sonra tarayıcıda `http://127.0.0.1:8080`.

## Uzaktan erişim

Varsayılan ayar paneli yalnızca sunucunun kendisine açar. Üç yol var, en güvenliden başlayarak:

1. **SSH tüneli (önerilen, ek kurulum yok).** Kendi bilgisayarınızda:
   ```bash
   ssh -L 8080:127.0.0.1:8080 kullanici@SUNUCU_IP
   ```
   Bağlantı açıkken tarayıcıda `http://localhost:8080`. Trafik SSH ile şifrelenir.
2. **Alan adı + HTTPS (telefondan rahat erişim).** Bir alan adını sunucuya yönlendirin, Caddy kurun ve `/etc/caddy/Caddyfile` içine:
   ```
   panel.alanadiniz.com {
       reverse_proxy 127.0.0.1:8080
   }
   ```
   `ufw allow 80,443/tcp` ile portları açın. Caddy sertifikayı kendisi alır; `PANEL_HOST` 127.0.0.1 kalır.
3. **Doğrudan port (en basit, şifrelenmez).** `PANEL_HOST=0.0.0.0` yapıp `ufw allow 8080/tcp`. Şifre ağda açık metin gibi taşınır; yalnızca geçici deneme için kullanın.

## Sayıları okurken

- Kazanma oranı yalnızca sonucu belli alarmlardan hesaplanır. İptal ve walkover "geçersiz" sayılır, paydaya girmez. Maçtan çekilmeyle biten sonuçlar ayrıca işaretlenir.
- Teorik getiri, alarm anındaki oranın gerçekten alınabildiğini varsayar. Gecikme, askıya alınan oranlar ve bahis sitesi farkları bunu düşürür.
- 100'den az sonuçta oranlar rastlantıya çok açıktır; panel bunu kartlarda uyarı olarak gösterir.
- Eşik analizi yalnızca fiyat değiştiğinde kaydedilen geçmişi kullanır. Bu sürümden önce toplanan maçlarda alarmdan sonraki oranlar kayıtlı değildir.
