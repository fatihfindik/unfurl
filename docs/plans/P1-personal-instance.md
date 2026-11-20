# P1 — Personal local instance (daily use) · Teknik plan (Design fazı)

## Context
Fatih (2026-11-21): domain ve hosting için şimdilik para ödemek istemiyor; Unfurl'ü gerçek iş başvuruları için kendi bilgisayarında kullanacak. Bu ortamın verisi geliştirme verisinden (dev SQLite, dev compose'un `pgdata` volume'u, testler) tamamen ayrı olmalı ve yedeklenmeli. Issue #84, branch `chore/personal-instance` (main `7885871`), tek PR. Milestone değil (etiket yok, LEARNING_LOG yok).

Bununla birlikte deploy (M13c) MVP'den çıkar: MVP kişisel local ortamla tamamlanır (D-155).

## Bulgular (koddan)
1. **Production modu console e-postayla açılmaz:** `config.py:109-112` production'da `EMAIL_BACKEND=smtp` ve `SMTP_HOST` ister. → Soru 1.
2. **Production'ın diğer farkları:** `/docs`, `/redoc`, `/openapi.json` kapanır (`main.py:257`); çerez `__Host-unfurl_session` + `Secure` (`main.py:290-293`). HSTS uygulamada yok.
3. **Çerezler porta göre değil, host'a göre ayrılır:** dev (`localhost:8000`) ve kişisel ortam aynı host'ta olursa ikisi de `unfurl_session` çerezini kullanır; birine girmek diğerinden çıkarır, ve dev `SECRET_KEY` kopyalanırsa dev çerezi kişisel ortamda geçerli olur. → Kişisel ortam **`http://127.0.0.1:8100`** adresinde (README.md:34 zaten localhost/127.0.0.1 ayrımına dayanıyor). Kod değişmez.
4. **Eklenti adresi iki yerde sabit:** `extension/config.js:3` ve `manifest.json` `host_permissions`; `tests/test_extension_manifest.py:27,54` bunu tek adres olarak test ediyor; SPEC §7.3 (SPEC.md:734) tek `<APP_BASE_URL>` gösteriyor. → Soru 2.
5. **Fotoğraflar volume'da** (`uploads`, D-038): yedek fotoğrafları da almalı.
6. **Compose değişkenleri:** `${POSTGRES_*}` gibi compose dosyasındaki değişkenler `env_file:`'dan değil, `.env`'den (dev dosyası) ya da `--env-file`'dan okunur. Her komut `--env-file .env.personal` ile çalışmalı (script'ler bunu tek bir fonksiyonda yapar); değişkenler `:?` ile zorunlu.
7. **Sabit `name:` volume'lar proje adını yok sayar:** test projesi gerçek volume'ları kullanır ve `down -v` gerçek veriyi siler. → Volume adları ve port proje değişkeninden.
8. `.gitignore`'daki `.env` satırı `.env.personal`'ı kapsamaz; `.dockerignore` da (`.dockerignore:4`).
9. Dockerfile zaten `alembic upgrade head && exec fastapi run …` ile başlıyor; ayrı entrypoint gerekmez. Zamanlayıcı saat dilimini açıkça kullanıyor (`scheduler.py:63-86`), container'ın UTC olması sorun değil.

## Kararlar
- **D-155 · Deploy yok, kişisel local ortam.** Fatih (2026-11-21). M13c MVP'den çıkar ("Sonra"); #17, #18, #64 E3 onunla bekler. MVP sırası: M8a → M8b → M9a → M13d. M13d README'si canlı URL yerine "kendi bilgisayarında çalıştır" + ekran görüntüleri + mimari şema. Kişisel ortam: ayrı compose dosyası, ayrı Postgres ve volume'lar, `127.0.0.1:8100`, yedek/geri yükleme script'leri. Alternatif: ücretsiz hosting katmanları (sınırlı Postgres, uyuyan servis); dev veritabanını günlük kullanmak (test hesapları ve migration denemeleri gerçek veriye dokunur).
- **D-156 · Kişisel ortamın modu ve eklenti** — Soru 1 ve 2'nin cevaplarına göre. (A seçilirse: SPEC §10.1'deki "Secure çerez" kuralı kişisel ortamda karşılanmaz; sadece loopback üzerinden http olduğu için kabul edilir.)
- D-152…D-154 M8a branch'inde kullanılıyor; numaralar çakışmaz.

## Dosyalar

