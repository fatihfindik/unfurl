# M1b — E-posta akışları · Teknik plan (Design fazı)

## Context
M1a oturum çekirdeğini getirdi: kayıt, giriş/çıkış, CSRF, korumalı sayfalar, `session_version`. M1b (issue #9, SPEC §12 › M1b, §6.1, D-020, D-021, D-023) e-posta gönderimini, e-posta doğrulamayı, şifre sıfırlamayı ve login kilidini ekler.

Onaydan sonra: bu plan issue #9'a yorum olarak eklenir (WORKFLOW §2 Design çıktısı). Branch: `m1b-auth-email` (D-024), etiket `v0.1b-m1b`.

Plan fazında kapanan soru: "Sign out everywhere" M1b'ye girmez → #14 (`idea`, `v2`). D-021'deki "M1b'de değerlendirilir" notu Adım 0'da bu sonuçla güncellenir.

Kararlar (Fatih, 2026-11-14):
1. Şifre sıfırlama e-posta sahipliğini kanıtlar → reset sonrası `is_verified=True`.
2. Reset sonrası otomatik giriş yok → 303 `/login?reset=1`.
3. Login kilidi 5. hatadan itibaren **sabit 15 dakika** sürer (`locked_until`).
4. `POST /forgot-password`'a IP başına slowapi limiti: **5/saat**.
5. Şifre sıfırlanınca o e-postanın tüm login kilidi kayıtları silinir.

**SPEC güncellemesi (Adım 0, v2.1.4):** §6.1'e 1, 3 ve 4 eklenir (reset → doğrulanmış; kilit 5. hatadan itibaren sabit 15 dk; forgot-password 5/saat/IP). Commit'le birlikte Fatih claude.ai'deki SPEC kopyasını yeniler (WORKFLOW §3).

Yeni kararlar (Adım 0'da `docs/DECISIONS.md`'ye):
- **D-027 · Token'lar durumsuz ve imzalı:** Doğrulama ve reset token'ları `itsdangerous.URLSafeTimedSerializer` ile imzalanır, DB'de token tablosu yok. Süre `max_age` ile kontrol edilir (48 saat / 1 saat). Her amaç ayrı `salt` kullanır, böylece verify token'ı reset için kullanılamaz. Tek kullanımlık reset SPEC §6.1'deki parmak iziyle sağlanır. Alternatif: token tablosu (iptal edilebilir ama migration ve temizlik işi gerekir).
- **D-028 · E-postalar arka planda gönderilir:** Route'lar e-postayı FastAPI `BackgroundTasks` ile yanıttan sonra gönderir. Gerekçe: SMTP yavaşlığı sayfayı bekletmez ve forgot-password'da kayıtlı/kayıtsız e-posta için yanıt süresi farkı oluşmaz (numaralandırma). Gönderim hataları `send_email` içinde yakalanır ve `unfurl.email` log'una yazılır; kullanıcıya gösterilmez. E-posta metinleri `app/templates/emails/*.txt` (SPEC §4 ağacına küçük ek).
- **D-029 · Production'da e-posta ayarı zorunlu:** `APP_ENV=production` iken `EMAIL_BACKEND=console` veya boş `SMTP_HOST` ile uygulama başlamaz (mevcut `SECRET_KEY` kontrolü gibi). Gerekçe: doğrulama ve reset e-postaları sessizce terminale düşmesin. Alternatif: sadece uyarı log'u.

Çalışma kuralı: her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push ve PR da onayla.

---

## 1. Paketler (`uv add`)

| Paket | Neden |
|---|---|
| `aiosmtplib` | SMTP gönderimi (SPEC §3) |
| `slowapi` | IP başına istek sınırı (SPEC §3) |
| `freezegun` (dev) | Token süresi ve 15 dk kilit testleri (SPEC §3) |

Hepsi SPEC §3'te; DECISIONS'a bağımlılık gerekçesi gerekmez. `itsdangerous` M1a'da eklendi.

---

## 2. Veri modeli

**Migration yok.** `User.is_verified` ve `User.session_version` M1a'da geldi. Token'lar DB'de tutulmaz (D-027), kilit sayaçları bellekte (D-020).

---

## 3. Modüller ve sorumluluklar

| Dosya | İçerik |
|---|---|
| `app/services/email_service.py` (yeni, SPEC §4'te var) | `send_email(to, subject, body)`: `EMAIL_BACKEND=console` → `logging.getLogger("unfurl.email")` ile terminale yazar; `smtp` → `aiosmtplib.send` (STARTTLS, `SMTP_*` ayarları). Hatalar yakalanıp loglanır (D-028). Route'lar her zaman `email_service.send_email` diye modül üzerinden çağırır, böylece testteki `outbox` yaması her yerde geçerli olur. `render_email(name, **context)`: `app/templates/emails/*.txt` Jinja şablonlarından konu + düz metin gövde. Linkler `APP_BASE_URL` ile mutlak URL olur. |
| `app/services/auth_service.py` | Token yardımcıları: `make_verify_token(user)` / `read_verify_token(token)` (payload `user_id` + `email`, 48 saat), `make_reset_token(user)` / `read_reset_token(session, token)` (payload `user_id` + parmak izi, 1 saat). Parmak izi: `sha256(password_hash)`'in ilk 16 karakteri; şifre değişince hash değişir, eski token geçersiz olur. `verify_email(session, user)`, `reset_password(session, user, new_password)` (hash + `session_version += 1` + `is_verified=True`). Token'ı okumadan önce uzunluk kontrolü (en fazla 512 karakter; daha uzunu doğrudan geçersiz). `require_verified` dependency (doğrulanmamışsa 403). |
| `app/security.py` | **`LoginLockout`**: bellekte `{(ip, email): kayıt}`; kayıt = son 15 dk'daki hata zamanları + `locked_until`. `record_failure(ip, email)`: 5. hatada `locked_until = şimdi + 15 dk` yazılır; kilit, hatalar pencereden çıksa da bu zamana kadar sürer. `is_locked(ip, email)`, `clear(ip, email)` (başarılı giriş), `clear_email(email)` (reset sonrası, tüm IP'ler). **`EmailLimiter`**: e-posta başına 1 saatte 3 gönderim (forgot-password, resend). Her ikisi de: e-posta önce normalize edilir ve `EMAIL_MAX_LENGTH`'ten uzunsa sayaca hiç girmez; her çağrıda **tüm** süresi dolmuş anahtarlar temizlenir ve sözlük en fazla 10.000 anahtar tutar (dolarsa en eski anahtar atılır), böylece e-posta değiştiren bir saldırgan belleği dolduramaz. slowapi `limiter` (`get_remote_address`, bellek backend'i) ve 429 handler'ı. |
| `app/routers/auth.py` | Yeni route'lar (aşağıda), login'e kilit, register'a doğrulama e-postası ve Check your inbox yönlendirmesi |
| `app/main.py` | `app.state.limiter`, `RateLimitExceeded` handler |
| `app/config.py` | Production kontrolüne ek: `EMAIL_BACKEND=console` ise başlamaz; `smtp` ise `SMTP_HOST` zorunlu |
| `app/templating.py` | Değişiklik yok (`current_user` zaten şablonlarda) |

