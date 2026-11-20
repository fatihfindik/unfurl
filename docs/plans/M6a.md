# M6a — Chrome extension without AI · Teknik plan (Design fazı)

## Context
SPEC §7 (eklenti), §8 (JSON API), §5.12 (ApiToken), §12 M6. SPEC'teki akış ilanı AI ile okur (`/jobs/extract`, M5) ve "Analyze fit" sunar (M7); API anahtarı henüz yok (D-088). Fatih #56'dan sonra M6'ya geçmeyi istedi (2026-11-18). Bu yüzden M6 ikiye bölünür (D-135):
- **M6a (bu plan):** token'lar ve Settings › Browser extension, JSON API (giriş, çıkış, `me`, `jobs/check`, `jobs`), URL normalizasyonu, eklentinin kendisi. Alanlar AI olmadan doldurulur (sayfadaki yapılandırılmış veri, site ipuçları, sayfa başlığı); kullanıcı düzeltir ve kaydeder. SPEC'teki "Save without AI" yolu bu.
- **M6b (M5 ve M7'den sonra):** "Auto-fill with AI" (`/jobs/extract`) ve "Analyze fit" (`/jobs/{id}/fit`), kredi ve kota hataları, `/me`'de krediler.

Durum: **Fatih onayladı (2026-11-18).** plan-reviewer: "Needs changes" (2 Blocker, 13 Should, 7 Nit); hepsi işlendi (en altta tablo). **Definition of Ready:** #56 (PR #62) merge edilmeden başlanmaz (token'lar `change_email`'deki sürüm artışına bağlı). Branch `m6a-extension`, etiket `v0.6a-m6a`, issue #63.

### Kabul kriterleri (SPEC §12 M6'nın AI'sız kısmı)
- LinkedIn, StepStone, karriere.at ve bir şirket kariyer sayfasında ilan, eklentiden board'a kaydediliyor (Fatih'in elle testi; `extension/TESTING.md`).
- Aynı ilan ikinci kez "Already saved → Open in Unfurl" gösteriyor (URL normalizasyonu, en az 12 örnekle birim testi).
- Token iptal edilince (Settings ya da eklentiden çıkış) eklenti giriş ekranına dönüyor.
- Eklenti sadece kullanıcı tıklayınca ve sadece açık sekmede çalışıyor; çerez okumuyor ve göndermiyor (§7.2).

## Kararlar (D-135'ten başlar)
- **D-135 · M6 ikiye bölündü:** M6a AI'sız, M6b AI'lı (yukarıda). Sıra: M6a → (API anahtarı gelince) M5 → M7 → M6b ve diğer AI milestone'ları → M13c → M13d. D-123'teki sıra buna göre güncellenir.
- **D-136 · ApiToken ve giriş (SPEC §5.12, §8):**
  - Tablo `apitoken`: `user_id` (cascade), `name` ("Chrome extension"), `token_hash` (SHA-256, unique), `session_version` (oluşturulduğu andaki kullanıcı sürümü), `last_used_at`, `expires_at` (90 gün), `revoked`, `created_at`. Ham token (`secrets.token_urlsafe(32)`) sadece girişte bir kez döner.
  - **Sürüme bağlı:** Bearer kontrolü, token'ın `session_version`'ı kullanıcınınkinden farklıysa reddeder. Şifre değişince/sıfırlanınca, e-posta değişince ve Sign out'ta sürüm arttığı için eklenti de çıkar — tek bir yerde kontrol, ileride eklenecek yeni bir sürüm artışı da unutulamaz. Hesap silinince cascade.
  - `POST /api/ext/auth/login` `{email, password}`: login formuyla aynı kontroller (login kilidi, `authenticate`, aynı genel hata: bilinen ve bilinmeyen e-posta için aynı mesaj; kilitliyken 429 `rate_limited`), 5/dk. Doğrulanmamış hesaplar da token alır (§8). Girişte kullanıcının iptal edilmiş ve süresi geçmiş token'ları silinir; en fazla 10 etkin token, fazlası en eskiyi iptal eder.
  - `last_used_at` en fazla 5 dakikada bir yazılır. Tarihler `as_utc()` ile karşılaştırılır (SQLite saat dilimsiz döndürür).
  - `POST /api/ext/auth/logout` sadece o token'ı iptal eder.
  - Dışa aktarmada `apitoken` hariç (token hash'i bir sır; D-130'daki kapsama testi zorlar).
