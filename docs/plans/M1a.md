# M1a — Oturum çekirdeği · Teknik plan (Design fazı)

## Context
M0 boş ama çalışan bir iskelet bıraktı: kullanıcı yok, her sayfa herkese açık. M1a (issue #8, SPEC v2.1.3 §12 › M1a, §6.1, §5.1, §17.2, D-020…D-024) kullanıcıların güvenli şekilde kayıt olup giriş/çıkış yapmasını, uygulama sayfalarının oturum istemesini ve plan/yetki altyapısını getirir. E-posta, doğrulama, şifre sıfırlama ve login kilidi **M1b**'dedir (issue #9). Bu milestone issue #4'ü (open redirect) kapatır.

Onaydan sonra: bu plan `docs/plans/M1a.md` olarak kaydedilir ve issue #8'e yorum olarak eklenir (WORKFLOW §2 Design çıktısı). Branch: `m1a-auth-core` (açık, `badafa6` üzerinde).

Kararlar (Fatih, 2026-11-14), Adım 0'da `docs/DECISIONS.md`'ye yazılır:
- **D-025:** User tablosu sadece M1'in kullandığı kolonlarla gelir; diğer §5.1 kolonları (theme M4, timezone, default_doc_language, ui_locale, anthropic_api_key_encrypted) kendi milestone'larında küçük migration'larla eklenir.
- **D-026:** CSRF ve yönlendirme güvenliği yeni `app/security.py` modülünde (SPEC §4'te yok).

Çalışma kuralı: her commit'ten önce Fatih'e sorulur; push edilmez.

---

## 1. Paketler (`uv add`)

| Paket | Neden | Ne zaman |
|---|---|---|
| `pwdlib[argon2]` | Şifre hash'i (SPEC §3, §10.1) | **M1a** |
| `itsdangerous` | Starlette `SessionMiddleware` imzalı çerez için ister; M1b'de token'lar | **M1a** |
| `aiosmtplib` | SMTP e-posta | M1b |
| `slowapi` | IP başına istek sınırı | M1b |
| `freezegun` (dev) | Token süresi / 15 dk kilit testleri | M1b |

- `python-multipart` (form okuma) ve `email-validator` (`EmailStr`) `fastapi[standard]` ile zaten kurulu; kodda doğrudan import edilmiyorlar → ayrıca eklenmez.
- `pytest-asyncio` gerekmiyor (testler senkron `TestClient` ile).
- Hepsi SPEC §3'te → DECISIONS'a bağımlılık gerekçesi gerekmez. CSRF için paket yok (D-022).

---

## 2. Veri modeli — `app/models/user.py` + migration `0002_user`

| Kolon | Tip | Not |
|---|---|---|
| id | int PK | |
| email | str(320), unique, index | Kayıtta `strip().lower()` |
| password_hash | str | argon2 (pwdlib) |
| full_name | str(200) | |
| is_verified | bool, default False | M1b kullanır |
| privacy_accepted_at | datetime (UTC) | Kayıtta zorunlu |
| last_login_at | datetime, nullable | |
| session_version | int, default 0 | D-021 |
| plan | str(20), default `"free"` | DB enum yerine metin: Postgres enum tipi migration'ları zorlaştırır; geçerli değerler `plans.PLANS` anahtarları, `set_plan()` doğrular |
| plan_expires_at | datetime, nullable | |
| created_at / updated_at | datetime (UTC) | |

- Tarihler `DateTime(timezone=True)`; `app/models/__init__.py`'ye küçük `utcnow()` yardımcısı. SQLite saat dilimini saklamadığı için okunan "naive" tarih UTC kabul edilir (`entitlements` içinde tek yerde).
- **`created_at` / `updated_at` nasıl dolar:** İkisi de SQLAlchemy kolon ayarlarıyla, uygulama tarafında:
  - `created_at`: `default=utcnow` (INSERT'te bir kez).
  - `updated_at`: `default=utcnow` + `onupdate=utcnow`. SQLAlchemy, satırı güncelleyen her ORM `session.commit()`'inde ve Core `update()` ifadesinde bu kolonu kendisi yazar; kodda elle `user.updated_at = ...` yapılmaz.
  - Sınır: SQLAlchemy dışından çalışan ham SQL (ör. `psql` ile elle UPDATE) `updated_at`'i değiştirmez. DB trigger'ı kullanılmaz (SQLite/Postgres farkı ve migration karmaşıklığı); bu, D-025 kaydına not olarak düşülür.
  - Test: `updated_at`'i elle eski bir tarihe çekip commit et, sonra `full_name`'i değiştirip commit et → `updated_at` yeni tarihten büyük, `created_at` değişmemiş.
- `app/models/__init__.py` `from app.models.user import User` ile tabloyu Alembic'e tanıtır.
- Migration: `uv run alembic revision --autogenerate -m "add user table"` → `0002`. Mevcut `test_migrations.py` zinciri (`upgrade → downgrade → upgrade` + `alembic check`) `0002`'yi de kapsar; beklenen revizyon `0002` olarak güncellenir.

---

## 3. Modüller ve sorumluluklar

| Dosya | İçerik |
|---|---|
| `app/security.py` (yeni) | `safe_redirect_path(value) -> str` (#4) · CSRF: `get_csrf_token(request)` (oturumda yoksa `secrets.token_urlsafe(32)` üretir), `verify_csrf` dependency |
| `app/services/auth_service.py` | `hash_password`, `verify_password` (bulunmayan kullanıcıda da sahte hash ile doğrulama → zamanlama farkıyla e-posta tahmini yok), `create_user`, `authenticate`, `start_session(request, user)` (oturumu temizler + `user_id`, `session_version`, yeni CSRF token yazar), `end_session(request)`, `current_user` / `require_user` dependency'leri, `LoginRequired` istisnası |
| `app/billing/plans.py` | `PLANS` sözlüğü (SPEC §17.2 birebir) |
| `app/billing/entitlements.py` | `effective_plan`, `can`, `limit`, `allowed_templates` |
| `app/billing/billing_service.py` | `set_plan(session, user, plan, expires_at, reason)`: tek değiştirme noktası, plan adını doğrular, `logging.getLogger("unfurl.billing")` ile log yazar |
| `app/billing/grant.py` | CLI: `uv run python -m app.billing.grant --email x@y.com --plan pro --days 90` (`argparse`, `main(argv)` test edilebilir) |
| `app/routers/auth.py` | `GET/POST /login`, `GET/POST /register`, `POST /logout` |
| `app/routers/legal.py` | `GET /privacy`, `/terms`, `/imprint` (herkese açık, yer tutucu + "TODO: legal review") |
| `app/main.py` | `SessionMiddleware(secret_key, session_cookie="unfurl_session", max_age=14 gün, same_site="lax", https_only=production)`; `FastAPI(dependencies=[Depends(verify_csrf)])`; `LoginRequired` exception handler; yeni router'lar |
| `app/templating.py` | Context processor'a `current_user` (`request.state.user`) ve `csrf_token`; Jinja global `safe_redirect_path` |

**Neden `app/security.py`:** CSRF ve yönlendirme güvenliği auth'a özel değil (her form, her `back_url`). SPEC §4'te yok → DECISIONS'a **D-026** olarak yazılır (M0'daki `templating.py` gibi küçük bir ek).

### Davranış ayrıntıları
- **Oturum (D-021):** Çerez `user_id`, `session_version`, `csrf` taşır. `current_user` her istekte User'ı yükler, `session_version` uyuşmazsa oturumu temizler ve `None` döner. Bulunan kullanıcı `request.state.user`'a yazılır (şablonlar oradan okur).
- **Session fixation:** Login ve register'da `start_session` önce `request.session.clear()` yapar; CSRF token da yenilenir.
- **Korumalı sayfalar:** `/`, `/learning`, `/documents`, `/settings` → `Depends(require_user)`. Landing page olmadığı için oturumsuz `/` da login'e gider.
  - Oturumsuz GET → `303 /login?next=<path+query>`; HTMX isteği (`HX-Request: true`) → `200` boş gövde + `HX-Redirect: /login?next=...` (htmx 3xx yanıtlarını göremez, fetch onları kendisi takip eder).
- **CSRF (D-022):** `verify_csrf` POST/PUT/PATCH/DELETE'te `csrf_token` form alanını veya `X-CSRF-Token` header'ını oturumdaki token'la `secrets.compare_digest` ile karşılaştırır; yoksa/yanlışsa 403. `/api/ext/` ile başlayan yollar hariç. Oturumsuz kullanıcının login/register formları da korunur (token anonim oturumda üretilir). HTMX için `base.html`'de `<body hx-headers='{"X-CSRF-Token": …}'>` (`tojson` ile; `allowEval` kapalıyken de çalışır).
- **Login:** e-posta normalize edilir; hata durumunda her zaman "Invalid email or password" (400, form değerleri korunur, şifre hariç). Başarıda `last_login_at` güncellenir, `next` `safe_redirect_path`'ten geçerek 303. Zaten giriş yapmış kullanıcı `/login` açarsa `/`'e gider.
- **Şifre uzunluğu:** en az 10, **en fazla 1024** karakter. Üst sınır, argon2'ye çok büyük girdi verilerek sunucunun yorulmasını önler. Register'da >1024 → "Password must be at most 1024 characters." (400). Login'de >1024 şifre hash'lenmeden genel "Invalid email or password" alır.
- **Register:** ad, e-posta (`EmailStr`), şifre 10–1024, gizlilik onayı zorunlu. Mevcut e-posta → "An account with this email already exists." + **sadece** "Log in" linki. Forgot password linki M1a'da hiç yok; sayfa ve link M1b'de birlikte gelir (D-023'ün geri kalanı M1b'de tamamlanır). Aynı anda iki kayıt (unique ihlali) da aynı mesaja düşer. Başarıda otomatik giriş → 303 `/` (Check your inbox ekranı M1b).
- **Logout:** sadece POST (+CSRF), oturumu temizler → 303 `/login`.
- **`safe_redirect_path`:** `/` ile başlamayan, `//` veya `/\` ile başlayan, kontrol karakteri içeren ya da `urlsplit` ile şema/host çıkan her değer → `/`. `page_header` macro'su `back_url`'i bundan geçirir.

---

## 4. Şablonlar ve arayüz

Referans: `docs/prototype/Login.dc.html` (sol mor tanıtım paneli + sağda form; "Sign in / Create account" sekmeleri).

- **Ortak parçaların ayrılması** (auth ve legal sayfaları sidebar'sız olduğu için):
  - `components/icons.html` ← `base.html`'deki `icon()` macro'su
  - `components/theme_picker.html` ← tema dropdown'ı (SPEC v2.1.2; prototipteki 3'lü grup değil)
  - `layouts/_head.html` ← `<head>` (CSS, htmx-config meta, favicon, script'ler)
  - `base.html` bunları include eder; görünüm değişmez (mevcut testler korur).
- **Yeni:** `auth/layout.html` (split ekran), `auth/login.html`, `auth/register.html`, `legal/page.html` (sade üst bar + metin) ve footer linkleri (Privacy · Terms · Imprint) auth ve legal sayfalarda + `base.html` içerik altında.
- **Sekmeler:** prototipteki "Sign in / Create account" `/login` ve `/register` linkleri olur (`aria-current="page"`), JS yok.
- **Form alanı stilleri** (M0'dan bekleyen not): `input.css` `@layer base`'e input/select/textarea için `border: 1px solid var(--border-strong)`, `border-radius: 8px`, `height: 40px`, `padding: 0 12px`, `font-size: 14px`; checkbox `accent-color: #4F46E5`. Değerler prototipten.
- **`components/forms.html`** macro'ları: `text_field(name, label, type, value, error, hint, autocomplete)` (`aria-invalid`, `aria-describedby`), `csrf_field()`, `primary_button`.
- **Hesap menüsü:** avatar menüsünde kullanıcının adı ve e-postası; "Sign out" `<form method="post" action="/logout">` + CSRF. "My profile" M2'ye kadar pasif kalır.

---

## 5. Testler

**`tests/conftest.py` değişikliği:** Her test için ayrı in-memory SQLite (`StaticPool`) + `SQLModel.metadata.create_all` (sadece testlerde; uygulama tablo oluşturmaz, model-migration uyumunu `alembic check` testi korur). `get_session` dependency override edilir. Fixture'lar: `session`, `client` (anonim), `user` (kayıtlı kullanıcı), `auth_client` (giriş yapmış), `csrf_token(client)` yardımcısı.

| Dosya | Kapsam |
|---|---|
| `test_security.py` | `safe_redirect_path`: `//evil.com`, `https://evil.com`, `/\evil.com`, `javascript:alert(1)`, `""`, `None`, `/%2F%2Fevil.com` → `/`; `/applications?x=1` korunur. CSRF: token'sız POST 403, yanlış token 403, form alanıyla geçer, `X-CSRF-Token` header'ıyla (HTMX) geçer, GET kontrol edilmez, `/api/ext/...` hariç |
| `test_models.py` | `created_at` / `updated_at` otomatik dolar; güncellemede `updated_at` ilerler, `created_at` değişmez; e-posta küçük harfe çevrilir |
| `test_auth.py` | Register → otomatik giriş → `/`; mevcut e-posta mesajı (büyük harf farkıyla da) ve sayfada forgot-password linki **olmadığı**; şifre <10, şifre 1025 karakter (1024 kabul), geçersiz e-posta, onay yok → 400 + mesaj. Login'de 1025 karakterlik şifre → genel hata. Login başarılı / yanlış şifre / bilinmeyen e-posta → aynı genel mesaj. Logout POST; logout sonrası `/settings` → `303 /login?next=/settings`; HTMX isteğinde `HX-Redirect`. `next` ile login sonrası doğru yere, kötü `next` → `/`. `session_version` +1 → eski oturum düşer. Giriş yapmışken `/login` → `/`. Oturum çerezi: HttpOnly, SameSite=Lax; production'da Secure |
| `test_entitlements.py` | Yeni kullanıcı `effective_plan == "free"`; geçerli `pro` → pro; süresi geçmiş `pro` → free; `can()`, `limit()`, `allowed_templates()`; bilinmeyen özellik/anahtar davranışı |
| `test_billing.py` | `set_plan()` geçersiz planı reddeder, log yazar (`caplog`); grant CLI `--days 90` ile `pro` verir, bilinmeyen e-postada hata kodu döner |
| `test_legal.py` | `/privacy`, `/terms`, `/imprint` oturumsuz 200, "TODO: legal review" |
| Mevcut testler | `test_pages`, `test_theme`, `test_home_route` → `auth_client` kullanır; anonim `/` için yeni beklenti 303. `test_migrations` → `0002` |

Uçtan uca tüm testler `TestClient` ile; gerçek e-posta/AI yok.

---

## 6. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)

| # | Adım | Commit |
|---|---|---|
| 0 | Bu planı `docs/plans/M1a.md` olarak ekle, D-025 ve D-026'yı DECISIONS'a yaz, issue #8'e yorum | `docs: add m1a technical plan` |
| 1 | `uv add "pwdlib[argon2]" itsdangerous` | `build: add password hashing and session signing packages` |
| 2 | User modeli + `0002` migration + test DB fixture'ları | `feat: add user model and migration` |
| 3 | `plans.py`, `entitlements.py`, `billing_service.set_plan`, grant CLI + testler | `feat: add plans, entitlements and grant cli` |
| 4 | `app/security.py`: `safe_redirect_path` (+ `page_header`) ve CSRF dependency + testler | `feat: add redirect safety and csrf protection` |
| 5 | Şablon parçalarına ayırma (head, icons, theme picker), form alanı stilleri, form macro'ları | `refactor: split base layout into reusable parts` |
| 6 | SessionMiddleware, auth_service, `/register` `/login` `/logout`, auth şablonları | `feat: add register, login and logout` |
| 7 | `require_user` ile sayfaları koru, HTMX yönlendirmesi, hesap menüsünde kullanıcı + Sign out, mevcut testleri güncelle | `feat: require login for app pages` |
| 8 | Legal sayfalar + footer linkleri | `feat: add placeholder legal pages` |
| 9 | README (ilk kullanıcıyı oluşturma, grant CLI), elle kabul testi, `security-reviewer`, LEARNING_LOG | `docs: update readme and learning log for m1a` |
| 10 | CLAUDE.md'de M1a ✓ (**aynı PR'da, merge'den önce**; ayrı PR yok) | `docs: mark m1a as done` |
| 11 | PR: açıklamada `Closes #8`, `Closes #4` ve "Login rate limit and lockout come in M1b (#9)" notu. Merge sonrası etiket `v0.1a-m1a` | — |

Her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push edilmez.

---

## 7. Riskler
1. **Global CSRF dependency ve form gövdesi:** dependency `await request.form()` okur; Starlette formu request üzerinde önbelleğe aldığı için route'un `Form(...)` parametreleri aynı veriyi görmeli. Adım 4'te testle doğrulanır; sorun çıkarsa route seviyesinde dependency'ye geçilir.
2. **Mevcut testlerin kırılması:** `/` artık oturum istiyor; Adım 7'de `auth_client`'a geçilir.
3. **SQLite ↔ Postgres tarih farkı:** naive/aware karışması `effective_plan`'da hata verir → tek yerde UTC'ye çevirme + test. Postgres'te CI `docker` job'u PR'da çalışır.
4. **M1a'da brute-force koruması yok:** login kilidi ve slowapi M1b'de. M1a tek başına deploy edilmez (deploy M13), PR açıklamasında not edilir.
5. **Oturum çerezi okunabilir:** Starlette çerezi imzalar ama şifrelemez (base64). İçinde sadece `user_id`, `session_version`, CSRF token var; gizli veri konmaz.

---

## 8. Doğrulama (Build bittiğinde)
1. `uv run pytest` ve `uv run ruff check . && uv run ruff format --check .` yeşil; `uv run alembic upgrade head` yeni `user` tablosunu oluşturur.
2. Fatih kendi terminalinde `uv run fastapi dev app/main.py`: oturumsuz `/` → `/login`; kayıt ol → `/`; hesap menüsünde ad + Sign out; çıkış → `/settings` açınca `/login?next=/settings`, giriş sonrası `/settings`. Yanlış şifre genel mesaj. Aynı e-postayla tekrar kayıt mesajı. Light/dark'ta auth sayfaları ve form alanları. Mobil genişlik.
3. `uv run python -m app.billing.grant --email <kayıtlı> --plan pro --days 90` → terminalde log satırı.
4. `docker compose up --build` → Postgres'te migration ve kayıt/giriş.
5. `security-reviewer` M1a diff'inde; bulgular düzeltilir veya issue olur. Gerekirse `test-writer` yeni endpoint'ler için.
6. PR'da CI (`lint-test` + `docker`) yeşil.