**Neden lockout `app/security.py`'de:** D-026'daki "isteğe ait güvenlik" tanımına uyuyor ve yeni modül (yeni karar) gerektirmiyor.

### Route'lar

| Metot | Yol | Erişim | Davranış |
|---|---|---|---|
| GET | `/check-inbox` | `require_user` | Doğrulanmışsa `/`'e 303. Değilse "Check your inbox" ekranı (e-posta adresi, "Continue to setup" → `/`, "Resend email") |
| POST | `/resend-verification` | `require_user` + CSRF | E-posta başına 3/saat. HTMX isteğinde buton yerine bir mesaj parçası döner: gönderildiyse "Sent again. Check your spam folder too.", limit aşıldıysa "Please wait a few minutes before asking for another email." İkisi de **200**, çünkü htmx 4xx yanıtlarını sayfaya koymaz. Normal form isteğinde 303 `/check-inbox`, limit aşımında 429 + aynı mesaj. Zaten doğrulanmışsa e-posta göndermez. |
| GET | `/verify-email?token=` | Herkese açık | `token` isteğe bağlı parametre (yoksa 422 yerine geçersiz link sayfası). Geçerli → `is_verified=True`, "Email verified" sayfası. Zaten doğrulanmış → "Email already verified". Eksik/süresi dolmuş/bozuk → "This link has expired or is invalid" (400) + giriş yapmışsa Resend butonu. Oturum açmaz (link başka cihazda da açılabilir). |
| GET/POST | `/forgot-password` | Herkese açık | POST her durumda aynı sayfa: "If an account exists, we sent an email." E-posta sadece kayıtlı kullanıcıya ve 3/saat/e-posta sınırı içinde gönderilir (arka planda, D-028). slowapi: POST 5/saat/IP. E-posta `EMAIL_MAX_LENGTH`'ten uzunsa DB'ye hiç gidilmez, yine aynı mesaj. |
| GET | `/reset-password?token=` | Herkese açık | `token` isteğe bağlı. Geçerli → yeni şifre formu (token gizli alanda). Eksik/geçersiz → "This link has expired or is invalid" (400) + "Request a new link" (`/forgot-password`). Yanıtta `Referrer-Policy: no-referrer` (token URL'de). |
| POST | `/reset-password` | Herkese açık + CSRF | Token tekrar kontrol edilir, şifre 10–1024. Başarıda hash değişir, `session_version += 1` (tüm oturumlar düşer), `is_verified=True`, o e-postanın login kilidi kayıtları silinir, bu tarayıcının oturumu da temizlenir → 303 `/login?reset=1` ("Your password was changed. Sign in with your new password."). |

### Davranış ayrıntıları
- **Register:** kayıt → doğrulama e-postası (arka planda) → otomatik giriş → 303 `/check-inbox`. Mevcut e-posta mesajına "Forgot password?" linki eklenir (D-023: login ve forgot-password linkleri).
- **Login:** önce kilit kontrolü; kilitliyse şifre hiç kontrol edilmez, 429 + "Too many attempts. Try again in 15 minutes." Başarısız denemede sayaç +1; başarılı girişte o IP + e-posta sayacı silinir. Form altına "Forgot password?" linki (prototip). slowapi: 10/dk/IP.
- **Register slowapi:** 5/saat/IP (sadece POST).
- **Doğrulanmamış bandı:** `base.html`'de `current_user` varsa ve doğrulanmamışsa, içeriğin üstünde amber bant: "Verify your email to unlock AI features." + "Resend email" (HTMX). Auth ve legal sayfalarında yok (sidebar'lı layout'ta).
- **`require_verified`:** `require_user`'ı kullanır, doğrulanmamışsa `HTTPException(403)`. M1b'de hiçbir route kullanmaz; AI endpoint'leri M7'den itibaren.
- **IP adresi:** `request.client.host`. Proxy arkasında (Render) gerçek IP için uvicorn `--proxy-headers` gerekir; M13 deploy notu olarak README'ye ve risklere yazılır.
- **E-posta içerikleri (İngilizce, düz metin):** "Verify your email for Unfurl" (link + "works for 48 hours"), "Reset your Unfurl password" (link + "works for 1 hour" + "If you didn't ask for this, ignore this email.").

---

## 4. Şablonlar ve arayüz

Referans: `docs/prototype/Login.dc.html` (Check your inbox ekranı, "Forgot password?" linki).

- `auth/check_inbox.html`: başlık "Check your inbox", "We sent a link to **{email}**. Click it to verify your email. The link works for 48 hours.", "You can set up your account now. AI features unlock once your email is verified.", "Continue to setup" (birincil), "Resend email". Sekmeler gizli (prototipte `showTabs: !isVerify`).
- `auth/forgot_password.html`, `auth/reset_password.html`, `auth/message.html` (verified / already verified / invalid link için ortak sonuç sayfası). Hepsi `auth/layout.html`'i kullanır, sekmeler gizli.
- `auth/login.html`: "Forgot password?" linki, `reset=1` bildirimi, kilit mesajı.
- `components/verify_banner.html`: amber bant (`--amber-bg` / `--amber-text`, light ve dark tokenları var).
- `emails/verify_email.txt`, `emails/reset_password.txt`.

---

## 5. Testler

**`tests/conftest.py`:** `outbox` fixture'ı (`email_service.send_email`'i listeye ekleyen bir sahte fonksiyonla değiştirir; TestClient arka plan görevlerini yanıttan önce çalıştırır). Autouse fixture: her testten önce `LoginLockout`, `EmailLimiter` ve slowapi `limiter` sıfırlanır.

