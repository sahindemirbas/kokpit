<p align="center">
  <img src="docs/media/logo.png" width="120" alt="Kokpit logosu">
</p>

<h1 align="center">Kokpit</h1>

<p align="center">
  Birden fazla AI ajanının tek beyin olarak paylaştığı, kendi cihazında çalışan kişisel panel.<br>
  Takvim, notlar, haberler, etkinlikler, iş ilanları ve AI kullanımı tek sayfada.
</p>

<p align="center">
  <a href="#hızlı-başlangıç">Hızlı başlangıç</a> ·
  <a href="#özellikler">Özellikler</a> ·
  <a href="#dokümanlar">Dokümanlar</a> ·
  <a href="docs/add-your-city.md">Şehrini ekle</a> ·
  <a href="CONTRIBUTING.md">Katkı</a> ·
  <a href="README.md">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/dependencies-stdlib%20only-2ea44f?style=flat-square" alt="Yalnız standart kütüphane">
  <img src="https://img.shields.io/badge/runs%20on-Raspberry%20Pi-C51A4A?style=flat-square&logo=raspberrypi&logoColor=white" alt="Raspberry Pi üzerinde çalışır">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT lisansı">
  <a href="https://github.com/sahindemirbas/kokpit/actions/workflows/tests.yml"><img src="https://github.com/sahindemirbas/kokpit/actions/workflows/tests.yml/badge.svg" alt="Testler"></a>
</p>

Farkı ne:

- Tek ortak beyin. Hermes, Claude Code, Codex ya da Antigravity aynı Markdown vault'unda çalışır; son oturumu ve açık işleri okur, bitince bir oturum kartı ve makbuz bırakır. Sonraki ajan, öncekinin kaldığı yerden devam eder.
- AI anahtarı gerekmez. Her modülün kural tabanlı bir yolu var; model isteğe bağlı. AI kullanım takibi dürüsttür: belirlenemeyen maliyet ya da kota "bilinmiyor" diye görünür, asla sıfır sayılmaz.
- Yalnız standart kütüphane. `dependencies = []`, derleme adımı yok; Raspberry Pi ya da herhangi küçük bir sunucuda çalışır.
- Varsayılan olarak gizli. Barındırılan servis yok, hesap yok; verin yerel SQLite ve düz Markdown dosyalarında kalır.

<p align="center">
  <img src="docs/media/demo.gif" alt="Sentetik veriyle Kokpit demosu: Today, Briefing, Events, Career ilanları ve pazar, Brain, AI usage, Social, Health, Connections, koyu temada Today" width="900">
</p>

Erken aşama, 1.0 öncesi: neyin çalışıp neyin henüz olmadığı [docs/status.md](docs/status.md) içinde.

## Özellikler

- Today: takvim, görevler, tarihler, hava durumu ve diğer modüllerden sayılar.
- Briefing: kendi kaynaklarından günlük özet, notlarındaki açık işlere göre sıralanır.
- Şehrindeki etkinlikler, yazılı gerekçeyle puanlanan iş ilanları ve arkalarındaki pazar.
- Brain: notların projeler ve açık işler olarak, kullandığın AI ajanlarıyla ortak.
- Hermes, Claude Code ve Codex için AI kullanımı, kendi yerel kayıtlarından okunur.
- Health ve Connections: her işin en son ne zaman çalıştığı ve her kaynağın ne okuduğu.
- İngilizce ve Türkçe ekranlar, ilk açılışta kurulum ekranı, istersen Docker.

| | | |
|---|---|---|
| <img src="docs/media/today.png" width="290" alt="Today sekmesi"> | <img src="docs/media/brain.png" width="290" alt="Brain sekmesi"> | <img src="docs/media/health.png" width="290" alt="Health sekmesi"> |
| Today | Brain | Health |

Tüm ekran görüntüleri sentetik demo verisi kullanır.

<details>
<summary>Her modül ne yapar</summary>

<br>

