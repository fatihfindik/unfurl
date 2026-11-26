# Unfurl — Kararlar (Decision Log)

> Mimari kararlar ve varsayımlar. Ürün kararları `docs/SPEC.md`'dedir.
> Her kayıt: **Karar**, **Gerekçe**, **Alternatifler**. Yeni kayıtlar en alta eklenir; bir karar değişirse eski kayıt silinmez, "Yerine geçen: D-0xx" notu düşülür.
> Sohbette alınıp buraya (veya SPEC'e) yazılmayan karar geçersizdir (WORKFLOW §3).

---

## M0 — Proje iskeleti (2026-11-10)

Kaynak: `docs/plans/M0.md` §12 (açık sorular, Fatih onayı) ve Build sırasında alınan kararlar.

### D-001 · Bağımlılıklar milestone'a göre eklenir
- **Karar:** M0'da sadece M0'ın kullandığı paketler var. Diğer SPEC §3 paketleri (pwdlib, slowapi, weasyprint, anthropic, …) kendi milestone'unda `uv add` ile gelir.
- **Gerekçe:** Daha küçük imaj; her paketin neden geldiği commit geçmişinden belli.
- **Alternatifler:** SPEC §3'ün tamamını M0'da kurmak.

### D-002 · SPEC §3'te açıkça olmayan paketler ve ortam değişkenleri
- **Karar:** `pydantic-settings` (config), `psycopg[binary]` (Postgres sürücüsü, SPEC §11 URL'i `postgresql+psycopg`), `fastapi[standard]` (`fastapi dev/run` CLI + uvicorn) eklendi. docker-compose için `.env.example`'a `POSTGRES_USER/PASSWORD/DB` eklendi (yerel değerlerle).
- **Gerekçe:** SPEC §4 `pydantic-settings`'i ve §11 `psycopg`'yi zaten varsayıyor; `fastapi[standard]` SPEC'teki `uv run fastapi dev` komutu için şart.
- **Alternatifler:** `os.environ` ile elle config; `psycopg2`; uvicorn'u ayrı kurmak.

### D-003 · Sürüm sabitleme
- **Karar:** Python paketleri `uv.lock` ile (tam sürüm + hash), CI ve Docker `uv sync --locked` kullanır. Diğerleri:

  | Ne | Sürüm | Nerede sabit |
  |---|---|---|
  | Python | 3.12 | `.python-version`, `requires-python` |
  | uv | 0.13.9 (Dependabot günceller, D-143) | Dockerfile, CI |
  | Tailwind CLI | v4.3.5 | `scripts/build_css.sh` |
  | htmx | 2.0.11 | `app/static/js/htmx.min.js` başlığı (+ sha256) |
  | @alpinejs/csp | 3.17.5 | `app/static/js/alpine.min.js` başlığı (+ sha256) |
  | SortableJS | 1.15.6 | `app/static/js/sortable.min.js` başlığı (+ sha256), D-037 |
  | Inter / Space Grotesk / Source Serif 4 | Fontsource variable 5.3.0 / 5.3.0 / 5.3.1 | bu tablo |
  | Docker imajları | `python:3.12-slim-bookworm`, `postgres:16` | Dockerfile, docker-compose.yml |
  | GitHub Actions | `actions/checkout@v7`, `astral-sh/setup-uv@v10.3.3` | `.github/workflows/ci.yml` |
- **Gerekçe:** Her makinede, CI'da ve Docker'da aynı kurulum. Vendored JS dosyalarının sha256'sı başlıkta, böylece değişip değişmediği `tail -n +10 <dosya> | shasum -a 256` ile kontrol edilebilir.
- **Not:** `setup-uv` kayan major etiket (`v10`) yayınlamıyor; tam sürüm kullanılmalı (ilk CI çalışması bu yüzden kırıldı).
- **Alternatifler:** Sadece major sürümler (`v4`, `latest`); Action'ları commit SHA ile sabitlemek (daha güvenli, ama okunması zor — ileride değerlendirilebilir).
- **Kısmen yerine geçen: D-143** (Action'lar SHA ile, Docker image'ları digest ile; Dependabot).

### D-004 · Tailwind v4
- **Karar:** Tailwind v4, CSS tabanlı config (`@theme inline`), `pytailwindcss` ile standalone CLI.
- **Gerekçe:** Tema CSS değişkenleri doğrudan sınıf olur (`bg-surface`, `text-text-2`); koyu tema `dark:` önekleri yerine değişkenlerin değişmesiyle çalışır. Ayrı JS config yok. Plan Risk 1 doğrulandı: pytailwindcss v4 binary'sini indiriyor ve checksum'ını kontrol ediyor.
- **Alternatifler:** Tailwind v3 + `tailwind.config.js`.

### D-005 · Alpine.js CSP build
- **Karar:** Standart Alpine yerine `@alpinejs/csp`. Tüm bileşenler `app/static/js/app.js`'te `Alpine.data(...)` ile tanımlanır; HTML attribute'larında sadece isim ve basit ifade kullanılır. Inline `<script>` yok.
- **Gerekçe:** Standart Alpine `unsafe-eval` ister; M13'teki sıkı CSP (SPEC §10.1 "sadece kendi origin'inden script") ile uyumlu olmak için şablonları sonradan yeniden yazmak gerekmesin.
- **Alternatifler:** Standart build + M13'te geçiş.

### D-006 · Fontlar self-hosted, variable font
- **Karar:** Inter, Space Grotesk, Source Serif 4 woff2 dosyaları `app/static/fonts/`'ta (OFL 1.1 lisansları yanında). Variable (wght ekseni), latin + latin-ext alt kümeleri, `unicode-range` ile.
- **Gerekçe:** Google Fonts CDN, AB'de GDPR sorunu (ziyaretçi IP'si aktarılır) ve CSP'ye aykırı. Variable: 16 statik dosya / 371 KB yerine 6 dosya / 268 KB. latin-ext Türkçe (ş, ğ, İ) ve Almanca için gerekli; sadece bu karakterler sayfada varsa indirilir.
- **Risk:** Variable fontlar M3'te WeasyPrint ile test edilecek (özellikle Source Serif 4, Classic CV); sorun çıkarsa PDF şablonları için statik dosyalar eklenir (plan Risk 8).
- **Alternatifler:** Google Fonts CDN; statik ağırlık dosyaları.

### D-007 · Tema tercihi M0'da cookie'de
- **Karar:** Seçilen tema `theme` cookie'sinde (`light|dark|system`, 1 yıl, `SameSite=Lax`). Sunucu okur ve `<html class="theme-…">` basar; geçersiz değer → `system`. M4'te `User.theme`'e bağlanır.
- **Gerekçe:** `User` tablosu M1'de geliyor ama tema seçici M0'da. Sunucu tarafında basıldığı için sayfa yüklenirken renk yanıp sönmez (SPEC §6.0).
- **Gizlilik notu:** SPEC §10.2 "sadece zorunlu oturum çerezi" diyor. `theme`, kullanıcının kendi seçtiği bir tercih çerezi; izleme yapmaz, kişisel veri içermez, cookie banner gerektirmez. **M13'te gizlilik sayfasında anılmalı.** Giriş yapmış kullanıcıda cookie ile `User.theme` arasındaki öncelik M4'te belirlenir.
- **Alternatifler:** `localStorage` (prototipteki gibi; sunucu göremez → yanıp sönme); M0'da tema yok.

### D-008 · Derlenmiş CSS commit edilmez
- **Karar:** `app/static/css/app.css` `.gitignore`'da. Lokal: `scripts/build_css.sh`; Docker kendi `css` aşamasında üretir.
- **Gerekçe:** Build çıktısı; her şablon değişikliğinde büyük diff ve çakışma üretir.
- **Alternatifler:** `app.css`'i commit etmek (Tailwind'siz kurulum mümkün olurdu).

### D-009 · Henüz olmayan bölümler için "Coming soon" sayfası
- **Karar:** `/learning`, `/documents`, `/settings` tek şablonlu "Coming soon" sayfası (`app/routers/placeholders.py`). Her biri kendi milestone'unda kaldırılır: Documents M3, Learning M10, Settings M11.
- **Gerekçe:** Sidebar linkleri 404'e gitmesin, sayfa iskeleti test edilebilsin.
- **Alternatifler:** 404; linkleri pasif göstermek.

