# Kokpit

Kendi cihazında çalışan kişisel kokpit: her sabah baktığın şeyleri tek bir küçük web sayfasında toplar, yalnız senin bağladığın verileri okur. Sabah kısa bir Telegram mesajı, seni bekleyen bir şey olup olmadığını söyler.

**Durum: M1 iskelet.** Bu depo ürün tasarımını, modül tanımlarını, gizlilik modelini ve yol haritasını, ayrıca çalışan bir iskeleti içerir (`./run`: başlangıç vault'u üzerinde Today, Events, Brain, Health ve Connections sekmeleri; PyPI paketi henüz yok). Tasarım, 2025'ten beri bir Raspberry Pi üzerinde her gün çalışan tek kullanıcılı bir kurulumdan çıkarıldı. Bugün ne var, ne genelleştirilecek, ne yalnız planda: [docs/status.md](docs/status.md).

İngilizce sürüm: [README.md](README.md)

![Kokpit demo: canlı havalı Today, Events, hızlı kayıtlı Brain, Health, Connections](docs/media/demo.gif)

Kayıt ve `docs/media/` altındaki ekran görüntüleri `docs/demo-vault/` içindeki sentetik örnek vault'tan alındı (sulama zamanlayıcısı, İtalyanca dersleri; gerçek veri yok); şehir olarak Lodi, İtalya seçildi. Beş M1 sekmesi ve canlı Open-Meteo havası görünür. Yeniden üretmek için: `docs/demo-vault/` klasörünü bir yere kopyala, `python -m kokpit init --vault <kopya> --db /tmp/demo.db --city "Lodi, Italy"`, `kokpit.toml` içinde `events = true` ve `expected = ["heartbeat", "brain-check", "weather", "events-collect"]`, dört işi çalıştır, sonra `./run`.

## Sıradan bir gün

Referans kurulum bugün böyle kullanılıyor. Aşağıdaki isimler ve sayılar örnektir.

- 07:30: tek satırlık Telegram mesajı gelir: "Senden beklenen 2 karar, gece 1 iş başarısız, hedeflerinle eşleşen 3 yeni ilan." Sorun yoksa mesaj bunu söyler ve orada biter.
- Paneli açarsın. Bugün sekmesinde takvim, bu hafta dolacak görevler, başkalarının senden beklediği iki karar ve şehrinle gidip geldiğin şehrin havası vardır.
- Beyin sekmesi her projenin nerede kaldığını "sende / bende / başkasında" diye ayırarak gösterir. Hızlı bir not yazarsın; inbox dosyasına bir satır eklenir, başka hiçbir şeye dokunulmaz.
- Bir AI ajanından (Hermes, Claude Code, Codex, Antigravity, hangisi olursa) bir işe devam etmesini istersin. Aynı not klasörünü okur, son oturum kartını ve açık işleri görür, bitince yeni bir oturum kartı ve makbuz bırakır. Sıradaki ajan ya da sen oradan devam edersin.
- Etkinlikler bu akşam opera binasında ücretsiz bir konseri ve cumartesi komşu kasabadaki sagrayı gösterir, her biri takvim dosyasıyla.
- Kariyer yeni ilanları her biri için yazılı gerekçeyle gösterir ("Python, SQL; Milano; uzaktan belirtilmemiş"), çıplak yüzde asla.
- AI kullanımı sekmesi ajanların bugün ne tükettiğini, sağlayıcı veriyorsa abonelik limitlerini gösterir; belirleyemediği fiyatı "bilinmiyor" diye işaretler.

## Ne işe yarar

Sabah tek sayfa açarsın ve görürsün:

- **Bugün**: takvim, açık görevler, yaklaşan tarihler, senden beklenen kararlar, önem verdiğin şehirlerin havası.
- **İkinci beyin**: kendi yerel Markdown notların (projeler, açık işler, "nerede kalmıştım"), hızlı not/görev ekleme. Notlar düz dosya kalır; Obsidian veya herhangi bir editörle açılır.
- **Kariyer**: CV'nden çıkarılan ve senin düzelttiğin beceri/deneyim/dil profili; hedef roller, konum ve uzaktan çalışma tercihlerin; bulunan ilanlar ve yalnız yüzde değil yazılı gerekçeyle eşleştirme.
- **Etkinlikler**: şehrinde ve çevresinde olanlar; kategori ve ücretsiz/ücretli filtresi, liste/takvim/harita görünümü, etkinlik başına takvim dosyası.
- **Sosyal medya**: kendi hesaplarının istatistikleri, içerik karşılaştırması ve takvimi, taslaklar ve hatırlatmalar. Yayın yalnız desteklenen platformlarda ve açık onayla.
- **AI kullanımı**: araç, model ve oturum bazında; sağlayıcı veriyorsa limit ve yenilenme zamanı; veri tazeliği. Gerçek API harcaması, abonelik bedeli ve liste fiyatı eşdeğeri ayrı tutulur.
- **Gündem**: takip ettiğin alanlarda günlük özet, istenirse kısa sesli özet. Aktif projelerinden biriyle ilgili haberler proje adı ve gerekçesiyle etiketlenir.
- **Sistem sağlığı**: bağlantı başına son başarılı güncelleme, başarısız işler, kopan bağlantılar. Sessizce boş dönen bir bağlantı "son güncelleme: 0 kayıt" diye görünür, sağlıklı diye değil.
- **Telegram botu** (isteğe bağlı): sabah satırı, hatırlatmalar ve yayın ya da harcama yapan her şey için onay düğmeleri.

Her şey modüldür. Bağlantılar ekranından istediğini açarsın; gerisi çalışmaz.

## Farkı ne

**Tek not klasörü, çok ajan.** İkinci beyin senin sahip olduğun düz bir Markdown klasörüdür. Birden fazla AI aracı aynı klasörde tek sözleşmeyle çalışabilir: salt okunur bir hook her ajana açık işleri ve son oturumu verir; oturum sonunda ajan `Last-Session.md` kartını yazar, `Threads.md` dosyasını günceller ve neyi değiştirdiğini söyleyen bir makbuz bırakır. Proje ortasında araç değiştirsen de ipi kaybetmezsin; her ajanın ne yaptığını denetleyebilirsin. Bu düzen [Avenox Beyin](https://github.com/avenoxai/avenoxbeyin) (MIT) üzerine kuruludur; biz değiştirilmiş yerel bir çatal kullanıyoruz. Sözleşmenin tamamı: [docs/brain-protocol.md](docs/brain-protocol.md).

**AI anahtarı olmadan çalışır.** Veri toplama düz koddur: zamanlanmış toplayıcılar bilinen kaynaklara gider (ilan siteleri, etkinlik sayfaları, RSS, resmi API'ler), ayrıştırır ve satır olarak kaydeder; toplamada model yoktur. Bunun üzerinde her modülün kural tabanlı bir yolu vardır: ilanlar için anahtar kelime puanı, etkinlik kategorileri için kelime kuralları, kural tabanlı kullanım tavsiyesi, şablon sabah raporu. Model; özet, çeviri ve daha zengin eşleştirme ekleyen isteğe bağlı bir bağlantıdır, modülün çalışması için şart değildir. Bu kural yolunun bugün hâlâ eksik olduğu yerler [docs/status.md](docs/status.md) içinde işaretlidir.

**Bilmediğini söyler.** Belirlenemeyen fiyat, verilmeyen kota ve başarısız ölçüm "bilinmiyor" olarak görünür. Boş sonuç gizlenmez. Özellikler çalışıyor / genelleştirilecek / planlanan diye etiketlenir.

**Arkada bir şey kalmaz.** Veri cihazındaki SQLite ve Markdown dosyalarındadır. Uygulamayı silsen de notların, dışa aktarımların ve veritabanın elinde kalır.

## İlkeler

1. **Senin cihazın, senin verin.** Kokpit yerelde çalışır (Raspberry Pi, ev sunucusu, dizüstü). Bize ait bir barındırılan servis yok.
2. **Her bağlantı görünür.** Açık her bağlantı için hangi veri okunuyor, hangi izin verildi, en son ne zaman başarılı oldu görünür.
3. **Analiz ve yayın ayrı izindir.** Instagram istatistiklerini okumak paylaşım hakkı vermez.
4. **Sessiz AI çağrısı yok.** Bir modül dış modele veri gönderecekse veri ve hedef önce belirtilir ve onay alınır. CV eşleştirmesi için ad, telefon, e-posta gerekmez.
5. **Bilinmeyen sıfır değildir.** Belirlenemeyen fiyat, sağlayıcının vermediği kota, başarısız ölçüm "bilinmiyor" olarak gösterilir, 0 olarak değil.
6. **Dürüst etiket.** Özellikler çalışıyor / genelleştirilecek / planlanan diye işaretlenir. Olmayan bir komut varmış gibi yazılmaz.
7. **Model şart değil.** Her modülün model olmadan çalışan kural tabanlı bir temel yolu vardır; model o modülü iyileştiren, diğer bağlantılar gibi listelenen ve onaylanan isteğe bağlı bir bağlantıdır. Hangi temel yolların bugün var olduğu [docs/status.md](docs/status.md) içinde izlenir.

## Ne gerekir, kimin için değil

İlk sürüm çıktığında hedeflenen gereksinimler:

- Sürekli açık bir cihaz (Raspberry Pi, ev sunucusu, eski dizüstü) ve Python 3.
- Bir Markdown not klasörü ya da uygulamayla gelen boş başlangıç yapısı.
- İsteğe bağlı: sabah satırı için Telegram bot token'ı; Instagram istatistikleri için Meta geliştirici uygulaması; özet ve çeviri için model API anahtarı.
- Bir yapılandırma dosyasını düzenlemeye gönüllü olmak; ilk sürüm buna alışık kişiler içindir.

Barındırılan servis, telefon uygulaması, cihazlar arası bulut eşitleme ya da ekip çalışma alanı istiyorsan bunun için değil. Herkes kendi kurulumunu çalıştırır; bunlar ilk sürümün kapsamı dışındadır.

## Şehrini ekle

Genel kaynaklar (AllEvents, Eventbrite, Open-Meteo) şehir adını girince her şehirde çalışır. Tiyatro, belediye, turizm ofisi gibi yerel kaynakların şehir parametresi yoktur; bunlar belgeli bir JSON sözleşmesiyle `adapters/events/<şehir>/` altında küçük adaptörler olarak yaşar. Yazarın Cremona adaptörleri varsayılan kapalı örnekler olarak gelecek. Birini kopyalayıp kendi kasabanın gündemine yöneltmek katkının amaçlanan yoludur; adım adım anlatım [docs/add-your-city.md](docs/add-your-city.md) içinde (İngilizce), kayıt sözleşmesi [adapters/README.md](adapters/README.md) içinde. Aynı sözleşme `adapters/jobs/` altındaki ilan kaynakları için de geçerlidir.

## Depo yapısı

README.md içindeki tablo ile aynıdır; dokümanlar İngilizce yazılmıştır.

## Dene (M1 iskelet)

Python 3.11 ve üstü yeterli, başka kurulum yok:

```
git clone https://github.com/sahindemirbas/kokpit
cd kokpit
./run                     # kokpit.toml + data/ altında başlangıç vault'u yazar, http://127.0.0.1:8501
```

Bugün çalışan: Today, Events, Brain, Health ve Connections sekmeleri; `weather` işinin doldurduğu hava kartı (Open-Meteo); `kokpit.toml` içinde `events` açılınca `events-collect` işinin doldurduğu şehir etkinlikleri (AllEvents, Eventbrite); işlerin (`heartbeat`, `brain-check`, `weather`, `events-collect`, `calendar`) doldurduğu sağlık tablosu. Today'deki takvim kartı `calendar_ics_urls` (ya da boşlukla ayrılmış `KOKPIT_CALENDAR_ICS_URLS` ortam değişkeni) ile bir veya daha fazla ICS adresine salt okunur bağlanır; `calendar` işi bugünü ve sonraki 6 günü önbelleğe alır. ICS adresini bir parola gibi sakla: yalnız yerel `kokpit.toml` dosyanda ya da ortam değişkeninde dursun. `kokpit.toml` içindeki `vault_path` değerini kendi Markdown klasörüne çevirince kendi thread ve görevlerin görünür; hızlı not `inbox/` veya `To-Do.md` dosyasına ekler, hiçbir notu yeniden yazmaz. Diğer komutlar: `python -m kokpit jobs`, `python -m kokpit run-job <ad>`. Testler: `python -m unittest discover -s tests`.

Yerel etkinlik adaptörleri, kariyer, sosyal, AI kullanımı, gündem ve bot iskelette henüz yok (`docs/roadmap.md`). `pip install kokpit` ve Docker planlı, mevcut değil.

## Lisans

MIT, bkz. [LICENSE](LICENSE). Paket yalnız Python standart kütüphanesine dayanır. İkinci beyin düzeni Avenox Beyin'den gelir (MIT, telif Avenox); bugün depoda Beyin'den kopyalanmış kod yok, ileride türetilen her dosya özgün bildirimi taşır. Ayrıntı: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Depo hâlâ private; lisans seçimi bunu değiştirmez.
