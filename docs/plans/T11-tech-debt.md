# T11 — Teknik borçlar #17, #18, #64

Branch `chore/tech-debt-2`. Fatih: "Teknik borçları da kapat" (2026-11-27); kararlar Claude'da ("Claude karar verdi"). Tek PR: "Closes #17, #18, #64". Her issue ayrı commit. Yeni paket yok. Bir migration (0023).

## #17 · D-198 · Giriş kilidi ve e-posta sınırı veritabanında
- **Sorun:** `LoginLockout` ve `EmailLimiter` (`app/security.py`) sayaçları bellekte: yeniden başlatma sıfırlar (kişisel instance her güncellemede yeniden başlar), tek süreçte çalışır, en fazla 10.000 anahtar tutar.
- **Karar:** Tek tablo `security_counter` (id, `kind`, `key`, `email_key`, `at`): `kind` ∈ `fail_pair`, `fail_email`, `fail_user`, `lock_pair`, `lock_email`, `lock_user`, `email_sent`. `key` ve `email_key` düz metin değil **SHA-256** (IP ve hesabı olmayan e-postalar da kişisel veri; sadece eşleşme için gerekir). Kilit satırının `at`'i kilidin bittiği an. İndeks (`kind`, `key`, `at`) ve `email_key`.
  - `is_locked`: o anahtarın `at > now` kilit satırı var mı.
  - `record_failure`: hata satırı ekler; penceredeki hata sayısı sınıra gelince kilit satırı yazar ve o anahtarın hata satırlarını siler (bugünkü "kilitlenince sayaç sıfırlanır" davranışı). Sınırlar, pencereler, kilit süreleri aynı (D-020, D-149).
  - `clear` / `clear_email`: aynı anlam; `clear_email` çift satırlarını `email_key` ile bulur.
  - `EmailLimiter.allow`: son 1 saatte o adrese ≤ 3 `email_sent`.
  - Her çağrı **kendi kısa oturumunu** açar ve commit eder (`Session(engine)`): route'un oturumundaki bekleyen değişiklikleri commit etmez, çağıranların imzası değişmez. Motor: `db.engine`; testlerde `engine` fixture'ı nesnelere test motorunu verir (`login_lockout.engine = engine`), motor yoksa test açıkça hata verir (dev veritabanına asla gitmez).
  - Gece temizliği: scheduler'a `purge_security_counters` (03:20): `at`'i 2 saatten eski satırlar silinir (en uzun pencere/kilit 1 saat).
  - 10.000 anahtar sınırı ve `OrderedDict` temizliği kalkar (`_drop_oldest*`, `MAX_TRACKED_KEYS`).