- **D-137 · JSON API'nin ortak kuralları (SPEC §8):**
  - Base `/api/ext`, CSRF'siz (zaten muaf), JSON gövde (Pydantic şemaları), hata biçimi `{"error": {"code", "message"}}` (`unauthorized`, `validation_error`, `not_found`, `rate_limited`, `forbidden`). D-125'teki `/api/` JSON dalı ve `rate_limit_response` bu biçime çevrilir.
  - **Kimlik sadece Bearer:** API oturum çerezini hiç okumaz (`request.session` kullanılmaz); çerezli ama Bearer'sız istek 401. Popup `fetch(..., {credentials: "omit"})` ile çerez göndermez.
  - **CORS gerekmez:** MV3'te eklenti sayfalarının (popup) `host_permissions`'taki adrese istekleri CORS'a takılmaz; içerik script'leri (`extract.js`) sayfanın CORS kurallarına tabidir ve **API'yi hiç çağırmaz**, sadece popup çağırır. Sunucu CORS header'ı göndermez. Ek savunma: `/api/ext` istekleri `Origin` varsa sadece `EXTENSION_ORIGINS`'teki `chrome-extension://…` değerlerini kabul eder (403 `forbidden`); popup'tan gelen istekler (GET dahil) her zaman `Origin` taşır. Eklenti kimliği geliştirmede sabit olsun diye `manifest.json`'da açık anahtar (`key`, sır değil); özel anahtar repoya girmez. Chrome Web Store kimliği farklı olabilir: `EXTENSION_ORIGINS` virgülle ayrılmış liste, M13c'de güncellenir.
  - **Gövde sınırı:** `/api/ext` istekleri en fazla 256 KB (`Content-Length` ve akış kontrolü, ara katman), fazlası 413 `validation_error` — FastAPI gövdeyi Bearer kontrolünden önce okur.
  - **Saat dilimi:** Bearer bağımlılığı isteği `clock.use_timezone(user.timezone)` ile çalıştırır (ör. "Applied" seçilirse `applied_at` kullanıcının günü).
  - **Limitler:** login 5/dk (IP), diğerleri 60/dk ve anahtar token hash'i (proxy arkasında IP'ler birleşse de kullanıcılar birbirini etkilemez, #18).
- **D-138 · URL normalizasyonu ve "Already saved" (SPEC §8.5):**
  - `app/services/url_service.canonical_url(url)`: şema `https`, host küçük harf ve `www.` olmadan, fragment atılır, sondaki `/` atılır. **Bilinen iş siteleri** için site kuralı: LinkedIn `currentJobId` ya da path → `linkedin.com/jobs/view/{id}`; Indeed `jk`; StepStone, karriere.at ve XING path'teki id; diğer sorgu atılır. **Diğer bütün siteler** (şirket ve ATS sayfaları: `?jobId=`, `gh_jid`, `career_job_req_id` gibi) için sorgu **korunur**, sadece izleme parametreleri (`utm_*`, `fbclid`, `gclid`, `msclkid`, `refId`, `trackingId`, `trk`, `source`, `ref`…) atılır ve kalanlar sıralanır. En az 12 örnekle birim testi (iki farklı `?jobId=` iki farklı ilan).
  - `Application.canonical_url` kolonu (nullable, `(user_id, canonical_url)` index; unique değil). `create` **ve** `update_details` (Details formunda link değişince) yeniden hesaplar.
  - `POST /api/ext/jobs/check` `{url}` → `{exists, application_id?, status?, app_url?}`; birden fazla eşleşmede en yenisi; arşivdekiler de sayılır. Web'deki "Add job" değişmez (uyarı yok).
