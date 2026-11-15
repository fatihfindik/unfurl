# M3b — CV PDF · Teknik plan (Design fazı)

## Context
M3a CV belgelerini, editörü ve classic şablonla canlı HTML önizlemeyi getirdi (`v0.3a-m3a`). M3b (issue #28, SPEC v2.1.7 §12 › M3b, §6.5, §3, D-014, D-042, D-044) CV'yi PDF olarak indirilebilir yapar: 3 şablon (classic, modern, compact) × 3 dil (DE, EN, TR), CV'de isteğe bağlı fotoğraf ve doğum tarihi, editörde "Download PDF". #23 (PDF'te linkler) ve M3a security review notları (#28 yorumu) burada kapanır.

Branch: `m3b-cv-pdf` (D-042; `main`, `6d9f0c6`). Etiket `v0.3b-m3b`. Onaydan sonra plan issue #28'e yorum olarak eklenir.

Çalışma kuralı: her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push ve PR da onayla.

### Ön koşul (Fatih, bir kez)
WeasyPrint, Pango adlı bir sistem kütüphanesi ister; Mac'te kurulu değil. Build başlamadan önce terminalde:
```
brew install pango
```
CI'da (`ubuntu-latest`) ve Docker'da (Debian bookworm) paketler workflow'a ve Dockerfile'a yazılır.

### Fatih'in kararları (2026-11-15, plan-reviewer sonrası)
1. **Belge dili** oluştururken seçilir, editörde değişmez (her dilde tek master). Şablon editörde değişir.
2. **Şablonlar:** classic (M3a), modern (sans-serif başlıklar, ince indigo çizgi ve başlık rengi), compact (küçük yazı ve aralıklar, tek sayfaya odaklı). Hepsi tek sütun, tablo ve ikon yok (ATS). Ayrıntılar ilk PDF'lerden sonra birlikte ayarlanır.
3. **Fotoğraf ve doğum tarihi CV'de varsayılan kapalı.**
4. **Uyruk ayrı kutu:** "Show nationality".
5. **Fotoğraf CV'ye sadece o CV'de "Show photo" açılınca kopyalanır**; profil fotoğrafı silinince taslaklar kendi kopyasını Refresh'e kadar, kilitli CV her zaman korur (D-052).

### Yeni kararlar (Adım 0'da `docs/DECISIONS.md`'ye)
- **D-051 · WeasyPrint ve sistem kütüphaneleri:** `uv add weasyprint` (sürüm `uv.lock`'ta). Dockerfile runtime aşamasına `apt-get install --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`, sonra `/var/lib/apt/lists` silinir (D-014'ün uygulanması); CI'da `apt-get update` ve aynı paketler. Lokal: `brew install pango` (README). PDF testleri Pango yoksa başarısız olur, atlanmaz (sessizce atlanan test güvence vermez).
- **D-052 · CV fotoğrafı belgenin içinde saklanır:** Fotoğraf CV'ye sadece kullanıcı o CV'de "Show photo"yu açtığında kopyalanır (veri minimizasyonu, SPEC §10.2): profil fotoğrafı 400 px'e küçültülür, saydamlık beyazla doldurulur ve her zaman JPEG, kalite 85 olarak base64 halinde `CVContent.personal.photo` alanına yazılır (~20–50 KB; test < 80 KB). Kutu kapatılınca fotoğraf içerikten silinir; "Refresh from profile" kutu açıksa güncel fotoğrafı alır. Kilitli CV gönderildiği fotoğrafı korur, kopyalar fotoğrafı içerikle taşır, yetim dosya olmaz, hesap silinince satırla gider. Profil fotoğrafı silinince taslaklar kendi kopyasını Refresh'e kadar korur. Sınırlar şemada: `photo` base64 deseni ve en fazla 200 KB metin, `photo_type: Literal["image/jpeg"]`; uymayan içerik reddedilir (test). Önizlemede `GET /cvs/{id}/photo` (sadece sahibi, `data:` URI yok, M13 CSP'si), PDF'te `data:` URI (D-059'un izin verdiği kaynak). Alternatifler: CV başına dosya kopyası; profil dosyasına referans (kilitli CV'nin fotoğrafı değişir); her snapshot'ta kopyalamak (gösterilmeyen fotoğrafı da saklar).
- **D-053 · Doğum tarihi ve uyruk snapshot'ta, gösterim kutularla:** `CVContent.personal.date_of_birth`, `nationality` snapshot ve Refresh'te kopyalanır (profil zaten saklıyor); CV'de sadece "Show date of birth" ve ayrı "Show nationality" kutusu açıksa görünür, ikisi de varsayılan kapalı. Uyruk kutusu için `CVDocument.show_nationality` kolonu (küçük migration `0006`). AI'a giden içerikten (M7+) fotoğraf, doğum tarihi ve uyruk çıkarılır (SPEC §9.1, §10.2); `CVContent.without_private()` şimdiden eklenir ve test edilir.
- **D-054 · Tek CV gövdesi, şablon başına CSS (D-044'ü günceller):** `pdf/_cv_body.html` CV'nin yapısını bir kez çizer; `pdf/cv_classic.html`, `cv_modern.html`, `cv_compact.html` (SPEC §4 ağacı) sadece şablon sınıfını verip gövdeyi çağırır. Stil `static/css/cv_classic.css`, `cv_modern.css`, `cv_compact.css` (`.cv-paper` altında). PDF için ek `cv_print.css`: `@page` A4 ve kenar boşlukları, gölge/min-height kapalı, `@font-face` ile `static/fonts` (Inter, Source Serif 4; latin ve latin-ext `unicode-range` ile). `cv-entry-head` (başlık + tarih aynı satırda) WeasyPrint'te de çalışan basit bir düzenle. Önizleme ve PDF aynı HTML'i kullanır.
- **D-055 · Bölüm başlıkları ve küçük metinler `app/i18n/document_labels.py`'de:** dil → {Summary, Experience, Education, Skills, Languages, Certifications, Projects, "present", "Native", "Date of birth", "Nationality"}. DE: Profil, Berufserfahrung, Ausbildung, Kenntnisse, Sprachen, Zertifikate, Projekte, "heute", "Muttersprache", "Geburtsdatum", "Staatsangehörigkeit". TR: Özet, İş Deneyimi, Eğitim, Yetenekler, Diller, Sertifikalar, Projeler, "devam ediyor", "Ana dil", "Doğum tarihi", "Uyruk". Doğum tarihi biçimi: DE ve TR `01.03.1990`, EN `1 March 1990`; ay/yıl her dilde `03/2021`. Önizleme de bu başlıkları kullanır.
- **D-056 · PDF'te linkler (#23):** Sadece PDF'te `<a href>` olur, şema profildeki `check_url` (`app/schemas/profile.py`) ile tekrar kontrol edilir (yalnız `http`/`https`, aksi halde düz metin); önizlemede düz metin kalır (tıklayınca uygulamadan çıkılmasın). Linkler metin olarak da görünür (ATS URL'yi okur).
- **D-057 · PDF indirme:** `GET /cvs/{id}/pdf` (sahibi; kilitli belge de indirilebilir). `Content-Type: application/pdf`, `Cache-Control: private, no-store`. Rate limit: slowapi `20/minute` (IP başına). Dosya adı CV'deki (snapshot) addan, kilitli CV gönderildiği adla iner: `CV_{Ad_Soyad}_{lang}.pdf` (şirket M4'te). ASCII yedek: önce ı→i, İ→I, ß→ss, ğ→g gibi küçük bir eşleme, sonra NFKD ve aksan silme; sadece `[A-Za-z0-9_-]`, boşluk `_`, en fazla 80 karakter; ad boşsa `CV_{lang}.pdf`; `filename*=UTF-8''` için `quote(safe="")` (RFC 6266). WeasyPrint hata verirse 503 ve küçük bir HTML hata sayfası ("We couldn't create the PDF. Please try again." + editöre dön linki); log'a sadece hata türü yazılır, CV içeriği asla. "Download PDF" bekleyen otomatik kaydetme bittikten sonra indirir (Adım 3'te tarayıcıda doğrulanır).
- **D-058 · pypdf geliştirme bağımlılığı (şimdilik):** Testlerde PDF'in metnini, sayfa sayısını, A4 boyutunu ve gömülü fontlarını okumak için `uv add --dev pypdf`. SPEC §3'te var; M9'da (CV içe aktarma) ana bağımlılığa taşınır.
- **D-059 · WeasyPrint sadece izinli kaynakları okur:** `render_cv_pdf` kendi `url_fetcher`'ını verir: yalnız `data:image/jpeg;base64,…` (CV fotoğrafı) ve `app/static/css` ile `app/static/fonts` içinde kalan `file://` yolları (resolve sonrası kontrol); `http(s)://`, diğer `file://` yolları ve her şey reddedilir. Kullanıcı verisi bugün `src`/`url()`'e girmiyor; bu katman ileride bir hata olursa da sunucunun yerel dosya veya ağ okumasını engeller (SPEC §10.1, derinlemesine savunma). Test: `http://…` ve `file:///etc/passwd` reddedilir.

### M3a review notlarının karşılığı (#28 yorumu, #23)
| Not | Nerede kapanır |
|---|---|
| PDF'te linkler sadece `http/https` (#23, E5) | Adım 6, D-056, `test_cv_links.py` |
| Kilitli CV'de fotoğraf kendi kopyası olmalı (M3a risk 4) | Adım 5a, D-052, `test_cv_photo.py` |
| İstek gövdesi boyut sınırı | M13 (proxy); M3b'de değil |

## 1. Paketler ve sistem
- `uv add weasyprint`, `uv add --dev pypdf` (D-051, D-058).
- `Dockerfile`: runtime aşamasında Pango paketleri. `.github/workflows/ci.yml`: `lint-test` job'unda `sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`.
- Adım 1'in ilk işi: Inter ve Source Serif 4 **variable woff2** dosyalarının WeasyPrint'te doğru çıktığını (kalın/normal, Türkçe harfler ğ ş ı İ, Almanca ä ö ü ß, latin/latin-ext `unicode-range`) bir deneme PDF'iyle doğrulamak (M0 riski 8). Olmazsa statik TTF'ler eklenir (D-006 güncellemesi). Test, PDF'e gömülü fontların adında Inter veya SourceSerif4 geçtiğini kontrol eder (metin okunsa da yedek bir font çizmiş olabilir; slim imajda sistem fontu yok).
- CI: `apt-get update` sonra paketler. Dockerfile: kurulumdan sonra `/var/lib/apt/lists` silinir. `docker` job'u konteynerde gerçekten bir PDF oluşturur (`docker compose exec app python -c …`), sadece import değil.

## 2. Veri modeli
- `CVContent` (`schema_version` 2): `personal.photo` (base64 JPEG) + `photo_type`, `personal.date_of_birth`, `personal.nationality`. Eski (v1) belgeler bu alanlar boş olarak okunur; kutuların yanında "Refresh from profile to add it" ipucu.
- `CVDocument.show_nationality: bool = False` (D-053) → migration `0006` (autogenerate, upgrade → downgrade → upgrade + `alembic check`, D-011). `template`, `show_photo`, `show_birth_date` M3a'dan var.

## 3. Modüller

| Dosya | İçerik |
|---|---|
| `app/i18n/document_labels.py` | `LABELS = {"en": {...}, "de": {...}, "tr": {...}}`, `labels(language)` |
| `app/services/pdf_service.py` | `render_cv_html(cv, *, for_pdf)` (şablon + etiketler + linkler), `render_cv_pdf(cv) -> bytes` (WeasyPrint, `base_url` = `app/static`), `pdf_filename(user, cv)`; WeasyPrint sadece burada import edilir |
| `app/services/photo_service.py` | `photo_for_cv(path) -> (base64, type) | None`: profil fotoğrafını 400 px'e küçültür |
| `app/services/document_service.py` | Snapshot ve refresh fotoğraf, doğum tarihi, uyruğu kopyalar; `update_settings(cv, template, show_photo, show_birth_date, show_nationality)` (aynı kilit/master kuralları) |
| `app/schemas/cv.py` | `CVContent` v2 alanları, `without_private()`; `CVEditForm`'a `template`, `show_photo`, `show_birth_date`, `show_nationality` |
| `app/routers/cvs.py` | `GET /cvs/{id}/pdf`, `GET /cvs/{id}/photo`; autosave şablon ve kutuları da kaydeder; `BUILT_TEMPLATES` = üç şablon |
| `app/templates/pdf/` | `_cv_body.html`, `cv_classic.html`, `cv_modern.html`, `cv_compact.html`, `_pdf_page.html` (PDF için tam HTML sayfası: `<html lang>`, CSS linkleri) |
| `app/static/css/` | `cv_modern.css`, `cv_compact.css`, `cv_print.css` |
| `app/templates/cvs/` | Formda Template seçimi, "Show photo (common in DACH)", "Show date of birth", "Show nationality"; eylem satırında ⤓ Download PDF ikonu (`data-tooltip`); önizleme şablona göre CSS yükler (üç CSS de editörde yüklenir, sınıf seçer) |

### Route'lar (hepsi `require_user`)

| Metot | Yol | Davranış |
|---|---|---|
| GET | `/cvs/{id}/pdf` | PDF (D-057). Başkasının CV'si 404. Rate limit 20/dk. |
| GET | `/cvs/{id}/photo` | CV'deki fotoğraf (sahibi), yoksa 404; `FileResponse` değil `Response(bytes)`, `nosniff`, `private, no-cache` |
| POST | `/cvs/{id}` | (mevcut) artık `template`, `show_photo`, `show_birth_date`, `show_nationality` de gelir; şablon `allowed_templates(user)` ∩ yapılmış şablonlar |

## 4. Testler

| Dosya | Kapsam |
|---|---|
| `test_cv_pdf.py` | **3 şablon × 3 dil (parametrize, 9 durum):** PDF oluşur (`%PDF`), gömülü font adlarında Inter/SourceSerif4 var, pypdf ile metin okunur (seçilebilir metin: ad, başlıklar, bir madde), dilin bölüm başlıkları doğru (Berufserfahrung / İş Deneyimi), sayfa A4 (595×842 pt), örnek profille 1–2 sayfa. Türkçe ve Almanca karakterler metinde doğru (ğ ş ı İ ä ö ü ß). Gizli bölüm ve seçilmemiş madde PDF'te yok. ATS: HTML'de `<table>`, `<img>` dışında görsel metin, ikon fontu yok; başlıklar `<h2>`. |
| `test_cv_pdf_route.py` | İndirme başlıkları (`application/pdf`, dosya adı ASCII + UTF-8, `no-store`; tırnak, `/`, `;`, satır sonu, boş ad, Türkçe ad); kilitli belge indirilir ve snapshot'taki adı kullanır; başkasının CV'si 404; girişsiz → login; rate limit 21. istek 429 (`render_cv_pdf` monkeypatch, gerçek render yok); WeasyPrint hatası → 503 + mesaj, log'da içerik yok |
| `test_pdf_fetcher.py` | `url_fetcher`: `http://`, `https://`, `file:///etc/passwd`, `static` dışı `file://` reddedilir; `static/css`, `static/fonts` ve `data:image/jpeg` kabul (D-059) |
| `test_cv_photo.py` | "Show photo" açılınca fotoğraf kopyalanır, kapatılınca silinir; saklanan her zaman JPEG ve < 80 KB (saydam PNG dahil); profil fotoğrafı değişince/silinince mevcut CV'nin fotoğrafı aynı; Refresh (kutu açıkken) yeni fotoğrafı alır; kapalıyken önizleme ve PDF'te fotoğraf yok; `GET /cvs/{id}/photo` sadece sahibine; fotoğrafsız CV 404; kopyalar fotoğrafı taşır; şemaya uymayan `photo`/`photo_type` reddedilir; profilinde fotoğraf yoksa kutunun yanında ipucu |
| `test_cv_private.py` | Doğum tarihi sadece `show_birth_date`, uyruk sadece `show_nationality` ile görünür; tarih biçimi dile göre; `without_private()` fotoğrafı, doğum tarihini, uyruğu çıkarır |
| `test_cv_links.py` (#23) | PDF HTML'inde linkler `<a href>` ve sadece `http(s)`; önizlemede `href` yok; garip değer (`https://"><x`) kaçışlı |
| `test_document_labels.py` | Üç dilde aynı anahtarlar var; editör önizlemesi CV dilinin başlıklarını kullanır |
| Mevcut | Editör testleri: şablon değişimi ve kutular kaydedilir, kilitli/kapalı master'da reddedilir; `test_m3a_gaps` kalıbına yeni route'lar (girişsiz, başkasının CV'si) |

`test-writer` Adım 6'dan sonra.

## 5. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)

| # | Adım | Commit |
|---|---|---|
| 0 | Plan, D-051…D-059, issue #28'e yorum | `docs: add m3b technical plan` |
| 1 | WeasyPrint + pypdf, Dockerfile ve CI paketleri (+ konteynerde PDF), font doğrulaması, `pdf_service` + `url_fetcher` (D-059), classic PDF + `cv_print.css`, ilk PDF testleri | `feat: render cv pdf with weasyprint` |
| 2 | `document_labels.py`, `_cv_body.html` + üç şablon dosyası, modern ve compact CSS, önizleme dili ve şablonu | `feat: add cv templates and section labels in three languages` |
| 3 | `GET /cvs/{id}/pdf`, dosya adı, rate limit, hata mesajı, editörde Download PDF | `feat: download cv as pdf` |
| 4 | Editörde şablon seçimi ve kutular, `update` kuralları | `feat: choose cv template and personal details in the editor` |
| 5a | Fotoğraf (D-052): kutu açılınca kopya, `/cvs/{id}/photo`, önizleme ve PDF'te gösterim, şema sınırları | `feat: show a photo on cvs` |
| 5b | Doğum tarihi ve uyruk (D-053), `show_nationality` + `0006`, `without_private()` | `feat: show date of birth and nationality on cvs` |
| 6 | PDF'te linkler (#23, D-056) + testler | `feat: add safe links to cv pdfs` |
| 7 | `test-writer` kontrolü | `test: fill m3b authorization gaps` (gerekirse) |
| 8 | README (`brew install pango`), Claude in Chrome ile kısa tarayıcı kontrolü (indirme, şablon değişimi), `security-reviewer`, LEARNING_LOG | `docs: update readme and learning log for m3b` |
| 9 | CLAUDE.md'de M3b ✓ | `docs: mark m3b as done` |
| 10 | PR (`Closes #28`, `Closes #23`), CI yeşil, merge sonrası etiket `v0.3b-m3b` | — |

## 6. Riskler
1. **Variable fontlar ve WeasyPrint:** kalınlıklar yanlış çıkabilir (M0 riski 8). Adım 1'de doğrulanır; gerekirse statik TTF.
2. **Pango CI/Docker'da:** paket adları Debian bookworm ve Ubuntu'da aynı; ilk CI çalışmasında doğrulanır. Docker imajı büyür (~40 MB); kabul.
3. **PDF oluşturma yavaş:** bir CV ~0.3–1 sn. Rate limit ile sınırlı; istek senkron (FastAPI sync route, worker thread). Arka plan işi gerekmez.
4. **Snapshot boyutu:** fotoğraf base64 ile içerik ~60 KB büyür; otomatik kaydetme her seferinde içeriği okur/yazar. 400 px ve JPEG kalite 85 ile sınırlı; kabul.
5. **CSS farkı (tarayıcı ve WeasyPrint):** flexbox desteği WeasyPrint'te sınırlı; `cv-entry-head` (başlık + tarih aynı satır) için WeasyPrint'te de çalışan yöntem (float veya basit flex) kullanılır, iki tarafta da testle bakılır.
6. **Ad ve dosya adı:** Türkçe karakterli ad dosya adında; ASCII yedek + `filename*` (RFC 6266) ile her tarayıcıda doğru.

## 7. Doğrulama
1. `uv run pytest` (Pango kurulu), ruff temiz, `alembic upgrade head` `show_nationality`'yi ekler, CI'da `lint-test` ve `docker` yeşil (docker job'u konteynerde gerçek bir PDF oluşturur).
2. Claude in Chrome ile: Download PDF iner, üç şablon arasında geçiş önizlemeyi değiştirir, DE/TR CV başlıkları doğru, fotoğraf kutusu çalışır.
3. Fatih: indirdiği PDF'leri açıp görünüme bakar (seçilebilir metin, 1–2 sayfa).
4. `security-reviewer`.

## 8. Kapsam dışı
Mektup PDF'i (M8). Şirket adı dosya adında ve başvuruya bağlı kopyalar (M4). Premium şablonlar (V2). Tailor to a job (M7). Belge dilini editörde değiştirmek (karar 1).