### 1. `docker-compose.personal.yml`
- Proje adı `${UNFURL_PROJECT:-unfurl-personal}`; volume adları `${UNFURL_PROJECT:-unfurl-personal}_db` ve `…_uploads` (sabit, ama proje adına bağlı: test projesi kendi volume'larını alır); port `127.0.0.1:${UNFURL_PORT:-8100}:8000`.
- `db`: aynı `postgres:16@sha256…` imajı, healthcheck, `restart: unless-stopped`, port açılmaz. `POSTGRES_USER/PASSWORD/DB` `${…:?set in .env.personal}` ile zorunlu.
- `app`: `build: .`, `env_file: .env.personal`, `environment:` içinde `DATABASE_URL` (compose ağındaki `db`), `SECRET_KEY: ${SECRET_KEY:?…}` ve `ENCRYPTION_KEY: ${ENCRYPTION_KEY:?…}` zorunlu (Soru 1-A'da production kontrolleri çalışmadığı için); uploads volume'u; `restart: unless-stopped`; `depends_on: db healthy`; healthcheck.
- Bütün komutlar: `docker compose -f docker-compose.personal.yml --env-file .env.personal …` (README ve script'ler).

### 2. `.env.personal.example` (+ `.gitignore` ve `.dockerignore`'a `.env.personal`)
`APP_ENV` (Soru 1), `APP_BASE_URL=http://127.0.0.1:8100`, `DEBUG=false`, `SECRET_KEY` ve `ENCRYPTION_KEY` boş + üretme komutları, `POSTGRES_*` (rastgele şifre komutu), `EMAIL_BACKEND=console`, `SCHEDULER_ENABLED=true`, `DEFAULT_TIMEZONE=Europe/Vienna`, `EXTENSION_ORIGINS` (aynı eklenti id'si), `LEGAL_*` boş.

### 3. Eklenti (Soru 2)
A seçilirse: `host_permissions` tam olarak `["http://localhost:8000/*", "http://127.0.0.1:8100/*"]`; `config.js` varsayılanı `http://127.0.0.1:8100`; yorum ve `extension/TESTING.md`: "elle test etmeden önce `config.js`'i 8000 yap, yoksa test ilanları gerçek verine düşer; değiştirdikten sonra eklentide yeniden giriş yap (tek token saklanır)". `test_extension_manifest.py`: liste tam olarak bu iki adres, `config.js`'teki adres listede. SPEC §7.3 ve `extension/README.md:8` güncellenir.

### 4. `scripts/personal.sh` (ortak) + `scripts/backup_personal.sh`
- `personal.sh`: `compose()` fonksiyonu (`-p "${UNFURL_PROJECT:-unfurl-personal}" -f docker-compose.personal.yml --env-file "${UNFURL_ENV_FILE:-.env.personal}"`), dosya yoksa durur.
- Yedek: `set -euo pipefail`, `umask 077`, `mkdir -p -m 700 ~/Backups/unfurl`.
  - DB: `compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'` → `unfurl-YYYYMMDD-HHMMSS.dump.partial` → `pg_restore --list` ile doğrula → `mv` ile son adı.
  - Fotoğraflar: `compose run --rm --no-deps -T app tar czf - -C /app uploads` (uygulama çökmüş olsa da çalışır) → aynı zaman damgalı `…-uploads.tar.gz`, aynı `.partial` → `mv`.
  - Son 14 yedek kalır; sadece doğrulanmış (son adlı) dosyalar sayılır ve silinir.

### 5. `scripts/restore_personal.sh <dump>`
1. `pg_restore --list <dump>` ile dosyayı doğrular; fotoğraf arşivi yoksa uyarır.
2. Hangi dosyanın yükleneceğini ve **mevcut verinin silineceğini** yazar, `yes` bekler.
3. **Otomatik ön yedek:** `backup_personal.sh` çalışır (geri dönüş yolu).
4. `app`'i durdurur; `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` → `pg_restore --exit-on-error --single-transaction` (eski bir yedekteki şema da temiz kurulur; `--clean` yeni migration'ların tablolarını bırakırdı).
5. Fotoğraflar: `compose run --rm --no-deps -T app sh -c 'rm -rf /app/uploads/* && tar xzf - -C /app'`.
6. `app`'i başlatır (CMD'deki `alembic upgrade head` eski şemayı günceller); healthcheck'i bekler.

### 6. Yedek → geri yükleme testi (Soru 3; gerçek ortamdan önce)
`UNFURL_PROJECT=unfurl-personal-test UNFURL_PORT=8199 UNFURL_ENV_FILE=<scratchpad'de test env>`; önce `docker volume ls` ile gerçek volume'ların (`unfurl-personal_*`) kullanılmadığı kontrol edilir. Hesap + başvuru + fotoğraf → yedek → veri değiştir (bir başvuru sil, migration'sız) → geri yükle → ilk hal geri geldi mi (Chrome + `psql`). Ayrıca eski şema senaryosu: bir yedeği `alembic downgrade -1` edilmiş bir veritabanından alıp geri yükle → uygulama `upgrade head` ile açılıyor mu. Sonra `down -v` (sadece test projesi) ve volume listesi yeniden kontrol.

### 7. README: "Personal local use"
Başlatma, durdurma, adres `http://127.0.0.1:8100` (neden localhost değil: ayrı çerezler), e-posta linkleri `logs app`'te, yedek (her güncellemeden önce ve haftada bir; isteğe bağlı launchd örneği, yeni bağımlılık yok), Settings › Privacy › Export'u ikinci, okunabilir yedek olarak öneri, geri yükleme, **güncelleme = önce yedek, sonra `git pull` + `up -d --build`**, eklentinin bağlandığı adres, `down -v` uyarısı, hatırlatma e-postaları teslim edilmez (Settings'ten e-postayı kapat), FileVault önerisi.

### 8. SPEC, DECISIONS, CLAUDE.md, diğer dokümanlar
- SPEC v2.1.32 (main v2.1.30; M8a branch'i v2.1.31): başlık satırı ve SPEC.md:6, §3 Deploy satırı, §7.3 host_permissions, §10.2 Hosting (SPEC.md:1009: "MVP'de hosting yok, D-155"), §11 `.env.personal`, §12 sıra (SPEC.md:1209), M13c "Sonra (D-155)", M13d kabulü canlı URL'siz, SPEC.md:1053 (Web Store M13c ile).
- DECISIONS: D-155, D-156; "Güncelleme: D-155" D-123 ve D-150'ye.
- CLAUDE.md: Commands'a kişisel ortam satırları; milestone sırası; M13c satırı "later (D-155)".
- WORKFLOW §2 Release: "M13'ten sonra canlıya deploy" → D-155 notu.
- README.md:5, :75; `extension/README.md:8`, `extension/TESTING.md:4`.

## Adımlar (her biri ayrı commit, önce Fatih'e sorulur)
0. `docs: plan personal instance` + `docs: SPEC and D-155, D-156 for the personal instance`
1. `chore: personal compose file and env example` (+ `.gitignore`, `.dockerignore`)
2. `chore: extension works with the personal instance` (+ test güncellemesi)
3. `chore: backup and restore scripts for the personal instance` (+ §6 testi, sonuç PR'da)
4. `docs: personal local use in the README` (+ CLAUDE.md, WORKFLOW, eklenti dokümanları)
5. Kontrol: `uv run pytest` (normal ve `TZ=UTC`), `ruff`, `docker compose … config` geçerli; security-reviewer (anahtarlar, portlar, yedek izinleri, geri yükleme); PR "Closes #84". Fatih ilk gerçek başlatmayı test geçtikten sonra yapar.

## Riskler
- Migration'lar artık gerçek veriye uygulanıyor → "önce yedek", ve geri yükleme eski şemayla test edildi.
- Yedekler şifresiz `~/Backups/unfurl/`'de (`700`/`600`); FileVault README'de önerilir.
- Eklenti tek token saklar: dev'de denerken adres değişir, yeniden giriş gerekir; TESTING.md uyarır.
- Docker Desktop kapalıyken uygulama çalışmaz.

## Fatih'in cevapları (2026-11-21: "katılıyorum")
Soru 1: **A**. Soru 2: **A**. Soru 3: **evet**.

## Sorular (cevaplandı)
1. **Mod ve e-posta:** **(A) önerim** `APP_ENV=development` + `DEBUG=false` + console e-posta (kod değişmez; anahtarlar compose'da zorunlu); (B) production + Mailpit (yeni imaj, e-postalar tarayıcıda); (C) yeni `personal` modu (config ve test işi).
2. **Eklenti:** **(A) önerim** iki adrese izin, varsayılan kişisel ortam (`127.0.0.1:8100`), dev'de denerken tek satır değişir; (B) varsayılan dev kalır, sen değiştirirsin.
3. **Yedek testi:** gerçek ortamı ilk kez başlatmadan önce, ayrı test projesiyle (port 8199, kendi volume'ları) Docker container'larını benim başlatıp sonra silmem uygun mu?

## Uygulama notları (2026-11-21)
- security-reviewer: F1 (yüksek: ön yedek geri yüklenecek dosyayı silebiliyordu), F2–F3 (orta: tek transaction, tam doğrulama), F4–F8 (düşük) ve E7, E9 düzeltildi; D-155 güncellemesi. E6 (Host header kontrolü) ayrı issue.
- Test (port 8199, ayrı volume'lar, sonra silindi): kısa `SECRET_KEY` reddedilir; yedek izinleri 700/600; `yes` olmadan değişiklik yok; değişen veri ve fotoğraf geri gelir; eski şemalı yedek yüklenip `upgrade head` ile güncellenir; 14 yedeğin en eskisi geri yüklenir ve silinmez; kesik yedek hiçbir şeye dokunmadan reddedilir; yükleme başarısız olursa veri olduğu gibi kalır; normal yedek sayıyı 14'e indirir; kilit her durumda bırakılır.