| Modül | Ne yapar |
|---|---|
| Today | Takvim (salt okunur ICS), açık görevler, yaklaşan tarihler, seni bekleyen kararlar, Open-Meteo'dan canlı hava, diğer modüllerden sayılar. |
| Briefing | Kendi RSS/Atom kaynaklarından ve anahtar kelimelerinden günlük özet. Notlarındaki açık bir işle eşleşen öğeler etiketlenir ve öne çıkar. Model özeti, sesli okuma ve iki sesli podcast isteğe bağlı; oynatıcı her ekranın altında kalır. |
| Events | AllEvents, Eventbrite ve yerel adaptörlerden şehrindeki etkinlikler, kendi dilinde kategori kurallarıyla. |
| Career | Üç yaprak. Jobs: yerel profil, yazılı gerekçeyle puanlanan ilanlar ("Python, SQL; uzaktan belirtilmemiş"), ilan yaşam döngüsü, CSV dışa aktarım. Market: son 8 haftanın ilan sayıları ve isteğe bağlı haftalık not. Skills: ilanların isteyip profilinde olmayanlar; her birini öğreniyorum, biliyorum ya da atla diye işaretlersin. Indeed ve Jooble isteğe bağlı kaynaklar (Playwright). |
| Brain | Markdown notların projeler ve açık işler olarak; yalnız ekleme yapan hızlı not ve bir notun soru listesini forma çeviren Meetings. |
| Social | Kendi Instagram hesabın resmi API üzerinden (gönderiler, reels, hikayeler, günlük anlık görüntüler), içerik takvimi ve senin onayını bekleyen paylaşım. Analiz ve paylaşımın ayrı anahtarları var, ikisi de varsayılan kapalı. |
| AI usage | Hermes, Claude Code ve Codex'in yerel kayıtlarını salt okunur okur (her okuyucu ayrı bir anahtar). Model ve saat bazında token, models.dev liste fiyatlarından yaklaşık maliyet, kendi girdiğin limitler, kanıtıyla kural tabanlı öneriler. |
| Organiser | Notların için düzenleme adımları öneren isteğe bağlı gece işi (kategori, bağlantı, yapılacak). Hiçbir notu değiştirmez, taşımaz, silmez. |
| Health ve Connections | Her işin son başarılı çalışması ve her veri kaynağını, ne okuduğunu, en son ne zaman çalıştığını gösteren tek ekran. |
| Telegram botu | Sabah satırı ve birkaç komut, varsayılan kapalı. |

Planlanan ([yol haritası](docs/roadmap.md)): `pip install kokpit`, `kokpit doctor` ve OAuth ile salt okunur Google takvimi (gizli ICS adresi Google Takvim için zaten çalışıyor).

</details>

## Neden Kokpit

Kokpit her sabah baktığın şeyleri kendi cihazında çalışan tek bir küçük web sayfasında toplar. Yalnız senin bağladığın verileri okur, her şeyi SQLite ve düz Markdown dosyalarında tutar.

- Tek not klasörü, çok AI ajanı. Her ajan son oturumu ve açık işleri okur, sonra neyi değiştirdiğini kaydeder. `kokpit brain agents init` Claude Code, Codex ve Antigravity kurulumunu tek adımda yapar. Bkz. [docs/brain-protocol.md](docs/brain-protocol.md).
- Model şart değil. Veri toplama düz koddur ve her modülün kural tabanlı bir yolu vardır. Model, özet ve çeviri için isteğe bağlı bir bağlantıdır: anahtar ortam değişkeninde ya da çağrılar makinedeki Hermes ajanı üzerinden (`[model] provider = "hermes"`).
- Bilinmeyen sıfır değildir. Belirlenemeyen fiyat, kota ya da ölçüm "bilinmiyor" diye görünür, asla 0 diye değil. Sessizce boş dönen kaynak "OK" değil "0 kayıt" görünür.
- Senin cihazın, senin verin. Sen açana kadar her şey kapalı. Uygulamayı silsen de notların ve veritabanın yerinde kalır.

## Nasıl çalışır

- Yalnız standart kütüphane: sunucu `http.server`, depolama `sqlite3`, ayarlar `tomllib`.
- İşler tazeliğini kendisi kaydeder. Her çalışma durumunu ve kayıt sayısını saklar; bu her kartta ve Health'te görünür.
- Kural tabanlı puanlama. Kariyer ilanları ve Briefing öğeleri, okuyup tartışabileceğin bir gerekçeyle puan alır.
- Gizlilik. Oturum kimlikleri yalnız kısa bir sha256 öneki olarak saklanır; mesaj metni veritabanına okunmaz. Daha fazlası: [docs/privacy.md](docs/privacy.md).
- Testler CI'da. GitHub Actions birim testlerini Python 3.11, 3.12 ve 3.13 ile çalıştırır.