- **D-139 · AI'sız doldurma (eklentide, `extract.js`):** öncelik (a) schema.org `JobPosting` JSON-LD (başlık, şirket, şehir, uzaktan, açıklama, maaş, `validThrough`), (b) `site_hints.js`'teki site-özel selector'lar (bozulursa sessizce atlanır), (c) sayfa başlığı ve `og:` etiketleri. **Güvenlik kuralları:** `JSON.parse` try/catch içinde, `@graph` ve diziler işlenir, en fazla 10 script ve 200.000 karakter okunur, `description` HTML'i `DOMParser` ile metne çevrilir (asla `innerHTML`), popup alanları sadece `.value`/`textContent` ile doldurur. `extract.js` izole dünyada çalışır (`world: "MAIN"` yok), `chrome.storage`'a dokunmaz, API çağırmaz. `job_text` görünen metinden (seçili metin önce), en fazla 50.000 karakter. Sadece `activeTab` + `scripting` ile tıklamada enjekte edilir (§7.2); servis işçisi (`background.js`) M6a'da yok.
- **D-140 · Settings › Browser extension (SPEC §6.9):** etkin token'ların listesi (ad, oluşturma, son kullanım, bitiş) ve her birinde "Revoke" (toast). Kurulum talimatı sayfanın içinde kısa adımlar olarak (uygulamanın sunmadığı bir dosyaya link yok); ayrıntı `extension/README.md`'de. Liste `user_id` ile filtrelenir; başkasının token'ı 404.
- **D-141 · Kaydedilen alanlar:** `POST /api/ext/jobs` şirket, pozisyon, konum, çalışma şekli, maaş (`salary_text`), ilanın son günü (`posting_closes_at`, JSON-LD `validThrough`'tan, varsa), durum (varsayılan Saved; seçilebilir), URL ve `job_text` alır. `application_service.create` bu iki alanı da alır. `source` her zaman `detect_source(url)`'den; istemcinin gönderdiği `site` yok sayılır. Gizlilik sayfasına eklenti ve token'lar için bir satır.

## Veri modeli
- Migration `0015` (adım 1): `application.canonical_url` + index; mevcut satırlar migration'da doldurulur (fonksiyonun o anki kopyasıyla; migration testi değerleri `url_service.canonical_url` ile karşılaştırır, kopya kaymasın).
- Migration `0016` (adım 2): `apitoken` tablosu. İkisi de autogenerate + elle düzenleme, downgrade'li.

## Modüller
- Sunucu: `app/models/api_token.py`, `app/services/token_service.py`, `app/services/url_service.py`, `app/routers/ext_api.py`, `app/schemas/ext_api.py`, `/api/ext` gövde sınırı ara katmanı, `app/errors.py` ve `main.rate_limit_response` JSON biçimi, `application_service.create`/`update_details`, Settings › Browser extension (`settings/_extension.html`, `POST /settings/extension/tokens/{id}/revoke`), gizlilik metni.
- Eklenti (`extension/`): `manifest.json`, `config.js`, `popup.html`, `popup.css`, `popup.js`, `extract.js`, `site_hints.js`, `icons/` (Pillow ile üretilir), `README.md`, `TESTING.md`.

## Testler
- `tests/test_url_service.py`: en az 12 örnek (LinkedIn view ve `currentJobId`, StepStone, karriere.at, Indeed `jk`, XING, iki farklı `?jobId=` şirket sayfası, Greenhouse `gh_jid`, `www`, büyük harf host, fragment, izleme parametreleri ve sıralama, sondaki `/`, bozuk URL).
- `tests/test_ext_api.py`: giriş (doğru, yanlış, bilinmeyen e-posta aynı cevap, kilit 429 JSON, 5/dk JSON 429, doğrulanmamış hesap, token bir kez döner ve hash saklanır, eski token'lar temizlenir, 10 sınırı), Bearer (yok, bozuk, süresi geçmiş — SQLite ve Postgres —, iptal, sürüm farklı: şifre/e-posta değişince ve Sign out'ta 401), çerezli Bearer'sız 401, `Origin` kontrolü, 256 KB sınırı 413, `me` (krediler yok/null), `check` (var, yok, arşivde, normalize eşleşme, en yenisi, başka kullanıcınınki görünmez), `jobs` (kaydeder; `source` URL'den, `site` yok sayılır; `salary_text`, `posting_closes_at`, `canonical_url`; "Applied" seçilince `applied_at` kullanıcının günü; sınırlar; 2000 iş; `job_text` 50.000), Details'te link değişince `check` yeni linki bulur eskisini bulmaz, çıkış, hata biçimi.
- `tests/test_extension_settings.py`: liste, iptal, başka kullanıcının token'ı 404, CSRF, girişsiz.
- Dışa aktarma kapsama testi `apitoken`'ı hariç listesinde ister. Migration testi (0015 backfill ve 0016). Postgres testi (cascade, süre karşılaştırma).
- Eklenti: otomatik test yok (SPEC §13); `TESTING.md` elle test listesi (CORS/preflight hatası olmadığının kontrolü dahil). `manifest.json` için Python testi: sadece `activeTab`, `scripting`, `storage`; `host_permissions` sadece uygulama adresi; `content_scripts` ve `background` yok; `key` var.
- Sonunda test-writer ve security-reviewer.

## Adımlar
| # | Adım | Commit |
|---|---|---|
| 0 | D-135…D-141, SPEC v2.1.24, issue, plan | `docs: plan m6a` |
| 1 | URL normalizasyonu, `canonical_url` + migration 0015, `create`/`update_details` + testler | `feat: canonical job urls` |
| 2 | ApiToken + migration 0016, token servisi (sürüme bağlı) + testler | `feat: api tokens for the extension` |
| 3 | `/api/ext` (login, logout, me, check, jobs), JSON hatalar ve 429, Origin ve gövde sınırı, saat dilimi + testler | `feat: extension api` |
| 4 | Settings › Browser extension, gizlilik satırı + testler | `feat: manage extension tokens in settings` |
| 5a | Eklenti: manifest, ikonlar, popup iskeleti, giriş/çıkış ve durumlar + manifest testi | `feat: extension sign in` |
| 5b | Eklenti: extract, site ipuçları, önizleme formu, check ve kaydetme, README, TESTING | `feat: save jobs from the extension` |
| 6 | test-writer, security-reviewer, LEARNING_LOG, README, CLAUDE.md, PR ve elle test listesi | `test: ...`, `docs: ...` |

