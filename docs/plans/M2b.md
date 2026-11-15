# M2b — Sıralama, fotoğraf, profil gücü · Teknik plan (Design fazı)

## Context
M2a profil bölümlerini getirdi (`v0.2a-m2a`). M2b (issue #21, SPEC §12 › M2b, §6.4, §10.1, D-030) üç şey ekler: deneyim, eğitim ve projelerin sırasını değiştirmek (sürükle-bırak + ↑/↓), isteğe bağlı profil fotoğrafı ve profil gücü kartı. Ayrıca M2a manuel testinden #21'e not edilen iki tasarım işi (Personal info kartı, footer yeri) burada ele alınır, çünkü fotoğraf Personal info kartında duracak.

Onaydan sonra: plan issue #21'e yorum olarak eklenir. Branch: `m2b-profile-extras` (D-030; `main`, `84cefbf`). Etiket `v0.2b-m2b`.

Çalışma kuralı: her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push ve PR da onayla.

### Fatih'in kararları (2026-11-15)
1. **Kapsam:** Fotoğrafın Personal info kartındaki yeri Adım 4'te; kartın geri kalan yeniden tasarımı da M2b'de (Adım 6, D-041). Footer'ın yeri uygulamanın tüm sayfalarını etkilediği için ayrı issue (`idea`, `v1`), M2b'de değil.
2. **Uzantı ve içerik:** İçerik JPEG veya PNG ise uzantı tutmasa da kabul edilir (ör. içi PNG, adı `.jpg`) ve içeriğe uygun uzantıyla kaydedilir. Resim olmayan veya başka biçimdeki dosya (`.jpg` adlı metin, GIF) her durumda reddedilir. Gerekçe: güvenliği içerik kontrolü ve yeniden kaydetme sağlar; SPEC'teki "uzantısı değiştirilmiş dosya reddedilir" kuralının amacı resim olmayan dosyayı durdurmak.
3. **Boyut:** Kaydederken uzun kenar en fazla 800 px.

### Yeni kararlar (Adım 0'da `docs/DECISIONS.md`'ye)
- **D-036 · Pillow bağımlılığı:** SPEC §10.1 fotoğrafın Pillow ile yeniden kaydedilmesini istiyor ama §3 tablosunda Pillow yok. D-002'ye göre gerekçe: biçimi içerikten okumak, EXIF'i atmak, yeniden kaydetmek. Alternatif: yok (SPEC zaten Pillow diyor). Sürüm `uv.lock`'ta.
- **D-037 · SortableJS vendored:** §3'te var. htmx ve Alpine gibi `app/static/js/sortable.min.js`, başlıkta paket, sürüm (1.15.6), kaynak URL, lisans (MIT) ve sha256. D-003 tablosuna satır eklenir.
- **D-038 · Fotoğraf saklama ve sunma:** Dosya `UPLOAD_DIR/photos/<rastgele>.jpg|png` (ad `secrets.token_hex(16)`, kullanıcının dosya adı hiç kullanılmaz). DB'de sadece göreli yol (`photos/<ad>`). Sunma `GET /profile/photo` ile, sadece giriş yapmış kullanıcının **kendi** fotoğrafı (URL'de id yok, başkasınınkini isteme yolu yok). Statik klasörden sunulmaz. `<img>` ile yüklendiği için `fragment()` değil `FileResponse` kullanır; `Content-Type` kayıtlı biçimden (`image/jpeg`/`image/png`), `Cache-Control: private, no-cache`, `X-Content-Type-Options: nosniff`. Şablonda `src="/profile/photo?v={{ profile.version }}"`, böylece yeni yüklemeden sonra tarayıcı eskisini göstermez. Fotoğraf yükleme ve silme `Profile.version`'ı artırır (AI'a gitmez ama M3'teki CV kopyaları kullanır). Alternatif: `/static/uploads` (herkese açık olurdu, §10.1'e aykırı).
- **D-039 · Fotoğraf yükleme kuralları:**
  - Boyut, form **okunmadan önce**: route'ta form veya `UploadFile` parametresinden önce tanımlanan ayrı bir dependency `check_upload_size(request)`, `Content-Length` yoksa veya 2 MB + 64 KB'tan büyükse 413 yerine mesajlı 200 döndürür (htmx). Okunan dosya da 2 MB'tan büyükse reddedilir.
  - Uzantı `.jpg/.jpeg/.png` olmalı ve içerik (Pillow'un okuduğu biçim) JPEG veya PNG olmalı. İkisi tutmazsa (içi PNG, adı `.jpg`) kabul edilir ve içeriğin uzantısıyla kaydedilir. Boş (0 bayt) veya okunamayan dosya reddedilir.
  - Piksel sınırı: `Image.open` sonrası, `load()` öncesi `width * height > 25_000_000` → reddedilir (Pillow'un kendi sınırı 1–2 kat arasında sadece uyarı verir).
  - Yeniden kaydetme: EXIF yönü uygulanır (`ImageOps.exif_transpose`), kip açıkça çevrilir (JPEG → `RGB`, PNG → `RGB`/`RGBA`; CMYK, 16 bit ve paletli dosyalar da çalışır), uzun kenar ≤ 800 px, `exif`, `pnginfo`, `icc_profile` **verilmeden** kaydedilir (EXIF, XMP ve metin parçaları atılır).
  - Klasör `save_photo` içinde `mkdir(parents=True, exist_ok=True)`.
- **D-040 · Profil gücü kartı htmx olayıyla yenilenir:** Profili değiştiren her yanıt `HX-Trigger: profile-changed` header'ı gönderir; kart `hx-trigger="profile-changed from:body"` ile `GET /profile/strength`'i yeniden ister. Alternatif: her yanıta out-of-band kart eklemek (her şablona dokunmak gerekir).
- **D-041 · Personal info kartı yeniden tasarımı M2b'de:** M2a manuel testinde kart uzun bulundu (10 satır, çoğu "Not added"). Fotoğraf aynı karta girdiği için tasarım M2b'de yapılır: ad + headline büyük, fotoğraf solda, dolu iletişim bilgileri tek satırda ikonlarla, boş alanlar yerine kısa ipucu, DACH bilgileri soluk satırda. Düzenleme formu değişmez. SPEC §6.4 görünümü tanımlamadığı için SPEC değişmez. Footer'ın yeri ayrı issue. Alternatif: tasarımı ayrı bir milestone'a bırakmak.

---

## 1. Paketler ve dosyalar
- `uv add pillow` (D-036).
- `app/static/js/sortable.min.js` vendored (D-037); `layouts/_head.html`'e `defer` script.

## 2. Veri modeli — migration `0004`
| Tablo | Değişiklik |
|---|---|
| `profile` | `photo_path: str | None` (max 200) |

- `sort_order` M2a'dan var; yeni kolon yok.
- Migration: `uv run alembic revision --autogenerate -m "add profile photo"` → `0004`; `test_migrations.py` zinciri (`upgrade → downgrade → upgrade` + `alembic check`) kapsar (D-011).
- `docker-compose.yml`: `uploads` named volume (`/app/uploads`), yoksa container yeniden kurulunca fotoğraflar kaybolur. `Dockerfile`: `USER unfurl`'den önce `RUN mkdir -p /app/uploads/photos && chown -R unfurl /app/uploads`; aksi halde yeni volume root'a ait olur ve kayıt izin hatası verir. Render kalıcı diski M13 (not).

## 3. Modüller

| Dosya | İçerik |
|---|---|
| `app/services/profile_service.py` | `move_item(session, profile, model, item, direction)`: önce listeyi mevcut sırasıyla 1..n diye yeniden numaralar (aynı anda iki ekleme eşit `sort_order` üretmiş olabilir), sonra komşusuyla takas eder. `reorder_items(session, profile, model, ids)`: verilen tam sırayı 1..n yazar. Sıra gerçekten değiştiyse `_touch` ile `version` +1 (CV'deki sırayı değiştirir); değişmediyse (ilk satırda ↑, aynı sıra) `version` aynı kalır. `profile_strength(session, profile) -> Strength` (yüzde + ilk eksik maddenin cümlesi). |
| `app/services/photo_service.py` (yeni) | `PhotoError(message)`, `save_photo(upload_bytes, filename) -> str` (doğrula + yeniden kaydet + göreli yol), `delete_photo_file(path)`, `photo_file(path) -> Path` (yolun `UPLOAD_DIR/photos` içinde kaldığını kontrol eder). Pillow sadece burada. |
| `app/routers/profile.py` | Yeni route'lar (aşağıda), `Section.sortable` alanı (experience, education, projects `True`), değişiklik yapan yanıtlara `HX-Trigger: profile-changed`. |
| `app/templates/profile/` | `_strength.html` (kart), `_photo.html` (fotoğraf bloğu), `_section.html` ve `_macros.html`'de tutamak (grip) + ↑/↓ butonları, `_personal.html` yeni görünüm (Adım 6) |
| `app/static/js/app.js` | `initSortable(root)`: `htmx:load`'da `[data-sortable]` listelerine Sortable bağlar; `onEnd`'de `htmx.ajax("POST", url, {source: list, target: section, values: {ids}})`. İstek hata verirse (ör. başka sekmede silinmiş kayıt → 404) bölüm `GET /profile/{section}` ile yeniden yüklenir, böylece ekran DB'deki sırayı gösterir. Sadece tutamaktan sürüklenir (`handle`), düzenleme formu açık satır sürüklenmez (`filter`). ↑/↓ sonrası odak aynı butona döner (`htmx:afterSwap`'te `data-focus-key`). |
| `app/config.py` | `upload_dir` zaten var; değişiklik yok. |

### Route'lar (hepsi `require_user`, POST/DELETE CSRF'li)
Kayıt sırası önemli: sabit yollar (`/profile/photo`, `/profile/strength`) ve `/profile/{section}/order` genel `/profile/{section}/{item_id}`'den **önce**.

| Metot | Yol | Davranış |
|---|---|---|
| GET | `/profile/strength` | Kart parçası |
| GET | `/profile/photo` | Sahibin fotoğraf dosyası (`FileResponse`, D-038); yoksa 404 |
| POST | `/profile/photo` | Yükle (multipart, `hx-encoding`). Önce `check_upload_size` (D-039), sonra form. Hata → 200 + mesajlı fotoğraf bloğu. Eski dosya, yeni kayıt commit'lendikten sonra silinir. |
| DELETE | `/profile/photo` | `photo_path = None`, dosya silinir → fotoğraf bloğu |
| POST | `/profile/{section}/order` | `ids` listesi (tam yeni sıra). Bölüm sıralanamaz → 404. `MAX_ITEMS_PER_SECTION`'dan fazla id veya sayı olmayan id → değişiklik yok, bölüm güncel sırayla döner. Listede başkasının veya olmayan bir id → 404, hiçbir şey değişmez (JS bölümü yeniden yükler). Eksik/tekrarlı id (sayfa eski) → değişiklik yok, bölüm güncel sırayla döner (200). |
| POST | `/profile/{section}/{item_id}/move` | `direction=up|down`; ilk satır yukarı / son satır aşağı → değişiklik yok. Başkasının kaydı 404. → bölümün tamamı |

- Fotoğraf POST'u: CSRF htmx header'ından gelir (`verify_csrf` önce header'a bakar, formu okumaz). Route, M2a'daki `SubmittedForm` dependency'sini kullanmaz (o formu route'tan önce okur); sıra: `check_upload_size` → `await request.form()` → `photo_service`. Header'sız istekte `verify_csrf` formu okur (Risk 2).

### Arayüz (prototip `docs/prototype/Profile.dc.html`)
- **Profile strength kartı** sağ sütunun en üstünde: başlık + yüzde, ilerleme çubuğu (`role="progressbar"`, `aria-valuenow`), "Add a certification to reach 90%. A complete profile gives better fit scores and cover letters." %100'de "Your profile is complete."
- **İlk eksik maddenin cümleleri** (SPEC §6.4 sırası): "Add a headline", "Add a summary", "Add your email and a phone number or location", "Add an experience", "Add a bullet to an experience", "Add an education", "Add at least 5 skills", "Add a language", "Add a certification", "Add a project" + " to reach {n}%."
- **Sıralama:** deneyim, eğitim, proje satırlarında solda tutamak (prototipteki 6 nokta, `cursor: grab`), sağdaki eylemlerin yanında ↑ / ↓ (`aria-label="Move {label} up"`). İlk satırda ↑, son satırda ↓ gizli değil `disabled`. Tek satırda ikisi de yok.
- **Fotoğraf:** Personal info kartında, sol üstte 64 px yuvarlak; yoksa baş harfler. "Upload photo" (dosya seçilince otomatik gönderilir) ve "Remove". Altında "JPEG or PNG, up to 2 MB. Hidden on CVs by default." Hata mesajları: "Use a JPEG or PNG file.", "The photo must be 2 MB or smaller.", "We couldn't read this image."

## 4. Testler

| Dosya | Kapsam |
|---|---|
| `test_profile_order.py` | Her sıralanabilir bölüm (parametrize): ↑/↓ sırayı değiştirir ve kalıcı; ilk satırda ↑ ve son satırda ↓ değişiklik yapmaz ve `version` aynı kalır; eşit `sort_order`'lı iki satırda ↑/↓ yine çalışır; `order` tam listeyi yazar ve `version` +1; aynı sırayla `order` → `version` aynı; 51 id veya `abc` id → değişiklik yok. Skills/languages/certifications için `order` ve `move` 404. Bilinmeyen `direction` → değişiklik yok. |
| `test_profile_access.py` (ek) | Girişsiz `order`, `move`, `photo` (GET/POST/DELETE), `strength` → login. Başka kullanıcının id'si `order` listesinde → 404 ve iki kullanıcının sırası değişmez; `move` → 404. CSRF'siz POST/DELETE → 403. |
| `test_photo.py` | Geçerli JPEG ve PNG kaydedilir, dosya `UPLOAD_DIR/photos` altında rastgele adla; **kaydedilen dosyada EXIF yok** (EXIF'li JPEG yüklenir, sonra `Image.open(...).getexif()` boş); kaydedilen PNG'de `img.info`'da `exif`/`xmp`/metin yok; CMYK JPEG ve paletli PNG kaydedilir (500 değil); uzun kenar ≤ 800; 2 MB + 1 bayt reddedilir; `Content-Length` çok büyükse **form okunmaz** (form ayrıştırma fonksiyonu monkeypatch ile izlenir ve çağrılmadığı doğrulanır); `Content-Length` yoksa reddedilir; ~30 milyon piksellik görsel reddedilir; 0 baytlık dosya reddedilir; GIF reddedilir; `.jpg` adlı metin dosyası reddedilir; içi PNG adı `.jpg` kabul edilir ve `.png` olarak kaydedilir; bozuk dosya reddedilir; yükleme ve silme `version` +1; yeni fotoğraf eskisinin dosyasını siler; DELETE dosyayı siler ve `photo_path` boş; fotoğrafı olan kullanıcıya `GET /profile/photo` → 200 ve `image/jpeg`; A'nın `GET /profile/photo` isteği sadece A'nın dosyasını döner, B'ninkini asla; fotoğrafı olmayan kullanıcıya 404; `photo_file` `UPLOAD_DIR` dışına çıkan yolu reddeder. `UPLOAD_DIR` testlerde `tmp_path`. |
| `test_profile_strength.py` | Boş profil %0 ve ilk eksik "headline"; 10 maddenin her biri tek tek (parametrize) +%10; iletişim kuralı: e-posta tek başına sayılmaz, e-posta + telefon veya e-posta + konum sayılır; 4 beceri sayılmaz, 5 sayılır; maddesiz deneyim (5) sayılmaz; fotoğraf/doğum tarihi/uyruk sayılmaz; %100'de "complete" metni; cümle sıradaki ilk eksiği söyler. |
| `test_profile.py` (ek) | Değişiklik yapan her yanıtta `HX-Trigger: profile-changed`; sayfada kart ve sıralanabilir listelerde tutamak + ↑/↓. |
| `test_migrations.py` | `0004`. |

`test-writer` Adım 4'ten sonra çalıştırılır.

## 5. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)

| # | Adım | Commit |
|---|---|---|
| 0 | Plan, D-036…D-041 (D-003 tablosu), issue #21'e yorum, footer issue'su, M13 issue'suna notlar (istek boyutu sınırı, hesap silmede dosyalar) | `docs: add m2b technical plan` |
| 1 | `profile_strength` + kart + `/profile/strength` + `HX-Trigger` + testler | `feat: add profile strength card` |
| 2 | `move_item`, `move` route'u, ↑/↓ butonları, odak + testler | `feat: reorder profile items with up and down buttons` |
| 3 | SortableJS vendored, `order` route'u, `reorder_items`, `initSortable` + testler | `feat: reorder profile items with drag and drop` |
| 4 | `uv add pillow`, `photo_path` + `0004`, `photo_service`, photo route'ları, Personal info kartında fotoğraf yeri (yuvarlak + Upload/Remove), Dockerfile klasör izni, compose volume + testler | `feat: add profile photo upload` |
| 5 | `test-writer` kontrolü | `test: fill m2b authorization gaps` (gerekirse) |
| 6 | Personal info kartı (D-041): ad + headline büyük, dolu iletişim bilgileri tek satırda ikonlarla, boş alanlar yerine kısa ipucu, DACH bilgileri soluk satırda; prototip `Profile.dc.html`'deki Personal info bölümüyle karşılaştırılır; DECISIONS kaydı | `feat: redesign personal info card` |
| 7 | README, elle kabul testi (kısa, sıralı liste), `security-reviewer`, LEARNING_LOG | `docs: update readme and learning log for m2b` |
| 8 | CLAUDE.md'de M2b ✓ | `docs: mark m2b as done` |
| 9 | PR (`Closes #21`), merge sonrası etiket `v0.2b-m2b` | — |

## 6. Riskler
1. **Sortable + htmx + Alpine CSP:** Sortable kendi JS'iyle DOM'u değiştirir; başarılı istekte htmx bölümü sunucudan gelen sırayla yeniden çizer, hata durumunda JS bölümü yeniden yükler, böylece ekran ve DB aynı kalır. `htmx:load` her yeni parçada Sortable'ı yeniden bağlar. CSRF header'ı `htmx.ajax`'ta `source` elemanının üstündeki `hx-headers`'tan gelir; Adım 3'te testle değil elle doğrulanır (JS testi yok), risk notu.
2. **Büyük yükleme:** `Content-Length` kontrolü uygulama seviyesinde ve sadece CSRF header'ı gönderen (htmx) isteklerde formdan önce çalışır. Header'sız bir istekte `verify_csrf` 403 vermeden önce tüm multipart gövdeyi okur (uygulama genelinde, M2b'ye özel değil). Gerçek istek boyutu sınırı proxy'de (M13, M2a security-reviewer notu); M13 issue'suna eklenir.
3. **Yetim dosyalar:** Kullanıcı silinince DB satırı CASCADE ile gider ama dosya kalır. Hesap silme M13'te (GDPR) `UPLOAD_DIR` dosyalarını da silmeli; M13 issue'suna not.
4. **Fotoğraf ve AI:** Fotoğraf AI'a hiç gönderilmez (§8.4, §10.2); CV'de gösterimi M3'teki `show_photo` ile.
5. **Paralel sıra değişikliği:** iki sekmede aynı anda sıralama son yazanı kazandırır; tek kullanıcı için kabul edilebilir.

## 7. Doğrulama
1. `uv run pytest`, ruff temiz, `uv run alembic upgrade head` `photo_path`'i ekler.
2. Fatih (kısa, sıralı elle test, Chrome + `localhost:8000`): kart yüzdesi bir kayıt ekleyince değişir; deneyimleri sürükleyip yenile → sıra kalır; ↑/↓ ve klavye (Tab + Enter); JPEG yükle, kaldır; GIF ve 3 MB dosya mesaj verir; light/dark ve mobil.
3. `docker compose up --build` → fotoğraf container yeniden başlayınca da duruyor.
4. `security-reviewer`; bulgular düzeltilir veya issue olur.

## 8. Kapsam dışı
Documents'taki Profile strength kartı (M3), CV'de fotoğraf gösterimi (M3, `show_photo`), fotoğraf kırpma, Import from PDF (M9), silmede Undo (M4, D-035), hesap silmede dosya temizliği (M13).
