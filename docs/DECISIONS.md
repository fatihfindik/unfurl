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
  | uv | 0.13.7 | Dockerfile, CI |
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
  - **M11 için:** zamanlayıcı önce SQL'de daraltır (durum, `applied_at`, `interview_at`, `next_action_at` tarih aralığı), sonra aynı `next_step()`'i çağırır; bildirim tekrarını `(application_id, type, at)` anahtarı önler.

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

### D-081 · Versiyon numarası master başına kalır
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** SPEC "aynı başvuru için 1, 2, 3" diyor; D-045'teki unique index `(parent_id, version)` master başına numaralıyor. Index'i değiştirmek bütün M3 kurallarını yeniden açar. Numara master başına kalır, ekranda başvurunun adıyla gösterilir ("Kranich Health · v3"). SPEC §5.3'e not düşülür.

### D-082 · Gönderilince kilitle
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** Bir başvuru **Saved'den** Applied, Interview veya Offer'a geçince (board, seçici, mülakat kaydı) bağlı taslak CV kilitlenir ve `ApplicationDocument(application_id, doc_type="cv", document_id, content_snapshot=cv.content, sent_at=now)` oluşur; timeline'a `document_linked` event'i ("CV v3 locked: this is what you sent"). Zaten gönderilmiş (Applied/Interview/Offer) bir başvuruya CV bağlamak da hemen kilitler. Undo kilidi açar ve `ApplicationDocument`'ı siler (token: `locked_cv_ids`, `sent_ids`; Undo sadece CV hâlâ o başvuruya bağlıysa ve başka bir belge kaydı eklenmediyse çalışır). Kilit `document_service.lock` ile (kurallar tek yerde).
- **Güncelleme (plan-reviewer):** Başvuru Applied, Interview veya Offer'a **her geçişte** (nereden olursa olsun) bağlı CV **taslaksa** kilitlenir. Zaten gönderilmiş başvuruya bağlanan CV taslak kalır; "Lock as sent" butonu aynı kilitlemeyi elle yapar. Durum değişikliği, kilit, `ApplicationDocument` ve event **tek transaction**: `document_service.lock(..., commit=False)`.
- **Güncelleme (plan-reviewer):** Token `locked_cv_ids` + her birinin `locked_at`'i ve `sent_ids` taşır. Undo sadece hâlâ kilitli ve `locked_at`'i token'dakiyle aynı olan CV'lerin kilidini açar; `ApplicationDocument`'ları `user_id` + `application_id` + satır sayısı kontrolüyle siler. Kilit açma fonksiyonu sadece `document_service`'tedir ve sadece bu Undo'dan çağrılır. **CLAUDE.md'deki "kilitli versiyon asla düzenlenmez" kuralına bu tek istisna:** içerik değişmez, yanlışlıkla yapılan gönderim 60 sn içinde geri alınır. CLAUDE.md'yi Claude değiştirmez; Fatih'e not olarak bırakılır.

### D-083 · `ApplicationDocument` tablosu
Kaynak: M4c Design fazı, plan-reviewer; Fatih'in yerine Claude karar verdi (2026-11-17, D-087).
- **Karar:** SPEC §5.4b alanları + `user_id` (her sorgu `user_id` ile filtrelenir), `document_id` `ON DELETE SET NULL` (kilitli belge silinemez ama savunma). `content_snapshot` JSON. Kapak mektubu M8'de aynı tabloyu kullanır (`doc_type`).
- **Güncelleme (plan-reviewer):** `ApplicationDocument.application_id` `ON DELETE CASCADE`. **D-085:** Kilitli CV'nin PDF adında şirketin bugünkü adı kullanılır (kabul).

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

### D-087 · Gece yetkisi
Kaynak: Fatih, 2026-11-17 (sohbet).
- **Karar:** Fatih 2026-11-17 gecesi ürün kararlarını, PR merge'ünü ve etiketi Claude'a bıraktı ("bana ihtiyacın olmayacak şekilde"; M4c ve sonraki AI'sız milestone'lar, D-088). WORKFLOW'daki "Fatih merge eder" ve "kabul kriterlerini Fatih elle dener" maddeleri bu milestone için ertelendi: elle test sabah yapılır, gerekirse düzeltme PR'ı açılır.

### D-088 · AI'ya bağlı milestone'lar sonraya (sıra değişikliği)
Kaynak: Fatih, 2026-11-17 (sohbet: "ai ile olan adımı atlayıp devam etsen?").
- **Karar:** M4c'den sonra AI'ya bağlı milestone'lar (M5, M7, M8, M9, M12a, M12b) gerçek API anahtarı, maliyet ve prompt kararları gerektirdiği için Fatih dönene kadar atlanır; AI'sız olanlar (M10 Learning, M11 Bildirimler, M13'ün deploy dışı parçaları) önce yapılır. M6 (eklenti) Chrome'da elle kurulup denenmesi gerektiği için Fatih'le yapılır. CLAUDE.md'deki "sırayla" kuralına bu bir istisnadır.
- **Gerekçe:** Fatih yokken ilerlenebilecek işler bunlar; AI özellikleri gerçek çağrı ve kalite değerlendirmesi olmadan "bitti" sayılamaz.
- **Alternatifler:** Sırayı korumak ve M5'i `FakeAIClient` ile yazıp PR'ı açık bırakmak.

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
- **Karar:** İşaretlenince `done=true`, `done_at=now`, görevin tahmini süresiyle bugüne bir `StudyLog`, göreve bağlı kaynak varsa `units_done += 1` (en fazla `units_total`). İşareti kaldırmak bunların tersini yapar. Toast + Undo (`undo-learning` tuzu, 60 sn, karşılaştır-ve-yaz). Haftalık saat: bu haftanın (Pzt–Paz) StudyLog dakikaları; hedefi: aktif hedeflerin `weekly_hours_target` toplamı. Üst kartlar ve Goals kartı aynı yanıtta `hx-swap-oob` ile güncellenir.

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

### D-096 · Learning görünüm detayları
Kaynak: M10 Design fazı, plan-reviewer; Fatih plan onayıyla onayladı (2026-11-17).
- **Karar:** `progress_pct` saklanmaz: kaynakta `units_total` varsa `units_done/units_total`, yoksa tamamlanan/tüm görevler, görev yoksa 0. Hedef rengi saklanmaz, hedeflerin sırasına göre 6 renklik sabit paletten gelir. All tasks grupları: Overdue, This week (bugün … Pazar), Next week, Later; boş grup gösterilmez. Tamamlananlar her sekmenin altında daraltılmış "Done (n)" bölümünde: Today ve Next 7 days'te o tarih aralığındakiler, Catch-up'ta bugün tamamlanan eski görevler, All tasks'ta son 30 gün. Today: bugün; Next 7 days: bugün … +6; Catch-up: geçmiş ve açık.