**`require_verified` testi:** Ana `app`'e geçici route eklenmez (OpenAPI şeması önbelleğe alınır ve `test_every_page_except_the_public_ones_needs_login` sıraya bağlı bozulabilir). Test dosyasında küçük ayrı bir `FastAPI()` uygulaması kurulur: `SessionMiddleware`, `LoginRequired` handler'ı, `get_session` override'ı ve `require_verified` kullanan tek bir route. Oturum, aynı uygulamada test için bir login route'uyla açılır.

| Dosya | Kapsam |
|---|---|
| `test_email.py` | Console backend log'a yazar (`caplog`); şablon konu + mutlak link içerir; smtp backend `aiosmtplib.send`'i doğru argümanlarla çağırır (monkeypatch, gerçek SMTP yok) |
| `test_verification.py` | Register → outbox'ta 1 e-posta, 303 `/check-inbox`; link doğrular; ikinci tıklama "already verified"; 48 saat + 1 sn sonra reddedilir (freezegun); bozuk token ve reset token'ı verify'da reddedilir; doğrulanmış kullanıcı `/check-inbox` → `/`; resend outbox'a ekler, 4. resend 429; banner doğrulanmamışta görünür, doğrulanmışta yok; token'sız `/verify-email` → 400 geçersiz link sayfası; 513 karakterlik token reddedilir; `require_verified` ayrı test uygulamasında 403 / doğrulanmışta 200 |
| `test_password_reset.py` | Kayıtlı ve kayıtsız e-posta için aynı yanıt metni ve status; kayıtsızda outbox boş; reset çalışır, yeni şifreyle giriş; aynı token ikinci kez reddedilir; 1 saat + 1 sn sonra reddedilir; eski oturum (`auth_client`) `/settings`'te login'e düşer; reset sonrası `is_verified=True`; şifre <10 ve >1024 hatası; token'sız `/reset-password` → 400; yanıtta `Referrer-Policy: no-referrer` |
| `test_rate_limits.py` (Adım 5) | 4. resend e-posta göndermez: HTMX'te 200 + "Please wait…" parçası, normal formda 429; 4. forgot-password isteği e-posta göndermez ama aynı mesajı gösterir; aynı IP'den 6. forgot-password POST/saat 429; reset kilitli e-postanın kilidini açar |
| `test_login_lockout.py` (Adım 5) | 5 hızlı hata → 6. deneme 429 ve doğru şifreyle de girilemez; 15 dk sonra açılır (freezegun); **zamana yayılmış hatalar** (0, 1, 2, 3, 14. dk) → kilit 29. dakikaya kadar sürer (15. dakikada hâlâ kilitli); başka e-posta etkilenmez; başarılı giriş sayacı sıfırlar; 321 karakterlik e-posta sayaca girmez; anahtar sınırı aşılınca en eski atılır (birim testi); slowapi: aynı IP'den 11. login isteği/dk 429, register 6. istek/saat 429 |
| `test_auth.py` | Adım 3: register sonrası yönlendirme `/check-inbox` (:77); `PUBLIC_PAGES`'e `/verify-email` eklenir ve aynı adımda `test_public_pages_list_is_up_to_date` (:396) 200 yerine "404 değil" kontrol edecek şekilde değişir (token'sız sayfalar 400 döner). Adım 4: `test_no_forgot_password_link_in_m1a` (:68) ve register'daki "forgot yok" beklentisi (:103) tersine çevrilir; `PUBLIC_PAGES`'e `/forgot-password`, `/reset-password` eklenir. |
| `test_config.py` | Production'da console backend ve boş `SMTP_HOST` reddedilir (D-029) |

