# Kokpit

Kendi cihazında çalışan kişisel kokpit: her sabah baktığın şeyleri tek bir küçük web sayfasında toplar, yalnız senin bağladığın verileri okur.

**Durum: tasarım ve dokümantasyon aşaması.** Bu depo şu an ürün tasarımını, modül tanımlarını, gizlilik modelini ve yol haritasını içerir; kurulabilir bir uygulama henüz yok. Tasarım, 2025'ten beri bir Raspberry Pi üzerinde her gün çalışan tek kullanıcılı bir kurulumdan çıkarıldı. Bugün ne var, ne genelleştirilecek, ne yalnız planda: [docs/status.md](docs/status.md).

İngilizce sürüm: [README.md](README.md)

## Ne işe yarar

Sabah tek sayfa açarsın ve görürsün:

- **Bugün**: takvim, açık görevler, yaklaşan tarihler, senden beklenen kararlar, önem verdiğin şehirlerin havası.
- **İkinci beyin**: kendi yerel Markdown notların (projeler, açık işler, "nerede kalmıştım"), hızlı not/görev ekleme. Notlar düz dosya kalır; Obsidian veya herhangi bir editörle açılır. Aynı kasa birden fazla AI ajanı (Hermes, Claude Code, Codex, Antigravity gibi) tarafından ortak kullanılabilir; gece çalışan düzenleyici notları dosyalar ve bağlar, gündem modülü aktif projelerinle eşleşen haberleri etiketler. Sözleşme: [docs/brain-protocol.md](docs/brain-protocol.md).
- **Kariyer**: CV'nden çıkarılan ve senin düzelttiğin beceri/deneyim/dil profili; hedef roller, konum ve uzaktan çalışma tercihlerin; bulunan ilanlar ve yalnız yüzde değil yazılı gerekçeyle eşleştirme.
- **Etkinlikler**: şehrinde ve çevresinde olanlar; kategori ve ücretsiz/ücretli filtresi, liste/takvim/harita görünümü, etkinlik başına takvim dosyası.
- **Sosyal medya**: kendi hesaplarının istatistikleri, içerik karşılaştırması ve takvimi, taslaklar ve hatırlatmalar. Yayın yalnız desteklenen platformlarda ve açık onayla.
- **AI kullanımı**: araç, model ve oturum bazında; sağlayıcı veriyorsa limit ve yenilenme zamanı; veri tazeliği. Gerçek API harcaması, abonelik bedeli ve liste fiyatı eşdeğeri ayrı tutulur.
- **Gündem**: takip ettiğin alanlarda günlük özet, istenirse kısa sesli özet.
- **Sistem sağlığı**: bağlantı başına son başarılı güncelleme, başarısız işler, kopan bağlantılar.

Her şey modüldür. Bağlantılar ekranından istediğini açarsın; gerisi çalışmaz.

## İlkeler

1. **Senin cihazın, senin verin.** Kokpit yerelde çalışır (Raspberry Pi, ev sunucusu, dizüstü). Bize ait bir barındırılan servis yok.
2. **Her bağlantı görünür.** Açık her bağlantı için hangi veri okunuyor, hangi izin verildi, en son ne zaman başarılı oldu görünür.
3. **Analiz ve yayın ayrı izindir.** Instagram istatistiklerini okumak paylaşım hakkı vermez.
4. **Sessiz AI çağrısı yok.** Bir modül dış modele veri gönderecekse veri ve hedef önce belirtilir ve onay alınır. CV eşleştirmesi için ad, telefon, e-posta gerekmez.
5. **Bilinmeyen sıfır değildir.** Belirlenemeyen fiyat, sağlayıcının vermediği kota, başarısız ölçüm "bilinmiyor" olarak gösterilir, 0 olarak değil.
6. **Dürüst etiket.** Özellikler çalışıyor / genelleştirilecek / planlanan diye işaretlenir. Olmayan bir komut varmış gibi yazılmaz.

## Depo yapısı

README.md içindeki tablo ile aynıdır; dokümanlar İngilizce yazılmıştır.

## Planlanan kurulum

Henüz yok. Hedef:

```
pip install kokpit        # veya: docker compose up
kokpit init               # şehir, CV yolu, hedef roller, açılacak modüller
kokpit run                # yerel panel http://localhost:8501
```

Bu komutlar tasarım hedefidir, mevcut özellik değil.

## Lisans

Henüz kararlaştırılmadı. Üçüncü taraf kod ve veri kaynağı koşulları incelendikten sonra seçilecek; o zamana kadar depo private ve tüm haklar saklı.
