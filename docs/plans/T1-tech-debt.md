# T1 — Dört küçük teknik borç (#85, #88, #89, #90) · Teknik plan

## Context
M9a ile AI'sız MVP'nin bütün özellikleri tamam. M13d'den (README) önce, review'larda bulunup ertelenen dört küçük sorun kapanır: "düzgün çalışan bir uygulama" (Fatih, 2026-11-23). Hepsi AI'sız, birbirinden bağımsız ve küçük.

Branch: `chore/tech-debt` (`main`, `21636ce`). Her issue ayrı commit; tek PR "Closes #85, #88, #89, #90". Her commit'ten önce Fatih'e sorulur. Migration yok, yeni paket yok.

## Kararlar (Adım 0'da `docs/DECISIONS.md`'ye; D-168'den başlar)

### D-168 · Host başlığı kontrolü (#85)
- **Sorun:** Host kontrolü yok; bir web sayfası DNS rebinding ile `127.0.0.1:8100`'deki kişisel ortama kendi origin'iyle istek atabilir (girişsiz kısımlar: kayıt, giriş denemeleri, development'ta `/openapi.json`).
- **Karar:** Starlette `TrustedHostMiddleware`, izinli adlar: `APP_BASE_URL`'in host'u + `localhost` + `127.0.0.1` + testlerde `testserver`. Başka Host → 400. Ayar `config.py`'de türetilir (`allowed_hosts`), yeni env değişkeni yok. Docker sağlık kontrolü `localhost:8000` kullanır, izinli. Eklenti `localhost:8000` / `127.0.0.1:8100`'e istek atar, izinli.
- **Alternatifler:** Sadece kişisel ortamda açmak (dev'de de aynı risk var, kural tek olsun); `[::1]` eklemek (kullanılmıyor, gerekirse sonra).
- SPEC §10.1'e satır: "Host başlığı izinli adlarla sınırlı (D-168)".

### #88 · CSRF kontrolü formu önbelleğe koymaz
- **Sorun:** `verify_csrf` normal (htmx olmayan) urlencoded POST'ta formu Starlette'in varsayılan 1000 alan sınırıyla ayrıştırır; Starlette sonucu önbelleğe alır, route'un kendi sınırı (`cover_letters.py` 50, `profile.py` fotoğraf 5) hiç uygulanmaz.
- **Düzeltme:** `verify_csrf` formu ayrıştırmaz: `await request.body()` (Starlette gövdeyi saklar, route aynı gövdeyi kendi sınırıyla yeniden okur) ve token sadece `urllib.parse.parse_qsl(..., max_num_fields=MAX_ANY_FORM_FIELDS)` ile aranır. `MAX_ANY_FORM_FIELDS = 1100` (en büyük meşru form: CV editörü, 50 deneyim × 20 madde = 1000 onay kutusu + diğer alanlar); aşan istek 400. Route'ların kendi `request.form(max_fields=…)` sınırları artık gerçekten çalışır.
- **Yan bulgu:** CV editörü route'u `request.form()` varsayılanını (1000) kullanıyor, ama form 1000'i aşabilir (1000 madde + ~12 alan). `cvs.py` formu `max_fields=MAX_ANY_FORM_FIELDS` ile okur. Test: 1000 maddelik bir CV kaydı.
- Karar kaydı gerekmez (hata düzeltmesi); DECISIONS'a kısa not D-169'un altında değil, commit ve PR'da.

### D-169 · "Edit master" kilidi adresten değil, tek kullanımlık token'dan (#89)
- **Sorun:** `GET /cvs/{id}?edit_master=confirmed` (ve mektup) master'ı onay penceresi olmadan açık getirir; başka bir siteden ya da e-postadan gelen link onay adımını atlar (D-048).
- **Karar:** Onay penceresindeki "Edit master" artık bir **POST** formu (`POST /cvs/{id}/edit-master`, `POST /cover-letters/{id}/edit-master`; CSRF, giriş, `user_id`, sadece master, 30/dk). Route rastgele bir değer üretir, oturumda belge başına saklar (`session["master_unlock"]["cv:12"] = değer`) ve `303 → /cvs/12?unlock={değer}` yapar. Editör kilidi sadece adresteki değer oturumdakiyle eşleşirse açar. Form ve "Save as new version" bu değeri gizli alanda taşır; kaydetme route'ları da oturumla karşılaştırır (`secrets.compare_digest`). Böylece:
  - davranış bugünküyle aynı: kilit o sayfa açık kaldıkça açık, master'ı yeniden açınca kapalı (yeni onay gerekir);
  - dışarıdan gelen bir link değeri bilemez; eski `?edit_master=confirmed` yok sayılır (kilitli açılır).
  - Oturumda en fazla birkaç belge (CV + mektup masterları, dil başına bir) tutulur; çıkışta oturum zaten silinir.
- **Alternatifler:** Kilidi oturumda süreli tutmak (ör. 1 saat; master'ı yeniden açınca habersiz açık gelir, D-048'in korumasını zayıflatır); imzalı süreli değer (itsdangerous; oturumsuz çalışır ama süre seçmek gerekir, oturum zaten var).

### D-170 · Çift tıklama kilidi sadece formlarda (#90, D-126'yı günceller)
- **Sorun:** `<body hx-disabled-elt="find [type=submit]">` her htmx isteğine miras kalır; formu olmayan 22 eleman (bildirim rozeti, board linkleri, Learning sekmeleri, profil "Edit" butonları, bölge yenilemeleri) her istekte konsola hata yazar. Davranış etkilenmez ama gerçek hatalar gürültüde kaybolur.
- **Karar:** Kural `<body>`'den kalkar; **değişiklik gönderen her `<form>`** (`hx-post/put/patch/delete`, şu an 28) kendi üzerinde `hx-disabled-elt="find [type=submit]"` taşır. Kendi başına istek atan butonlar `this`, otomatik kaydeden formlar `unset` olarak kalır (D-126). `tests/test_loading.py`: (1) değişiklik gönderen her eleman, form dahil, `hx-disabled-elt` taşır; (2) `base.html` artık bu kuralı taşımaz. Elle kontrol: konsolda hata kalmadı.
- **Alternatifler:** `<body>`'de bırakıp formu olmayan 22 elemana `unset` eklemek (kural yine her yere miras kalır, yeni bir `hx-get` eklendikçe hata geri gelir).

## plan-reviewer (2026-11-23) ve güncellemeler
Sonuç "Needs changes": 3 Blocker (hepsi #88), 7 Should, 4 Nit. Hepsi aşağıdaki maddelerle kapandı; bunlar yukarıdaki kararların yerine geçer.

- **#88 yaklaşımı değişti (3 Blocker).** FastAPI, `Form()` parametreli route'larda (login, register, şifre sıfırlama, settings …) gövdeyi bağımlılıklardan **önce** `request.form()` ile okur; akış tükenir ve `_body` kalmaz. `verify_csrf`'te `request.body()` bu route'larda `RuntimeError` (500) verirdi, ayrıca gövdeyi sınırsız belleğe alırdı. Yeni yaklaşım:
  - `verify_csrf` formu yine Starlette ile, ama **üst sınırla** okur: `await request.form(max_fields=MAX_ANY_FORM_FIELDS)` (önbelleğe alınır, ayrıştırma sınırlı ve erken durur; özel alanlara dokunulmaz, ikinci ayrıştırma yok).
  - Daha küçük sınırı olan route'lar (`cover_letters.read_form_data` 50, `profile.read_photo_upload` 5) ayrıştırmadan **sonra** alanları sayar (`len(form.multi_items())`) ve fazlaysa 400 verir. Hangi yoldan (CSRF ya da route) ayrıştırılmış olursa olsun sınır uygulanır.
  - `cvs.py` formu `max_fields=MAX_ANY_FORM_FIELDS` ile okur (htmx'li kayıtta ilk ayrıştırma route'ta olur; 1000 madde + ~17 alan 1000'i aşar).
  - **D-171 · Form alanı üst sınırı** (yeni karar): uygulama genelinde bir formda en fazla 1100 alan (CV editörü: 50 deneyim × 20 madde = 1000 onay kutusu + 7 bölüm + ~10 alan); route'lar daha küçük sınır koyabilir.
  - Testler: 51 alanlı htmx'siz (token formda) mektup kaydı 400 ve mektup değişmez; aynısı htmx'li 400; 1101 alanlı htmx'siz form 400; CSRF token formda hâlâ çalışır (login); 1000 maddeli CV kaydı (testte alan sayısının 1000'i aştığı doğrulanır) 200.
- **#89 kapsamı ve iki sekme.**
  - "Refresh from profile" de master'da `edit_master` okuyup `?edit_master=confirmed`'a yönlendiriyor (`cvs.py:359-372`, `cover_letters.py:215-225`, iki editörün `hx-vals`'ı): adım 3'e eklendi, test: değersiz refresh 409.
  - "Save as new version" kilidi kontrol etmiyor (master'dan yeni versiyon zaten master'ı değiştirmez): plandaki o madde çıkarıldı.
  - **İki sekme (Fatih'e soru 1):** önerim değer **oturum başına** ve yeniden kullanılır: oturumda zaten bir değer varsa yeni "Edit master" onu kullanır, değiştirmez. İki sekme de çalışır; dışarıdan gelen link değeri yine bilemez. Başlık "tek kullanımlık" değil, **"oturuma bağlı değer"**. Çıkışta oturumla birlikte silinir.
  - Onay penceresindeki buton htmx `hx-post` (CSRF başlıkta), cevap `go_to` ile `HX-Redirect: /cvs/{id}?unlock={değer}`. Master değilse (kopya) 409; başkasının belgesi 404.
  - Mevcut 31 `edit_master` kullanımı (test_cv_editor, test_m3a_gaps, test_m8a_gaps, test_cv_actions, test_cover_letter_pages, test_m3b_gaps) bir yardımcıya geçer: `tests/helpers.unlock_master(client, kind, id) -> değer`.
- **#85 ayrıntıları.**
  - Sıra: `TrustedHostMiddleware`, `ExtensionApiGuard`'dan sonra ve `SecurityHeaders`'tan önce eklenir (yani SecurityHeaders'ın içinde, ExtensionApiGuard'ın dışında): 400 cevabı CSP ve `nosniff` başlıklarını taşır, kötü Host'un gövdesi okunmaz. Test: 400 cevabında bu başlıklar var.
  - `testserver`: sadece `app_env == "development"` iken izinli (DNS rebinding'de Host saldırganın alan adıdır, "testserver" olamaz; dev zaten yerel). Production ile kurulan test uygulaması (`tests/helpers.py` `PRODUCTION_SETTINGS`) `APP_BASE_URL`'in adıyla test edilir.
  - IPv6 `[::1]` Starlette'in `host.split(":")` mantığıyla çalışmaz; D-168'in alternatiflerine yazılır.
- **#90 testi güçlenir:** değişiklik gönderen her eleman (form dahil, `name != "form"` istisnası kalkar) `hx-disabled-elt` taşır; formlarda değer `find [type=submit]` ya da `unset`; `base.html`'in bu kuralı **taşımadığı** test edilir. D-126'nın metni (filtre formları `unset`) D-170'te güncellenir.
- **Kapanış:** Bu bir milestone değil: etiket yok, CLAUDE.md'de kutu yok. SPEC v2.1.36 commit'inden sonra Fatih claude.ai kopyasını yeniler (WORKFLOW §3).

## Adımlar (her biri ayrı commit, önce Fatih'e sorulur)
0. `docs: plan for four small fixes` + `docs: SPEC and D-168…D-171` (SPEC v2.1.36: başlık; §10.1 Host satırı; D-048 ve D-126'ya atıf).
1. `fix: one place for the double-click guard (#90)` — 28 form, `base.html`, test_loading.
2. `fix: form field limits apply after the CSRF check (#88)` — `security.py`, `cvs.py`; testler: 51 alanlı htmx'siz mektup formu 400, 6 alanlı htmx'siz fotoğraf formu 400 (multipart zaten header'sız reddedilir, sadece urlencoded), CSRF token formda hâlâ çalışır, 1000 maddelik CV kaydı çalışır, 1101 alanlı form 400.
3. `fix: unlock a master with a one-time value (#89)` — iki POST route, editörler, kaydetme route'ları, şablonlar; testler: eski `?edit_master=confirmed` kilitli açar, POST'suz değer işe yaramaz, başka belgenin değeri işe yaramaz, başkasının master'ı 404, kopya için 409/404, CSRF'siz 403, kaydetme değer olmadan master'ı değiştirmez, Save as new version değerle çalışır.
4. `fix: refuse unknown Host headers (#85)` — `main.py`, `config.py`; testler: `Host: evil.example` 400, `localhost:8000`, `127.0.0.1:8100` ve `testserver` 200, `APP_BASE_URL`'deki ad izinli.
5. Kapanış: test-writer, security-reviewer, `uv run pytest` (normal ve `TZ=UTC`), ruff, CI, Chrome'da kısa elle test (konsol temiz, Edit master akışı, eklenti kayıt), PR "Closes #85, #88, #89, #90". LEARNING_LOG'a kısa giriş.

## Riskler
- **#88 gövde iki kez okunur:** token için bir kez `parse_qsl`, route'ta bir kez form. Gövde zaten sınırlı ve bu sadece htmx'siz POST'larda olur (htmx token'ı başlıkta gönderir, gövdeye dokunulmaz).
- **#89 iki sekme:** aynı master iki sekmede açılırsa ikincisinin "Edit master"ı yeni değer üretir, ilk sekmenin kaydı reddedilir ("Your session changed" benzeri mesaj, master değişmez). Kabul edilebilir; mesaj test edilir.
- **#85 başka adla erişim:** kullanıcı kişisel ortamı başka bir adla açıyorsa (ör. makine adı) 400 alır. `APP_BASE_URL` o adla ayarlanırsa çalışır; README/`.env.personal.example` notu.
- **#90 yeni formlar:** yeni bir değişiklik formu eklenince attribute unutulursa test yakalar.

## Fatih'in cevapları (2026-11-23)
1. **#89 iki sekme:** değer oturum başına ve yeniden kullanılır, iki sekme de çalışır (öneri kabul).
2. **Plan onaylandı:** issue başına bir commit, dördünü kapatan tek PR.