Gerçek SMTP veya AI çağrısı yok.

---

## 6. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)

| # | Adım | Commit |
|---|---|---|
| 0 | Planı ekle; SPEC v2.1.4 (§6.1, başlıktaki versiyon satırı); D-027, D-028, D-029; D-021 notunu #14 ile güncelle; issue #9'a yorum. Commit sonrası Fatih claude.ai'deki SPEC kopyasını yeniler. | `docs: add m1b technical plan` |
| 1 | `uv add aiosmtplib slowapi` + `uv add --dev freezegun` | `build: add email, rate limit and time freezing packages` |
| 2 | `email_service` (console/smtp, hata yakalama), e-posta şablonları, `outbox` fixture'ı, config production kontrolü (D-029) + testler | `feat: add email service` |
| 3 | Verify token'ı, register sonrası e-posta, `/check-inbox`, `/verify-email`, `/resend-verification`, banner, `require_verified` + testler; `test_auth.py` register yönlendirmesi ve `PUBLIC_PAGES` | `feat: add email verification` |
| 4 | Reset token'ı, `/forgot-password`, `/reset-password`, Forgot password linkleri + testler; `test_auth.py` "forgot" beklentileri ve `PUBLIC_PAGES` | `feat: add password reset` |
| 5 | `LoginLockout`, `EmailLimiter`, slowapi (login, register, forgot-password) + testler; reset sonrası kilit temizliği | `feat: add login lockout and rate limits` |
| 6 | README (`EMAIL_BACKEND`, SMTP ayarları, proxy notu), `.env.example` kontrolü, elle kabul testi, `security-reviewer`, LEARNING_LOG | `docs: update readme and learning log for m1b` |
| 7 | CLAUDE.md'de M1b ✓ (aynı PR'da) | `docs: mark m1b as done` |
| 8 | PR: `Closes #9`. Merge sonrası etiket `v0.1b-m1b` | — |

