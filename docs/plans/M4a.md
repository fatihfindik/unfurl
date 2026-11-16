# M4a — Board · Teknik plan (Design fazı)

## Context
M3b ile CV'ler PDF olarak iniyor (`v0.3b-m3b`). M4 üçe bölündü (D-060, SPEC v2.1.9). M4a (issue #32, SPEC §12 › M4a, §5.5, §5.6, §6.0, §6.3, §6.7) başvuru takibinin temelini kurar: ilan elle eklenir, board'da sürüklenir, aranır, filtrelenir, arşivlenir ve her işlem Undo ile geri alınır. Tema seçimi hesaba kaydedilir.

Branch: `m4a-board` (`main`, `926e9b9`). Etiket `v0.4a-m4a`. Onaydan sonra plan issue #32'ye yorum olarak eklenir.

Çalışma kuralı: her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push ve PR da onayla. Yeni paket yok (SortableJS, itsdangerous, slowapi zaten var).

### Fatih'in kararları (2026-11-16, Plan fazı)
1. M4 üç parça: M4a Board, M4b Detay + next step, M4c Başvuruda belgeler (D-060).
2. Sonraki milestone'lara bağlı parçalar **gösterilmez**: uyum skoru rozeti, minimum skor filtresi, Fit sıralaması (M7), "Auto-fill with AI" (M5), eklenti bilgi kutusu (M6).
3. Silmede Undo (profil satırı, CV) M4c'de.

### plan-reviewer (2026-11-16)
Sonuç "Needs changes": 2 Blocker, 13 Should, 3 Nit; hepsi bu sürüme işlendi. Blocker'lar: (1) yeniden numaralama komşu kartların `updated_at`'ini değiştiriyordu → `last_activity_at` (D-061); (2) Undo ham `position` yazıyordu ve çift Undo'ya açıktı → index'e ekle + yeniden numarala, event'i silerek sahiplenme (D-064).

### Fatih'in kararları (2026-11-16, plan-reviewer sonrası)
4. **Arşivleme M4a'da liste satırından** ("Archive"; arşivli satır ve kartta "Restore") (D-066).
5. **Tema kolonu varsayılan `system`** (SPEC §5.1'deki gibi; mevcut test hesapları bir kez `system`'e döner) (D-068).
6. **Klavyeyle durum değiştirme M4b'yi bekler** (D-069); M4b'nin kabul kriterlerine eklenir.
7. İtiraz gelmeyen varsayımlar: "Updated" son durum / arşiv hareketidir (`last_activity_at`); kullanıcı başına en fazla 2.000 başvuru; aynı sütunda sadece sıra değiştirmenin Undo'su yok.