Daha fazlası için [docs/architecture.md](docs/architecture.md) (İngilizce).

## Hızlı başlangıç

Python 3.11 ve üstü. Başka kurulum yok.

```bash
git clone https://github.com/sahindemirbas/kokpit
cd kokpit
./run        # kokpit.toml ve başlangıç vault'unu oluşturur, http://127.0.0.1:8501
```

Tarayıcıda kurulum ekranı açılır. Yedi kısa soruyu cevapla (dil, ad, şehir, haber konuları, özgeçmiş, model, özet) ya da istediğini atla, sonra Bitir'e bas. Sekme açılmazsa terminalde yazan adresi kullan; ekransız bir makinede sekmeyi atlamak için `KOKPIT_NO_BROWSER=1` ayarla. Tek bir çalıştırma için başka port: `./run --port 8622`; kalıcı olsun istersen `kokpit.toml` içinde `[server]` altındaki `port` değerini değiştir. Her cevap sonra Bağlantılar > Kurulum'dan ya da `kokpit.toml` içinden değiştirilebilir. Girdiğin anahtarlar `kokpit.toml`'a değil, yanındaki `.env` dosyasına yazılır.

Ekranlar İngilizce ya da Türkçe; seçtiğin dil her yere uygulanır, yeni bir dil tek bir dosyadır (bkz. [docs/modules.md](docs/modules.md#languages)). Aynı soruları terminalde cevaplamak için ilk `./run`'dan önce `python -m kokpit init` çalıştır. Aşağıdaki komutlar `python` kullanır; yalnızca `python3` olan sistemlerde (Debian, Raspberry Pi OS) `python3` yaz.

### Docker

```bash
docker compose up -d      # panel http://127.0.0.1:8501 adresinde, açık modüller için bir zamanlayıcıyla birlikte
```

Veri `kokpit-data` volume'unda durur. Kokpit'te giriş yok; bu yüzden port yalnızca loopback'e bağlı. Telegram botu, LAN erişimi, yedek: [docs/docker.md](docs/docker.md).

<details>
<summary><b>Diğer komutlar ve ayarlar</b></summary>

<br>

| Komut | Ne yapar |
|---|---|
| `python -m kokpit place "Ghent"` | Open-Meteo geocoder'dan adayları listeler; birini kaydetmek için `--pick 1 --write` ekle |
| `python -m kokpit jobs` | işleri ve açık olup olmadıklarını listeler |
| `python -m kokpit run-job <ad>` | bir işi hemen çalıştırır (`weather`, `calendar`, `events-collect`, `career`, ...) |
| `python -m kokpit schedule` | açık işleri zamanında çalıştıran ön plan döngüsü |
| `python -m kokpit adapters` | yerel etkinlik adaptörlerini ve açık olup olmadıklarını listeler |
| `python -m kokpit career --help` | kariyer profili, özgeçmiş içe aktarma ve ilanlar |
| `python -m kokpit ai-usage summary` | bugün, 7 ve 30 gün için AI kullanım toplamları |
| `python -m kokpit context` | bir ajan hook'una verilecek beyin bağlamını yazdırır |
| `python -m kokpit bot send-test` | ayarlı Telegram sohbetine bir deneme mesajı gönderir |
| `python -m kokpit hermes status` / `hermes setup` | Hermes'e neyin bağlı olduğunu ve her eksiğin çözümünü gösterir / adım adım bağlar (her adımda y/N sorar, `--dry-run`) |
| `python -m kokpit instagram setup` / `instagram check` | Instagram'ı bağlar: bağlantılarıyla Meta adımları, gizli token sorusu, tek salt okunur kontrol / kayıtlı token'ı kontrol eder |
| `python -m unittest discover -s tests` | testleri çalıştırır |

- Notlar: `kokpit.toml` içindeki `vault_path` değerini kendi Markdown klasörüne çevir.
- Takvim: `calendar_ics_urls` (ya da `KOKPIT_CALENDAR_ICS_URLS`) içine ICS adresleri ekle. ICS adresini parola gibi sakla.
- İsteğe bağlı modüller: `kokpit.toml` içinde (`events`, `briefing`, `bot` ve diğerleri) ya da Connections ekranından aç. Etkinlik sayfası slug'ları ancak sitede kontrol ettikten sonra `--confirm-slug` ile yazılır.
- Demo verisi: GIF ve ekran görüntüleri, [docs/demo-vault/](docs/demo-vault/) içindeki sentetik vault, Ghent, Belçika şehri ve diğer sekmeler için üretilmiş örnek satırlarla çekildi:
  `python -m kokpit init --vault <kopya> --db /tmp/demo.db --city "Ghent, Belgium" --name Sam --language en --yes`

</details>

### Hermes'i bağla

`python -m kokpit hermes setup` çalıştır. Model çağrılarını Hermes üzerinden gönderir (Kokpit'te API anahtarı yok), Hermes kullanım okuyucusunu açar ve beyin eklentisini bağlar; her adımdan önce sorar. `python -m kokpit hermes status` eksik kalanı ve nasıl düzeltileceğini gösterir. Ayrıntı (İngilizce): [docs/connections.md#hermes-off-by-default](docs/connections.md#hermes-off-by-default).

### Instagram'ı bağla

`python -m kokpit instagram setup` çalıştır ya da Sosyal sekmesini aç. İkisi de Meta uygulama adımlarını bağlantılarıyla gösterir, token'ı alır, tek bir salt okunur çağrıyla dener ve analizi açar. Ayrıntı ve sorun giderme tablosu (İngilizce): [docs/connections.md#instagram-analysis-off-by-default](docs/connections.md#instagram-analysis-off-by-default).

## Şehrini ekle

Genel kaynaklar şehir adını yazınca her şehirde çalışır. Tiyatro, belediye, turizm ofisi gibi yerel kaynaklar `adapters/events/<şehir>/` altında küçük adaptörlerdir; şablonu kopyalayıp kendi şehrine yöneltmek katkı vermenin en kolay yolu. Etkinlik kategorileri JSON anahtar kelime paketiyle kendi dilinde eşleşebilir.

Rehberler (İngilizce): [add-your-city.md](docs/add-your-city.md) · [adaptör sözleşmesi](adapters/README.md) · [category-packs.md](docs/category-packs.md)

## Dokümanlar

Dokümanlar İngilizce yazılmıştır.

| | |
|---|---|
| [Architecture](docs/architecture.md) | çalışma yapısı, veri akışı, depolama |
| [Modules](docs/modules.md) | her modül: amaç, girdiler, durum |
| [Brain protocol](docs/brain-protocol.md) | birden fazla AI ajanının paylaştığı tek vault |
| [Connections](docs/connections.md) | izinler ve gizli bilgilerin korunması |
| [Privacy](docs/privacy.md) | cihazından asla çıkmayanlar |
| [Docker](docs/docker.md) | konteyner kurulumu, bot, yedek |
| [Status](docs/status.md) · [Roadmap](docs/roadmap.md) | ne var, sırada ne var |
| [Hermes entegrasyonu](integrations/hermes/README.md) | isteğe bağlı, şart değil |
| [Contributing](CONTRIBUTING.md) | yerelde çalıştırma, testler, şehir ekleme, pull request |

## Kimin için

Sürekli açık bir cihazı olan ve ara sıra bir yapılandırma dosyasını düzenlemekten çekinmeyenler için. Barındırılan servis, telefon uygulaması ya da ekip çalışma alanı değil; herkes kendi kurulumunu çalıştırır.

Kokpit, yazarı tarafından yapılan ve kullanılan kişisel bir projedir. Issue ve pull request'ler memnuniyetle karşılanır (bkz. [CONTRIBUTING.md](CONTRIBUTING.md)), ancak destek ya da yanıt süresi garantisi yoktur. 1.0 öncesinde uyumsuz değişiklikler olabilir.

## Lisans ve teşekkür

MIT, bkz. [LICENSE](LICENSE). Paket yalnız Python standart kütüphanesini kullanır.

İkinci beyin düzeni [Avenox Beyin](https://github.com/avenoxai/avenoxbeyin) (MIT, © Avenox) üzerine kurulu; burada değiştirilmiş yerel bir çatal olarak çalışıyor. Depoya Beyin kodu kopyalanmadı; ayrıntı [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) ve [docs/brain-protocol.md](docs/brain-protocol.md) §0 içinde.

[Sahin Demirbas](https://github.com/sahindemirbas) tarafından, İtalya'da yaşayan bir veri profesyonelinin önce kendi sabahları için yapıldı.
