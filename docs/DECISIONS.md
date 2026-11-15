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
- **Karar:** M1a migration'ı User'a sadece M1'in kullandığı kolonları ekler: `email`, `password_hash`, `full_name`, `is_verified`, `privacy_accepted_at`, `last_login_at`, `session_version`, `plan`, `plan_expires_at`, `created_at`, `updated_at`. Diğer §5.1 kolonları kendi milestone'larında küçük migration'larla gelir: `theme` (M4), `default_doc_language` (M3'ten Settings sayfasına ertelendi, Fatih 2026-11-15), `timezone` (M10/M11), `anthropic_api_key_encrypted` (M7), `ui_locale` (ihtiyaç olunca).
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
