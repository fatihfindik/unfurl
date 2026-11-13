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