### Definition of Done
- [ ] `uv run pytest` (`TZ=UTC` dahil), ruff, CI yeşil (`postgres-tests` dahil)
- [ ] security-reviewer bulguları kapatıldı ya da issue oldu
- [ ] PR'da `Closes #63` ve elle test listesi (eklentiyi yükleme dahil)
- [ ] Fatih'in elle kabul testi (4 sitede kaydetme)
- [ ] Fatih merge eder, sonra etiket `v0.6a-m6a`
- [ ] SPEC değişti: claude.ai kopyası yenilenecek (Fatih)
- [ ] LEARNING_LOG, CLAUDE.md'de M6 satırı M6a/M6b'ye bölünür ve M6a işaretlenir

## SPEC değişiklikleri (v2.1.24)
- §12: M6 → M6a ve M6b (D-135); sıra notu.
- §5.5: `Application.canonical_url`; §5.12: ApiToken `session_version` ve iptal kuralları.
- §7.3: `background.js` M6a'da yok; manifest `key`; §7.4: AI'sız doldurma (D-139), M6a'da `/jobs/extract` ve fit yok.
- §8: CORS yerine host_permissions + Origin kontrolü, sadece Bearer, gövde sınırı (D-137); `/me` krediler M6b'ye kadar `null`; `check` yanıtına `app_url`; §8.5 şirket sayfalarında sorgu korunur.
- §10.2: gizlilik sayfasında eklenti satırı. §11: `EXTENSION_ORIGINS` sabit kimlik notu.

## Fatih'in cevapları (2026-11-18)
1. M6 → M6a (AI'sız) ve M6b (AI'lı): **evet**.
2. Şifre/e-posta değişince ve Sign out'ta eklentinin oturumu da kapanır: **evet**.
3. Eklentide durum seçilir (varsayılan Saved); ilan kayıtlıysa sadece "Open in Unfurl", "Save anyway" yok: **evet**.
Plan onaylandı; #62 merge edilince başlanır; commit ve push serbest, merge Fatih'te.

## Kapsam dışı
AI ile doldurma ve uyum analizi (M6b), Chrome Web Store yayını (M13c sonrası), Firefox/Safari, eklentide offline kuyruk.

## plan-reviewer bulguları (2026-11-18) ve işlenişi
| # | Önem | Bulgu | Karşılığı |
|---|---|---|---|
| 1 | Blocker | Details'te link değişince `canonical_url` eskir | `update_details` da hesaplar, test (D-138) |
| 2 | Blocker | Şirket/ATS sayfalarında sorgu atılınca farklı ilanlar birleşir | Bilinmeyen sitelerde sorgu korunur, izleme parametreleri atılır (D-138) |
| 3 | Should | İki adım tek migration'ı paylaşıyor | 0015 ve 0016 |
| 4 | Should | Dört yerde iptal kırılgan | Token'da `session_version`, tek kontrol (D-136) |
| 5 | Should | İptal edilmiş satırlar birikir | Girişte temizlik (D-136) |
| 6 | Should | Gövde sınırı yok | 256 KB, 413 (D-137) |
| 7 | Should | 429 HTML | JSON 429, kilit cevabı (D-136, D-137) |
| 8 | Should | API'de saat dilimi yok | `use_timezone` (D-137) |
| 9 | Should | Uygulamanın sunmadığı README'ye link | Adımlar sayfada (D-140) |
| 10 | Should | `/me` kredileri | M6b'ye kadar `null` |
| 11 | Should | Çerez kullanımı belirsiz | Sadece Bearer, `credentials: "omit"`, test (D-137) |
| 12 | Should | JSON-LD güvenliği | Kurallar (D-139) |
| 13 | Should | Maaş alanı `create`'te yok; `site` güveni | D-141 |
| 14 | Should | UTC karşılaştırma | `as_utc`, test (D-136) |
| 15 | Should | #56'ya bağımlı | Definition of Ready |
| 16 | Nit | Birden fazla eşleşme; web "Add job" | En yenisi; web değişmez |
| 17 | Nit | Özel anahtar; Store kimliği | D-137 |
| 18 | Nit | Proxy arkasında IP limiti | Token hash'ine göre limit |
| 19 | Nit | 8 mi 12 mi | 12 |
| 20 | Nit | Backfill kopyası kayabilir | Migration testi karşılaştırır |
| 21 | Nit | Adım 5 büyük | 5a ve 5b; TESTING'de CORS kontrolü |
| 22 | Nit | Gizlilik sayfası | D-141 |