- **slowapi** (istek/dk sınırları) bellekte kalır: kısa pencereler (dakika), yeniden başlatmada sıfırlanması zararsız; dağıtık depo Redis ister (yeni altyapı, deploy'da tek instance). Bu D-198'de yazılı, M13c'de birden çok instance olursa tekrar bakılır.
- **Alternatifler:** Redis (kişisel kurulumda yeni servis); route oturumunu kullanmak (çağıran 9 yerin imzası değişir, route'un bekleyen değişikliklerini erken commit etme riski); hiçbir şey yapmamak (yeniden başlatma kilidi sıfırlar).

## #18 · D-199 · Production'da APP_BASE_URL ve proxy kontrolü
- `Settings.check_production_settings`: `APP_BASE_URL` `https://` ile başlamalı ve varsayılan (`http://localhost:8000`) olmamalı.
- Proxy: `fastapi run` (uvicorn) proxy başlıklarını zaten okur ama sadece `FORWARDED_ALLOW_IPS`'teki adreslerden (varsayılan 127.0.0.1). Production'da `FORWARDED_ALLOW_IPS` ortam değişkeni **açıkça** ayarlanmış olmalı (Render: proxy'nin adresi ya da `*`; proxy yoksa `127.0.0.1`); yoksa başlamaz. `Settings`'e `forwarded_allow_ips: str | None` alanı (uvicorn aynı değişkeni okur). `.env.example`, `DEVELOPMENT.md`, `security.py` yorumu güncellenir.
- Kişisel instance development modunda çalışır (D-155): etkilenmez.

## #64 · Eklenti takipleri
- **E2 (V2):** SPEC'te zaten kural var (§AI güvenliği, prompt injection). M5 ve M7'nin kabul kriterine "eklentiden gelen `description_raw` (gizli JSON-LD dahil) güvenilmeyen veri olarak etiketli gider; araç eylemi PendingChangeSet önizlemesi olmadan başlamaz (#64)" satırı eklenir. V2'de bu kriterle kapanır.
- **E3:** `scripts/build_extension.sh <https-url> [extension-id]`: `extension/`'ı `dist/extension/`'a kopyalar, `config.js` ve `manifest.json` `host_permissions`'ı verilen adrese çevirir; adres `https://` değilse ya da localhost ise hata verir. Test: script http'yi reddeder, https'te dosyaları doğru yazar. `dist/` `.gitignore`'da.
- **E4:** zaten yapıldı (D-148, `purge_api_tokens`). Issue'da not edilir.

## Testler
- `test_login_lockout.py`, `test_i56/57/60_gaps.py`, `test_email_change.py`, `test_account_delete.py`, `test_settings_account.py`: test motoruyla aynı davranış; 10.000 anahtar testleri kalkar, yerine: yeniden başlatma (yeni nesne, aynı veritabanı) kilidi korur; satırlar hash'li (düz e-posta/IP yok); gece temizliği; route oturumu commit edilmez.
- Migration 0023 upgrade/downgrade (`test_migrations.py` modelle eşleşme).
- `test_config.py`: production APP_BASE_URL ve FORWARDED_ALLOW_IPS.
- `test_build_extension.py`.

## Adımlar
1. #17 model + migration + security.py + scheduler + testler.
2. #18 config + dokümanlar + testler.
3. #64 script + SPEC satırları + testler.
4. SPEC sürüm notu, DECISIONS D-198/D-199, LEARNING_LOG, security review, tam test, PR, CI, merge, kişisel instance (yedekle, migration 0023).

## plan-reviewer (2026-11-27) ve güncellemeler
- Eklenti derlemesi Python (`scripts/build_extension.py`), `key` çıkarılır, http/yerel/yollu adres reddedilir; `[extension-id]` argümanı yok.
- SPEC M13c satırı, §5.13, sürüm notu; D-020, D-149, D-155 altına güncelleme notları.
- Route oturumu kuralı D-198'de ve `security.py`'de yazılı (çağıranlar yazılmamış değişiklik yokken çağırır); StaticPool bunu kanıtlayamaz, ayrı test yok. Postgres'te ayrı test (`test_postgres.py`, CI `postgres-tests`, yerelde geçici container ile çalıştırıldı).
- Anahtarlar HMAC-SHA256 (`SECRET_KEY`).
- Eşzamanlılık ve ikinci havuz bağlantısı D-198'de kabul edilen risk.
- Test motoru `_DbCounters.engine` sınıf alanına `monkeypatch` ile (kendini geri alır); motorsuz test boş in-memory veritabanına çarpar.
- `FORWARDED_ALLOW_IPS` `os.environ`'dan okunur (`.env` yetmez, testi var); `Settings` alanı yok.
- Scheduler kapalıyken de: anahtarın eski satırları bir sonraki yazımında silinir.
- Sorular (Claude karar verdi): #64 kapanır, E2 için yeni V2 issue (#112); kişisel ortamda kilit yeniden başlatmada kalkmaz, çıkış şifre sıfırlama (`docker logs unfurl-personal-app`) ya da beklemek; slowapi bellekte, M13c'de kalan sınırlama.