### Yeni kararlar (Adım 0'da `docs/DECISIONS.md`'ye)
- **D-061 · M4a'da `Application` kolonları (D-025 kalıbı):** `user_id` (FK, index), `company` (≤200), `job_title` (≤200), `location` (≤200, nullable), `remote_type` (onsite/hybrid/remote/unknown), `job_url` (≤2000, nullable), `source`, `description_raw` (text, ≤50.000), `status`, `position`, `applied_at`, `archived`, `last_activity_at`, `created_at`, `updated_at`. **`last_activity_at`** kartta ve listede "Updated" olarak gösterilen ve varsayılan sıralamada kullanılan tarihtir: sadece ekleme, durum değişikliği, arşiv ve restore'da değişir, Undo eski değerine döndürür. `updated_at` bunun için kullanılamaz, çünkü bir sütunu yeniden numaralamak komşu kartların `updated_at`'ini de değiştirir (plan-reviewer #1). `applied_at` şimdilik UTC tarihidir (`User.timezone` M10/M11'de). `description_raw` boşsa `""`. `user_id` FK'si `ON DELETE CASCADE`. Enum'lar User.plan gibi düz metin + Python'da izinli değer listesi. Sonraki parçalarda eklenecekler (hepsi nullable veya `server_default`'lu): `employment_type` (M5), `next_action_*`, `interview_*`, `contact_*`, `notes`, `salary_text` (M4b); `cv_id` (M4c); `cover_letter_id` (M8); `canonical_url` (M6, §8.5; mevcut satırlar `job_url`'den doldurulur); `description_structured`, `language`, `posting_closes_at` (M5). `ApplicationEvent`: `application_id` (FK, cascade), `type`, `from_status`, `to_status`, `message`, `created_at`, `updated_at`. Event'ler sadece sahibi doğrulanmış bir başvuru üzerinden, `(id, application_id)` ile okunur; asla sadece id ile. Kullanıcı başına en fazla `MAX_APPLICATIONS_PER_USER = 2000` başvuru (arşivliler dahil; `MAX_CVS_PER_USER` kalıbı).
- **D-062 · "Add job" formu (AI'sız):** Alanlar: "Job link or posting text" (tek alan), Company (zorunlu), Role (zorunlu), Location, Work mode (Remote / Hybrid / Onsite / boş; prototipte yok, çalışma şekli filtresi için gerekli), Status (Saved / Applied). Tek alan kuralı: değer boşluksuz tek bir `http(s)://` adresiyse `job_url` olur (profildeki `check_url` ile doğrulanır), değilse `description_raw`. `source` adresten türetilir: linkedin.com → linkedin, stepstone.* → stepstone, karriere.at → karriere_at, indeed.* → indeed, xing.com → xing, başka adres → company_site, adres yoksa → manual. Host `urlsplit` ile okunur ve tam ya da nokta-sonek eşleşmesi aranır (`linkedin.com.evil.example` linkedin değildir); `stepstone` ve `indeed` için host'un bir etiketi tam olarak bu ad olmalıdır (`www.stepstone.at`, `at.indeed.com`). Kullanıcı kaynağı seçmez. Status = Applied ile eklenirse `applied_at` bugünün tarihi olur. Yeni kayıt sütunun en üstüne gelir. Hız sınırı: slowapi `30/minute`.
- **D-063 · Sütun içi sıra:** `position` sütun (durum) içinde 0'dan başlar, arşivliler dahil değil. Taşıma isteği: `PATCH /applications/{id}/move`, gövde `status` + `after_id` (bırakıldığı kartın üstündeki kartın id'si; en üste bırakıldıysa boş). Sunucu kartı hedef sütunun **tam** sırasında o karttan hemen sonraya koyar ve iki sütunu yeniden numaralar. Sıra numarası yerine komşu id'si gönderilir, çünkü filtre veya "ilk 20" açıkken ekrandaki sıra numarası tam sütundakiyle aynı değildir. Arşivli kartlar sütunun sonunda durur ve sıraya girmez; tarayıcı `after_id` olarak en yakın arşivli olmayan üst kartı yollar. `after_id == id` ise hiçbir şey değişmez. `after_id` başka kullanıcınınsa 404; kullanıcının ama artık o sütunda olmayan bir kartıysa (eski ekran) kart sütunun üstüne konur ve bölge yeniden çizilir. `status` sadece `BOARD_STATUSES`'tan olabilir (422).
- **D-064 · Undo: imzalı, kısa ömürlü token; event'i "sahiplenme" kuralı:** Geri alınabilen her işlem (durum değişikliği, arşiv, restore, ekleme) `ApplicationEvent` oluşturur. Yanıt, `itsdangerous` ile imzalanmış bir token taşır: `{user_id, application_id, event_ids: [...], before: {...}}`, ömür 60 sn (toast 5 sn; yavaş ağ payı). `before` izinli alanlardan oluşan genel bir sözlüktür (M4a: `status`, `index`, `applied_at`, `archived`, `last_activity_at`); `event_ids` bir listedir. Böylece M4b (bir taşıma birden çok event ve `next_action_*` yazar) ve M4c (kilit + `ApplicationDocument`) aynı yapıyı genişletir. `POST /applications/undo` (CSRF'li) tek bir transaction'da: (1) imza, süre, `user_id == oturum`; (2) `get_owned_application`; (3) token'daki son event hâlâ o başvurunun **en büyük id'li** event'i mi; (4) event'ler `DELETE … WHERE id IN (…) AND application_id = :id` ile silinir ve silinen satır sayısı beklenenle aynıysa devam edilir (aynı anda gelen iki Undo'dan sadece biri geçer); (5) alanlar geri yazılır, kart eski sütununa `move` ile aynı "index'e ekle (sınırlanmış) + yeniden numarala" koduyla konur (ham `position` yazılmaz; arada sıra değişmiş olabilir). Eklemenin Undo'su başvuruyu ve `created` event'ini siler. Token ikinci kez kullanılırsa veya arada event yazan başka bir değişiklik olduysa hiçbir şey değişmez, toast "Nothing to undo." Tablo gerekmez. Alternatifler: `PendingUndo` tablosu (temizlik işi ister); adreste "önceki durum" taşımak (imzasız, event'i geri alamaz).
- **D-065 · Sunucudan toast:** htmx yanıtlarında `HX-Trigger: {"toast": {"message": …, "undo_url": …, "undo_token": …}}` (Undo yoksa son ikisi boş); başlık `json.dumps` ile ASCII'ye kaçırılarak yazılır ("Şişecam" başlıkta hata vermesin; test). `app.js` bu olayı dinler ve M0'daki toast bileşenini gösterir (mesaj `x-text` ile), Undo tıklanınca token'ı `undo_url`'ye yollar. Adres yanıtta geldiği için M4c'deki profil ve CV silme Undo'su aynı bileşeni kullanır. Değişiklik yapan yanıtlar ayrıca `applications-changed` olayını tetikler; bölge kendini o anki filtrelerle yeniden ister (D-040 kalıbı). Metinler prototipten: "{Company} moved to {Status}.", "{Company} saved to {Status}.", "{Company} archived.", "{Company} restored.", Undo sonrası "Undone." Aynı sütun içinde sadece sıra değiştirmek event ve toast üretmez (kart geri sürüklenir; §6.0/2 listesinde de yok). SPEC §12 › M4a'daki "sıra" kelimesi Adım 0'da çıkarılır.
- **D-066 · M4a'da arşivleme liste satırından:** Liste satırında "Archive", arşivli satır ve kartta "Restore". `POST /applications/{id}/archive`, `/restore`; event `archived` / `restored`, toast + Undo. Archive ve Restore gerçek `<button>`'dır. Arşivli başvuru sürüklenemez, özet sayılarına girmez; Restore eski sütununun en üstüne koyar.
- **D-067 · Arama Python'da, filtreler SQL'de:** Kaynak, çalışma şekli, durum ve arşiv filtreleri SQL'de (`user_id` ile birlikte). Arama metni (şirket, pozisyon, konum) Python'da tek bir `search_key()` ile karşılaştırılır: D-057'deki eşleme (ı→i, İ→I, ß→ss…) + NFKD + aksan silme + `casefold()`, hem aranan metne hem alanlara uygulanır. Yalnız `casefold()` yetmez ("İstanbul" → "i" + birleşen nokta; "istanbul" bulmaz). Aynı anahtar Company sıralamasında da kullanılır. SQL'de yapılmamasının nedeni: SQLite'ın `lower()`'ı sadece ASCII'dir ve iki veritabanında aynı sonucu istiyoruz. Sınırlar: `q` en fazla 100 karakter, `page` 1…son sayfa arasına çekilir, kullanıcı başına en fazla 2.000 başvuru (D-061); liste sıralaması ve sayfalama da aynı listede yapılır, `description_raw` yüklenmez.
- **D-068 · Tema `User.theme`'de (D-007'yi günceller):** `User.theme`, SPEC §5.1'deki gibi NOT NULL, varsayılan `system` (`server_default`). Giriş yapmış sayfalarda `user.theme`, girişsiz sayfalarda cookie, o da yoksa `system`. Seçici, giriş yapılmışsa `POST /settings/theme` (CSRF) ile kaydeder; kaydetme adresi sadece `current_user` varken sayfaya yazılır (girişsiz sayfada seçici sadece cookie yazar, login'e yönlenmez; test). Cookie her durumda yazılmaya devam eder ve girişte sunucu tarafından `user.theme` ile eşitlenir (Path=/, 1 yıl, SameSite=Lax, HttpOnly değil, `Secure` yok: D-019). Mevcut hesaplar `system` ile başlar (henüz gerçek kullanıcı yok). Alternatif: nullable kolon + cookie'ye düşme (SPEC'ten sapar, bir kural daha).
- **D-069 · M4a'da durum klavyeyle değişmez (bilinen eksik):** Board'da durum sadece sürükleyerek değişir (SPEC §6.7). Klavye yolu M4b'deki durum seçiciyle gelir; M4b'nin kabul kriterlerine "durum klavyeyle değiştirilebiliyor" eklenir (SPEC §12 ve #33, Adım 0). M13'ten önce canlıya çıkış yok.

## 1. Veri modeli
- `app/models/application.py`: `Application`, `ApplicationEvent`, `STATUSES` (saved, applied, interview, offer, rejected, withdrawn), `BOARD_STATUSES` (ilk 5; withdrawn M4a'da arayüzde yok), `REMOTE_TYPES`, `SOURCES`, `EVENT_TYPES`.
- Migration `0007_add_applications.py` (`--autogenerate`, sonra elle gözden geçirilir; iki tablo; index: `(user_id, archived, status, position)`), `0008_add_user_theme.py` (`server_default='system'`). Her biri upgrade → downgrade → upgrade + `alembic check` (D-011); NOT NULL kolonlarda `server_default` elle kontrol edilir (0006 dersi).

## 2. Modüller

| Dosya | İçerik |
|---|---|
| `app/models/application.py` | Tablolar ve izinli değer listeleri |
| `app/schemas/application.py` | `AddJobForm` (uzunluklar, zorunlular, link/metin kuralı, `check_url`), `BoardQuery` (q, source, mode, status, view, archived, sort, page; bilinmeyen değer → varsayılan) |
| `app/services/application_service.py` | `get_owned_application` (404), `create`, `move` (durum + sıra + event + `applied_at`), `archive`, `restore`, `undo`, `counts` (özet kartları), `search` (filtre + arama + sıralama), `detect_source(url)`. Durum ve sıra kurallarının tek yeri. |
| `app/services/undo_service.py` | `make_token(user, application, event, before)`, `read_token(user, token)` (imza, süre, kullanıcı); `auth_service`'teki serializer kalıbı, ayrı salt |
| `app/routers/applications.py` | Aşağıdaki route'lar; sadece servisi çağırır |
| `app/routers/settings.py` (yeni, küçük) | `POST /settings/theme` |
| `app/templating.py` | `theme_context` kullanıcıyı da okur |
| `app/templates/applications/` | `index.html`, `_summary.html`, `_filters.html`, `_board.html`, `_column.html`, `_card.html`, `_list.html`, `_add_job_form.html` |
| `app/static/js/app.js` | `toast` olayı dinleyicisi, board için Sortable (gruplar arası sürükleme, `after_id`), Rejected aç/kapa |

### Route'lar (hepsi `require_user`; POST/PATCH CSRF'li)
| Route | Ne yapar |
|---|---|
| `GET /` | Sayfa; htmx isteğinde sadece `#applications-region` (özet + filtreler + board/liste). Sorgu parametreleri adreste kalır (`hx-push-url`); bölgede `hx-history="false"`: htmx sayfa kopyasını (şirket adlarıyla) localStorage'a yazmaz, çıkıştan sonra kalıntı olmaz ve geri tuşunda sayfa sunucudan gelir. |
| `GET /applications/column/{status}` | "Show more": sütunun tüm kartları (aynı filtrelerle); `status` sadece `BOARD_STATUSES` (404) |
| `POST /applications` | Add job. Hata: form 200 ile yeniden çizilir. Başarı: toast + Undo, bölge yenilenir, modal kapanır. `30/minute` |
| `PATCH /applications/{id}/move` | `status`, `after_id` (D-063). Yanıt: toast + `applications-changed` |
| `POST /applications/{id}/archive`, `/restore` | D-066 |
| `POST /applications/undo` | D-064 |
| `POST /settings/theme` | `theme` ∈ light/dark/system, değilse 422 |

Geçersiz veya başkasına ait id her yerde 404 (CV route'larındaki `get_owned_cv` kalıbı: sayı olmayan, 0, negatif, çok büyük id dahil).

## 3. Ekran (prototip `Applications.dc.html`; SPEC kazanır)
- Başlık: "Applications", "Every job you saved or applied to. Drag a card to change its status.", sağda **Add job**.
- Özet kartları: Saved "Jobs you are considering", Applied "Waiting for a reply", Interview "In the interview process", Offer "Offers on the table", Rejected "Closed, keep the learnings". Tıklayınca o duruma filtre, tekrar tıklayınca kalkar. Sayılar filtrelerden etkilenmez, arşivliler sayılmaz.
- Filtre satırı: arama kutusu (300 ms debounce), "All sources", "Any work mode", "Show archived (n)", "Clear filters" (filtre varken), sağda Board / List.
- Board: 5 sütun, başlıkta sayı; her sütun kendi içinde kaydırılır. Durum filtresi (özet kartı) board'da da geçerlidir: diğer sütunlar boş görünür (prototipteki gibi). Kart: şirket, pozisyon, "Vienna · Hybrid", "LinkedIn · 3 days ago" (`time_ago`). Rejected daraltılmış ("Show" / "Hide"; "Hidden to keep the board calm. Drop a card here to reject it."), kapalıyken de bırakma alanı. Boş sütun: "No jobs here". Sütunda ilk 20 + "Show more (n)". Arşivli kart soluk, "ARCHIVED" etiketi ve "Restore", sürüklenmez.
- Liste: Company (sıralanabilir ▲▼), Role, Status, Source, Updated, satır sonunda Archive / Restore. Varsayılan sıra: son güncellenen üstte. "Showing 1–25 of 40 · 25 per page", Previous / Next.
- Add job modalı (M0 `modal` bileşeni): D-062 alanları, "Cancel", "Save job". Eklenti kutusu ve AI butonu yok.
- Hiç başvuru yokken boş durum: kısa bir metin ve "Add job" butonu.
- Klavye: sürükle-bırak fareyle; klavye ile durum değiştirme M4b'de (D-069).
- Mobil: sütunlar yatay kaydırılır; dokunmatik ekranda sürükleme kısa bir basılı tutmayla başlar (Sortable `delayOnTouchOnly`), kaydırmayla karışmasın.

## 4. Testler (hepsi `uv run pytest`; gerçek zaman yerine `freezegun`)
- `test_application_service.py`: `create` (event `created`, sütunun üstünde), `move` (event `status_changed` from/to; `applied_at` ilk kez dolar, dolu ise değişmez; iki sütun yeniden numaralanır; `after_id` ile araya girer; aynı sütunda sıra değişimi event üretmez), Status = Applied ile eklemede `applied_at`; **A kartını taşımak B kartının "Updated" değerini değiştirmez**; `archive` / `restore`, `counts`, `detect_source` (her kaynak + bilinmeyen + adres yok + `linkedin.com.evil.example`); 2.000 sınırı; başka kullanıcının sırası yeniden numaralamadan etkilenmez.
- `test_application_undo.py`: durum, arşiv, restore ve eklemenin Undo'su (alanlar ve `applied_at` eski haline döner, event silinir); sıra değiştir, sonra Undo (sıra numaraları tekrarsız kalır); token ikinci kez → değişmez; arka arkaya iki Undo isteği 500 vermez; arada başka değişiklik → değişmez; süresi dolmuş (freezegun), bozulmuş, başka kullanıcının token'ı → değişmez.
- `test_applications_page.py`: özet sayıları; arama ("istanbul" → "İstanbul", "osterreich" → "Österreich", 100 karakterden uzun `q`); B kullanıcısının başvuruları A'nın board'unda, listesinde, sayılarında, aramasında ve `column/{status}`'ında hiç görünmez; sayfa numarası sınır dışıysa son sayfa; kaynak ve çalışma şekli filtresi; durum filtresi; arşivli gizli / "Show archived" ile görünür; liste sıralaması ve sayfalama (26 kayıt → 2 sayfa); bilinmeyen parametre değerleri varsayılana düşer; gizlenenler (Fit, minimum skor, AI butonu) sayfada yok; htmx isteği sadece bölgeyi döner.
- `test_applications_routes.py`: Add job doğrulama (boş şirket, çok uzun alan, `javascript:` adresi, 50.001 karakter); başarıda `HX-Trigger` toast + token (Türkçe şirket adıyla); `move` geçersiz durum 422; her route girişsiz 303 / htmx `HX-Redirect`; başka kullanıcının başvurusu ve `after_id`'si 404; CSRF'siz POST reddedilir; hız sınırı.
- `test_theme.py`: seçim kaydedilir; geçersiz değer 422; başka bir istemcide girişte aynı tema; girişsiz sayfada cookie kullanılır ve seçici login'e yönlendirmez; girişte cookie eşitlenir.
- `test_migrations.py` mevcut (model ↔ migration). Build sonunda `test-writer` yetki boşluklarına bakar.

## 5. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)
| # | Adım | Commit |
|---|---|---|
| 0 | Kararlar D-061…D-069 `DECISIONS.md`'ye; SPEC v2.1.10: §12 › M4a ("sıra" çıkar, Work mode alanı, arşivleme liste satırından, `last_activity_at`), §5.5 (`last_activity_at`), §12 › M4b ve #33 (klavyeyle durum). Fatih claude.ai kopyasını yeniler. | `docs: add m4a plan and decisions` |
| 1 | Model + migration `0007` + `application_service.create` / `get_owned_application` / `detect_source` + testler | `feat: add application and event models` |
| 2 | Sayfa (salt okunur): özet kartları, board sütunları ve kartlar, boş durum | `feat: show applications on a board` |
| 3 | Add job modalı + `POST /applications` + sunucudan toast (D-065; Undo henüz yok) | `feat: add jobs by hand` |
| 4 | `move` servisi + `PATCH …/move` + sürükle-bırak (Sortable), Rejected aç/kapa, "Show more". **Tarayıcı kontrolü (Claude in Chrome).** | `feat: change status by dragging cards` |
| 5 | Undo: `undo_service`, `POST /applications/undo`, durum ve ekleme için toast'ta Undo | `feat: undo status changes and new jobs` |
| 6 | Liste görünümü: sıralama, sayfalama, Board / List geçişi | `feat: add the applications list view` |
| 7 | Arama + filtreler + özet kartı filtresi + "Clear filters" | `feat: search and filter applications` |
| 8 | Arşiv / Restore + "Show archived" + Undo | `feat: archive and restore applications` |
| 9 | Tema: migration `0008`, `POST /settings/theme`, `theme_context`, girişte cookie eşitleme | `feat: save the theme to the account` |
| 10 | `test-writer`, `security-reviewer`, tarayıcı kontrolü, Fatih'in elle testi (kısa kontrol listesi), README, LEARNING_LOG, CLAUDE.md, PR (`Closes #32`), merge sonrası etiket `v0.4a-m4a` | `test: …`, `docs: …` |

## 6. Riskler
1. **Sürükle-bırak ile sunucu sırasının ayrışması:** Sortable DOM'u hemen değiştirir, sunucu reddederse (404, 422) ekran yanlış kalır. Her taşımadan sonra bölge sunucudan yeniden çizilir; hata olursa da (M2b'deki `data-sort-reload` kalıbı).
2. **Aynı anda iki taşıma:** iki sekmeden gelen istekler sırayı bozabilir. Her taşıma sütunu baştan numaralar; en kötü durumda sıra beklenmedik olur, veri kaybolmaz. Kabul; #22'deki gibi eşzamanlılık notu.
3. **Undo token'ında durum taşımak:** içerik imzalıdır, gizli değildir (şirket adı yok, sadece id ve önceki alanlar). "Son event" kuralı eski token'ın yeni değişiklikleri ezmesini engeller.
4. **Erişilebilirlik:** M4a'da durum sadece sürükleyerek değişir; D-069 ile kayda geçti, M4b kapatır.
5. **Bölgenin yeniden çizilmesi ve arayüz durumu:** Rejected açık/kapalı ve arama kutusundaki odak yenilemede kaybolabilir. Rejected durumu bölgenin dışındaki Alpine verisinde tutulur; arama kutusu bölgenin dışında kalır. `hx-history="false"` olduğu için geri tuşunda Sortable yeniden kurulur.
6. **Python'da arama:** 2.000 başvuru sınırıyla (D-061) her istekte en fazla 2.000 küçük satır okunur; `description_raw` listede yüklenmez (kolon seçimi veya `defer`).
7. **Kapsam:** 10 adım; M4a 1–2 haftalık sınırın üst ucunda. Tema (Adım 9) M4a'nın kabul kriteri olduğu için burada kalır; süre aşılırsa SPEC §12 ve #32/#33 aynı değişiklikte güncellenerek M4b'ye taşınır (ayrı PR yok: milestone başına bir PR).

## 7. Doğrulama
- `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, CI (Postgres'te migration'lar).
- Tarayıcı: ilan ekle → board'da görün → sürükle (durum + sıra kalıcı) → Undo → listede sırala → ara ("İstanbul") → filtrele → arşivle → Show archived → Restore → temayı değiştir, çıkış yap, gir.
- Kabul kriterleri issue #32'de tek tek işaretlenir.

## 8. Kapsam dışı
Detay sayfası, karta tıklama, Needs attention, mülakat modalı, next step, notlar (M4b). Belge bağlama ve kilitleme, silmede Undo (M4c). Uyum skoru, AI ile doldurma, `canonical_url` ve tekrar kaydı önleme (M5–M7). Bildirimler (M11).