Adım 5'teki limitler Adım 3–4'teki route'lara eklenir; bu yüzden en sona kondu (önce akışlar çalışsın, sonra sınırlansın). Resend ve forgot-password'daki e-posta başına 3/saat limiti de Adım 5'te gelir; Adım 3–4 testleri limitsiz akışı doğrular. Her adım sonunda tüm testler yeşil kalır.

---

## 7. Riskler
1. **slowapi ve senkron route'lar:** slowapi decorator'ı route'ta `request: Request` parametresi ister; mevcut route'larda zaten var. Testler arasında limiter sayaçları sızarsa testler birbirini etkiler → autouse reset fixture'ı.
2. **Proxy arkasında IP:** `request.client.host` proxy'nin IP'si olursa tüm kullanıcılar aynı sayacı paylaşır. M13 deploy'unda `--proxy-headers` + `forwarded_allow_ips`; README'de not.
3. **Bellekteki sayaçlar (D-020):** restart sıfırlar, çok instance'ta çalışmaz. Kabul edilmiş maliyet.
4. **Arka plan e-posta hataları (D-028):** SMTP hatası `send_email` içinde yakalanır, kullanıcıya görünmez. Log'a `unfurl.email` ile yazılır; kullanıcı Resend ile tekrar deneyebilir.
5. **Token'da e-posta adresi:** imzalı ama şifreli değil (base64), linkte e-posta okunabilir. E-posta zaten alıcının kendi adresi; kabul edilebilir. Şifre hash'i token'a girmez, sadece kısa parmak izi.
6. **freezegun ve itsdangerous:** itsdangerous zamanı `time.time()` ile alır, freezegun bunu dondurur. Adım 3'te ilk testle doğrulanır.
7. **Bellekte sayaç büyümesi:** 10.000 anahtar sınırı dolarsa en eski kayıt atılır; çok yoğun bir saldırıda gerçek bir kilit erken silinebilir. slowapi'nin IP limitleri bunu zorlaştırır; tek instance MVP için kabul edilebilir.

---

## 8. Doğrulama (Build bittiğinde)
1. `uv run pytest` ve `uv run ruff check . && uv run ruff format --check .` yeşil.
2. Fatih kendi terminalinde `uv run fastapi dev app/main.py` (`EMAIL_BACKEND=console`): kayıt ol → Check your inbox; terminaldeki linki aç → Email verified; linki tekrar aç → already verified; bant kayboldu. Çıkış yap → Forgot password → terminaldeki link → yeni şifre → eski şifreyle giriş başarısız, yenisiyle başarılı. Aynı e-postaya 6 kez yanlış şifre → kilit mesajı. Light/dark ve mobil genişlik.
3. `docker compose up --build` → Postgres'te aynı akış.
4. `security-reviewer` M1b diff'inde; bulgular düzeltilir veya issue olur. Gerekirse `test-writer` yeni endpoint'ler için.
5. PR'da CI (`lint-test` + `docker`) yeşil.

## 9. Kapsam dışı
Settings'teki şifre değiştirme formu (Settings sayfası ile), "Sign out everywhere" (#14), onboarding (M9), HTML e-posta tasarımı, gerçek yasal metinler (M13), Google ile giriş (V2).

## 10. Açık soru
Yok. Plan fazındaki sorular yukarıdaki "Kararlar" listesinde kapandı.