### D-010 · Ortak bileşenler (M0'da iskelet)
- **Karar:**
  - `page_header`, toast + Undo, modal, drawer: görsel ve erişilebilir iskelet + test; davranış (Undo endpoint'leri, sunucudan tetiklenen toast) M4'te.
  - **Modal ve drawer native `<dialog>`** (`showModal()`); `data-dialog-open="<id>"` ile açılır, arka plana tıklama kapatır.
  - **Avatar menüsü "disclosure" kalıbı** (buton + `aria-expanded` + düz linkler), prototipteki `role="menu"` değil.
  - **Toast Undo** `htmx.ajax("POST", undo_url)` ile.
- **Gerekçe:** `<dialog>` Esc, odak tuzağı, odağı açana geri verme ve `aria-modal`'ı tarayıcıdan bedava getirir. `role="menu"` ok tuşlarıyla gezinme gerektirir; basit link listesi için disclosure daha doğru. Undo URL'i her toast'ta değiştiği için butona sabit `hx-post` yazılamaz.
- **Alternatifler:** `role="dialog"` div + elle odak yönetimi; prototipteki `role="menu"`.

### D-011 · Alembic kurulumu
- **Karar:**
  - Veritabanı URL'i `alembic.ini`'de değil, `env.py` uygulama ayarlarından (`.env`) okur.
  - `render_as_batch=True` (SQLite'ta `ALTER TABLE` kısıtları).
  - Constraint naming convention (`app/models/__init__.py`).
  - Boş `0001_initial` migration'ı; User M1'de `0002`.
  - Yeni migration'lar ruff ile otomatik düzeltilip formatlanır (`alembic.ini` post-write hook).
  - `tests/test_migrations.py`: upgrade → downgrade → upgrade zinciri ve `alembic check` (model değişip migration unutulursa test kırılır).
- **Gerekçe:** Uygulama ve migration'lar hep aynı veritabanını kullanır; SQLite ↔ Postgres farkları azalır; unutulan migration CI'da yakalanır.
- **Alternatifler:** URL'i `alembic.ini`'de tekrar yazmak; M0'da migration olmaması.

### D-012 · CI yapısı
- **Karar:** `lint-test` her push'ta (ve fork PR'larında); `docker` (image build + Postgres + `curl /` + `alembic_version` kontrolü) sadece `main`'e açılan PR'larda. Aynı branch'te eski çalışma iptal edilir. Workflow izni sadece `contents: read`.
- **Gerekçe:** Aynı repodaki branch'in PR'ında lint-test'i tekrar çalıştırmak gereksiz (sonuç aynı commit'te görünür). Docker job'u yavaş; sadece merge öncesi gerekli. "Postgres üzerinde çalışıyor" kabul kriteri CI'da da doğrulanır.
- **Alternatifler:** Sadece ruff + pytest (issue #1'deki minimum); her push'ta docker.

### D-013 · M0'da `base.html`'de olmayanlar
- **Karar:** AI kredi kartı (M5), asistan butonu (M12a), zil rozeti/paneli (M11), avatar menüsünde gerçek kullanıcı (M1) yok. Zil statik; My profile ve Sign out pasif.
- **Gerekçe:** Arkasında veri/özellik olmayan arayüz yanıltıcı olur.

### D-014 · WeasyPrint sistem kütüphaneleri M3'te
- **Karar:** Dockerfile'a pango vb. M3'te eklenir.
- **Gerekçe:** D-001 ile aynı: M0 imajı küçük kalsın.

### D-015 · Odak halkası tüm etkileşimli öğelerde (SPEC v2.1.1)
- **Karar:** `:focus-visible` halkası (2px `#6366F1`) checkbox ve radio dahil tüm etkileşimli öğelerde; fareyle tıklamada görünmez. SPEC §6.0/5 ve §14 güncellendi.
- **Gerekçe:** Önceki "checkbox/radio hariç" kuralı klavye kullanıcılarının hangi öğede olduğunu görmesini engelliyordu (erişilebilirlik).
- **Alternatifler:** Prototipteki kural (`outline: none`).

### D-016 · `DEBUG` varsayılanı `false`
- **Karar:** `app/config.py`'de `debug` varsayılanı `False`; lokal geliştirmede `.env.example` `DEBUG=true` yapar.
- **Gerekçe:** `.env` olmadan açılan bir ortam (ör. yanlış yapılandırılmış production) güvenli tarafta başlar.

### D-017 · Etiket adı
- **Karar:** M0 sürüm etiketi `v0.0-m0` (WORKFLOW formatı `v0.{n}-m{n}`).

### D-018 · Tema seçici: tek ikon + açılır menü (SPEC §2, §6.0)
- **Karar:** Üst bardaki üç butonlu tema grubu yerine tek ikon butonu (o anki temanın ikonu, hover'da `bg-muted`). Tıklayınca Light / Dark / System menüsü açılır; seçili satırda ✓. Erişilebilirlik: `aria-haspopup="menu"`, `role="menuitemradio"` + `aria-checked`, ↑/↓ ile gezinme, Esc ve dışarı tıklama kapatır. SPEC §2 ("tek tıkla") ve §6.0 güncellendi.
- **Gerekçe:** Elle kabul testinde seçili ve hover durumları karıştı; etiketli menü daha net ve üst barda daha az yer kaplar (Fatih'in kararı, issue #2).
- **Maliyet:** Tema değiştirmek tek tık yerine iki tık.
- **Alternatifler:** Üç butonlu grup (prototip), hover/seçili ayrımı düzeltilmiş hâliyle (`b8c5bc0`).

### D-019 · `theme` cookie'sinde `Secure` bayrağı yok (güvenlik incelemesi #9, kabul)
- **Karar:** Kabul edildi: cookie gizli bilgi taşımıyor, sunucu değeri sabit listeyle (`light`/`dark`/`system`) doğruluyor ve testler HTML enjeksiyonunu kapsıyor.

---

## M1 — Kimlik doğrulama (2026-11-13)

Kaynak: claude.ai Plan fazı (`docs/plans/M1-handoff.md`), Fatih onayı.

### D-020 · Login kilidi sayacı bellekte
- **Karar:** Başarısız login sayacı process belleğinde tutulur (anahtar IP + e-posta, 15 dk pencere). slowapi genel limitleri de bellek backend'iyle çalışır.
- **Gerekçe:** Tek instance (APScheduler da bunu varsayıyor), ek altyapı gerekmiyor.
- **Maliyet:** Restart sayaçları sıfırlar. Birden fazla instance'a geçilirse DB veya Redis'e taşınmalı.
- **Alternatifler:** DB tablosu (kalıcı ama her login'de yazma), Redis (yeni servis).
- **Güncelleme: D-149** (#57): e-posta başına ikinci sayaç (aynı bellek deposu mantığı).

### D-021 · Oturum geçersiz kılma: `User.session_version`
- **Karar:** İmzalı oturum çerezi `session_version` taşır, `current_user` bunu DB ile karşılaştırır. Şifre değişince/sıfırlanınca değer +1 olur.
- **Gerekçe:** İmzalı çerez sunucuda tutulmaz. Eski oturumları düşürmenin en ucuz yolu bu.
- **Alternatifler:** Sunucu tarafı session tablosu (cihaz listesi gibi özelliklere açık, V2'de düşünülebilir).
- **Bilinen sınır (M1a güvenlik incelemesi #1):** Logout çerezi sadece tarayıcıdan siler. Kopyalanmış bir çerez, girişten itibaren 14 gün geçerli kalır (Starlette çerezi sadece oturum değişince yeniden imzalar). M1a'da tek iptal yolu `session_version`'ı artırmaktır; M1b'de şifre sıfırlama bunu yapar. "Sign out everywhere" M1b Plan fazında değerlendirildi ve V2'ye ertelendi (#14): reset sonrası oturum düşürme asıl riski (çalınan şifre) kapsıyor, butonun yeri henüz olmayan Settings sayfası.

### D-022 · CSRF: kendi küçük çözümümüz
- **Karar:** Oturum başına token, form alanı `csrf_token` ve HTMX için `X-CSRF-Token` header'ı. Ek kütüphane kullanılmaz. `/api/ext/*` hariç.
- **Gerekçe:** SPEC §3'te CSRF kütüphanesi yok. İhtiyaç küçük ve öğretici.
- **Alternatifler:** starlette-csrf, fastapi-csrf-protect.

### D-023 · Kayıtta mevcut e-posta açıkça söylenir
- **Karar:** Register'da "An account with this email already exists." gösterilir. forgot-password numaralandırmaya kapalı kalır.
- **Gerekçe:** UX kazancı bu üründeki numaralandırma riskinden büyük. Register zaten rate limit'li.
- **Alternatifler:** Her durumda "Check your inbox" deyip mevcut hesaba bilgi e-postası göndermek (daha güvenli ama kafa karıştırıcı).

### D-024 · M1 ikiye bölündü
- **Karar:** M1a (oturum çekirdeği) ve M1b (e-posta akışları), ayrı branch, PR ve etiketlerle: `m1a-auth-core` / `v0.1a-m1a`, `m1b-auth-email` / `v0.1b-m1b`.
- **Gerekçe:** Tek M1, WORKFLOW'daki 1-2 haftalık sınırı aşma riski taşıyordu.
- **Alternatifler:** Tek M1.

### D-025 · User tablosu sadece M1 kolonlarıyla
- **Karar:** M1a migration'ı User'a sadece M1'in kullandığı kolonları ekler: `email`, `password_hash`, `full_name`, `is_verified`, `privacy_accepted_at`, `last_login_at`, `session_version`, `plan`, `plan_expires_at`, `created_at`, `updated_at`. Diğer §5.1 kolonları kendi milestone'larında küçük migration'larla gelir: `theme` (M4), `default_doc_language` (M3'ten Settings sayfasına ertelendi, Fatih 2026-11-15), `timezone` (M11; M10 `DEFAULT_TIMEZONE` kullanır, D-089), `anthropic_api_key_encrypted` (M7), `ui_locale` (ihtiyaç olunca).
- **Gerekçe:** Her kolon onu kullanan kodla birlikte gelir; kullanılmayan kolon olmaz.
- **Not:** `created_at` ve `updated_at` uygulama tarafında SQLAlchemy `default` / `onupdate` ile dolar. Ham SQL ile yapılan güncellemeler `updated_at`'i değiştirmez; DB trigger'ı kullanılmaz.
- **Alternatifler:** SPEC §5.1'in tamamını şimdi eklemek.

### D-026 · `app/security.py`
- **Karar:** CSRF (`get_csrf_token`, `verify_csrf`) ve yönlendirme güvenliği (`safe_redirect_path`) yeni `app/security.py` modülünde. SPEC §4'teki yapıya küçük bir ek.
- **Gerekçe:** İkisi de auth'a özel değil; her form ve her `back_url` kullanır.
- **Alternatifler:** `services/auth_service.py` içine koymak.

---

## M1b — E-posta akışları (2026-11-14)

Kaynak: `docs/plans/M1b.md` (Design fazı), `plan-reviewer` onayı, Fatih onayı.

### D-027 · Token'lar durumsuz ve imzalı
- **Karar:** Doğrulama ve reset token'ları `itsdangerous.URLSafeTimedSerializer` ile imzalanır, DB'de token tablosu yok. Süre `max_age` ile kontrol edilir (48 saat / 1 saat). Her amaç ayrı `salt` kullanır, böylece verify token'ı reset için kullanılamaz. Reset token'ı `password_hash`'ten türetilen kısa bir parmak izi taşır; şifre değişince geçersiz olur (tek kullanımlık, SPEC §6.1). 512 karakterden uzun token okunmadan reddedilir.
- **Gerekçe:** Migration ve süresi dolan kayıtları temizleme işi gerekmez. `itsdangerous` zaten kurulu (M1a).
- **Maliyet:** Verilmiş bir token tek tek iptal edilemez; süresi dolana kadar geçerlidir (reset token'ı şifre değişince düşer).
- **Alternatifler:** Token tablosu (iptal edilebilir, kullanım kaydı tutulur).

### D-028 · E-postalar arka planda gönderilir
- **Karar:** Route'lar e-postayı FastAPI `BackgroundTasks` ile yanıttan sonra gönderir. Gönderim hataları `email_service.send_email` içinde yakalanır ve `unfurl.email` log'una yazılır. E-posta metinleri düz metin Jinja şablonlarıdır: `app/templates/emails/*.txt` (SPEC §4 ağacına küçük ek).
- **Gerekçe:** SMTP yavaşlığı sayfayı bekletmez. forgot-password'da kayıtlı ve kayıtsız e-posta için yanıt süresi farkı oluşmaz (numaralandırmaya kapalı).
- **Maliyet:** Gönderim hatası kullanıcıya gösterilmez; kullanıcı "Resend email" ile tekrar dener.
- **Alternatifler:** Yanıttan önce senkron gönderim, iş kuyruğu (yeni altyapı).

### D-029 · Production'da e-posta ayarı zorunlu
- **Karar:** `APP_ENV=production` iken `EMAIL_BACKEND=console` veya boş `SMTP_HOST` ile uygulama başlamaz (mevcut `SECRET_KEY` kontrolü gibi).
- **Gerekçe:** Doğrulama ve reset e-postaları canlıda sessizce terminale düşmesin.
- **Alternatifler:** Sadece uyarı log'u.

---

## M2 — Profil (2026-11-14)

Kaynak: Plan fazı, Fatih onayı.

### D-030 · M2 ikiye bölündü
- **Karar:** M2a (profil bölümleri: ekle, düzenle, sil) ve M2b (sıralama, fotoğraf, profil gücü), ayrı branch, PR ve etiketlerle: `m2a-profile` / `v0.2a-m2a`, `m2b-profile-extras` / `v0.2b-m2b`. Profil gücü formülü SPEC §6.4'te (10 madde × %10). Sıralama sürükle-bırak + ↑ / ↓ butonları (erişilebilirlik).
- **Gerekçe:** Tek M2 (6 alt tablo, satır içi düzenleme, sürükle-bırak, dosya yükleme, yüzde) WORKFLOW'daki 1-2 haftalık sınırı aşma riski taşıyordu. M2a tek başına SPEC'teki asıl kabul kriterini karşılıyor.
- **Alternatifler:** Tek M2. Profil gücü için bölüm başına eşit puan (daha basit ama "Add 2 more skills" gibi yönlendirme yapamaz). Sadece sürükle-bırak (klavyeyle sıralama yok).

### D-031 · Liste bölümleri için tek route kalıbı
- **Karar:** Experience, Education, Skill, LanguageSkill, Certification ve Project için ayrı route'lar yerine `/profile/{section}/...` route'ları ve küçük bir `Section` dataclass'ından oluşan `SECTIONS` sözlüğü (`app/routers/profile.py`). Sözlük her bölüm için model, form şeması ve şablon adlarını tutar.
- **Gerekçe:** Altı bölüm aynı işlemlere sahip (ekle, göster, düzenle, sil). ~30 neredeyse aynı route yerine 5 route; testler bölüm listesi üzerinden parametrize edilir.
- **Maliyet:** Biraz soyutlama. Okunabilir kalması için dataclass alanları ve form okuma yardımcısı yorumlu.
- **Alternatifler:** Her bölüme ayrı route'lar.

### D-032 · Profil sahipliği ve `profile_service`
- **Karar:** Alt tablolarda `user_id` yok (SPEC §5.2). Her alt kayıt sorgusu `Profile.user_id == user.id` ile join yapan `get_owned_item` yardımcısından geçer; bulunamazsa veya id sayı değilse 404. Kod `app/services/profile_service.py`'de (SPEC §4 ağacına küçük ek). Profil ilk açılışta boş oluşturulur, e-postası hesap e-postasıyla başlar (kullanıcı değiştirebilir).
- **Gerekçe:** CLAUDE.md'deki "her sorgu `user_id` ile filtrelenir" kuralı tek bir yerde sağlanır.
- **Alternatifler:** Alt tablolara `user_id` eklemek (tekrar eden veri). Profili kayıtta oluşturmak.

### D-033 · Profil tarihleri ay hassasiyetinde
- **Karar:** Deneyim, eğitim ve sertifika tarihleri `<input type="month">` ile alınır, DB'de ayın 1'i olarak `date` saklanır. Placeholder "YYYY-MM" ve biçim hatası mesajı var (bazı tarayıcılar alanı düz metin gösterir). "Devam ediyor" bir kutucukla seçilir; işaretliyse bitiş tarihi boş kalır.
- **Gerekçe:** CV'lerde gün yazılmaz. SPEC'teki `date` tipi korunur.
- **Alternatifler:** Sadece yıl. Tam tarih.

### D-034 · SQLite'ta foreign key kontrolü açık (migration'lar hariç)
- **Karar:** Uygulama engine'i ve test engine'i her bağlantıda `PRAGMA foreign_keys=ON` çalıştırır (`app/db.py`'de tek yardımcı). Listener asla `Engine` sınıfına (her engine'e) ya da `alembic/env.py`'ye eklenmez.
- **Gerekçe:** SQLite foreign key kurallarını ve `ON DELETE CASCADE`'i varsayılan olarak uygulamaz; açınca localde ve testlerde Postgres ile aynı davranış. Migration'larda kapalı kalmalı: SQLite'ta tablo değiştiren batch migration tabloyu kopyalayıp eskisini siler, foreign key açıkken bu silme bağlı satırları da (ör. `user` değişince bütün profilleri) siler.
- **Alternatifler:** Testleri Postgres'te çalıştırmak (CI'da yeni job). CASCADE testini M13'e bırakmak.
- **Güncelleme (2026-11-18, gece çalışması):** `tests/test_postgres.py` CASCADE'i (kullanıcı silinince `user_id`'li her tablo boşalır) ve D-121'deki iki yarışı gerçek Postgres 16'da test eder. CI'da PR'larda `postgres-tests` job'u; yerelde `TEST_POSTGRES_URL` yoksa atlanır.

### D-035 · M2a'da silmede Undo yok
- **Karar:** Profil kayıtları onay sormadan silinir (SPEC §6.0/2'deki işlemler gibi), ama Undo altyapısı M4'te geldiği için M2a'da silinen kayıt geri alınamaz. M4 issue'suna "profile delete undo" maddesi eklenir. SPEC değişmez.
- **Gerekçe:** M2a'nın kapsamı küçük kalır; M4 Undo altyapısını kurunca profil silme de ona bağlanır.
- **Alternatifler:** M2a'da basit Undo.


## M2b — Sıralama, fotoğraf, profil gücü (2026-11-15)

Kaynak: Design fazı (`docs/plans/M2b.md`), `plan-reviewer`, Fatih onayı.

### D-036 · Pillow bağımlılığı
- **Karar:** Profil fotoğrafları için `pillow` eklenir (sürüm `uv.lock`'ta).
- **Gerekçe:** SPEC §10.1 fotoğrafın Pillow ile yeniden kaydedilmesini ve EXIF'in silinmesini istiyor, ama §3 tablosunda Pillow yok (D-002). Biçimi dosya adından değil içerikten okumak, EXIF'i atmak ve yeniden kaydetmek için gerekli.
- **Alternatifler:** Yok; SPEC Pillow'u adıyla istiyor.

### D-037 · SortableJS vendored
- **Karar:** SortableJS 1.15.6, htmx ve Alpine gibi `app/static/js/sortable.min.js` olarak repoya eklenir; başlıkta paket, sürüm, kaynak URL, lisans (MIT) ve sha256 (D-003).
- **Gerekçe:** §3'te sürükle-bırak için SortableJS seçili. CDN yok: script'ler sadece kendi origin'imizden (§10.1).
- **Alternatifler:** HTML5 sürükle-bırak API'si ile kendi kodumuz (mobilde zayıf).

### D-038 · Fotoğraf saklama ve sunma
- **Karar:** Dosya `UPLOAD_DIR/photos/<secrets.token_hex(16)>.jpg|png` olarak saklanır; kullanıcının dosya adı hiç kullanılmaz, DB'de sadece göreli yol (`Profile.photo_path`). Sunma `GET /profile/photo` ile, sadece giriş yapmış kullanıcının kendi fotoğrafı (URL'de id yok). `FileResponse`, `Cache-Control: private, no-cache`, `X-Content-Type-Options: nosniff`; şablon `?v={profile.version}` ile önbelleği atlar. Yükleme ve silme `Profile.version`'ı artırır. Docker'da `uploads` named volume; klasör imajda `unfurl` kullanıcısına ait.
- **Gerekçe:** §10.1: fotoğrafı sadece sahibi görebilir. Statik klasör herkese açık olurdu. Rastgele ad, dosya adı üzerinden yol saldırısını (path traversal) imkânsız kılar.
- **Alternatifler:** `/static/uploads` (herkese açık). Fotoğrafı DB'de saklamak (DB büyür, yedekleme ağırlaşır).

### D-039 · Fotoğraf yükleme kuralları
- **Karar:** `Content-Length` yoksa veya 2 MB + 64 KB'tan büyükse, form okunmadan reddedilir (route'a özel dependency, M2a'daki `SubmittedForm` kullanılmaz). Okunan dosya 2 MB'tan büyükse reddedilir. Uzantı `.jpg/.jpeg/.png` ve içerik JPEG veya PNG olmalı; ikisi tutmazsa (içi PNG, adı `.jpg`) kabul edilir ve içeriğin uzantısıyla kaydedilir. 25 milyon pikselden büyük görsel `load()`'dan önce reddedilir. Kaydederken EXIF yönü uygulanır, kip çevrilir (JPEG → RGB, PNG → RGB/RGBA), uzun kenar ≤ 800 px, `exif`/`pnginfo`/`icc_profile` olmadan kaydedilir.
- **Gerekçe:** Güvenliği içerik kontrolü ve yeniden kaydetme sağlar; SPEC'teki "uzantısı değiştirilmiş dosya reddedilir" kuralının amacı resim olmayan dosyayı durdurmak, bu yine reddedilir. Uzantısı tutmayan gerçek fotoğrafı reddetmek kullanıcıyı şaşırtır, güvenlik kazandırmaz (Fatih, 2026-11-15). 800 px CV fotoğrafı için yeterli, disk ve PDF küçük kalır. Pillow'un kendi piksel sınırı 1–2 kat arasında sadece uyarı verir.
- **Alternatifler:** Uzantı ve içerik tutmazsa reddetmek. Orijinal boyutu saklamak.

### D-040 · Profil gücü kartı htmx olayıyla yenilenir
- **Karar:** Profili değiştiren her yanıt `HX-Trigger: profile-changed` gönderir; kart `hx-trigger="profile-changed from:body"` ile `GET /profile/strength`'i yeniden ister. Hesap `profile_service.profile_strength()`'te (M3'teki Documents kartı da kullanır).
- **Gerekçe:** Tek yerde değişiklik; her parça şablonuna kart eklemek gerekmez.
- **Alternatifler:** Her yanıta out-of-band (`hx-swap-oob`) kart eklemek.

### D-041 · Personal info kartı yeniden tasarımı M2b'de
- **Karar:** Fotoğraf Personal info kartına girdiği için kartın görünümü M2b'de yenilenir: ad + headline büyük, fotoğraf solda, dolu iletişim bilgileri tek satırda ikonlarla, boş alanlar yerine kısa ipucu, DACH bilgileri soluk satırda. Düzenleme formu değişmez. SPEC §6.4 görünümü tanımlamadığı için SPEC değişmez. Uygulama footer'ı ilk önce ayrı issue'ya (#25) bırakılmıştı; değişiklik tek satır çıkınca M2b'de yapıldı (linkler sağ altta, Fatih, 2026-11-15).
- **Gerekçe:** M2a manuel testinde kart uzun bulundu (10 satır, çoğu "Not added"); aynı kart, aynı test turu (Fatih, 2026-11-15).
- **Alternatifler:** Tasarımı ayrı bir işe bırakmak.

## M3 — CV oluşturucu (2026-11-15)

Kaynak: Plan fazı, Fatih onayı.

### D-042 · M3 ikiye bölündü
- **Karar:** M3a (CV belgeleri: model, `document_service`, Documents sayfası, editör + canlı HTML önizleme) ve M3b (PDF: 3 şablon × 3 dil, WeasyPrint, indirme), ayrı branch, PR ve etiketlerle: `m3a-cv-documents` / `v0.3a-m3a`, `m3b-cv-pdf` / `v0.3b-m3b`. Başvurular M4'te geldiği için M3a'daki kopyalar başvuruya bağlı değildir ("Not linked to an application" grubu); kilitleme servis fonksiyonu olarak gelir ve testlerle kanıtlanır, arayüzden M4'te tetiklenir. Sonraki milestone'ların butonları (Tailor to a job M7, Edit with assistant M12, Download PDF M3b) gösterilmez; Cover letters sekmesi "Coming soon" (M8).
- **Gerekçe:** Tek M3 (model + versiyon/kilit kuralları + liste + editör + canlı önizleme + 3 şablon × 3 dil PDF + Docker'da WeasyPrint) WORKFLOW'daki 1-2 haftalık sınırı aşar. PDF'in kendine has riskleri var (sistem kütüphaneleri, fontlar, sayfa düzeni). M3a tek başına SPEC'teki snapshot / master / kilit kabul kriterlerini karşılar.
- **Alternatifler:** Tek M3. Önce PDF, sonra editör (kuralsız belge olurdu). Kilit butonunu M3a'da göstermek (M4'teki akışla çakışır).

## M3a — CV belgeleri ve editör (2026-11-15)

Kaynak: Design fazı (`docs/plans/M3a.md`), `plan-reviewer`, Fatih onayı.

### D-043 · CV içeriği bir Pydantic şeması (`CVContent`)
- **Karar:** `CVDocument.content` JSON'u `app/schemas/cv.py`'deki `CVContent` ile okunur ve yazılır: `schema_version` (1), personal (ad, headline, e-posta, telefon, konum, linkler), summary, experience (bullet'lar `{text, selected}`), education, skills, languages, certifications, projects, `hidden_sections`. Snapshot = profilin o anki hali (`snapshot_from_profile`). Fotoğraf, doğum tarihi ve uyruk M3a'da snapshot'a girmez (M3b); `show_photo`/`show_birth_date` kolonları şimdiden gelir (SPEC §5.3). Alternatif: içeriği ilişkisel tablolara kopyalamak.

### D-044 · Önizleme ve PDF aynı şablonu kullanır
- **Karar:** SPEC §4 ağacına uygun olarak `app/templates/pdf/cv_classic.html` sadece CV'yi çizer; stili Tailwind değil düz CSS `app/static/css/cv_classic.css`, `.cv-paper` altında kapsamlı, satır içi `style=`/`<style>` yok (CSP). Editör önizlemesi bu şablonu parça olarak koyar; M3b'de WeasyPrint aynı şablonu ve CSS'i kullanır. CSS sadece editörde yüklenir: `base.html`'e `{% block head_extra %}`. Kâğıt koyu temada da beyaz (SPEC §6.0). Alternatif: `<iframe srcdoc>`.

### D-045 · Başvuru yokken versiyon kuralları
- **Karar:** Soy (lineage) = aynı `parent_id` (M3a'da hep o dilin master'ı). Master'ın `version`'ı 0. Yeni versiyon numarası **her yerde** `max(soydaki version) + 1`; unique index `(parent_id, version)` + `IntegrityError` olursa bir kez yeniden dene. New CV: o dilde master yoksa master; varsa master'ın soyunda yeni kopya (profilden snapshot). Save as new version / Duplicate as new version: içeriği kopyalayıp aynı soyda yeni taslak. Duplicate: aynı içerik, başlık "{başlık} (copy)", aynı soyda yeni taslak. M4'te `application_id` gelince soy (parent, application) olur; kolon M4'te (D-025 kalıbı).

### D-046 · M4'e kadar CV silme onay modalıyla
- **Karar:** M0'daki `modal` bileşeni, "Delete this CV? This can't be undone." SPEC §6.0/2 modalı hesap silmeye ayırır; Undo M4'te geldiği için geçici istisna (D-035'teki profil satırlarından farkı: bir belge çok daha büyük kayıp). M4'te Undo ile değişir.

### D-047 · Kopyası olan master silinemez
- **Karar:** `delete_cv` master'ın soyunda kopya varsa reddeder ("This master has {n} copies. Delete the copies first."). `parent_id` FK yine `ON DELETE SET NULL` (savunma için), ama kurala göre sahipsiz kopya oluşmaz.

### D-048 · Master sadece açık onayla düzenlenir
- **Karar:** Master editörde salt okunur açılır; "Edit master" + onay modalı → o sayfada form açılır (sunucu tarafında: master'a POST ancak formda `edit_master=confirmed` alanı varsa kabul edilir, yoksa 409). Refresh from profile master'da da aynı onayı ister. Mevcut kopyalar etkilenmez (test). CLAUDE.md'deki "never edit a master … in place" kuralına istisna eklenir: "…except when the user explicitly unlocks the master in the editor". SPEC §9.8 ile uyumlu; asistan (M12) master'ı asla değiştirmez.

### D-049 · M3a editör kapsamı
- **Karar:** Editörde belge adı, headline, summary, bölüm göster/gizle ve deneyim maddesi seçimi düzenlenir. Deneyim, eğitim vb. metinleri profilde düzenlenip Refresh from profile ile gelir veya M12'de asistanla. Alternatif: tüm snapshot'ı düzenlenebilir yapmak (büyük form, M3a'yı şişirir).

### D-050 · M3a'da Duplicate ve "All documents" yok, değişmemiş taslak yeni versiyon olmaz
- **Karar:** M3a editöründe "Duplicate" butonu ve "All documents" linki gösterilmez; M4'te gelir (Duplicate = başka bir başvuru için kopya, "All documents" = editör bir başvurudan açıldığında). Servisteki `duplicate()` kalır, başlıkta "(copy)" bir kez yazılır. Hiç düzenlenmemiş bir taslaktan (oluşturulduğundan beri `updated_at == created_at`) "Save as new version" yeni versiyon oluşturmaz ve "Nothing changed since this version…" der; kilitli belgeden "Duplicate as new version" her zaman çalışır. Editör eylemleri ikon butonları (ipucu + ekran okuyucu adı); Documents listesinde her taslak satırında onaylı Delete. İkon butonlarının adı hover ve klavye odağında hemen görünür (`data-tooltip`, input.css). Sayfa genişliği tek yerde (`page_width`: 1240 px, editör 1600 px); sayfalar sola yaslı kalır (ortalama denendi, Fatih beğenmedi; sağ taraf ileride doldurulabilir).
- **Gerekçe:** Fatih'in elle testi (2026-11-15): aynı içerikle v8…v13 ve "(copy) (copy) (copy)" oluştu; başvuru yokken iki buton aynı işi yapıyordu; geniş ekranda sağ taraf boş kalıyordu; tarayıcının `title` ipucu geç çıkıyordu.
- **Alternatifler:** İki butonu da göstermek. Değişmemiş taslağı içerik karşılaştırmasıyla bulmak (kopyanın kaynağı kayıtlı olmadığı için güvenilmez). Sayfaları tam genişlikte göstermek (uzun satırlar okunaksız) veya ortalamak.

## M3b — CV PDF (2026-11-15)

Kaynak: Design fazı (`docs/plans/M3b.md`), `plan-reviewer`, Fatih onayı.

### D-051 · WeasyPrint ve sistem kütüphaneleri
- **Karar:** `uv add weasyprint` (sürüm `uv.lock`'ta). Dockerfile runtime aşamasına `apt-get install --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`, sonra `/var/lib/apt/lists` silinir (D-014'ün uygulanması); CI'da `apt-get update` ve aynı paketler. Lokal: `brew install pango` (README). Apple Silicon'da Homebrew kütüphaneleri `/opt/homebrew/lib`'de ve sistem orada aramıyor; `DYLD_FALLBACK_LIBRARY_PATH`'i kabukta vermek işe yaramıyor (macOS, imzalı `uv`'ı başlatırken `DYLD_*` değişkenlerini siliyor), bu yüzden `pdf_service`, WeasyPrint'i import etmeden hemen önce bu değişkeni kendi süreci için ayarlıyor (sadece macOS'ta, klasör varsa). PDF testleri Pango yoksa başarısız olur, atlanmaz (sessizce atlanan test güvence vermez). Docker imajında konteynerde gerçek bir PDF oluşur (CI `docker` job'u).

### D-052 · CV fotoğrafı belgenin içinde saklanır
- **Karar:** Fotoğraf CV'ye sadece kullanıcı o CV'de "Show photo"yu açtığında kopyalanır (veri minimizasyonu, SPEC §10.2): profil fotoğrafı 400 px'e küçültülür, saydamlık beyazla doldurulur ve her zaman JPEG, kalite 85 olarak base64 halinde `CVContent.personal.photo` alanına yazılır (~20–50 KB; test < 80 KB). Kutu kapatılınca fotoğraf içerikten silinir; "Refresh from profile" kutu açıksa güncel fotoğrafı alır. Kilitli CV gönderildiği fotoğrafı korur, kopyalar fotoğrafı içerikle taşır, yetim dosya olmaz, hesap silinince satırla gider. Profil fotoğrafı silinince taslaklar kendi kopyasını Refresh'e kadar korur. Sınırlar şemada: `photo` base64 deseni ve en fazla 200 KB metin, `photo_type: Literal["image/jpeg"]`; uymayan içerik reddedilir (test). Önizlemede `GET /cvs/{id}/photo` (sadece sahibi, `data:` URI yok, M13 CSP'si), PDF'te `data:` URI (D-059'un izin verdiği kaynak). Alternatifler: CV başına dosya kopyası; profil dosyasına referans (kilitli CV'nin fotoğrafı değişir); her snapshot'ta kopyalamak (gösterilmeyen fotoğrafı da saklar).

### D-053 · Doğum tarihi ve uyruk snapshot'ta, gösterim kutularla
- **Karar:** `CVContent.personal.date_of_birth`, `nationality`: fotoğraf gibi (D-052) sadece kutusu açıkken CV'de tutulur: "Show date of birth" veya ayrı "Show nationality" açılınca profilden kopyalanır, kapatılınca silinir, Refresh kutu açıksa güncel değeri alır; ikisi de varsayılan kapalı. (İlk karar "her snapshot'ta kopyala" idi; Build'de fotoğraf kuralıyla aynı yapıldı: gösterilmeyen kişisel veri saklanmaz ve eski CV'lerde Refresh gerekmez.) Uyruk kutusu için `CVDocument.show_nationality` kolonu (küçük migration `0006`). AI'a giden içerikten (M7+) fotoğraf, doğum tarihi ve uyruk çıkarılır (SPEC §9.1, §10.2); `CVContent.without_private()` şimdiden eklenir ve test edilir.

### D-054 · Tek CV gövdesi, şablon başına CSS (D-044'ü günceller)
- **Karar:** `pdf/_cv_body.html` CV'nin yapısını bir kez çizer; `pdf/cv_classic.html`, `cv_modern.html`, `cv_compact.html` (SPEC §4 ağacı) sadece şablon sınıfını verip gövdeyi çağırır. Stil `static/css/cv_classic.css`, `cv_modern.css`, `cv_compact.css` (`.cv-paper` altında). PDF için ek `cv_print.css`: `@page` A4 ve kenar boşlukları, gölge/min-height kapalı, `@font-face` ile `static/fonts` (Inter, Source Serif 4). Fontların latin-ext yarısı **ayrı bir aile adıyla** ("Inter Ext", "Source Serif 4 Ext") font listesinde ikinci sırada: uygulamadaki gibi tek aile + `unicode-range` WeasyPrint'te ğ ş İ harflerini yanlış şekillerle çizdi (metin katmanı doğruydu, görüntü değil; ilk PDF'e bakınca bulundu). Şablon CSS'leri font listelerini `--cv-sans` / `--cv-serif` değişkenlerinden alır, baskı CSS'i sadece bu değişkenleri değiştirir. Test: Türkçe harf varsa "Ext" fontu PDF'e gömülü. `cv-entry-head` (başlık + tarih aynı satırda) WeasyPrint'te de çalışan basit bir düzenle. Önizleme ve PDF aynı HTML'i kullanır.

### D-055 · Bölüm başlıkları ve küçük metinler `app/i18n/document_labels.py`'de
- **Karar:** dil → {Summary, Experience, Education, Skills, Languages, Certifications, Projects, "present", "Native", "Date of birth", "Nationality"}. DE: Profil, Berufserfahrung, Ausbildung, Kenntnisse, Sprachen, Zertifikate, Projekte, "heute", "Muttersprache", "Geburtsdatum", "Staatsangehörigkeit". TR: Özet, İş Deneyimi, Eğitim, Yetenekler, Diller, Sertifikalar, Projeler, "devam ediyor", "Ana dil", "Doğum tarihi", "Uyruk". Doğum tarihi biçimi: DE ve TR `01.03.1990`, EN `1 March 1990`; ay/yıl her dilde `03/2021`. Önizleme de bu başlıkları kullanır.

### D-056 · PDF'te linkler (#23)
- **Karar:** Sadece PDF'te `<a href>` olur, şema profildeki `check_url` (`app/schemas/profile.py`) ile tekrar kontrol edilir (yalnız `http`/`https`, aksi halde düz metin); önizlemede düz metin kalır (tıklayınca uygulamadan çıkılmasın). Linkler metin olarak da görünür (ATS URL'yi okur).

### D-057 · PDF indirme
- **Karar:** `GET /cvs/{id}/pdf` (sahibi; kilitli belge de indirilebilir). `Content-Type: application/pdf`, `Cache-Control: private, no-store`. Rate limit: slowapi `20/minute` (IP başına). Dosya adı CV'deki (snapshot) addan, kilitli CV gönderildiği adla iner: `CV_{Ad_Soyad}_{lang}.pdf` (şirket M4'te). ASCII yedek: önce ı→i, İ→I, ß→ss, ğ→g gibi küçük bir eşleme, sonra NFKD ve aksan silme; sadece `[A-Za-z0-9_-]`, boşluk `_`, en fazla 80 karakter; ad boşsa `CV_{lang}.pdf`; `filename*=UTF-8''` için `quote(safe="")` (RFC 6266). WeasyPrint hata verirse 503 ve küçük bir HTML hata sayfası ("We couldn't create the PDF. Please try again." + editöre dön linki); log'a sadece hata türü yazılır, CV içeriği asla. "Download PDF" bekleyen otomatik kaydetme bittikten sonra indirir (Adım 3'te tarayıcıda doğrulanır).

### D-058 · pypdf geliştirme bağımlılığı (şimdilik)
- **Karar:** Testlerde PDF'in metnini, sayfa sayısını, A4 boyutunu ve gömülü fontlarını okumak için `uv add --dev pypdf`. SPEC §3'te var; M9'da (CV içe aktarma) ana bağımlılığa taşınır.

### D-059 · WeasyPrint sadece izinli kaynakları okur
- **Karar:** `render_cv_pdf` kendi `url_fetcher`'ını verir: yalnız `data:image/jpeg;base64,…` (CV fotoğrafı) ve `app/static/css` ile `app/static/fonts` içinde kalan `file://` yolları (resolve sonrası kontrol); `http(s)://`, diğer `file://` yolları ve her şey reddedilir. Kullanıcı verisi bugün `src`/`url()`'e girmiyor; bu katman ileride bir hata olursa da sunucunun yerel dosya veya ağ okumasını engeller (SPEC §10.1, derinlemesine savunma). Test: `http://…` ve `file:///etc/passwd` reddedilir.

### D-060 · M4 üçe bölündü
Kaynak: Plan fazı, Fatih onayı (2026-11-16).
- **Karar:** M4a (Board: `Application` + `ApplicationEvent`, Add job, Kanban, liste, arama/filtre, arşiv, Toast + Undo altyapısı, `User.theme`), M4b (detay sayfası iskeleti, `next_step_service`, mülakat modalı, Needs attention + Snooze) ve M4c (CV'yi başvuruya bağlama, `applied`'da kilitleme + `ApplicationDocument`, M3'ten ertelenen Duplicate / "All documents" / PDF adında şirket, profil satırı ve CV silmede Undo). Ayrı branch, PR ve etiketlerle: `m4a-board` / `v0.4a-m4a`, `m4b-application-detail` / `v0.4b-m4b`, `m4c-application-documents` / `v0.4c-m4c`. Sonraki milestone'lara bağlı parçalar M4'te **gösterilmez** (pasif buton yok): uyum skoru rozeti, minimum skor filtresi ve Fit sıralaması M7'de; "Auto-fill with AI" M5'te; eklenti bilgi kutusu M6'da gelir.
- **Gerekçe:** Tek M4 (2 tablo, board, liste, filtreler, detay sayfası, next step kuralları, mülakat, kilitleme, Undo, tema ve 7 ertelenmiş iş) WORKFLOW'daki 1-2 haftalık sınırı aşar. Sıra bağımlılığı izler: Undo altyapısı M4a'da kurulur, M4b ve M4c kullanır; kilitleme için önce başvuru ve detay sayfası gerekir. Çalışmayan butonları göstermemek D-050'deki kuralın devamıdır.
- **Alternatifler:** Tek M4. İki parça (ikinci PR çok büyük olurdu). Sonraki özellikleri pasif göstermek (ölü butonlar).

### D-061 · M4a'da `Application` kolonları (D-025 kalıbı)
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** `user_id` (FK, index), `company` (≤200), `job_title` (≤200), `location` (≤200, nullable), `remote_type` (onsite/hybrid/remote/unknown), `job_url` (≤2000, nullable), `source`, `description_raw` (text, ≤50.000), `status`, `position`, `applied_at`, `archived`, `last_activity_at`, `created_at`, `updated_at`. **`last_activity_at`** kartta ve listede "Updated" olarak gösterilen ve varsayılan sıralamada kullanılan tarihtir: sadece ekleme, durum değişikliği, arşiv ve restore'da değişir, Undo eski değerine döndürür. `updated_at` bunun için kullanılamaz, çünkü bir sütunu yeniden numaralamak komşu kartların `updated_at`'ini de değiştirir (plan-reviewer #1). `applied_at` şimdilik UTC tarihidir (`User.timezone` M10/M11'de). `description_raw` boşsa `""`. `user_id` FK'si `ON DELETE CASCADE`. Enum'lar User.plan gibi düz metin + Python'da izinli değer listesi. Sonraki parçalarda eklenecekler (hepsi nullable veya `server_default`'lu): `employment_type` (M5), `next_action_*`, `interview_*`, `contact_*`, `notes`, `salary_text` (M4b); `cv_id` (M4c); `cover_letter_id` (M8); `canonical_url` (M6, §8.5; mevcut satırlar `job_url`'den doldurulur); `description_structured`, `language`, `posting_closes_at` (M5). `ApplicationEvent`: `application_id` (FK, cascade), `type`, `from_status`, `to_status`, `message`, `created_at`, `updated_at`. Event'ler sadece sahibi doğrulanmış bir başvuru üzerinden, `(id, application_id)` ile okunur; asla sadece id ile. Kullanıcı başına en fazla `MAX_APPLICATIONS_PER_USER = 2000` başvuru (arşivliler dahil; `MAX_CVS_PER_USER` kalıbı). Sınır önce sayıp sonra eklediği için aynı anda gelen isteklerle birkaç kayıt aşılabilir; `POST /applications` dakikada 30 ile sınırlı olduğu için kabul (security review M4a, B; #22'deki gibi).
- **Güncelleme (D-071, D-072):** "Bugün" artık `DEFAULT_TIMEZONE`'daki tarih (UTC değil); `posting_closes_at` M5 yerine M4b'de (Details formundan elle) gelir.

### D-062 · "Add job" formu (AI'sız)
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Alanlar: "Job link or posting text" (tek alan), Company (zorunlu), Role (zorunlu), Location, Work mode (Remote / Hybrid / Onsite / boş; prototipte yok, çalışma şekli filtresi için gerekli), Status (Saved / Applied). Tek alan kuralı: değer boşluksuz tek bir `http(s)://` adresiyse `job_url` olur (profildeki `check_url` ile doğrulanır), değilse `description_raw`. `source` adresten türetilir: linkedin.com → linkedin, stepstone.* → stepstone, karriere.at → karriere_at, indeed.* → indeed, xing.com → xing, başka adres → company_site, adres yoksa → manual. Host `urlsplit` ile okunur ve tam ya da nokta-sonek eşleşmesi aranır (`linkedin.com.evil.example` linkedin değildir); `stepstone` ve `indeed` için host'un bir etiketi tam olarak bu ad olmalıdır (`www.stepstone.at`, `at.indeed.com`). Kullanıcı kaynağı seçmez. Status = Applied ile eklenirse `applied_at` bugünün tarihi olur. Yeni kayıt sütunun en üstüne gelir. Hız sınırı: slowapi `30/minute`.

### D-063 · Sütun içi sıra
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** `position` sütun (durum) içinde 0'dan başlar, arşivliler dahil değil. Taşıma isteği: `PATCH /applications/{id}/move`, gövde `status` + `after_id` (bırakıldığı kartın üstündeki kartın id'si; en üste bırakıldıysa boş). Sunucu kartı hedef sütunun **tam** sırasında o karttan hemen sonraya koyar ve iki sütunu yeniden numaralar. Sıra numarası yerine komşu id'si gönderilir, çünkü filtre veya "ilk 20" açıkken ekrandaki sıra numarası tam sütundakiyle aynı değildir. Arşivli kartlar sütunun sonunda durur ve sıraya girmez; tarayıcı `after_id` olarak en yakın arşivli olmayan üst kartı yollar. `after_id == id` ise hiçbir şey değişmez. `after_id` başka kullanıcınınsa 404; kullanıcının ama artık o sütunda olmayan bir kartıysa (eski ekran) kart sütunun üstüne konur ve bölge yeniden çizilir. `status` sadece `BOARD_STATUSES`'tan olabilir (422).

### D-064 · Undo: imzalı, kısa ömürlü token; event'i "sahiplenme" kuralı
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Geri alınabilen her işlem (durum değişikliği, arşiv, restore, ekleme) `ApplicationEvent` oluşturur. Yanıt, `itsdangerous` ile imzalanmış bir token taşır: `{user_id, application_id, event_ids: [...], before: {...}}`, ömür 60 sn (toast 5 sn; yavaş ağ payı). `before` izinli alanlardan oluşan genel bir sözlüktür (M4a: `status`, `index`, `applied_at`, `archived`, `last_activity_at`); `event_ids` bir listedir. Böylece M4b (bir taşıma birden çok event ve `next_action_*` yazar) ve M4c (kilit + `ApplicationDocument`) aynı yapıyı genişletir. `POST /applications/undo` (CSRF'li) tek bir transaction'da: (1) imza, süre, `user_id == oturum`; (2) `get_owned_application`; (3) token'daki son event hâlâ o başvurunun **en büyük id'li** event'i mi; (4) event'ler `DELETE … WHERE id IN (…) AND application_id = :id` ile silinir ve silinen satır sayısı beklenenle aynıysa devam edilir (aynı anda gelen iki Undo'dan sadece biri geçer); (5) alanlar geri yazılır, kart eski sütununa `move` ile aynı "index'e ekle (sınırlanmış) + yeniden numarala" koduyla konur (ham `position` yazılmaz; arada sıra değişmiş olabilir). Eklemenin Undo'su başvuruyu ve `created` event'ini siler. Token ikinci kez kullanılırsa veya arada event yazan başka bir değişiklik olduysa hiçbir şey değişmez, toast "Nothing to undo." Tablo gerekmez. Bir Undo'dan sonra bir önceki işlemin event'i yeniden en yeni olur; o token 60 sn içindeyse çalışır ve zinciri bir adım daha geri alır. Bu bilerek kabul edildi (çok adımlı Undo; security review M4a, C). Token imzalıdır ama gizli değildir (sadece base64): kullanıcı kendi id'lerini okuyabilir, başka bir şey taşımaz. Alternatifler: `PendingUndo` tablosu (temizlik işi ister); adreste "önceki durum" taşımak (imzasız, event'i geri alamaz).

### D-065 · Sunucudan toast
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** htmx yanıtlarında `HX-Trigger: {"toast": {"message": …, "undo_url": …, "undo_token": …}}` (Undo yoksa son ikisi boş); başlık `json.dumps` ile ASCII'ye kaçırılarak yazılır ("Şişecam" başlıkta hata vermesin; test). `app.js` bu olayı dinler ve M0'daki toast bileşenini gösterir (mesaj `x-text` ile), Undo tıklanınca token'ı `undo_url`'ye yollar. Adres yanıtta geldiği için M4c'deki profil ve CV silme Undo'su aynı bileşeni kullanır. Değişiklik yapan yanıtlar ayrıca `applications-changed` olayını tetikler; bölge kendini o anki filtrelerle yeniden ister (D-040 kalıbı). Metinler prototipten: "{Company} moved to {Status}.", "{Company} saved to {Status}.", "{Company} archived.", "{Company} restored.", Undo sonrası "Undone." Aynı sütun içinde sadece sıra değiştirmek event ve toast üretmez (kart geri sürüklenir; §6.0/2 listesinde de yok). SPEC §12 › M4a'daki "sıra" kelimesi Adım 0'da çıkarılır.

### D-066 · M4a'da arşivleme liste satırından
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Liste satırında "Archive", arşivli satır ve kartta "Restore". `POST /applications/{id}/archive`, `/restore`; event `archived` / `restored`, toast + Undo. Archive ve Restore gerçek `<button>`'dır. Arşivli başvuru sürüklenemez, özet sayılarına girmez; Restore eski sütununun en üstüne koyar.

### D-067 · Arama Python'da, filtreler SQL'de
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Kaynak, çalışma şekli, durum ve arşiv filtreleri SQL'de (`user_id` ile birlikte). Arama metni (şirket, pozisyon, konum) Python'da tek bir `search_key()` ile karşılaştırılır: D-057'deki eşleme (ı→i, İ→I, ß→ss…) + NFKD + aksan silme + `casefold()`, hem aranan metne hem alanlara uygulanır. Yalnız `casefold()` yetmez ("İstanbul" → "i" + birleşen nokta; "istanbul" bulmaz). Aynı anahtar Company sıralamasında da kullanılır. SQL'de yapılmamasının nedeni: SQLite'ın `lower()`'ı sadece ASCII'dir ve iki veritabanında aynı sonucu istiyoruz. Sınırlar: `q` en fazla 100 karakter, `page` 1…son sayfa arasına çekilir, kullanıcı başına en fazla 2.000 başvuru (D-061); liste sıralaması ve sayfalama da aynı listede yapılır, `description_raw` yüklenmez.

### D-068 · Tema `User.theme`'de (D-007'yi günceller)
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** `User.theme`, SPEC §5.1'deki gibi NOT NULL, varsayılan `system` (`server_default`). Giriş yapmış sayfalarda `user.theme`, girişsiz sayfalarda cookie, o da yoksa `system`. Seçici, giriş yapılmışsa `POST /settings/theme` (CSRF) ile kaydeder; kaydetme adresi sadece `current_user` varken sayfaya yazılır (girişsiz sayfada seçici sadece cookie yazar, login'e yönlenmez; test). Cookie her durumda yazılmaya devam eder ve girişte sunucu tarafından `user.theme` ile eşitlenir (Path=/, 1 yıl, SameSite=Lax, HttpOnly değil, `Secure` yok: D-019). Mevcut hesaplar `system` ile başlar (henüz gerçek kullanıcı yok). Alternatif: nullable kolon + cookie'ye düşme (SPEC'ten sapar, bir kural daha).

### D-069 · M4a'da durum klavyeyle değişmez (bilinen eksik)
Kaynak: M4a Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Board'da durum sadece sürükleyerek değişir (SPEC §6.7). Klavye yolu M4b'deki durum seçiciyle gelir; M4b'nin kabul kriterlerine "durum klavyeyle değiştirilebiliyor" eklenir (SPEC §12 ve #33, Adım 0). M13'ten önce canlıya çıkış yok.

### D-070 · Özet kartları sadece liste görünümünde
Kaynak: M4a Build, Fatih'in geri bildirimi (2026-11-16).
- **Karar:** Applications sayfasındaki 5 özet kartı (durum + sayı + kısa açıklama) board görünümünde gösterilmez, sadece liste görünümünün üstünde durur. Duruma göre filtre (karta tıklama) da liste görünümüne aittir. Board'da sütun başlığı durumu ve sayıyı verir.
- **Gerekçe:** Board'da kartlar ve sütun başlıkları aynı sırada aynı bilgiyi iki kez gösteriyordu ("Saved 1" üstte, "Saved 1" altta). Board'u duruma göre filtrelemek de anlamsız: sütunlar zaten duruma göre gruplar, diğer dört sütun boş kalırdı. Liste görünümünde sütun olmadığı için sayıları ve durum filtresini kartlar taşır.
- **Alternatifler:** Prototipteki gibi ikisini de göstermek. Kartları sütun başlığıyla birleştirmek (liste görünümünde yine ayrı kart gerekir).

### D-071 · Tarih ve saatler `DEFAULT_TIMEZONE`'da
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Veritabanında her zaman UTC. Kullanıcıya gösterilen ve kullanıcının girdiği tarih/saatler `settings.default_timezone` (Europe/Vienna) ile çevrilir (`zoneinfo`). "Bugün" (Needs attention, `applied_at`) bu saat dilimindeki tarihtir; D-061'deki "UTC tarihi" bununla değişir. `User.timezone` M10/M11'de gelince tek bir fonksiyon (`app/services/clock.py`: `today()`, `local(dt)`, `to_utc(date, time)`) kullanıcıya bakar. Docker imajında `zoneinfo` verisi yoksa `tzdata` paketi eklenir (Adım 1'de CI docker job'u kontrol eder; gerekirse D-071'e yazılır). Yaz/kış saati: girilen saat iki kez yaşanıyorsa (Ekim) ilki (`fold=0`), hiç yaşanmıyorsa (Mart, 02:30) UTC'ye çevrilip geri çevrilerek bir saat ileri kayar; ikisi de test edilir. D-061'e "D-071 ile güncellendi" notu düşülür.
- **Güncelleme (D-109, M11b):** Tamamlandı: `clock.tz()` artık kullanıcının saat dilimini kullanır (istekte session'dan, zamanlayıcıda kullanıcı başına).

### D-072 · M4b kolonları (D-025 kalıbı)
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** `Application`'a: `next_action_type` (apply_by/follow_up/interview/thank_you/reply_offer/custom, nullable), `next_action_at` (datetime, nullable), `next_action_note` (≤200, nullable), `next_action_manual` (bool, `server_default false`), `interview_at` (datetime, nullable), `interview_type` (video/phone/onsite, nullable), `interview_with` (≤200, nullable), `posting_closes_at` (date, nullable; SPEC'te M5'te AI'dan dolar, M4b'de Details formundan; D-061'deki "M5" notu güncellenir), `contact_name` (≤200), `contact_email` (≤320), `salary_text` (≤200), `notes` (text, ≤5.000), `snoozed_until` (datetime, nullable; SPEC'te yok, D-074). `ApplicationEvent.message` 200'den `Text`'e (≤2.000) çıkar: notlar timeline'da tam görünür. Hepsi tek migration `0009`; downgrade `message`'ı 200 karaktere keser (Postgres uzun satırı kısa kolona sığdıramaz), bu bilerek kabul edilir.

### D-073 · Next step hesaplanır; sadece elle konan saklanır
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** `app/services/next_step_service.py` saf fonksiyonlarla (veritabanına dokunmaz) `next_step(application, now) -> NextStep | None` döner (tür, tarih, kısa metin, gerekçe, otomatik mi). Kurallar (SPEC §6.13):
  - Saved → **Apply by**: `posting_closes_at` − 1 gün; yoksa eklemeden 7 gün sonra (bu ikincisi Needs attention'da "Waiting").
  - Applied → **Follow up**: `applied_at` + 10 gün.
  - Interview → **Interview** (`interview_at`; tarih yoksa tarihsiz "Add the interview date"); mülakat geçince → **Send thank-you note** (aynı gün). Saati olmayan (tüm gün) mülakat o günün sonunda geçmiş sayılır, thank-you ertesi gün görünür.
  - Offer → **Reply to offer** (tarihsiz: "Add the deadline from the offer letter."; tarihi "Change" ile kullanıcı girer).
  - Rejected ve arşivli → yok.
  - **Elle konan** (`next_action_manual = true`): saklanan tür/tarih/not kullanılır (kural 3). `next_action_type` boşsa "yok" demektir (Done sonrası).
  - **Change:** tür (SPEC'teki 6 tür) + tarih (+ Custom için not) → manual true, event `next_step_changed`.
  - **Done:** event `next_step_changed` ("Done: Follow up"). Thank-you Done → elle konmuş **Follow up, tıklamadan 5 gün sonra** (manual true). Diğer her Done (mülakat saatinden önce Interview Done dahil) → manual true, tür boş: durum değişene kadar next step yok. Böylece sonsuz döngü yok (follow-up Done → yok).
  - **Temizleme:** Durum değişince `next_action_*` ve `snoozed_until` temizlenir (manual false), kurallar yeniden işler. Yeni mülakat kaydetmek de temizler (yeniden planlama). Arşiv ve restore temizlemez: arşivliyken next step gösterilmez, restore'dan sonra eski elle konan adım geri gelir. Interview'dan çıkıp geri dönünce eski `interview_at` kalır ve modal onunla dolu açılır.
  - **Neden hesaplanıyor:** "mülakat saati geçti" gibi zamanla değişen kurallar için zamanlanmış iş gerekmez ve gösterilen hep doğru. SPEC §5.5'teki `next_action_*` notu buna göre güncellenir ("elle konan next step; otomatik olan hesaplanır").
  - **M11 için:** zamanlayıcı önce SQL'de daraltır (durum, `applied_at`, `interview_at`, `next_action_at` tarih aralığı), sonra aynı `next_step()`'i çağırır; bildirim tekrarını `(application_id, type, at)` anahtarı önler. **Uygulandı:** M11a, D-099 ve D-100 (anahtar adımın gününü içerir, 7 günlük pencere).

### D-074 · Snooze `snoozed_until` kolonunda, event yazmaz
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** "Snooze" `snoozed_until = now + 3 gün` yazar; o tarihe kadar başvuru Needs attention'da görünmez. Durum veya next step değişince temizlenir. Timeline'a event yazmaz (gürültü). Toast: "{Company} snoozed for 3 days." + Undo.

### D-075 · Details formu
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** `GET/POST /applications/{id}/details` (htmx, profil formlarındaki kalıp), alanlar yukarıdaki gibi, `job_url` M4a'daki gibi sadece http(s). Kaydedince event yazmaz, toast "Details saved." (Undo yok; form zaten iptal edilebilir).

### D-076 · Durum seçici ve mülakat modalı
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Detay sayfasında `<select>` (Saved/Applied/Interview/Offer/Rejected/Archived), klavyeyle kullanılır (D-069'u kapatır). Seçim değişince `POST /applications/{id}/status`: board durumu ise M4a'daki `move` (yeni sütunun en üstü), "Archived" ise `archive`, arşivliden bir duruma geçiş `restore` + `move`. Toast + Undo. **Interview'a geçiş** (board'da sürükleme veya seçici) önce durumu değiştirir, sonra yanıttaki `HX-Trigger: {"open-interview": id}` mülakat modalını açar (tarih, saat, tür, kiminle). "Save interview" → `POST /applications/{id}/interview` (event `interview_scheduled`, toast + Undo), "Skip, I will add it later" sadece kapatır: durum zaten değişmiştir (SPEC kabul kriteri). Detay sayfasında next step kartındaki "Add interview details" aynı modalı açar. Detay sayfasındaki değişiklikler ve Undo `HX-Trigger`'a `application-changed` da ekler; detay sayfasının başlığı, next step kartı ve timeline'ı bu olayla kendini sunucudan yeniler (board'daki `applications-changed` kalıbı).

### D-077 · Needs attention
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Ana sayfada board ve listenin üstünde, sadece maddesi varsa. Madde = next step tarihi bugün veya geçmişte olan, arşivli olmayan, snooze'lanmamış başvuru. Etiket ve sıra (SPEC §6.3'ün yorumu): önce tarihli işler — **Overdue / Due today** (elle ya da kapanış tarihinden gelen apply_by, custom) ve **Offer** (reply_offer, kendi etiketi) → **Interview** (interview, thank_you) → **Follow up** → **Waiting** (sadece otomatik "eklemeden 7 gün sonra" apply_by: "saved 9 days ago, apply or archive?"); grup içinde eski tarih önce. Needs attention kutusu board'un yenilenen bölgesinin içindedir, değişikliklerden sonra kendini yeniler. İlk 3 + "Show all (n)" (aynı sayfada açılır, sunucu tarafında). Her maddede "Open" (detay sayfası; "Draft a follow-up" gibi AI eylemleri M5+) ve "Snooze". Python'da hesaplanır (en fazla 2.000 başvuru, D-061).

### D-078 · Detay sayfasında "‹ 1 of n ›"
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Aynı durum sütunundaki kartlar arasında (`column_of(user, …)`, `user_id` filtreli), board sırasıyla (arşivli başvuruda arşivliler arasında, en yeni önce). "1 of 10 in Saved".

### D-079 · Undo token v2 (D-064'ü genişletir, Adım 3'te)
Kaynak: M4b Design fazı, plan-reviewer, Fatih onayı (2026-11-16).
- **Karar:** Token `{u, a, e, n, f}`: `e` işlemin yazdığı event'ler (boş olabilir), `n` işlemden hemen sonraki en yeni event id'si, `f` işlemin değiştirdiği alanlar için `{alan: [önce, sonra]}` (izinli liste; Snooze sadece `snoozed_until` taşır). Undo şartları: imza, süre, kullanıcı, sahiplik; en yeni event hâlâ `n`; **alanlar hâlâ "sonra" değerinde** (karşılaştır-ve-yaz). Sonra `e` silinir (sayı tutmalı) ve sadece `f`'teki alanlar "önce"ye döner. Böylece event yazmayan bir işlemin token'ı tekrar oynatılamaz (ikinci seferde alanlar artık "sonra"da değil) ve araya giren bir değişiklik ezilmez. M4a token'ları (60 sn ömür) `n`/`f` taşımaz: `n` yoksa `max(e)`, `f` yoksa eski `before` yolu. `TOKEN_MAX_LENGTH` 4.000'e çıkar (en kötü durum: iki 200 karakterlik Türkçe metin, testle).

### D-080 · Bağlama
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** `CVDocument.application_id` (FK, nullable, `ON DELETE SET NULL`) ve `Application.cv_id` (FK, nullable, `ON DELETE SET NULL`). Bir başvurunun **bir** CV'si olur; bir kopya en fazla **bir** başvuruya aittir. Master hiçbir zaman bağlanmaz (sadece kopyalar gönderilir, SPEC §5.3). Detay sayfasında **Documents** kartı:
- **Güncelleme (plan-reviewer):** `CVDocument.application_id` = **soy**: bu başvuru için yapılmış kopyalar (birden çok versiyon olabilir). `Application.cv_id` = **şu anki** CV (bir tane). Bağlı bir kopyadan "Save as new version" / "Duplicate as new version" yeni taslağa `application_id`'yi aktarır; kilitli bağlı CV'den "Duplicate as new version" yeni taslağı şu anki `cv_id` yapar (gönderilmiş versiyon `ApplicationDocument`'ta kalır). "Duplicate" (başka başvuru için) `application_id` aktarmaz. `application.cv_id` üzerinde unique index. "{Company} · CV" başlığında şirket adı 200 karaktere sığacak şekilde kesilir.
- **Güncelleme (plan-reviewer):** Kopya oluşturmanın Undo'su kopyayı sadece hâlâ taslak, hiç düzenlenmemiş (`never_edited`) ve hâlâ bağlıysa siler; yoksa sadece bağı kaldırır. Bağlama/ayırma `document_linked` event'i yazar, böylece "add job"un Undo'su araya giren bir bağlamayı ezmez (`creation_fingerprint`'e `cv_id` de eklenir).
- **Güncelleme: D-157** (2026-11-21): mektup aynı kalıpla bağlanır (`CoverLetter.application_id`, `Application.cover_letter_id`).

### D-081 · Versiyon numarası master başına kalır
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** SPEC "aynı başvuru için 1, 2, 3" diyor; D-045'teki unique index `(parent_id, version)` master başına numaralıyor. Index'i değiştirmek bütün M3 kurallarını yeniden açar. Numara master başına kalır, ekranda başvurunun adıyla gösterilir ("Kranich Health · v3"). SPEC §5.3'e not düşülür.

### D-082 · Gönderilince kilitle
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** Bir başvuru **Saved'den** Applied, Interview veya Offer'a geçince (board, seçici, mülakat kaydı) bağlı taslak CV kilitlenir ve `ApplicationDocument(application_id, doc_type="cv", document_id, content_snapshot=cv.content, sent_at=now)` oluşur; timeline'a `document_linked` event'i ("CV v3 locked: this is what you sent"). Zaten gönderilmiş (Applied/Interview/Offer) bir başvuruya CV bağlamak da hemen kilitler. Undo kilidi açar ve `ApplicationDocument`'ı siler (token: `locked_cv_ids`, `sent_ids`; Undo sadece CV hâlâ o başvuruya bağlıysa ve başka bir belge kaydı eklenmediyse çalışır). Kilit `document_service.lock` ile (kurallar tek yerde).
- **Güncelleme (plan-reviewer):** Başvuru Applied, Interview veya Offer'a **her geçişte** (nereden olursa olsun) bağlı CV **taslaksa** kilitlenir. Zaten gönderilmiş başvuruya bağlanan CV taslak kalır; "Lock as sent" butonu aynı kilitlemeyi elle yapar. Durum değişikliği, kilit, `ApplicationDocument` ve event **tek transaction**: `document_service.lock(..., commit=False)`.
- **Güncelleme (plan-reviewer):** Token `locked_cv_ids` + her birinin `locked_at`'i ve `sent_ids` taşır. Undo sadece hâlâ kilitli ve `locked_at`'i token'dakiyle aynı olan CV'lerin kilidini açar; `ApplicationDocument`'ları `user_id` + `application_id` + satır sayısı kontrolüyle siler. Kilit açma fonksiyonu sadece `document_service`'tedir ve sadece bu Undo'dan çağrılır. **CLAUDE.md'deki "kilitli versiyon asla düzenlenmez" kuralına bu tek istisna:** içerik değişmez, yanlışlıkla yapılan gönderim 60 sn içinde geri alınır. CLAUDE.md'yi Claude değiştirmez; Fatih'e not olarak bırakılır.
- **Güncelleme: D-159** (2026-11-21): bağlı mektup da aynı geçişte, aynı transaction'da kilitlenir; Undo istisnası mektup için de geçerli. Fatih kuralı CLAUDE.md'ye istisnayla yazdırdı (2026-11-21).

### D-083 · `ApplicationDocument` tablosu
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** SPEC §5.4b alanları + `user_id` (her sorgu `user_id` ile filtrelenir), `document_id` `ON DELETE SET NULL` (kilitli belge silinemez ama savunma). `content_snapshot` JSON. Kapak mektubu M8'de aynı tabloyu kullanır (`doc_type`).
- **Güncelleme (plan-reviewer):** `ApplicationDocument.application_id` `ON DELETE CASCADE`. **D-085:** Kilitli CV'nin PDF adında şirketin bugünkü adı kullanılır (kabul).
- **Güncelleme: D-159** (2026-11-21): mektup için yeni `cover_letter_id` kolonu (FK → coverletter, `SET NULL`); `document_id` CV'ye bağlı kalır.

### D-084 · Editör ve Documents sayfası
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** Editör `?application={id}` ile açılırsa (sahipliği kontrol edilir; değilse yok sayılır) geri link "← {Company}", ayrıca "All documents" linki ve bilgi bandı "Copy for {Company}, based on {Master}. Your master CV stays unchanged." Editörde **Duplicate** geri gelir (D-050): başvuruya bağlı olmayan yeni taslak "… (copy)". Documents sayfasında kopyalar başvuruya göre gruplanır ("{Company} · {Role}"), bağlı olmayanlar "Not linked to an application" grubunda.
- **Güncelleme (plan-reviewer):** Başvuru `cv.application_id`'den alınır; `?application=` sadece onunla aynıysa kabul edilir. Versiyon seçici: aynı başvurunun versiyonları + master.

### D-085 · PDF adında şirket (D-057'yi tamamlar)
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** Bağlı CV'nin adı `CV_{Ad}_{Şirket}_{lang}.pdf`; aynı ASCII + `filename*` kuralları, şirket adı en fazla 40 karakter.

### D-086 · Silmede Undo: `DeletedItem` tablosu
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** Silinen satırın tüm kolonları JSON olarak `DeletedItem(user_id, kind, item_id, payload, deleted_at)`'e yazılır, sonra satır silinir. Undo aynı id ile geri ekler (`kind`: cv, experience, education, skill, language, certification, project). Token sadece `DeletedItem` id'sini taşır; Undo sahipliği (`user_id`) ve türü kontrol eder, bir kez çalışır. Silinen veri 1 saat sonra temizlenir (her yeni silmede eskiler silinir; hesap silinince cascade). CV'nin bağlı olduğu başvuru `cv_id`'si de geri bağlanır. Alternatif: `deleted_at` ile yumuşak silme (bütün sorgulara filtre gerekir, bir tanesi unutulursa silinen veri görünür); token'da içeriği taşımak (CV fotoğrafıyla çok büyük). CV silmedeki onay modalı kalkar (D-046 kapanır). Editörden silince Documents'a gidilir ve toast orada görünür: oturumda tek seferlik bir "flash" (sadece mesaj + küçük token).
- **Güncelleme (plan-reviewer):** Satır **yeni bir id** ile geri eklenir (SQLite'ın id'yi yeniden kullanma sorunu yok). Profil satırı: aynı alanlar ve sıra, bölüm sınırı (`MAX_ITEMS_PER_SECTION`) yeniden kontrol edilir. CV: master ise o dilde başka master varsa "Nothing to undo"; kopya ise master'ı hâlâ yoksa "Nothing to undo"; versiyon numarası boşsa eskisi, değilse `max+1`; `MAX_CVS_PER_USER` kontrolü; her şey `IntegrityError` → "Nothing to undo". Başvuru bağı sadece `application.cv_id` hâlâ boşsa ve başvuru hâlâ sahibinin ise geri kurulur.
- **Güncelleme (plan-reviewer):** Silinen veri `UNDO_SECONDS` + 60 sn (2 dakika) tutulur; her silme, geri yükleme ve Undo isteğinde eskiler temizlenir; `user_id` FK `ON DELETE CASCADE`. Ayrı token tuzu (`undo-delete`), giriş + CSRF, `DeletedItem` `user_id` + `kind` ile aranır. Flash toast'ta başlık 60 karaktere kesilir, flash sadece tam sayfa GET'te okunur.
- **Güncelleme: D-152** (M8a): `kind` listesine `cover_letter` eklendi; geri yükleme kuralları CV ile aynı (`lineage.py`, D-153).

### D-087 · Gece yetkisi
Kaynak: Fatih, 2026-11-17 (sohbet).
- **Karar:** Fatih 2026-11-17 gecesi ürün kararlarını, PR merge'ünü ve etiketi Claude'a bıraktı ("bana ihtiyacın olmayacak şekilde"; M4c ve sonraki AI'sız milestone'lar, D-088). WORKFLOW'daki "Fatih merge eder" ve "kabul kriterlerini Fatih elle dener" maddeleri bu milestone için ertelendi: elle test sabah yapılır, gerekirse düzeltme PR'ı açılır.

### D-088 · AI'ya bağlı milestone'lar sonraya (sıra değişikliği)
Kaynak: Fatih, 2026-11-17 (sohbet: "ai ile olan adımı atlayıp devam etsen?").
- **Karar:** M4c'den sonra AI'ya bağlı milestone'lar (M5, M7, M8, M9, M12a, M12b) gerçek API anahtarı, maliyet ve prompt kararları gerektirdiği için Fatih dönene kadar atlanır; AI'sız olanlar (M10 Learning, M11 Bildirimler, M13'ün deploy dışı parçaları) önce yapılır. M6 (eklenti) Chrome'da elle kurulup denenmesi gerektiği için Fatih'le yapılır. CLAUDE.md'deki "sırayla" kuralına bu bir istisnadır.
- **Gerekçe:** Fatih yokken ilerlenebilecek işler bunlar; AI özellikleri gerçek çağrı ve kalite değerlendirmesi olmadan "bitti" sayılamaz.
- **Alternatifler:** Sırayı korumak ve M5'i `FakeAIClient` ile yazıp PR'ı açık bırakmak.
- **Güncelleme: D-150** (2026-11-20): AI milestone'ları V2'ye ertelendi; M8 ve M9'un AI'sız kısımları M8a, M8b, M9a olarak MVP'de.

### D-089 · M10'da olmayanlar
Kaynak: M10 Design fazı, plan-reviewer; Fatih onayladı (2026-11-17).
- **Karar:** "Add to learning plan" modalı ve "eksikten oluşturulan hedef başvuruya bağlı" kabul kriteri fit analizine bağlı olduğu için M7'ye taşınır; `source`, `source_application_id`, `source_requirement` kolonları M10'da eklenir. "Next reminder" kartı ve "Change reminder" M11'de (D-060: çalışmayan şey gösterilmez). Catch-up'taki "Ask the assistant to reschedule" M12'de. StudyLog'u elle düzeltme M10'da yok (issue). Tarihler `DEFAULT_TIMEZONE`'da; `User.timezone` M11'de (D-025).
- **Gerekçe:** Bu parçalar henüz olmayan özelliklere (fit analizi, zamanlayıcı, asistan) dayanıyor.
- **Alternatifler:** M10'u M7 bitene kadar açık bırakmak.

### D-090 · Dinlenme günleri `NotificationPreference.rest_days`'te
Kaynak: M10 Design fazı; Fatih plan onayıyla onayladı (2026-11-17).
- **Karar:** SPEC §5.11'deki tablo M10'da sadece `user_id` (unique) ve `rest_days` (varsayılan `["sat","sun"]`) ile açılır; kalan kolonlar M11'de (D-025 kalıbı). Streak kartındaki not seçilen günlerden yazılır, yanında "Change" linki küçük bir modal açar (7 gün kutusu, en fazla 6 gün). M11'de aynı ayar Settings › Notifications'a da gelir.
- **Alternatifler:** Ayarı M11'e bırakıp M10'da sabit Cumartesi-Pazar kullanmak.

### D-091 · Seri her istekte hesaplanır
Kaynak: M10 Design fazı, plan-reviewer; Fatih onayladı (2026-11-17). SPEC §5.10'daki "günlük önbellek" cümlesini değiştirir.
- **Karar:** Son 400 günün tamamlanan görevleri (`done_at >= since`) ve StudyLog'ları okunur, seri Python'da sayılır. Aktif gün: en az bir görev tamamlanmış veya StudyLog toplamı en az 10 dakika. Bugünden geriye: aktif gün +1 (dinlenme günü dahil); aktif olmayan dinlenme günü atlanır; aktif olmayan normal gün seriyi bitirir; bugün henüz aktif değilse seri dünden başlar.
- **Gerekçe:** Kullanıcı başına veri küçük; önbellek, görev işaretlenince ayrıca güncellenmesi gereken ikinci bir doğruluk kaynağı olurdu. Gerekirse M11'in zamanlayıcısıyla eklenir.
- **Alternatifler:** SPEC'teki gibi günlük önbellek.

### D-092 · Görev tamamlama
Kaynak: M10 Design fazı; Fatih plan onayıyla onayladı (2026-11-17).
- **Karar:** İşaretlenince `done=true`, `done_at=now`, görevin tahmini süresiyle bugüne bir `StudyLog`, göreve bağlı kaynak varsa `units_done += 1` (en fazla `units_total`). İşareti kaldırmak bunların tersini yapar. Toast + Undo (`undo-learning` tuzu, 60 sn, karşılaştır-ve-yaz). Haftalık saat: bu haftanın (Pzt–Paz) StudyLog dakikaları; hedefi: aktif hedeflerin `weekly_hours_target` toplamı. Üst kartlar, plan ve Goals kartı D-065 düzeniyle güncellenir: yanıt `learning-changed` olayını gönderir, `#learning-region` aynı sekmeyle kendini yeniden yükler (sayılar hep sunucudan).

### D-093 · "Create one task per week"
Kaynak: M10 Design fazı; Fatih plan onayıyla onayladı (2026-11-17).
- **Karar:** Kaynakta `units_total` varsa her birim için bir görev: "{unit_label} {n}", `due_date` = bugün + 7·(n−1), süre = haftalık saat × 60 (yoksa 60 dk, en fazla 600), kaynak bağlı. En fazla 52 görev.

### D-094 · Learning sınırları ve sahiplik
Kaynak: M10 Design fazı, plan-reviewer; Fatih plan onayıyla onayladı (2026-11-17).
- **Karar:** Kullanıcı başına en fazla 50 hedef ve 1.000 görev. Beş tablonun hepsinde `user_id` (FK cascade + index); her sorgu `user_id` ile filtrelenir. URL'ler sadece http(s), linkler `rel="noopener noreferrer"`. Alan sınırları: hedef başlığı 1–200, açıklama ≤ 2.000, `target_date` bugün … +5 yıl, `weekly_hours_target` 0,5–40, kaynak adı 1–200, sağlayıcı ≤ 200, URL ≤ 500, `kind` ve `unit_label` listeden, `units_total` 1–52, görev başlığı 1–200, `due_date` bugün −30 gün … +2 yıl, `estimated_minutes` 5–600, `rest_days` tekrarsız en fazla 6 gün.

### D-095 · Görev tarihini değiştirme ve silme
Kaynak: M10 Design fazı, plan-reviewer; Fatih onayladı (2026-11-17).
- **Karar:** Görev satırında "Reschedule" (tamamlanmış görevde yok) ve "Delete"; Goals kartında hedef için "Delete" (onay modalı: "This also deletes its tasks"). Silme D-086'daki `DeletedItem` düzeniyle yapılır (`kind`: `learning_goal`, `learning_task`); hedef payload'ı kaynaklarını ve görevlerini de taşır, Undo yeni id'lerle geri koyup birbirine bağlar. `StudyLog.goal_id` ve `task_id` `SET NULL`: silmek geçmiş saati ve seriyi değiştirmez; Undo logları payload'daki id'lerle (`user_id` filtresiyle) yeniden bağlar. Tam düzenleme ve hedef durumu değiştirme M10'da yok.
- **Gerekçe:** Catch-up bandı "you can reschedule them" diyor; yanlış hedef silinebilmeli.
- **Alternatifler:** Hiç düzenleme olmaması ve band metnini değiştirmek.
- **Güncelleme (D-116…D-119, #39):** Görevi ve hedefi düzenleme ile hedef durumu sonradan geldi.

### D-096 · Learning görünüm detayları
Kaynak: M10 Design fazı, plan-reviewer; Fatih plan onayıyla onayladı (2026-11-17).
- **Karar:** `progress_pct` saklanmaz: kaynakta `units_total` varsa `units_done/units_total`, yoksa tamamlanan/tüm görevler, görev yoksa 0. Hedef rengi saklanmaz, hedeflerin sırasına göre 6 renklik sabit paletten gelir. All tasks grupları: Overdue, This week (bugün … Pazar), Next week, Later; boş grup gösterilmez. Tamamlananlar her sekmenin altında daraltılmış "Done (n)" bölümünde: Today ve Next 7 days'te o tarih aralığındakiler, Catch-up'ta bugün tamamlanan eski görevler, All tasks'ta son 30 gün. Today: bugün; Next 7 days: bugün … +6; Catch-up: geçmiş ve açık.

### D-097 · Elle test uygulamanın sonunda
Kaynak: Fatih, 2026-11-18 (sohbet: "elle testi uygulama bitince yapıcam").
- **Karar:** M10'dan başlayarak Definition of Done'daki "Kabul kriterlerini Fatih elle denedi" maddesi ertelenir: Fatih tüm uygulamayı sonda bir kez elle dener. Bir milestone, testler, lint, CI ve security-reviewer temiz olunca merge edilir ve etiketlenir. Elle testte bulunan sorunlar düzeltme PR'ı olarak gelir. Her PR açıklamasında elle test listesi yine yazılır, sondaki test için toplanır.
- **Gerekçe:** Fatih'in zamanı; PR'ları açık bırakıp üst üste branch açmak birleştirmeyi zorlaştırır.
- **Alternatifler:** Her milestone sonunda elle test (WORKFLOW'daki kural); PR'ları açık bırakıp sonraki milestone'u onların üstüne kurmak.

### D-098 · M11 ikiye bölünür
Kaynak: Fatih, 2026-11-18 (M11 Plan fazı).
- **Karar:** M11a: Notification tablosu, zil ve panel, zamanlayıcı, iş arama bildirimleri (follow_up, interview_tomorrow, stale_saved), temizlik. M11b: Settings › Notifications, `User.timezone`, günlük çalışma hatırlatması (uygulama içi + e-posta), kapatma linki, `streak_milestone`, Learning'de "Next reminder". Aynı gün Fatih'in diğer kararları: kapatma linki giriş istemeyen bir sayfa + buton açar; zildeki sayı sayfa açılınca ve 2 dakikada bir güncellenir; "Streak and weekly goal" kapalıyken sadece kutlama bildirimleri gelmez ve hatırlatma metninde seri geçmez.
- **Gerekçe:** Tek M11, WORKFLOW'daki 1-2 haftalık sınırı aşma riski taşıyordu (D-024 gibi).
- **Alternatifler:** Tek M11.

### D-099 · Bildirimler `next_step()`'ten üretilir
Kaynak: M11a Design fazı, plan-reviewer; Fatih onayladı (2026-11-18). D-073'ü uygular.
- **Karar:** Kaynak, Needs attention'ın kullandığı `next_step_service.next_step()`. Elle konmuş adımlar ve "Done" dikkate alınır; bildirim ve Needs attention aynı günü söyler. Her yeni next step için en fazla bir bildirim (ör. thank-you "Done" olup yeni bir follow-up doğarsa bir kez daha).
- **Alternatifler:** Son hareketten (`last_activity_at`) saymak; başvuru başına bir kez.

### D-100 · Tekrar engelleme ve 7 günlük pencere
Kaynak: M11a Design fazı, plan-reviewer; Fatih plan onayıyla onayladı (2026-11-18).
- **Karar:** `Notification.dedupe_key` adımın gününü içerir (`follow_up:{id}:{yyyy-mm-dd}`, `stale_saved:…`, `interview:…`); `(user_id, dedupe_key)` unique. follow_up ve stale_saved sadece adımın günü son 7 gün içindeyse, interview_tomorrow sadece mülakat yarınsa üretilir; böylece temizlenen bir bildirim yeniden üretilmez. Ertelenmiş başvuru atlanır, erteleme bitince pencere içindeyse gelir. Arşivlenmiş başvurunun adımı, dolayısıyla bildirimi yok.
- **Alternatifler:** Anahtarı ayrı bir "gönderildi" tablosunda tutmak (bir tablo daha); anahtarda tarih olmadan (temizlikten sonra yeniden üretim).

### D-101 · Bildirim kuralları ve metinleri
Kaynak: M11a Design fazı; Fatih plan onayıyla onayladı (2026-11-18).
- **Karar:** follow_up: adım `follow_up`, durum `applied`; "Time to follow up" / "{Company} · no reply for {n} days" (n = bugün − başvuru günü). stale_saved: durum `saved`, adım "Waiting"; "Saved {n} days ago" / "{Company} · apply or archive?". interview_tomorrow: durum `interview`, `interview_at` yarın (next step'ten bağımsız: adımda "Done" hatırlatmayı susturmaz, test-writer notu); "Interview tomorrow, {HH:MM}" (tüm gün: saatsiz) / "{Company} · {Role}". Link `/applications/{id}`; `Notification.application_id` FK `ON DELETE CASCADE`. Metin üretim anında yazılır (başlık 200, metin 300 karakter). Tarihler `DEFAULT_TIMEZONE`'da (D-089).

### D-102 · Tercihler M11a'da: iş arama türleri
Kaynak: M11a Design fazı; Fatih plan onayıyla onayladı (2026-11-18).
- **Karar:** `NotificationPreference`'a `follow_up_enabled`, `interview_enabled`, `stale_saved_enabled` (`server_default` true). Üretici kapalı türü atlar; arayüz M11b'de. Diğer kolonlar kendi milestone'larında (D-025 kalıbı).

### D-103 · Zamanlayıcı
Kaynak: M11a Design fazı, plan-reviewer; Fatih plan onayıyla onayladı (2026-11-18).
- **Karar:** APScheduler 3.x `BackgroundScheduler`, FastAPI `lifespan` içinde sadece `SCHEDULER_ENABLED=true` ise; kapanışta `shutdown(wait=False)`. `timezone=DEFAULT_TIMEZONE`, `max_instances=1`, `coalesce=True`. Her 15 dakikada `generate_due(now)`, her gün 03:00'te `purge(now)`. Her iş kendi Session'ını açar, her kullanıcıdan sonra commit eder; `IntegrityError` → rollback, sonraki kullanıcı. Hata log'unda kişisel veri yok (user id ve iş adı). Mantık `now` alan saf fonksiyonlar; testler onları çağırır. Birden fazla worker/instance'ta sadece biri `SCHEDULER_ENABLED=true`.
- **Alternatifler:** Ayrı bir worker süreci (M13'te gerekirse); cron + CLI komutu.

### D-104 · Bildirim temizliği ve sınırları
Kaynak: M11a Design fazı; Fatih onayladı (2026-11-18).
- **Karar:** Okunmuş ve 30 günden eski bildirimler silinir. Kullanıcı başına en fazla 200; aşılırsa sadece okunmuşlar en eskiden silinir. Panel en yeni 20'yi gösterir. Hesap silinince cascade.
- **Alternatifler:** 200 sınırında okunmamışları da silmek (görülmemiş hatırlatma kaybolur).
- **Güncelleme (security-reviewer):** Okunmamış bildirimler de 90 gün sonra silinir; hiçbir şey süresiz saklanmaz (GDPR'da saklama sınırı). 200 sınırı yine sadece okunmuşları siler.

### D-105 · Zil ve panel
Kaynak: M11a Design fazı, plan-reviewer; Fatih plan onayıyla onayladı (2026-11-18).
- **Karar:** Rozet `GET /notifications/badge`, `hx-trigger="load, every 120s"` (0 gizli, 99'dan büyükse "99+"). Panel açılınca `GET /notifications/panel` ile yüklenir (Alpine dropdown). Satır düz bir `<form method="post">`: `POST /notifications/{id}/open` okundu yapar ve 303 ile `safe_redirect_path(link_url)`'e gönderir. "Mark all as read" htmx ile; rozet "notifications-changed" olayıyla yenilenir. "Notification settings" linki M11b'de (D-060).
- **Güncelleme (security-reviewer):** İlk plandaki "rozet isteği oturumu yeniler" kabulü kaldırıldı. Rozet ve panel GET yanıtları `Set-Cookie` göndermez (`app/main.py` `KeepSessionCookie`): arka plandaki bir istek, çıkıştan sonra eski oturumu geri yazamaz ve açık bir sekme oturumu süresiz uzatmaz. Girişsiz rozet isteği login'e yönlendirmez, 204 döner (form dolduran kullanıcının sayfası kaymaz). Veritabanı motoru `hide_parameters=True`: hata metinleri sorgu değerlerini (şirket adı gibi) içermez. M13 notları: çıkışta `session_version` artırmak; zamanlayıcıda SQL tarih aralığıyla daraltma (D-073).

### D-106 · M11a'da e-posta yok
Kaynak: M11a Design fazı; Fatih plan onayıyla onayladı (2026-11-18).
- **Karar:** follow_up, interview_tomorrow ve stale_saved SPEC §6.11'e göre sadece uygulama içi. E-posta M11b'de, sadece çalışma hatırlatması için.

### D-107 · M11b: Fatih'in kararları
Kaynak: M11b Design fazı, plan-reviewer soruları; Fatih onayladı (2026-11-18).
- **Karar:** Saat dilimi kayıt ve girişte tarayıcıdan algılanır, Settings'te değiştirilebilir. Hatırlatma saati yarım saatlik listeden (06:00–23:00, varsayılan 19:00). O gün için planlı ya da gecikmiş görev yoksa hatırlatma gelmez. Settings'te M11b'de sadece Notifications bölümü var (D-060). Dilim değişince tüm gün tarihler taşınır (D-108). Görev yokken Next reminder kartı "No tasks planned" + "Add task" gösterir. Seri ayarı kapalıyken Learning'deki Streak kartı kalır.
- **Alternatifler:** Serbest saat girişi; dinlenme günü dışında her gün hatırlatma; Settings'in tüm bölümlerini yer tutucuyla açmak.

### D-108 · `User.timezone`
Kaynak: M11b Design fazı, plan-reviewer B1/S12; Fatih onayladı (2026-11-18).
- **Karar:** `timezone` (str 64, nullable); boşsa `DEFAULT_TIMEZONE`. Geçerli değerler `zoneinfo.available_timezones()` (önbellekte). Giriş ve kayıt formlarındaki gizli alanı app.js `Intl` ile doldurur; kayıtta kaydedilir, girişte sadece boşsa. Dilim değişince "saatsiz" (eski dilimde 00:00) `interview_at` ve `next_action_at` aynı işlemde yeni dilimde aynı günün 00:00'ına taşınır; saatli değerler gerçek an olduğu için değişmez.
- **Alternatifler:** Zorunlu kolon ve mevcut kullanıcılar için Vienna yazmak; tüm gün tarihleri `date` kolonunda saklamak (büyük değişiklik).

### D-109 · `clock` kullanıcının saat dilimini kullanır
Kaynak: M11b Design fazı, plan-reviewer S1/S2/S5/S11 (2026-11-18). D-071'i tamamlar.
- **Karar:** `clock.tz()` bir `ContextVar`'dan okur (yoksa `DEFAULT_TIMEZONE`); çağıranlar değişmez. İstekte `UserTimezone` ASGI ara katmanı `session["tz"]`'i doğrulayıp koyar (SessionMiddleware'in içinde, `finally` ile reset); `current_user` farkı görürse session'ı günceller; başka cihazda değişen dilim bir sonraki istekten itibaren geçerli olur (senkron dependency'ler ayrı bir thread'de, kopyalanmış context'te çalışır, ContextVar'ı oradan route'a taşımak mümkün değil). Zamanlayıcıda her kullanıcı `clock.use_timezone(user.timezone)` ile; iş arama bildirimleri de kullanıcının diline geçer. Üretim işi `CronTrigger(minute="*/15")` (Claude karar verdi: hatırlatma tam saatinde gelsin). Kabul edilen sınırlamalar: dilim değişince M11a anahtarlarının günü kayabilir ve aynı adım için bir bildirim daha gelebilir.
- **Alternatifler:** Her fonksiyona `user` parametresi (60 çağrı yeri); her istekte veritabanından okumak (ara katman session'dan okur, `current_user` zaten okuyor).

### D-110 · Hatırlatma tercihleri
Kaynak: M11b Design fazı (2026-11-18). SPEC §5.11.
- **Karar:** `NotificationPreference`'a `study_reminder_enabled`, `study_reminder_time` (19:00), `study_reminder_email`, `streak_enabled`; hepsinde `server_default`. `ai_result_enabled` ilk AI sonucu bildirimiyle. `Notification.type`'a `study_reminder` ve `streak_milestone`.

### D-111 · Günlük çalışma hatırlatması
Kaynak: M11b Design fazı, plan-reviewer B3 (2026-11-18). SPEC §6.11.
- **Karar:** 15 dakikalık işte kullanıcının diliminde: hatırlatma açık, yerel saat ≥ seçilen saat, dinlenme günü değil, bugün görev tamamlanmamış, bugün ya da öncesine planlı açık görev var. Anahtar `study:{yerel gün}` (günde bir; ertesi güne taşmaz). Başlık "Time to study: {n} min", metin görevin başlığı (bugünün ilk açık görevi, yoksa en eski gecikmiş) ve seri açıksa ve ≥ 2 ise " · keep your {n}-day streak going". Hesap `now`'dan (`clock.today()` değil) ve satır oluşturmadan.

### D-112 · Hatırlatma e-postası ve kapatma linki
Kaynak: M11b Design fazı, plan-reviewer B2/S3/S4 (2026-11-18). D-098'i uygular.
- **Karar:** `study_reminder_email` açık ve e-posta doğrulanmışsa, bildirim satırı bu çalışmada yazılıp commit edildikten sonra düz metin e-posta (konu "Time to study"). Link `/reminders/off?token=…` (itsdangerous, tuz `study-reminder-off`, user id + e-posta, 90 gün). GET sadece onay sayfası (maskeli adres, buton); POST (CSRF, 10/dk) token'ı yeniden doğrular ve sadece `study_reminder_email`'i kapatır.
- **Alternatifler:** GET'te kapatmak (e-posta tarayıcılarının önizlemesi kapatırdı); hatırlatmanın tamamını kapatmak.
- **Güncelleme (security-reviewer, Claude karar verdi):** Sayfalar `Cache-Control: no-store` ile döner. E-postalar zamanlayıcının çalışmasının sonunda, en fazla 5'i aynı anda gönderilir; yavaş bir posta sunucusu uygulama içi hatırlatmaları geciktirmez. Kabul edilen risk: link 90 gün boyunca geri alınamaz; eline geçen biri sadece çalışma e-postalarını kapatabilir (veri sızmaz), kullanıcı Settings'ten yeniden açar.

### D-113 · Seri kutlaması
Kaynak: M11b Design fazı, plan-reviewer S8; **Claude karar verdi** (gece çalışması, 2026-11-18), Fatih'in onayını bekliyor.
- **Karar:** `streak_enabled` açıkken seri 7, 30 ya da 100 ve son aktif gün bugün ya da dünse "{n}-day streak" / "You studied {n} days in a row. Nice work." Anahtar `streak:{n}:{son aktif gün}`: gece yarısına yakın tamamlanan görevin kutlaması ertesi gün de gelir, tekrar etmez.
- **Alternatifler:** Sadece bugün aktifse (23:58'de tamamlanınca kaçar).

### D-114 · Settings › Notifications
Kaynak: M11b Design fazı, plan-reviewer S7/S10 (2026-11-18). SPEC §6.9.
- **Karar:** `/settings` sadece Notifications: Learning (hatırlatma, saat, e-posta, dinlenme günleri, saat dilimi, seri) ve Job search (follow-up, interview, stale saved). "AI results" M5'te; "Weekly email summary" "Coming soon". Tek htmx formu, alan yanında hatalar. **Claude karar verdi:** dilim değişmediyse form yeniden çizilir + toast; değiştiyse sayfa yeniden yüklenir ve toast yeni sayfada gösterilir (saatler yeni dilimle görünsün).

### D-115 · Next reminder kartı ve panel linki
Kaynak: M11b Design fazı, plan-reviewer S6 (2026-11-18). SPEC §6.10.
- **Karar:** Kart bir sonraki hatırlatmayı gösterir ("Today, 19:00", "Tomorrow, 19:00", "Mon, 19:00"; dinlenme günleri, bugün gitmiş hatırlatma ve açık görevler hesaba katılır, 14 gün ileri) + "Change reminder". Kapalıysa "Off" + "Turn on"; hiç açık görev yoksa "No tasks planned" + "Add task". Zil panelinin altında "Notification settings" linki.

### D-116 · Görevi düzenleme
Kaynak: #39 Design fazı, plan-reviewer; **Claude karar verdi** (gece çalışması, 2026-11-18), Fatih'in onayını bekliyor. D-095'i günceller.
- **Karar:** Görev menüsünde "Edit" → "Edit task" modalı. Açık görevde başlık, tarih, süre (D-094 sınırları; değişmeyen tarih aralık kontrolüne girmez, eski Catch-up görevleri kaydedilebilsin). Tamamlanmış görevde başlık ve "Time spent" (1–600 dk, D-117). Hedef değiştirilemez. POST formu veritabanındaki şu anki duruma göre seçer; görev arada açıldı/kapandıysa hiçbir şey değişmez ve doğru form mesajla gelir. Undo yok (sadece silmede).
- **Alternatifler:** Satır içinde düzenleme; görevi başka hedefe taşımak.

### D-117 · Çalışma süresini düzeltme
Kaynak: #39 Design fazı, plan-reviewer; **Claude karar verdi** (2026-11-18). D-089'u günceller; SPEC §5.10.
- **Karar:** "Time spent" görevin `StudyLog` satırını günceller (fazlası silinir); satır yoksa `goal_id`, `task_id` ve tamamlandığı yerel günle yazılır. "Done (n)" satırları kaydedilen süreyi gösterir. Görev geri açılınca log yine silinir; düzeltilen süre kaybolur (kabul). Seri değişmez (tamamlanmış görev günü zaten aktif yapar).
- **Alternatifler:** Ayrı bir "log" ekranı; 5–600 sınırı.

### D-118 · Hedefi düzenleme
Kaynak: #39 Design fazı, plan-reviewer; **Claude karar verdi** (2026-11-18). D-095'i günceller.
- **Karar:** Hedef kartında "Edit" → "Edit goal" modalı: başlık, hedef tarih, haftalık saat, durum (Active / Paused / Done). D-094 sınırları; değişmeyen geçmiş hedef tarihi kalabilir; tarih ve saat boşaltılabilir. Kaynak düzenlenmez.
- **Alternatifler:** Kaynağı da düzenlemek (sonraya).

### D-119 · Hedef durumunun etkisi
Kaynak: #39 Design fazı, plan-reviewer; **Claude karar verdi** (2026-11-18).
- **Karar:** "This week" hedefi sadece `active` hedefleri sayar (D-092, değişmez). Paused/Done hedef kartında rozet, kart solgun. Görevleri planda, hatırlatmada ve "Tasks done" sayısında kalır; hatırlatmadan çıkarmak ayrı bir issue (#46, M13 cilasında karar).
- **Güncelleme (D-122):** Paused/Done hedefin görevleri artık çalışma hatırlatmasını ve Next reminder kartını tetiklemez; planda ve "Tasks done" sayısında kalır.
- **Alternatifler:** Duraklatılmış hedefin görevlerini gizlemek.

### D-120 · Güvenlik header'ları ve `no-store`
Kaynak: #10 (M1a güvenlik incelemesi #7, #8); **Claude karar verdi** (gece çalışması, 2026-11-18), Fatih'in onayını bekliyor. SPEC §10.1.
- **Karar:** En dıştaki küçük bir ASGI ara katmanı (`SecurityHeaders`) her yanıta, route kendi koymadıysa: `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` (dış linklere adresimiz gitmez; token sayfaları `no-referrer` kalır). `/static/` dışındaki yanıtlara `Cache-Control: no-store` (çıkıştan sonra geri tuşu kişisel sayfayı göstermez). Şablonlarda (CV önizlemesinde tarayıcıda da görünen pdf/ şablonları dahil) inline script, `on…=` ve `style=""` olmadığı bir testle korunur. PDF indirmesi `attachment` olduğu için tarayıcının PDF görüntüleyicisi CSP'den etkilenmez. Sunucu hatası (500) yanıtı ara katmanların dışında üretilir ve header almaz (kabul). HSTS deploy'da.
- **Güncelleme (security-reviewer):** Profil ve CV fotoğrafları `private, no-store` (paylaşılan bilgisayarda çıkıştan sonra kopya kalmaz). Şablon testi `<style>`, Alpine `:style` ve `javascript:` linklerini de yakalar. 500 yanıtı için header eklemek kabul edilen sınırlama olarak kalır.
- **Güncelleme (D-125, M13a):** 500 yanıtı da header'ları alacak; kabul edilen sınırlama kapanıyor.
- **Alternatifler:** Header'ları route'larda tek tek koymak; ek paket.

### D-121 · Eski güvenlik incelemelerinden küçük düzeltmeler (#15, #16, #22)
Kaynak: M1b ve M2a security-reviewer bulguları; **Claude karar verdi** (gece çalışması, 2026-11-18).
- **Karar:** Forgot password, hesap yokken de aynı işi yapar (kaydedilmeyen bir yer tutucu kullanıcıyla token imzalanır, e-posta oluşturulur, sınırlayıcı sayılır; gönderilmez). Şifre sıfırlama tek bir koşullu UPDATE'tir (`WHERE password_hash = eski`); aynı linkle aynı anda gelen ikinci istek "geçersiz link" görür. Profil bölümüne satır eklerken profil satırı `SELECT … FOR UPDATE` ile kilitlenir (Postgres; SQLite zaten tek yazıcı), 50 sınırı yarışta aşılmaz.
- **Alternatifler:** Token'ı arka plan görevinde üretmek (arka plan görevinin kendi veritabanı oturumu gerekirdi); eklemeden sonra yeniden saymak.

### D-122 · Duraklatılmış ya da bitmiş hedefin görevleri hatırlatma göndermez (#46)
Kaynak: #46 (D-119'dan kalan soru); **Claude karar verdi**, Fatih kararı Claude'a bıraktı (2026-11-18).
- **Karar:** Çalışma hatırlatması ve Learning'deki Next reminder kartı sadece `active` hedeflerin açık görevlerine bakar. Paused/Done hedefin görevleri planda görünmeye ve "Tasks done" sayısında sayılmaya devam eder; hedef tekrar Active olunca görevleri hatırlatmaya geri döner. O gün duraklatılmış bir hedefin görevi tamamlandıysa da o günün hatırlatması gelmez (çalışılmış sayılır). Yalnız duraklatılmış hedeflerde açık görev varsa kart "No tasks planned" der.
- **Neden:** Bir hedefi duraklatmak "şimdilik bununla uğraşmıyorum" demek; onun gecikmiş görevleri için her akşam hatırlatma gelmesi tam tersini söyler. Görevleri gizlemek ya da silmek ise veriyi kaybettirmiş gibi görünür.
- **Alternatifler:** Görevlerin hatırlatmada kalması (D-119'daki durum); duraklatılmış hedefin görevlerini plandan da gizlemek.

### D-123 · M13 dörde bölündü
Kaynak: M13 bölme önerisi (`docs/plans/M13-split.md`, PR #50), plan-reviewer; Fatih onayladı (2026-11-18: "Dörde bölelim", diğer sorularda "önerilerin gayet makul").
- **Karar:** M13a cilalama, M13b GDPR + yasal sayfalar + Settings › Account, M13c deploy, M13d portföy README'si. Sıra: M13a → M13b → AI milestone'ları (API anahtarı gelince) → M6 → M13c → M13d (güncelleme D-135: M6a AI'dan önce, M6b AI milestone'larıyla). Güvenlik header'ları #10 ile M13'ten önce bitti (D-120).
- **Cevaplar:** Dışa aktarma bugün var olan verileri alır, yeni tablolar otomatik bir testle yakalanır. Hesap silmede Undo yok (şifre onayı ve açık uyarı yeterli). Yasal metinlerin taslağını Claude yazar, Fatih bir uzmana inceletir (özellikle Impressum ve gizlilik metni).
- **Neden:** Tek milestone WORKFLOW'daki 1–2 haftalık sınırı aşıyordu; deploy AI milestone'larına bağlı (mektup indirme, D-088), cilalama ve GDPR değil.
- **Alternatifler:** Tek M13; GDPR ve cilalamayı birleştirmek.
- **Güncelleme: D-150** (2026-11-20): sıra M13a → M13b → M6a → M8a → M8b → M9a → M13c → M13d; AI milestone'ları V2.
- **Güncelleme: D-155** (2026-11-21): M13c (deploy) MVP'den çıktı; sıra M8a → M8b → M9a → M13d.

### D-124 · Settings bölümlerinin sahipleri
Kaynak: M13 bölme önerisi, soru 4 ve 7; Fatih önerileri onayladı (soru 7 için öneriyi **Claude karar verdi**, 2026-11-18).
- **Karar:** Settings › Account (ad, şifre) M13b'de; e-posta değiştirme ayrı bir PR (doğrulama akışını değiştirir, D-027). Settings › Documents (varsayılan belge dili) M13a'da. Settings › Plan (Free plan kartı, "Pro · coming soon") M5'te, çünkü "Upgrade to Pro" bekleme listesi modalı ve `WaitlistSignup` zaten M5'in fake door kapsamında. Settings › AI M5'te (BYOK ve krediler) ve M12a'da (asistan ayarları), Browser extension M6'da, Privacy M13b'de.
- **Neden:** SPEC §6.9'daki bölümlerin §12'de sahibi yoktu; her bölüm, verisini getiren milestone'a gider; verisi zaten var olan küçük bölüm (Documents) cilalamaya.
- **Alternatifler:** Bütün Settings'i M13b'de toplamak (M13b'yi büyütür).
- **Güncelleme: D-150** (2026-11-20): Settings › Plan ve Settings › AI M5 ile birlikte V2'ye ertelendi.

### D-125 · Hata sayfaları
Kaynak: M13a Design fazı (`docs/plans/M13a.md`), plan-reviewer; Fatih planı onayladı (2026-11-18). D-120'deki 500 sınırlamasını kapatır.
- **Karar:** Handler'lar `starlette.exceptions.HTTPException`'a bağlanır. 404 → sade düzende "Page not found" sayfası, link hep `/`. Yol parametresi doğrulama hatası (`/learning/tasks/abc`) → 404; diğer doğrulama hataları 422 durumunu korur ama JSON yerine mesaj sayfası. Diğer `HTTPException`'lar (403, 405, 409) kısa mesaj sayfası; `detail` sadece bizim yazdığımız metinse gösterilir. 500 → "Something went wrong" sayfası, stack trace yok; handler `ServerErrorMiddleware`'de çalıştığı için güvenlik header'larını ve `no-store`'u kendisi ekler ve paylaşılan `templates`'i (oturuma bakan context processor'lar) kullanmaz; o da hata verirse header'lı düz metin. Log'a yöntem ve yol yazılır, sorgu dizesi yazılmaz. htmx isteğinde boş gövde ve sunucunun `HX-Trigger` toast'u (404, 403 — doğrulanmamış hesapta sunucunun metni, Fatih seçti —, 422, 429, 500). `/api/` yolunda JSON kalır (M6 için).
- **Alternatifler:** FastAPI'nin JSON hataları (bugünkü durum); 500'ü bir ara katmanla yakalamak.
- **Güncelleme (security-reviewer):** Sayfada ve toast'ta sadece `UserFacingHTTPException`'ın metni gösterilir (L2); diğer `HTTPException`'lar durum koduna göre sabit metin alır, iç hata metni sayfaya düşemez. 500 log'unda sorgu değerleri zaten yok (`hide_parameters=True`, D-105; L1). uvicorn traceback'i ikinci kez loglar (kabul).

### D-126 · Butonda dönen simge ve çift tıklama kilidi
Kaynak: M13a Design fazı, plan-reviewer; simgeyi Fatih seçti (2026-11-18; öneri üstte ince çizgiydi).
- **Karar:** `<body hx-disabled-elt="find [type=submit]">`: htmx isteği süresince formun submit butonu kilitlenir; kendi başına istek gönderen butonlar `hx-disabled-elt="this"`, otomatik kaydeden ve filtre formları `unset`. İsteği gönderen butonda 300 ms'den sonra küçük bir dönen simge (sadece CSS, `prefers-reduced-motion`'da dönmez). Sonradan yüklenen modallar önce "Loading…" iskeletiyle açılır, yükleme hata verirse kapanır.
- **Alternatifler:** Sayfanın üstünde ince yükleme çizgisi; her butona elle `hx-indicator`.

### D-127 · Boş durumlar
Kaynak: M13a Design fazı, plan-reviewer.
- **Karar:** `docs/plans/M13a.md`'deki envanter tablosu her listeyi sayar; eksikler kısa, suçlamayan ve bir sonraki adımı söyleyen metinlerle tamamlanır. Bir parametreli test tablonun her satırını (var olanlar dahil) boş veriyle açar.
- **Alternatifler:** Sadece eksikleri test etmek.

### D-128 · "Sign out" her cihazdan çıkarır
Kaynak: M13a Design fazı, D-105 notu, #14; Fatih seçti (2026-11-18).
- **Karar:** Çıkış `session_version`'ı tek bir `UPDATE … SET session_version = session_version + 1` ile artırır; çerezin oturumu zaten geçersizse hiçbir şey artırılmaz, sadece çerez silinir. Bu tarayıcının, kopyalanmış bir çerezin ve diğer cihazların oturumları biter. Doğrulama, sıfırlama, hatırlatma kapatma ve Undo token'ları `session_version` kullanmadığı için etkilenmez. #14 bununla kapanır. (Güncelleme D-134: e-posta değişikliği token'ı `session_version` taşır; çıkış bekleyen bir değişikliği iptal eder.)
- **Neden:** Oturum çerezde saklanıyor (sunucuda oturum listesi yok); kopyalanmış bir çerezi geçersiz kılmanın tek yolu bu. Paylaşılan bilgisayarda "Sign out" gerçekten çıkış olmalı.
- **Alternatifler:** Sadece bu tarayıcının çerezini silmek (bugünkü durum); sunucu tarafı oturum tablosu (V2).

### D-129 · Settings › Documents
Kaynak: M13a Design fazı, D-124.
- **Karar:** `User.default_doc_language` (SPEC §5.1): `CV_LANGUAGES`'tan biri, varsayılan `en`, NOT NULL (migration `0014`, mevcut kullanıcılar `en`). Settings'te kendi formu (`POST /settings/documents`, CSRF, 30/dk), toast "Settings saved." New CV modalı bu dille açılır; M8'deki mektup da kullanır. Kayıtta `en`, tarayıcı dilinden tahmin yok.
- **Alternatifler:** Tarayıcı dilinden tahmin; son oluşturulan CV'nin dilini hatırlamak.

### D-130 · Veri dışa aktarma ("Export my data")
Kaynak: M13b Design fazı (`docs/plans/M13b.md`), plan-reviewer; Fatih planı onayladı (2026-11-18). SPEC §10.2.
- **Karar:** Settings › Privacy'de bir form (`POST /settings/export`, CSRF, 5/saat/IP, kullanıcı başına aynı anda tek dışa aktarma). Yanıt `unfurl-export-YYYY-MM-DD.json` (UTC tarih), `attachment`. Biçim `{"format": "unfurl-export", "version": 1, …}`: hesap, profil (bölümleriyle), CV'ler, başvurular (eventleri ve belgeleriyle), Learning, bildirimler ve tercihler. Hariç: `password_hash`, `session_version`, (M5'te) şifreli API anahtarı, satırların `user_id`'si ve `deleteditem` (dakikalık Undo kopyaları). Fotoğraf base64 (en fazla 2 MB; Fatih onayladı). En kötü durum sınırlardan GB'larca olabildiği için JSON akış olarak yazılır (`StreamingResponse`, `yield_per`). Bir test, FK yoluyla `user`'a bağlı her tablonun dışa aktarmada ya da gerekçeli hariç listesinde olmasını zorlar.
- **Alternatifler:** Bellekte üretmek (en kötü durumda sunucu belleğini tüketir); ZIP içinde ayrı dosyalar; GET linki (tarayıcı ve önizlemeler kendiliğinden indirebilir).
- **Güncelleme (security-reviewer):** Her bölüm sayfa sayfa (keyset, `id > son`) ve her sayfa kendi kısa oturumunda okunur; yavaş bir indirme veritabanı bağlantısı tutmaz (M1). Aynı anda en fazla 2 dışa aktarma; parçalar gönderildikçe kullanıcının yeri tazelenir, gövde hiç okunmazsa arka plan görevi yeri boşaltır (L1). Yarıda kalan dosya kapanış `}` olmadan biter ve hatanın sadece türü loglanır (L2). `photo_path` dosyaya girmez (L3).

### D-131 · Hesap silme ("Delete my account")
Kaynak: M13b Design fazı, plan-reviewer; Fatih onayladı (2026-11-18). SPEC §10.2, D-034.
- **Karar:** Settings › Privacy'de modal: kalıcı olduğunu söyleyen uyarı, şifre, kırmızı buton; Undo yok (D-123). `POST /settings/delete-account` (CSRF, 5/saat/IP; htmx'te `HX-Redirect`). Şifre `check_password_with_lockout` ile: login kilidi (IP + e-posta), uzunluk sınırı, başarıda kayıt temizlenir. Sıra: e-posta, doğrulanmış mı ve fotoğraf yolu alınır → kullanıcı silinir (cascade) → fotoğraf dosyası silinir → oturum temizlenir → `/login?deleted=1` → onay e-postası **sadece doğrulanmış adrese** (Fatih onayladı). Zamanlayıcının topladığı hatırlatma e-postaları göndermeden önce kullanıcının hâlâ var ve doğrulanmış olduğu tek sorguyla kontrol edilir.
- **Alternatifler:** Bekleme süresi ve geri alma (V2); doğrulanmamış adrese de e-posta (adresin sahibine bir hesap olduğunu söylerdi).
- **Güncelleme (security-reviewer):** Oturum, kullanıcının oluşturulma zamanını da taşır (`SESSION_ACCOUNT`); SQLite silinen kullanıcının id'sini yeni kayda verebildiği için eski bir çerez yeni hesaba giremez (M2). Kilidin IP'den bağımsız bir e-posta sınırı ayrı bir issue (L4).

### D-132 · Settings › Account
Kaynak: M13b Design fazı, D-124, plan-reviewer.
- **Karar:** Ad (`full_name`, kayıttaki kurallar, 30/dk). Şifre: mevcut şifre (D-131'deki kilitli kontrol), yeni şifre ve tekrarı (kayıttaki kurallar), 10/dk; `password_hash` ve `session_version` tek koşullu UPDATE'te (`WHERE password_hash = eski`, D-121). Bu oturumda sadece `SESSION_VERSION` güncellenir (`start_session` CSRF token'ını yenilerdi, sayfadaki htmx istekleri 403 alırdı); diğer cihazlar çıkar (D-021). Doğrulanmış adrese "şifren değişti" e-postası. E-posta salt okunur; değiştirme ayrı bir issue.
- **Alternatifler:** Şifre değişince bu cihazdan da çıkış.

### D-133 · Yasal sayfalar
Kaynak: M13b Design fazı, D-123, plan-reviewer; Fatih'in cevapları (2026-11-18).
- **Karar:** Claude İngilizce taslak yazar; uzman incelemesine kadar sayfaların üstünde "Draft · not yet reviewed by a lawyer" bandı (`LEGAL_REVIEWED=false`). Gizlilik DSGVO Art. 13/14'ü kapsar (sorumlu, veriler ve amaçlar, dayanaklar, alt işleyenler, saklama süreleri, haklar ve Datenschutzbehörde, çerezler, otomatik karar yok, kullanıcının girdiği üçüncü kişi verileri, IP adresleri, verilerin gerekliliği). Şartlar tüketici korumalarını saklı tutar (KSchG § 6, Roma I md. 6). Alt yaş sınırı **16** (Fatih). Impressum ad, adres ve e-postayı `.env`'den alır (`LEGAL_OPERATOR_NAME`, `LEGAL_ADDRESS`, `LEGAL_EMAIL`), repoda kişisel bilgi yok; şimdilik ticari olmayan bir site için yazılır (Fatih: "önce portföy, ileride ticari olabilir"); ticarileşince § 5 ECG'nin tam bilgileri eklenir. Anthropic gizlilik metnine M5'te eklenir; sağlayıcı adları M13c'de.
- **Alternatifler:** Metinleri doğrudan Fatih'in yazması; kişisel bilgileri şablona yazmak.

### D-134 · E-posta değiştirme (#56)
Kaynak: `docs/plans/I56.md`, plan-reviewer; Fatih onayladı (2026-11-18).
- **Karar:** Settings › Account'ta yeni e-posta + mevcut şifre (`POST /settings/account/email`, 5/saat/IP). Kontrol sırası: biçim → farklı → şifre (login kilidi, D-131) → başka hesapta yok → yeni adresin e-posta sınırı (saatte 3). Yeni adrese adsız bir onay e-postası (`/confirm-email?token=…`, 24 saat), eski adrese (doğrulanmışsa) maskeli bildirim. Token imzalı, tablo yok: `user_id`, eski ve yeni adres, `session_version`; onayda e-posta ve sürüm eşleşmeli. Şifre sıfırlama/değiştirme, Sign out ve onaylanan değişiklik sürümü artırdığı için bekleyen linkler ölür (Fatih onayladı). Onay sayfası girişsiz: GET sayfa, POST (CSRF) tek koşullu UPDATE ile e-postayı değiştirir, doğrulanmış yapar, sürümü artırır (diğer cihazlar çıkar, aynı kullanıcının tarayıcısı devam eder; Fatih onayladı); adres arada alındıysa temiz hata. Onaydan sonra eski adrese de "e-postan değişti" e-postası (Fatih onayladı). Eski adrese giden doğrulama, sıfırlama ve hatırlatma kapatma linkleri e-posta taşıdıkları için ölür.
- **Alternatifler:** `pending_email` kolonu; eski adresten de onay istemek; sadece e-posta eşleşmesi (sıfırlama bekleyen değişikliği iptal etmezdi ve A→B→A sonrası eski link canlanırdı).
- **Güncelleme (security-reviewer):** Eski adrese giden e-postalarda yeni adresin alan adı da maskelenir (`j***@e***.com`; istekte bulunan alan adını seçer, oltalama için kullanılabilirdi) ve "Only trust links that start with …" satırı var. Onay sayfası hangi hesabın değiştiğini (maskeli eski adres) söyler. Aynı tarayıcı kontrolü hesap damgasını da karşılaştırır. Kötü niyetli ve onaylanmış bir değişikliği eski adresten geri alma linki ayrı bir issue.


### D-135 · M6 ikiye bölündü (M6a AI'sız, M6b AI'lı)
Kaynak: `docs/plans/M6a.md`, plan-reviewer; Fatih onayladı (2026-11-18).
- **Karar:** M6a: token'lar ve Settings › Browser extension, JSON API (giriş, çıkış, `me`, `jobs/check`, `jobs`), URL normalizasyonu, eklentinin kendisi; alanlar AI olmadan doldurulur (D-139), kullanıcı düzeltip kaydeder. M6b (M5 ve M7'den sonra): "Auto-fill with AI" (`/jobs/extract`), "Analyze fit" (`/jobs/{id}/fit`), kredi ve kota hataları, `/me`'de krediler. Sıra: M6a → (API anahtarı gelince) M5 → M7 → M6b ve diğer AI milestone'ları → M13c → M13d. D-123'teki sıra bununla değişir.
- **Neden:** API anahtarı henüz yok (D-088); eklentinin AI'sız kısmı ("Save without AI" yolu) anahtar beklemeden kullanılabilir.
- **Alternatifler:** M6'yı AI milestone'larından sonraya bırakmak (D-123'teki sıra); tek M6'da AI'yı sahte istemciyle yapmak.
- **Güncelleme: D-150** (2026-11-20): M6b V2'ye ertelendi; `/me`'deki `credits_remaining` ve `using_byok` MVP'de `null` kalır.

### D-136 · ApiToken ve eklenti girişi
Kaynak: M6a Design fazı, plan-reviewer; Fatih onayladı (2026-11-18). SPEC §5.12, §8.
- **Karar:** Tablo `apitoken`: `user_id` (cascade), `name` ("Chrome extension"), `token_hash` (SHA-256, unique), `session_version` (oluşturulduğu andaki kullanıcı sürümü), `last_used_at`, `expires_at` (90 gün), `revoked`, `created_at`. Ham token (`secrets.token_urlsafe(32)`) sadece girişte bir kez döner. Bearer kontrolü token'ın `session_version`'ı kullanıcınınkinden farklıysa reddeder: şifre değişince/sıfırlanınca, e-posta değişince ve Sign out'ta eklenti de çıkar (Fatih onayladı); tek bir kontrol olduğu için ileride eklenecek bir sürüm artışı da unutulmaz. Hesap silinince cascade. `POST /api/ext/auth/login` login formuyla aynı kontrolleri yapar (login kilidi, `authenticate`, bilinen ve bilinmeyen e-posta için aynı hata, kilitliyken 429 `rate_limited`), 5/dk; doğrulanmamış hesaplar da token alır. Girişte iptal edilmiş ve süresi geçmiş token'lar silinir; en fazla 10 etkin token, fazlası en eskiyi iptal eder. `last_used_at` en fazla 5 dakikada bir yazılır; tarihler `as_utc()` ile karşılaştırılır. `POST /api/ext/auth/logout` sadece o token'ı iptal eder. Dışa aktarmada `apitoken` hariç (hash bir sır; D-130'daki kapsama testi).
- **Neden:** Dört ayrı yerde token iptal etmek kırılgandı; sürüm zaten oturumlar için tek doğruluk kaynağı (D-021, D-128).
- **Alternatifler:** Şifre/e-posta değişiminde ve çıkışta token'ları tek tek iptal etmek; token'ların oturumdan bağımsız yaşaması.

### D-137 · Eklenti JSON API'sinin ortak kuralları
Kaynak: M6a Design fazı, plan-reviewer. SPEC §8.
- **Karar:** Base `/api/ext`, CSRF'siz, JSON gövde (Pydantic), hata biçimi `{"error": {"code", "message"}}` (`unauthorized`, `validation_error`, `not_found`, `rate_limited`, `forbidden`); D-125'teki `/api/` JSON dalı ve `rate_limit_response` bu biçime çevrilir. Kimlik sadece Bearer: API oturum çerezini okumaz, çerezli ama Bearer'sız istek 401; popup `credentials: "omit"` ile çerez göndermez. CORS header'ı gönderilmez: MV3'te popup'ın `host_permissions`'taki adrese istekleri CORS'a takılmaz, içerik script'i API'yi hiç çağırmaz. Ek savunma: `Origin` varsa sadece `EXTENSION_ORIGINS`'teki değerler kabul edilir (403 `forbidden`). Eklenti kimliği geliştirmede sabit olsun diye `manifest.json`'da açık anahtar (`key`, sır değil); özel anahtar repoya girmez; Web Store kimliği M13c'de `EXTENSION_ORIGINS`'e eklenir. Gövde en fazla 256 KB (ara katman, 413 `validation_error`). Bearer bağımlılığı isteği `clock.use_timezone(user.timezone)` ile çalıştırır. Limitler: login 5/dk (IP), diğerleri 60/dk token hash'ine göre (#18: proxy arkasında IP'ler birleşebilir).
- **Neden:** Eklenti çerez kullanmayınca CSRF ve CORS riskleri ortadan kalkar; JSON istemci HTML hata sayfasını okuyamaz.
- **Alternatifler:** CORS middleware ile `chrome-extension://` origin'ine izin vermek (SPEC'in eski metni); çerez + CSRF.
- **Güncelleme (security-reviewer):** Route limitleri ancak token kabul edildikten sonra çalıştığı için (slowapi kontrolü route'un içinde, bağımlılıklar önce), çalışmayan token'lara IP başına ayrı bir sınır var (30/dk, sonra 429). Limit anahtarı header'dan değil token'ın kendisinden üretilir; "bearer X" ve "Bearer  X" aynı sınırı paylaşır (L1). `.gitignore` `*.pem` ve `*.crx`'i dışarıda tutar (L2). Sonraki milestone'lara kalanlar #64'te.

### D-138 · URL normalizasyonu ve "Already saved"
Kaynak: M6a Design fazı, plan-reviewer (Blocker 1 ve 2). SPEC §8.5.
- **Karar:** `url_service.canonical_url(url)`: şema `https`, host küçük harf ve `www.` olmadan, fragment ve sondaki `/` atılır. Bilinen iş sitelerinde site kuralı (LinkedIn `currentJobId` ya da path → `linkedin.com/jobs/view/{id}`; Indeed `jk`; StepStone, karriere.at ve XING path'teki id), diğer sorgu atılır. Diğer bütün sitelerde (şirket ve ATS sayfaları: `?jobId=`, `gh_jid`…) sorgu korunur, sadece izleme parametreleri (`utm_*`, `fbclid`, `gclid`, `msclkid`, `refId`, `trackingId`, `trk`, `source`, `ref`…) atılır ve kalanlar sıralanır. En az 12 örnekle birim testi. `Application.canonical_url` (nullable, `(user_id, canonical_url)` index, unique değil); `create` ve `update_details` yeniden hesaplar; migration mevcut satırları doldurur. `POST /api/ext/jobs/check` `{url}` → `{exists, application_id?, status?, app_url?}`; birden fazla eşleşmede en yenisi, arşivdekiler de sayılır. Eklentide "Save anyway" yok (Fatih). Web'deki "Add job" değişmez.
- **Alternatifler:** Her sitede sorguyu atmak (farklı ilanları birleştirirdi); unique index (aynı ilana bilerek ikinci başvuruyu engellerdi).

### D-139 · Eklentide AI'sız doldurma
Kaynak: M6a Design fazı, plan-reviewer. SPEC §7.4, §7.5.
- **Karar:** `extract.js` öncelik sırası: (a) schema.org `JobPosting` JSON-LD (başlık, şirket, şehir, uzaktan, açıklama, maaş, `validThrough`), (b) `site_hints.js` selector'ları (bozulursa sessizce atlanır), (c) sayfa başlığı ve `og:` etiketleri. Güvenlik: `JSON.parse` try/catch içinde, `@graph` ve diziler işlenir, en fazla 10 script ve 200.000 karakter, `description` HTML'i `DOMParser` ile metne çevrilir (asla `innerHTML`), popup alanları sadece `.value`/`textContent` ile doldurur. `extract.js` izole dünyada çalışır, `chrome.storage`'a dokunmaz, API çağırmaz. `job_text` görünen metinden (seçili metin önce), en fazla 50.000 karakter. Sadece `activeTab` + `scripting` ile tıklamada enjekte edilir; M6a'da `background.js` yok.
- **Alternatifler:** Sadece sayfa başlığı (çok zayıf); sunucuda HTML ayrıştırma (sayfanın tamamını göndermek gerekirdi, §7.2).
- **Güncelleme (security-reviewer):** Popup açılınca sayfanın linki "Already saved" kontrolü için gönderilir (saklanmaz); gizlilik metni ve SPEC §7.2 bunu söyler (E1).
- **Güncelleme: D-144** (site okuyucuları; karriere.at arama panelinde ilanın kendi sayfasına tek bir istek).

### D-140 · Settings › Browser extension
Kaynak: M6a Design fazı, plan-reviewer. SPEC §6.9.
- **Karar:** Etkin token'ların listesi (ad, oluşturma, son kullanım, bitiş) ve her birinde "Revoke" (`POST /settings/extension/tokens/{id}/revoke`, CSRF, toast). Liste `user_id` ile filtrelenir; başkasının token'ı 404. Kurulum talimatı sayfada kısa adımlar olarak; ayrıntı `extension/README.md`'de (uygulama o dosyayı sunmadığı için link yok).
- **Alternatifler:** README'ye link; "Revoke all" butonu (Sign out zaten hepsini bitirir, D-136).

### D-141 · Eklentiden kaydedilen alanlar
Kaynak: M6a Design fazı, plan-reviewer; Fatih onayladı (durum seçimi, 2026-11-18). SPEC §8.
- **Karar:** `POST /api/ext/jobs` şirket, pozisyon, konum, çalışma şekli, maaş (`salary_text`), ilanın son günü (`posting_closes_at`, JSON-LD `validThrough`'tan), durum (varsayılan Saved; seçilebilir), URL ve `job_text` alır → `{application_id, app_url}`. `application_service.create` `salary_text` ve `posting_closes_at`'i de alır. `source` her zaman `detect_source(url)`'den; istemcinin gönderdiği `site` yok sayılır. Gizlilik sayfasına eklenti ve token'lar için bir satır.
- **Alternatifler:** `site`'a güvenmek (istemci yanlış kaynak yazabilirdi); durumu her zaman Saved yapmak.

### D-142 · HEAD istekleri
Kaynak: #5 (M0 test-writer), `docs/plans/T5.md`, plan-reviewer. **Claude karar verdi** (2026-11-20, Fatih'in onayını bekliyor).
- **Karar:** En dışta küçük bir ASGI ara katmanı (`HeadAsGet`, `app/main.py`): `/static/` dışındaki HEAD isteklerini uygulamaya GET olarak verir (scope'un kopyası) ve yanıtın gövdesini atar; status ve header'lar (Content-Length dahil) GET'inkiyle aynı. FastAPI 0.162.3 GET route'larına HEAD eklemiyor. HEAD, GET ile aynı kodu çalıştırır; rate limit'ler HEAD'i de sayar. Ara katman kopyaya bir işaret koyar (`is_head_request`): `/verify-email` HEAD'de doğrulamaz ve bekleyen toast HEAD'de harcanmaz (security review L1, I1). Dosya yanıtlarının `pathsend` mesajı da gövdesiz gönderilir (I3).
- **Alternatifler:** Her route'a `methods=["GET", "HEAD"]` (yeni route'larda unutulur); özel bir `APIRouter` sınıfı; `/verify-email`'de doğrulamayı GET yerine bir butona (POST) taşımak, `/confirm-email` gibi (D-112): akış değişir, ayrı issue.
- **Güncelleme: D-146** (#66): `/verify-email` artık GET'te hiç doğrulamıyor, `is_head_request` özel durumu kalktı.

### D-143 · Action'lar SHA'ya, Docker image'ları digest'e; Dependabot
Kaynak: #3 (M0 güvenlik incelemesi #5, #6), `docs/plans/T3.md`, plan-reviewer. **Claude karar verdi** (2026-11-20, Fatih'in onayını bekliyor). D-003'ün Action ve image satırlarının yerine geçer.
- **Karar:** `ci.yml`'deki Action'lar tam commit SHA'sı + `# vX.Y.Z` yorumuyla. Image'lar `tag@sha256:<multi-arch index digest>` ile (Dockerfile: python ve uv; docker-compose ve CI: postgres). Dockerfile'da `ARG PYTHON_IMAGE` yerine `base` stage'i. `.github/dependabot.yml`: `github-actions`, `docker`, `docker-compose`; haftalık, 7 gün cooldown, `build(deps)` commit'leri. Python'un minor/major ve Postgres'in major güncellemeleri `ignore`'da: SPEC §3 sürümleri (Python 3.12, PostgreSQL 16) bot PR'ıyla değişmez. CI'daki postgres image'ı ve uv sürümü Dependabot'un dışında: CI'ın `docker` job'ı bunların compose/Dockerfile ile aynı olduğunu kontrol eder; bir Dependabot PR'ından sonra ci.yml elle güncellenir.
- **Gerekçe:** Tag'ler başka koda taşınabilir; SHA ve digest değişmez, build birebir tekrarlanır. Dependabot sabitlenmiş sürümleri güncel tutar.
- **Alternatifler:** Tag'lerde kalmak; digest'i tag'siz yazmak (okunmaz); Renovate (ayrı GitHub App, daha fazla ayar).

### D-144 · Eklentide LinkedIn ve karriere.at okuma
Kaynak: M6a elle testi (2026-11-20, adım 5 ve 7 başarısız), `docs/plans/M6a-fix.md`, plan-reviewer. Fatih onayladı (2026-11-20): karriere.at isteği, İngilizce + Almanca LinkedIn, eski satırlar için migration yok. SPEC §7.2, §7.4, §7.5, §8.5.
- **Karar:** `extract.js`'te site okuyucuları; öncelik JSON-LD → site okuyucusu / selector'lar → sayfa başlığı (sondaki ` | <site>` atılır).
  - LinkedIn (sınıflar rastgele, `h1` ve JSON-LD yok): şirket `a[aria-label^="Company, "]` / `"Unternehmen: "` gibi etiketlerden ya da `/company/` linkinden; üst kart şirket linkinden en fazla 6 seviye yukarı, kaydet/başvur butonunu içeren kap; pozisyon sayfa başlığından (baştaki `(3) `, sondaki ` | LinkedIn` ve ` | <şirket>` atılır; uyuşmazsa kartta şirketten sonraki satır); konum sonraki satırın ` · `'dan önceki kısmı; maaş para birimi + rakam içeren kısa satır; çalışma şekli Remote/Hybrid/On-site ve Almanca karşılıkları; açıklama ilk `[data-testid="expandable-text-box"]`. İngilizce ve Almanca arayüz.
  - karriere.at arama sayfası (host `karriere.at`/`www.karriere.at`, path'in son parçası rakam değil, fragment `^#(\d{1,12})$`): `new URL("/jobs/" + id, location.origin)`'a `fetch` (`credentials: "omit"`, `redirect: "error"`, aynı origin ve path kontrolü, `ok` + `text/html`, 5 sn, en fazla 2 MB akışla), `DOMParser`, aynı JSON-LD sınırları. Hata olursa listedeki satırdan şirket. Bulunan link `/jobs/<id>`; popup bunu kontrol ve kayıtta kullanır. Hepsi try/catch içinde: extract.js her zaman bulduğuyla sonuçlanır.
  - `canonical_url`: karriere.at'te aynı kural (detay path'i kazanır). Eski satırlar yeniden hesaplanmaz (yayında değil, sadece geliştirme verisi).
  - İş formundaki alanlara `autocomplete="off"`.
- **Gerekçe:** LinkedIn'in sınıfları her yayında değişiyor; erişilebilirlik etiketleri ve sayfa başlığı kalıcı. karriere.at panelinde güvenilir yapı yok; ilanın kendi sayfası tam JSON-LD veriyor ve aynı sitede.
- **Alternatifler:** Rastgele sınıfları selector olarak yazmak; karriere.at panelinin DOM'unu okumak; eklentiye karriere.at host izni vermek; arama sayfasında "ilanı açın" demek.

### D-145 · Testlerde ucuz argon2
Kaynak: #72, `docs/plans/T72.md`, plan-reviewer. **Claude karar verdi** (2026-11-20, Fatih "kendin ilerleyebilirsin" dedi; onayını bekliyor).
- **Karar:** `tests/conftest.py`'de autouse fixture `auth_service.password_hasher`'ı `Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1)` ile, `_DUMMY_HASH`'i onunla yapılmış bir hash'le değiştirir (`monkeypatch`). Gerçek nesneler conftest'in başında saklanır; bir test production hasher'ın ve dummy hash'in `$argon2id$v=19$m=65536,t=3,p=4$` ile başladığını kontrol eder (pwdlib varsayılanlarını değiştirirse test bilerek kırılır). Bu ayarlar sadece testler için, `app/` içinde asla. security-reviewer bilinçli olarak çalıştırılmadı: production kodu değişmiyor, ayarı yeni test koruyor.
- **Gerekçe:** Test takımının ~230 sn'lik setup'ının çoğu argon2'ydi (~60 ms/hash). Hash/verify yolu aynı kod, sadece maliyet düşük.
- **Alternatifler:** `pytest-xdist` (yeni bağımlılık); önceden hesaplanmış hash'ler (her şifre için işe yaramaz); `freezegun` (yeni bağımlılık).

### D-146 · E-posta doğrulama butonla
Kaynak: #66 (#5 security review L1), `docs/plans/I66.md`, plan-reviewer. **Claude karar verdi** (2026-11-20, Fatih "kendin ilerleyebilirsin" dedi; onayını bekliyor). SPEC §6.1. D-112 ve D-134'ün kalıbı.
- **Karar:** `GET /verify-email?token=…` (30/dk) hiçbir şeyi değiştirmez: geçerli ve doğrulanmamışsa "Verify your email" sayfası (adres, "If you didn't create an Unfurl account, close this page", **Verify my email** POST formu: gizli token + CSRF); zaten doğrulanmışsa "Email already verified"; geçersizse bugünkü 400 sayfası. `POST /verify-email` (10/dk) token'ı yeniden kontrol eder ve doğrular. Adres maskelenmez (imzalı token'da zaten okunur, link o adrese gitti); `full_name` gösterilmez. Yanıtlar `TOKEN_PAGE_HEADERS` ile.
- **Gerekçe:** E-posta programları ve link tarayıcıları linkleri kendiliğinden açar; başkasının adresiyle açılan bir hesap, adres sahibi tıklamadan doğrulanabiliyordu.
- **Alternatifler:** GET'te doğrulamak; JavaScript ile otomatik POST (CSP inline script'e izin vermez, tarayıcılar yine çalıştırabilir); doğrulama için giriş istemek.

### D-147 · E-posta değişikliğini geri alma (#60)
Kaynak: #60 (#56 security review L1), `docs/plans/I60.md`, plan-reviewer (Blocker: saldırgan şifreyi biliyor). Fatih onayladı (2026-11-20): 7 gün; geri alma = eski adres + bütün oturumlar/token'lar kapanır + şifre rastgele + eski adrese reset linki; zincirde de çalışır; eski adres alınmışsa sayfa yol gösterir; risk (a) kabul. SPEC §6.1, §6.9.
- **Karar:** Onay bildiriminde (`email_changed.txt`, sadece doğrulanmış eski adrese) `/undo-email-change?token=…` linki. Token (tuz `email-change-undo`, 7 gün) `{user_id, account, old_email}`: oturum sürümüne ve yeni adrese bağlı değil; hesap eski adresi kullandığı sürece geçersiz. GET sadece onay sayfası (eski adres açık, şu anki adres maskeli), POST (CSRF, 10/dk) tek koşullu UPDATE ile eski adres, `is_verified`, rastgele şifre hash'i, `session_version + 1`; `IntegrityError`/alınmış adres → 409 ve yol tarifi. Sonra eski adresin login kilidi temizlenir ve yanıttan sonra reset e-postası gider. `TOKEN_MAX_LENGTH` 1024 (254 karakterlik adresler).
- **Güncelleme (security-reviewer H1, M1, L2; Fatih onayladı 2026-11-20):** `User.email_changed_at` (migration 0017): değişiklik ve geri alma onu yazar. Değiştirilen adres son 8 gün içinde (link ömrü + 1 gün pay, ikinci inceleme L-a) bir değişiklikle gelmişse bildirimde undo linki **yok**: yoksa A→B→C yapan saldırgan kendi B adresine link alıp sahibin geri almasından sonra hesabı yeniden alabilirdi. Kayıttaki adres ve bir geri almayla dönen adres (kutusu kendini kanıtladı; `email_changed_at = NULL`, ikinci inceleme L-b) her zaman link alır. Geri alınınca değiştirilen adrese "changed back" bildirimi gider. Settings'te değiştirirken "for 7 days it can undo the change and take the account back" uyarısı. Arada adres değişirse "Something changed, open the link again" (409).
- **Kabul edilen riskler:** (a) 7 gün boyunca eski adrese erişimi olan biri (ör. eski iş yerinin posta sunucusu) **hesabın tamamını** ele geçirebilir: adres geri gelir, şifre sıfırlanır ve sıfırlama linki ona gider (Fatih kabul etti; azaltma: değiştirilen adrese bildirim ve Settings uyarısı); (b) pencere içinde silinen/dışa aktarılan veri geri gelmez; (c) eski adresle açılan bir hesap geri almayı geciktirebilir; (d) adresini 8 gün içinde iki kez değiştiren kullanıcı, aradaki adres için undo linki almaz; (e) 0017 öncesi değişmiş hesaplar (canlıda yok, M13c) kayıt adresi sayılır.
- **Alternatifler:** oturum sürümüne bağlamak (saldırgan Sign out ile öldürürdü); sadece B'deyken çalışmak (B→C ile atlatılır); şifreyi bırakmak (D-134 değişiklik için şifre istediği için saldırgan şifreyi biliyor); eski adresi 7 gün ayırmak (migration).

### D-148 · Ölü eklenti token'larının gece temizliği
Kaynak: #64 (M6a security review E4), `docs/plans/I64.md`, plan-reviewer. **Claude karar verdi** (2026-11-20; Fatih "#64 ile devam edelim" dedi). SPEC §5.12.
- **Karar:** `token_service.purge_dead(session, now)` tek bir SQLAlchemy `DELETE` ile bütün kullanıcıların ölü token'larını siler (`revoked`, `expires_at <= now`, ya da `session_version` kullanıcınınkinden farklı; `_is_active`'in tam tersi), commit eder ve sayıyı döner. Kural `_dead(now, current_version)`'da, `issue()` ile paylaşılır. Zamanlayıcıda ayrı bir `purge_api_tokens` görevi, her gece 03:10 (bildirim temizliğinden 10 dk sonra); hata loglanır, ertesi gece tekrar dener.
- **Kabul edilen yarış:** `check()` okuduktan sonra token tam o anda ölür ve silinirse `last_used_at` güncellemesi 500 verebilir (`issue()`'da da vardı).
- **Alternatifler:** bildirim temizliğinin içine (bir hata ikisini durdururdu); iptal edilenleri bir süre saklamak; hiçbir şey yapmamak.

### D-149 · E-posta başına ikinci login sınırı (#57)
Kaynak: #57 (M13b security review L4), `docs/plans/I57.md`, plan-reviewer. Fatih onayladı (2026-11-20): bütün IP'lerden saatte 20 hata → e-posta 1 saat kilitli; sıfırlama kilidi açar ve sıfırlayan tarayıcı 15 dk muaf; Settings şifre kontrolleri sadece IP + e-posta kilidi. SPEC §6.1.
- **Karar:** `LoginLockout`'ta ayrı e-posta deposu (1 saat pencere, 1 saat kilit, bellek sınırı `MAX_TRACKED_KEYS`). `is_locked`/`record_failure` `per_email` parametresi alır: web ve eklenti girişi `True`, Settings `False`. `clear` sadece çifti, `clear_email` ikisini de temizler. Sıfırlamadan sonra tarayıcı oturumunda `lockout_exempt` (e-posta + 15 dk): o tarayıcının web girişi e-posta kilidine bakmaz. Mesaj: "Too many attempts. Try again later, or reset your password."
- **Kabul edilen riskler:** e-postayı bilen biri sahibi 1 saat girişten alıkoyabilir (sıfırlama + 15 dk muafiyet çıkış yolu; eklentinin muafiyeti yok); bellek sınırı ~10.000 farklı adresle eski kayıtları düşürebilir (çift deposunda da aynı; kilitli kayıtlar için güncellemeye bakınız).
- **Alternatifler:** sadece Settings; hiçbir şey yapmamak; bekletme; CAPTCHA; sıfırlamada otomatik giriş.
- **Güncelleme (security-reviewer M1, L1, L3, L4; Fatih onayladı 2026-11-20):** Settings şifre kontrolleri için `user.id` başına üçüncü sayaç (bütün IP'lerden saatte 20 hata → 1 saat; mesaj "Too many wrong passwords. Try again later."): çalınmış oturumla IP değiştirerek tahmin edilemez, dışarıdaki biri dolduramaz. Sıfırlama ve hesap silme temizler. Depolar `threading.Lock` ile korunur; depo dolunca en eski 100 kayıt içinden önce kilitsiz (ya da kilidi bitmiş) olan düşer, hepsi kilitliyse en eskisi; muaf tarayıcının hataları da e-posta sayacına yazılır. **Kabul edilen risk (L2):** kontrol ile sayma arasında argon2 olduğu için aynı anda gelen istekler sınırı thread havuzu kadar aşabilir.

### D-150 · AI'sız MVP
Kaynak: Fatih (2026-11-20): Anthropic API için şimdilik ödeme yapmak istemiyor (claude.ai Pro aboneliği API'yi kapsamıyor); "A'yı tercih ediyorum". `docs/plans/MVP-no-AI.md`, plan-reviewer. SPEC v2.1.30.
- **Karar:** MVP AI'sız tamamlanır. V2'ye ertelenen: M5 (AI altyapısı, ilan çıkarımı, krediler, BYOK, maliyet raporu), fake door + Settings › Plan + Settings › AI + "Pro" (krediye dayandığı için; Claude karar verdi), M6b, M7, M12a, M12b, M8'in AI taslağı, M9'un AI kısımları (Should I apply?, `tailor_cv`, `readiness_check`, `import_cv`), onboarding sihirbazı (Fatih: V2; "Continue to setup" `/profile`'a gider), Job & details'teki yapılandırılmış ilan (Fatih: V2), `ai_result` bildirimi. SPEC'teki AI tasarımı silinmez, "V2 (D-150)" diye işaretlenir; işaretli şeyler MVP'de uygulanmaz (§0/8). Yeni sıra: M8a (mektup modeli ve editörü) → M8b (mektup PDF'i ve başvuru) → M9a (Apply sekmesi AI'sız 2 adım: Prepare documents → Final check; Fatih) → M13c → M13d. M8 ikiye bölündü (Fatih). Arayüzdeki AI vaatleri (doğrulama metinleri, giriş sayfası maddeleri, profil ipuçları, yasal sayfalar) kaldırıldı; e-posta doğrulaması bildirim ve hesap e-postalarını açar. Branch `mvp-without-ai` (kapsam işi, milestone değil).
- **Kabul edilen riskler:** ürünün ana farkı (AI) MVP'de yok, README'de "AI V2'de" diye anlatılır; Final check'in kural tabanlı hali (D-151, M9a) AI'lı tasarımdan zayıf; deploy (M13c) de maliyet doğurabilir, orada ayrıca karar verilir.
- **Alternatifler:** B: sadece BYOK ile AI, `FakeAIClient` ile geliştirme (prompt'lar gerçek modelle hiç denenmez, eval doğrulanamaz); C: API anahtarı gelene kadar beklemek.

### D-152 · `CoverLetter` modeli ve alanları (M8a)
Kaynak: `docs/plans/M8a.md`, plan-reviewer; Fatih onayladı (2026-11-21; gönderen adresi mektupta çok satırlı alan, yeni master'ın gövdesi boş + placeholder). SPEC §5.4, §6.6.
- **Karar:** Mektup düz kolonlardır, JSON değil: `title`, `language`, `sender_name/email/phone`, `sender_address` (çok satır, 500), `recipient_company/name`, `recipient_address` (çok satır, 500), `place`, `subject`, `salutation`, `body` (`Text`, uygulamada en fazla 10.000), `closing`, `source_profile_version` + CV ile aynı soy kolonları. `tone`, `length`, `ai_generated` V2 (D-150); `application_id` M8b'de (D-045 kalıbı). Sınır: kullanıcı başına 100 mektup. Editörde "Duplicate", "All documents" ve "← {Başvuru}" M8b'de (D-050: başvuru yokken Duplicate = Save as new version). Adresler `/cover-letters` (SPEC §4: `routers/cover_letters.py`, `templates/cover_letters/`).
- **Neden:** Mektubun iç içe bölümü yok; form alanları kolonlarla bire bir, okumak ve doğrulamak kolay. Profilde sokak adresi yok; mektupta çok satırlı alan profil, CV ve dışa aktarmayı değiştirmeden tam adres yazmaya izin verir.
- **Alternatifler:** CV gibi JSON içerik (şema sürümü gerekir, fayda yok); profile adres alanı (M8a'yı büyütür).

### D-153 · Ortak soy (lineage) yardımcıları (M8a)
Kaynak: `docs/plans/M8a.md`, plan-reviewer; **Claude karar verdi**, Fatih planı onayladı (2026-11-21).
- **Karar:** CV ve mektubun ortak master/kopya/versiyon kuralları (D-045, D-047, D-048, D-050; master yarışı, geri yükleme kontrolleri, "Nothing changed") `app/services/lineage.py`'de, model sınıfını parametre alarak. Hata sınıfları orada tanımlanır, `document_service` aynı isimle import eder. Sınır çağrı anında verilir (testler `MAX_CVS_PER_USER`'ı değiştirir). `lineage.py` başka servis import etmez.
- **Neden:** Aynı kural iki yerde yazılırsa bir gün ayrışır; mevcut CV testleri refaktörün davranışı değiştirmediğini gösterir.
- **Alternatifler:** CV kodunu mektup için kopyalamak; CV ve mektubu tek tabloda tutmak (SPEC §5.3/§5.4 ayrı tablo, içerik biçimleri farklı).

### D-154 · Mektupta dile göre varsayılanlar ve tarih (M8a)
Kaynak: `docs/plans/M8a.md`, plan-reviewer; **Claude karar verdi**, Fatih planı onayladı (2026-11-21).
- **Karar:** Selamlama ve kapanış D-055 gereği `document_labels.py`'de: DE "Sehr geehrte Damen und Herren," / "Mit freundlichen Grüßen"; EN "Dear Hiring Team," / "Kind regards,"; TR "Sayın Yetkili," / "Saygılarımla,". Tarih mevcut `full_date` ile (DE/TR `21.11.2026`, EN `21 November 2026`; DIN 5008 sayısal tarihi kabul eder). M8a'da tarih her zaman bugün, `clock.today()` ile kullanıcının saat diliminde (D-109); kaydedilmez, M8b'de kilitte sabitlenir. Yeni master: gönderen alanları ve `place` profilden, konu ve gövde boş. Yeni kopya master'ın bütün alanlarını alır. "Refresh from profile" sadece gönderen alanlarını yeniler ve CV'deki gibi onay ister.
- **Alternatifler:** Ay adlarıyla tarih (yeni sözlük, DE/TR için ek iş); tarihi elle girilen alan yapmak (unutulur, eski tarihli mektup gider).

- **Güncelleme: D-155** (2026-11-21): deploy da MVP'den çıktı (para ödenmeden); MVP kişisel local ortamla tamamlanır.
- **Güncelleme: D-160** (2026-11-21): kilitli mektubun tarihi gönderim anında sabitlenir.

### D-155 · Deploy yok, kişisel local ortam
Kaynak: Fatih (2026-11-21): domain ve hosting için şimdilik ödeme yapmak istemiyor, uygulamayı önce kendi bilgisayarında gerçek başvurularla kullanmak istiyor. Issue #84, `docs/plans/P1-personal-instance.md`, plan-reviewer. SPEC v2.1.32.
- **Karar:** M13c (deploy) MVP'den çıkar ("Sonra"); #17, #18 ve #64 E3 onunla bekler. MVP sırası: M8a → M8b → M9a → M13d. M13d README'si canlı URL yerine "kendi bilgisayarında çalıştır" adımları, ekran görüntüleri ve mimari şema içerir. Günlük kullanım için kişisel local ortam: `docker-compose.personal.yml` (proje `unfurl-personal`, kendi Postgres'i ve volume'ları `unfurl-personal_db`, `unfurl-personal_uploads`; proje adı ve port değişkenle değişir, böylece test projesi gerçek volume'lara dokunmaz), `http://127.0.0.1:8100` (sadece bu bilgisayar; `localhost` değil, çünkü çerezler host'a göre ayrılır ve dev `localhost:8000`'de), `restart: unless-stopped`, her komut `--env-file .env.personal` ile (compose değişkenleri `env_file:`'dan okunmaz), `SECRET_KEY`/`ENCRYPTION_KEY`/`POSTGRES_*` zorunlu. `scripts/backup_personal.sh` (`pg_dump -Fc` + fotoğraf arşivi, `~/Backups/unfurl/`, `umask 077`, doğrulanmadan sayılmaz, son 14) ve `scripts/restore_personal.sh` (önce doğrular, `yes` sorar, otomatik ön yedek, şemayı sıfırlayıp tek transaction'da yükler, sonra `alembic upgrade head`).
- **Kabul edilen riskler:** migration'lar gerçek veriye uygulanır (güncellemeden önce yedek; geri yükleme eski şemayla test edildi); yedekler şifresiz yerel dosyalar (izinler 700/600, FileVault önerisi); Docker kapalıyken uygulama çalışmaz; hatırlatma e-postaları teslim edilmez (loglarda).
- **Alternatifler:** ücretsiz hosting katmanları (sınırlı/süreli Postgres, uyuyan servis); dev veritabanını günlük kullanmak (test hesapları ve migration denemeleri gerçek veriye dokunur).
- **Güncelleme (security-reviewer F1–F8, E7, E9):** Geri yükleme önce bütün yedeği SQL'e çözerek doğrular, kilit alır (yedek ve geri yükleme aynı anda çalışmaz), ön yedekte eski yedekleri silmez (geri yüklenecek dosyayı silebilirdi), şemayı boşaltma ve yüklemeyi tek transaction'da yapar (başarısız olursa mevcut veri kalır). Yedek dosya adına süreç numarası, klasöre her seferinde `chmod 700`, bütün `.env.*` dosyaları git'e ve imaja girmez (örnekler hariç git'te). Compose `APP_ENV=development`, `DEBUG=false`, `EMAIL_BACKEND=console`'u sabitler; `scripts/start_personal.sh` `SECRET_KEY`'in en az 32 karakter olduğunu kontrol eder; app logları 2×5 MB ile sınırlı. `.env.personal` yedeğe girmez; README güvenli bir yerde saklanmasını söyler. Açık kalan (ayrı issue): Host header kontrolü (`TrustedHostMiddleware`, DNS rebinding; oturum çerezi gitmediği için sadece girişsiz işlemler).

### D-156 · Kişisel ortamın modu ve eklenti
Kaynak: `docs/plans/P1-personal-instance.md`, plan-reviewer; Fatih önerileri onayladı (2026-11-21).
- **Karar:** Kişisel ortam `APP_ENV=development`, `DEBUG=false`, `EMAIL_BACKEND=console`: production modu SMTP ister (`config.py`), kod değişmez. Sonuçları: `/docs` açık (sadece loopback), çerez `Secure` değil ve `__Host-` önekli değil; SPEC §10.1'deki "Secure çerez" kuralı kişisel ortamda karşılanmaz, sadece loopback üzerinden http olduğu için kabul edilir. Production'ın anahtar kontrolleri yerine compose zorunlu değişkenleri. E-posta linkleri `docker compose … logs app`'te. Eklenti: `host_permissions` tam olarak `http://localhost:8000/*` ve `http://127.0.0.1:8100/*`; `config.js` varsayılanı kişisel ortam; eklentiyi geliştirirken `config.js` tek satır 8000 yapılır ve yeniden giriş gerekir (tek token saklanır; TESTING.md uyarır).
- **Alternatifler:** production + Mailpit (yeni imaj; e-postalar tarayıcıda); yeni `APP_ENV=personal` modu (config ve test işi); eklenti varsayılanı dev kalır (her `git pull`'da yerel değişiklik çakışır).

## M8b — Mektup: PDF ve başvuru (2026-11-21)

Kaynak: `docs/plans/M8b.md`, plan-reviewer (1 Blocker, 7 Should, 6 Nit; hepsi plana işlendi); Fatih planı ve üç sorunun önerilen cevaplarını onayladı (2026-11-21). SPEC v2.1.34.

### D-157 · Mektubu başvuruya bağlama
- **Karar:** D-080'in aynısı. `CoverLetter.application_id` (FK → application, `SET NULL`) = soy; `Application.cover_letter_id` (FK → coverletter, `SET NULL`, unique, `use_alter`) = şu anki mektup. Master bağlanmaz. Detay sayfasındaki Documents kartında CV'nin altında Cover letter bölümü: dil başına "Copy my master letter ({DİL})" (başlık "{Company} · Cover letter", 200 karaktere kesilir), "Use an existing copy", "Open", "PDF", taslakta "Remove", gönderilmiş başvuruda "Lock as sent". Hepsi toast + Undo (token'da `created_letter`, `linked_letter`; `NOTE_FIELDS`, `_from_text`, `creation_fingerprint` ve `undo_change`'in sahiplik kontrolü `cover_letter_id`'yi bilir). Route'lar CV'ninkiyle aynı kurallarda (arşivli başvuru 409, `letter=` ile master 422, 60/dk).
- **Alternatifler:** CV ve mektup için tek "documents" ilişki tablosu (M4c kodunu ve testlerini baştan açar).

### D-158 · Başvuru için kopyada ön doldurma
- **Karar:** Master'dan bir başvuru için kopya oluşurken **boş** alanlar ilandan dolar: `recipient_company` ← şirket, `recipient_name` ← iletişim kişisi, `subject` ← dile göre kalıp (DE "Bewerbung als {job_title}", EN "Application for {job_title}", TR "{job_title} pozisyonu için başvuru"; `job_title` konu 200 karaktere sığacak şekilde kesilir). Dolu alanlara dokunulmaz. Değerler kopya kaydedilmeden önce verilir, böylece kopya `never_edited` kalır ve Undo onu silebilir.
- **Alternatifler:** Hiç ön doldurmamak (her başvuruda aynı üç alanı elle yazmak); dolu alanların üzerine yazmak (master'daki bilinçli metni ezer).

### D-159 · Gönderilince mektubu da kilitle
- **Karar:** D-082'nin aynısı: Applied, Interview veya Offer'a her geçişte (board, durum seçici, mülakat modalı, arşivden dönüş dahil) bağlı taslak CV ve bağlı taslak mektup tek transaction'da kilitlenir (`lock_sent_documents`). `ApplicationDocument.cover_letter_id` yeni kolon; her satırda `doc_type`'a göre `document_id` ya da `cover_letter_id` dolu (kod kuralı, CHECK değil: `SET NULL` ikisini de boşaltabilir). Snapshot: mektubun bütün alanları + `"date"` (gönderim anındaki tarih satırı). Undo token'ında `locked_letters`; kilit açma sadece `cover_letter_service.unlock_after_undo`'da. "Lock as sent" her belge için ayrı.
- **Alternatifler:** `document_id`'yi FK'siz genel kimlik yapmak (sahipsiz satır riski); `document_id`'yi `cv_id` olarak yeniden adlandırmak (M4c kodu ve testleri değişir).

### D-160 · Mektup tarihi gönderimde sabitlenir
- **Karar:** Taslakta tarih satırı bugün (kullanıcının saat diliminde). Kilitli mektupta tarih `ApplicationDocument.content_snapshot["date"]`'ten, yoksa `locked_at`'ten (kullanıcının saat diliminde). Yeni kolon yok.
- **Alternatifler:** Mektupta `letter_date` kolonu (aynı bilgi iki yerde).

### D-161 · Mektup PDF'i
- **Karar:** Önizlemedeki `pdf/cover_letter.html` aynen kullanılır, WeasyPrint için `pdf/_letter_page.html` sarmalayıcısıyla (sadece `static/` okunur, D-059). `GET /cover-letters/{id}/pdf`: giriş, `user_id`, 20/dk, kilitlide de. Dosya adı `CoverLetter_{Ad}_{Şirket?}_{lang}.pdf`: ad `sender_name`'den, şirket bağlı başvurudan, yoksa `recipient_company`'den (Fatih); D-057/D-085'teki ASCII + `filename*` kuralları. `pdf_filename` ve `content_disposition` genelleşir, CV davranışı aynı kalır.
- **Alternatifler:** Şirketi sadece başvurudan almak (başvurusuz mektupta şirket adı kaybolur).

### D-162 · Editörde başvuru, Duplicate ve Copy text
- **Karar:** D-084'ün aynısı: başvuru `letter.application_id`'den, `?application=` sadece onunla aynıysa: "← {Company}", "All documents", bant "Copy for {Company}, based on {Master}. Your master letter stays unchanged.", versiyon seçicide aynı başvurunun versiyonları + master. Bağlı kopyadan "Save as new version" `application_id`'yi taşır ve **başvuru gönderilmemişse** başvurunun şu anki mektubu olur (M4c E1). "Duplicate": bağsız yeni taslak "… (copy)". "Copy text": selamlama + gövde + kapanış + gönderen adı düz metin olarak panoya (Fatih), `app.js`'te Alpine bileşeni, inline script yok.
- **Alternatifler:** Copy text'te bütün mektup (adresler ve tarih başvuru formlarının mesaj kutusunda fazlalık).

### D-163 · Documents sayfasında gruplama ve silmede bağ
- **Karar:** D-084 ve D-086'nın aynısı: Cover letters sekmesinde kopyalar başvuruya göre gruplanır, bağsızlar "Not linked to an application". Bağlı taslak mektup silinince başvurunun `cover_letter_id`'si boşalır; Undo `restore_cv`'nin üç koşuluyla yeniden bağlar (başvuru sahibinin, `cover_letter_id` boş, durum `saved`); başvuru yoksa `application_id` temizlenir.
- **Alternatifler:** Silmede bağı hiç geri kurmamak (Undo yarım kalır).

## M9a — Apply akışı, AI'sız (2026-11-23)

Kaynak: `docs/plans/M9a.md`, plan-reviewer (1 Blocker, 7 Should, 5 Nit; hepsi plana işlendi); Fatih planı ve dört sorunun önerilen cevaplarını onayladı (2026-11-23). SPEC v2.1.35.

### D-151 · Final check: kural tabanlı kontrol listesi
- **Karar:** Apply › Final check AI'sız, kodla ve veritabanındaki alanlardan 8 kontrol yapar (`final_check_service`); her biri `ok`, `warn` ya da `missing`, eksiklerde düzeltme sayfasına link:
  - `cv_linked` (yoksa missing → Apply › Prepare), `cv_contact` (CV'de ad ve e-posta; missing → profil + "Refresh from profile" notu, çünkü bu alanlar profilden gelir), `cv_content` (görünen bir deneyim ya da özet; warn → özet için CV editörü, deneyim için profil);
  - `letter_linked` (warn: mektup isteğe bağlı), `letter_recipient` (mektubun alıcı şirketi başvurunun şirketi mi; büyük/küçük harf, boşluk ve GmbH/AG/KG/SE/Ltd/Inc/e.U./OG gibi ekler yok sayılır; warn → mektup editörü), `letter_body` (boşsa missing), `language` (CV ve mektup aynı dilde mi; warn). Mektup bağlı değilse son üçü listelenmez;
  - `posting_open` (kapanış tarihi kullanıcının saat diliminde geçmiş mi; warn → Job & details).
  - Liste **hiçbir şeyi engellemez** (Fatih): "I have applied" uyarılara rağmen çalışır; durum seçici zaten her geçişe izin verir (D-076). Kilitli belgede düzeltme linki yok. Sayfa sayısı (PDF çizmek gerekir) ve anahtar kelime/dil tespiti (AI) yok.
- **Alternatifler:** `missing` varken "Mark as applied"ı kapatmak (durum seçiciyle aşılır, kullanıcıyı gereksiz durdurur); PDF sayfa sayısını kontrol etmek (her açılışta ~1 sn WeasyPrint).

### D-164 · Başvuru detayında sekmeler
- **Karar:** **Apply** (durum `saved`) / **Progress** (`applied`, `interview`, `offer`, `rejected`) ve **Job & details**. Sekme adresle: `/applications/{id}` ilk sekme, `?tab=details`; düz linkler, JS yok; bilinmeyen değer ilk sekme. `application-changed` yenilemesi bulunulan sekmenin adresini çeker. Sağ panel (Next step, Documents, Timeline) iki sekmede de aynı. Arşivli başvuruda sekme arşivden önceki duruma göre, işlemler gösterilmez ("Restore it to prepare documents.").
- **Alternatifler:** Alpine ile istemci tarafı sekmeler (yenilemede kaybolur, adres paylaşılamaz).

### D-165 · Apply sekmesi akışı
- **Karar:** ① **Prepare documents:** CV ve mektup satırları, M8b'deki formlar ve route'lar (master'dan kopya, mevcut kopyayı kullan), bağlıysa "Preview & edit". CV ve mektup satırları ortak bir makroda (`id_prefix`), Documents kartıyla aynı; sayfada id'ler tekil. Adım **CV bağlanınca** tamamlanır ve daralır (mektup isteğe bağlı). ② **Final check:** CV bağlanana kadar pasif; D-151 listesi, "Ready? Send it yourself, then come back" kutusu, iki ayrı link "CV (PDF)" ve "Cover letter (PDF)" (Fatih), "I have applied · mark as applied" = mevcut `POST /applications/{id}/status` ile `applied` (kilit, `ApplicationDocument`, toast + Undo tek yerden, D-159). Applied'dan Saved'e dönen başvuruda kilitli belge kilitli kalır, yerine başkası bağlanamaz; tekrar "Mark as applied" ikinci kilit ya da kayıt oluşturmaz.
- **Alternatifler:** Birleşik tek PDF (`pypdf` çalışma zamanına girer, portallar çoğunlukla ayrı dosya ister).

### D-166 · Belge paneli (drawer)
- **Karar:** "Preview & edit" mevcut `components/drawer.html` makrosunu (native `<dialog>`, sağ kenar; önizleme için daha geniş) mevcut `data-dialog-load` ile açar (izin listesine `/applications/`). İçerik `GET /applications/{id}/drawer?doc=cv|cover_letter` (giriş, `user_id`, 60/dk; bağlı belge yoksa 404, bilinmeyen `doc` 422): CV / Cover letter sekmeleri, ad · vN · rozet, editördeki önizlemenin aynısı (CSS'ler detay sayfasında yüklenir; mektubun `preview_context`'i servise taşınır), "Open full editor", "Download PDF". Panel her zaman salt okunur; kilitli belgede "This is what you sent." Panel `#application-page`'in dışında durur.
- **Alternatifler:** Panelde düzenleme (editörün kopyası olur; asistanla düzenleme V2).

### D-167 · Progress görünümü
- **Karar:** "Applied on {applied_at}" kartı (`applied_at` boşsa, ör. Saved → Rejected: kart yok, "You did not apply to this job."), başvurunun şu anki CV ve mektubu (rozet, "Preview", "PDF", kilitli değilse "Lock as sent"). "What happens next": next step'in kendi başlığı ve tarihi (`next_step_service`; elle değişmiş adım da doğru adıyla), `applied` iken "I got an interview" (= durum `interview`, mülakat modalı). "How you applied" (daraltılmış): timeline olaylarından (belge bağlama, kilit, Applied) tarihleriyle; Final check sonucu saklanmaz (Fatih). "Draft a follow-up email" V2.
- **Alternatifler:** "Mark as applied" anında Final check özetini timeline'a yazmak (board ve durum seçiciden geçişte olmaz, tutarsız).

## T1 — Dört küçük teknik borç (2026-11-23)

Kaynak: `docs/plans/T1-tech-debt.md`, issue #85, #88, #89, #90; plan-reviewer (3 Blocker, 7 Should, 4 Nit; hepsi plana işlendi); Fatih planı ve iki sekme cevabını onayladı (2026-11-23). SPEC v2.1.36.

### D-168 · Host başlığı kontrolü (#85)
- **Karar:** Starlette `TrustedHostMiddleware`; izinli adlar `APP_BASE_URL`'in host'u, `localhost`, `127.0.0.1` ve sadece development'ta `testserver` (test istemcisi; DNS rebinding'de Host saldırganın alan adıdır). Başka Host → 400. Middleware `SecurityHeaders`'ın içinde, `ExtensionApiGuard`'ın dışında: 400 cevabı güvenlik başlıklarını taşır, kötü Host'un gövdesi okunmaz. Yeni env değişkeni yok; kişisel ortamı başka bir adla açan `APP_BASE_URL`'i o ada ayarlar.
- **Alternatifler:** Sadece kişisel ortamda açmak (dev'de aynı risk); `[::1]` eklemek (Starlette'in host ayrıştırması köşeli parantezli IPv6'yı tanımaz, kullanılmıyor).

### D-169 · "Edit master" kilidi oturuma bağlı bir değerle (#89, D-048'i tamamlar)
- **Karar:** Onay penceresindeki "Edit master" htmx POST'u gönderir (`POST /cvs/{id}/edit-master`, `POST /cover-letters/{id}/edit-master`; giriş, CSRF, `user_id`, 30/dk; master değilse 409, başkasınınki 404). Oturumda belge başına rastgele bir değer saklanır (varsa yeniden kullanılır, Fatih: iki sekme de çalışır) ve editör `?unlock={değer}` ile açılır. Editör, form kaydı ve "Refresh from profile" kilidi sadece değer oturumdakiyle eşleşirse (`secrets.compare_digest`) açık sayar. Eski `?edit_master=confirmed` yok sayılır. Çıkışta oturumla birlikte silinir.
- **Alternatifler:** Kilidi oturumda süreli tutmak (master'ı yeniden açınca habersiz açık gelir); her onayda yeni değer (eski sekmenin yazdıkları kaybolur).

### D-170 · Çift tıklama kilidi formun kendisinde (#90, D-126'yı günceller)
- **Karar:** `<body hx-disabled-elt="find [type=submit]">` kalkar. Değişiklik gönderen her eleman kendi `hx-disabled-elt`'ini taşır: formlar `find [type=submit]` (otomatik kaydeden ve filtre formları `unset`), kendi başına istek atan butonlar ve alanlar `this`. Böylece kural miras kalmaz, formu olmayan `hx-get` elemanları konsola hata yazmaz. `tests/test_loading.py` her yazan elemanı ve `base.html`'in kuralı taşımadığını kontrol eder.
- **Alternatifler:** Kuralı `<body>`'de bırakıp formu olmayan elemanlara `unset` eklemek (her yeni `hx-get`'te hata geri gelir).

### D-171 · Form alanı üst sınırı (#88)
- **Karar:** Uygulama genelinde bir form en fazla 1100 alan taşır (`MAX_ANY_FORM_FIELDS`; en büyük meşru form CV editörü: 1000 madde onay kutusu + 7 bölüm + ~10 alan). `verify_csrf` formu bu sınırla ayrıştırır (Starlette önbelleğe alır); daha küçük sınırı olan route'lar (mektup 50, fotoğraf 5) ayrıştırmadan sonra alanları sayar ve fazlasını 400 ile reddeder. Gövde ham olarak okunmaz (FastAPI `Form()` route'larında akışı önceden tüketir; ham okuma sınırsız bellek demektir).
- **Alternatifler:** Ham gövdeden token'ı ayrıca okumak (login gibi `Form()` route'larında 500, bellek sınırı yok); Starlette'in önbelleğini silmek (özel alan).

## M13d — Portföy README'si (2026-11-23)

Kaynak: `docs/plans/M13d.md`, issue #94; plan-reviewer (0 Blocker, 7 Should, 5 Nit; hepsi plana işlendi); Fatih planı ve altı sorunun cevaplarını onayladı (2026-11-23). SPEC v2.1.37.

### D-172 · README portföy sayfası, işletim ayrıntıları ayrı dosyada
- **Karar:** Repo herkese açık; `README.md` portföy okuyucusu için: ne olduğu, ekran görüntüleri, özellikler, mimari, nasıl yapıldı, What I learned, kendi bilgisayarında çalıştır (dev ve kişisel ortam; önkoşullar ve kişisel ortam uyarısı README'de kalır), belgeler. Ayrıntılı işletim notları `docs/DEVELOPMENT.md`'ye taşınır, metin kaybolmaz. Dürüst kapsam: AI'sız MVP, AI V2'de (D-150); canlı demo yok (D-155). Lisans dosyası yok, "All rights reserved" (Fatih). Emek satırı: "Built by Fatih Findik (product owner) with Claude Code".
- **Alternatifler:** Her şeyi README'de tutmak (uzun, portföy okuyucusu kaybolur); MIT lisansı (Fatih şimdilik istemedi, sonra eklenebilir).

### D-173 · Ekran görüntüleri sahte verili demo hesaptan
- **Karar:** `scripts/seed_demo.py` kurgusal "Jana Novak"ı (fotoğrafsız) ve verisini uygulamanın servisleriyle yazar. Sadece `DATABASE_URL` SQLite ve dosya adı `demo.db` iken çalışır (hiçbir `app` modülünü import etmeden kontrol eder) ve veritabanında kullanıcı varsa durur; şifre çalışırken üretilir, sadece terminale yazılır. Tarihler bugüne göre, durumlar ve kilitler servislerle. Görüntüler açık temada (Fatih), 1440 px, ayrı bir Chrome profilinde çekilir (çerezler port ayırmaz; kişisel yer imi/otomatik doldurma görünmez), `docs/screenshots/` altında. Eklenti görüntüsü için demo bir kez `localhost:8000`'de çalışır (eklenti sadece 8000 ve 8100'e bağlanır, D-156).
- **Alternatifler:** Prototip ekranları (uygulamanın kendisi değil); kendi hesabından bulanıklaştırılmış görüntü (gerçek veri, herkese açık repo).

### D-174 · Mimari şema Mermaid ile
- **Karar:** README'de GitHub'ın çizdiği Mermaid `flowchart`: tarayıcı (HTMX, Alpine, Tailwind) ve Chrome eklentisi (Bearer, `/api/ext`) → FastAPI middleware (TrustedHost, SecurityHeaders, Session, CSRF) → routers → services → SQLModel → PostgreSQL/SQLite; WeasyPrint, APScheduler, e-posta, Alembic; kişisel ortam Docker Compose.
- **Alternatifler:** Çizim aracıyla PNG (güncellemesi zor, metin olarak incelenemez).

## M14a — Mülakat hazırlığı: Interview prep sekmesi (2026-11-26)

Kaynak: `docs/plans/M14a.md`, issue #43; plan-reviewer (0 Blocker, 10 Should, 6 Nit; hepsi plana işlendi); Fatih bütün sorularda önerileri seçti ve planı onayladı (2026-11-26). SPEC v2.1.38.

### D-175 · Mülakat hazırlığı AI'sız, iki milestone
- **Karar:** #43 AI'sız yapılır ve ikiye bölünür: M14a başvuruya bağlı Interview prep sekmesi (olası sorular ve cevap taslakları, şirket ve rol notları, soracağın sorular), M14b hesap düzeyinde STAR hikâye bankası. AI ile soru üretme, uyum analizinden "refresh" görevleri ve mock interview V2 (D-150). AI'sız hazırlık herkese ücretsiz: `entitlements` kontrolü yok; SPEC §17.1'de Pro değeri "AI ile mülakat hazırlığı" olur.
- **Alternatifler:** Tek milestone (2 haftayı aşar, WORKFLOW §2); AI'ı beklemek (API anahtarı yok); Pro'ya bağlamak (ödeme V3, MVP'de anlamsız).

### D-176 · Interview prep sekmesi
- **Karar:** Başvuru detayında üçüncü sekme, `?tab=interview`, sıra Apply/Progress · Interview prep · Job & details. Durum `applied`, `interview`, `offer` veya `rejected` iken görünür; `saved`'da gizli (adresle açılırsa ilk sekme; veri silinmez). Durum `interview` iken etikette mor nokta (`aria-hidden`, ekran okuyucu için "(interview stage)"). Arşivli başvuruda salt okunur ("Restore it to edit your prep."), bütün yazma endpoint'leri 409.
- **Alternatifler:** Sadece `interview`'da göstermek (hazırlık mülakat gelmeden başlar); Progress içinde bölüm (sekme çok uzar); ayrı sayfa (sağ panel ve gezinme tekrar eder).

### D-177 · Veri modeli ve router
- **Karar:** Yeni tablo `InterviewQuestion` (user_id, application_id, kind `likely`/`to_ask`, category sadece `likely`'de, text ≤ 300, answer ≤ 5000, asked, position) ve `Application.interview_notes` (≤ 10 000), `Application.interview_prep_started_at`. Ayrı "prep" tablosu yok. Endpoint'ler ayrı `app/routers/interview_prep.py`'de, yollar `/applications/{id}/prep/…` (`/interview` mülakat tarihi modalının). Dışa aktarmada `interview_questions` bölümü; hesap silinince cascade.
- **Alternatifler:** İki tür için iki tablo (aynı kolonlar); notları ayrı tabloda tutmak (başvuru başına tek alan).

### D-178 · Hazır soru seti, butonla ve bir kez
- **Karar:** Kodda sabit İngilizce 12 olası soru (General, Behavioural, Role, Company; `{company}` şirket adıyla dolar) ve 4 "to ask" sorusu. GET veri yazmaz (D-142); boş sekmede "Start interview prep" (set) ve "Start with an empty list" (set yok) butonları. Başlama atomik: `interview_prep_started_at IS NULL` koşullu UPDATE, sorular sadece bir satır değiştiyse eklenir; iki sekme ya da çift tıklama tek set verir. Sorular silinse de set geri gelmez.
- **Alternatifler:** İlk açılışta otomatik ekleme (GET'te yazma); belge diline göre üç set (Fatih istemedi).

### D-179 · Kaydetme, düzenleme, silme ve sınırlar
- **Karar:** Cevap taslağı, "their answer" notu ve şirket notları yazarken kaydedilir (`input delay:500ms`, `hx-sync="this:replace"`, `hx-disabled-elt="unset"`, "Saved"); bu endpoint'ler sadece "Saved" parçasını döner. Soru metni htmx ile satırda düzenlenir (`GET …/edit` form, `GET …` okuma hâli, `POST …` kaydet; JS yok). Hiçbir prep endpoint'i `application-changed` göndermez; ekle/sil kendi kartını, asked kendi satırını değiştirir, "{n} of {m} answered" out-of-band güncellenir. Başka işlemler sayfayı yenilediğinde son ~0,5 sn'de yazılan kaybolabilir: kabul edildi. Silme toast + Undo (`DeletedItem` kind `interview_question`); Undo başvuru arşivliyse ya da 100 soru doluysa "Nothing to undo.". Sınırlar: başvuru başına 100 soru; fazla form alanı 400 (D-171); geçersiz tür/kategori, `to_ask` + kategori, boş ya da uzun metin 422; geçersiz `qid` 404. Sıra: kategori, sonra `position`; sürükle-bırak yok. Timeline'a olay yok.
- **Alternatifler:** "Save" butonu (mektup editöründen farklı); Alpine ile düzenleme (CSP build'de ayrı bileşen, gereksiz JS); prep kartlarını sayfa yenilemesinin dışında tutmak (karmaşık şablon).

### D-180 · Bildirim ve Progress'ten sekmeye link
- **Karar:** "Interview tomorrow" bildirimi `/applications/{id}?tab=interview`'a gider (yeni bildirimler; eskiler değişmez). Progress › "What happens next" durum `interview` iken "Prepare for it →" gösterir.
- **Alternatifler:** Bildirimin başvuru sayfasına gitmesi (bir tık fazla).
