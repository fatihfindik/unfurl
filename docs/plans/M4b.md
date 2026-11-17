# M4b — Detay sayfası ve next step · Teknik plan (Design fazı)

## Context
M4a board'u getirdi (`v0.4a-m4a`): ilan ekleme, sürükle-bırak, liste, filtreler, arşiv, Undo, tema. M4b (issue #33, SPEC v2.1.11 §12 › M4b, §5.5, §5.6, §6.0/3, §6.3, §6.7, §6.13) her başvuruya bir **detay sayfası**, tek bir **next step** (sonraki iş + tarih), **mülakat modalı** ve ana sayfada **Needs attention** ekler. M4a'nın bilinen eksiği (D-069: durum sadece sürükleyerek değişiyor) burada kapanır.

Branch: `m4b-application-detail` (`main`, `8dbff88`). Etiket `v0.4b-m4b`. Onaydan sonra plan issue #33'e yorum olarak eklenir.

Çalışma kuralı: her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push ve PR da onayla. Yeni paket yok (freezegun dev bağımlılığı zaten var; saat dilimleri Python'un `zoneinfo`'su).

### Fatih'in kararları (2026-11-16, Design fazı)
1. **Details formu M4b'de** (D-075): Details kartında "Edit" ile küçük bir form.
2. **"Done"** (D-073): adımı bitirir, durum değişene kadar next step boş kalır; tek istisna thank-you Done → 5 gün sonra Follow up.
3. **Saatler Viyana'da** (D-071): `User.timezone` gelene kadar herkes için `DEFAULT_TIMEZONE`.

### plan-reviewer (2026-11-16)
Sonuç "Needs changes": 2 Blocker, 11 Should, 3 Nit; hepsi bu sürüme işlendi. Blocker'lar: (1) tek bir `next_action_done_at` hangi adımın bittiğini bilemiyordu, thank-you → follow-up sonsuz döngüye girebiliyordu → thank-you Done artık saklanan bir follow-up yazar, diğer Done'lar "durum değişene kadar yok" demek (D-073, kolon kalktı); (2) event yazmayan işlemlerin (Snooze) Undo'su tekrar oynatılabiliyor ve fazla alan geri yazıyordu → Undo token v2: her işlem sadece kendi alanlarını taşır ve Undo, alanlar hâlâ işlemin bıraktığı değerdeyse çalışır (D-079).
Reviewer'ın sorularına verdiğim cevaplar (Fatih'in önerilerle devam kararı kapsamında): B — Needs attention'da Offer'ın kendi etiketi var (prototipteki gibi), ilk grupta; C — thank-you sonrası follow-up tıklamadan 5 gün sonra; D — arşiv elle konmuş next step'i silmez, sadece durum değişikliği siler.

### Yeni kararlar (Adım 0'da `docs/DECISIONS.md`'ye; numaralar D-071'den başlar)
- **D-071 · Tarih ve saatler `DEFAULT_TIMEZONE`'da:** Veritabanında her zaman UTC. Kullanıcıya gösterilen ve kullanıcının girdiği tarih/saatler `settings.default_timezone` (Europe/Vienna) ile çevrilir (`zoneinfo`). "Bugün" (Needs attention, `applied_at`) bu saat dilimindeki tarihtir; D-061'deki "UTC tarihi" bununla değişir. `User.timezone` M10/M11'de gelince tek bir fonksiyon (`app/services/clock.py`: `today()`, `local(dt)`, `to_utc(date, time)`) kullanıcıya bakar. Docker imajında `zoneinfo` verisi yoksa `tzdata` paketi eklenir (Adım 1'de CI docker job'u kontrol eder; gerekirse D-071'e yazılır). Yaz/kış saati: girilen saat iki kez yaşanıyorsa (Ekim) ilki (`fold=0`), hiç yaşanmıyorsa (Mart, 02:30) UTC'ye çevrilip geri çevrilerek bir saat ileri kayar; ikisi de test edilir. D-061'e "D-071 ile güncellendi" notu düşülür.
- **D-072 · M4b kolonları (D-025 kalıbı):** `Application`'a: `next_action_type` (apply_by/follow_up/interview/thank_you/reply_offer/custom, nullable), `next_action_at` (datetime, nullable), `next_action_note` (≤200, nullable), `next_action_manual` (bool, `server_default false`), `interview_at` (datetime, nullable), `interview_type` (video/phone/onsite, nullable), `interview_with` (≤200, nullable), `posting_closes_at` (date, nullable; SPEC'te M5'te AI'dan dolar, M4b'de Details formundan; D-061'deki "M5" notu güncellenir), `contact_name` (≤200), `contact_email` (≤320), `salary_text` (≤200), `notes` (text, ≤5.000), `snoozed_until` (datetime, nullable; SPEC'te yok, D-074). `ApplicationEvent.message` 200'den `Text`'e (≤2.000) çıkar: notlar timeline'da tam görünür. Hepsi tek migration `0009`; downgrade `message`'ı 200 karaktere keser (Postgres uzun satırı kısa kolona sığdıramaz), bu bilerek kabul edilir.
- **D-073 · Next step hesaplanır; sadece elle konan saklanır:** `app/services/next_step_service.py` saf fonksiyonlarla (veritabanına dokunmaz) `next_step(application, now) -> NextStep | None` döner (tür, tarih, kısa metin, gerekçe, otomatik mi). Kurallar (SPEC §6.13):
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
- **D-074 · Snooze `snoozed_until` kolonunda, event yazmaz:** "Snooze" `snoozed_until = now + 3 gün` yazar; o tarihe kadar başvuru Needs attention'da görünmez. Durum veya next step değişince temizlenir. Timeline'a event yazmaz (gürültü). Toast: "{Company} snoozed for 3 days." + Undo.
- **D-079 · Undo token v2 (D-064'ü genişletir, Adım 3'te):** Token `{u, a, e, n, f}`: `e` işlemin yazdığı event'ler (boş olabilir), `n` işlemden hemen sonraki en yeni event id'si, `f` işlemin değiştirdiği alanlar için `{alan: [önce, sonra]}` (izinli liste; Snooze sadece `snoozed_until` taşır). Undo şartları: imza, süre, kullanıcı, sahiplik; en yeni event hâlâ `n`; **alanlar hâlâ "sonra" değerinde** (karşılaştır-ve-yaz). Sonra `e` silinir (sayı tutmalı) ve sadece `f`'teki alanlar "önce"ye döner. Böylece event yazmayan bir işlemin token'ı tekrar oynatılamaz (ikinci seferde alanlar artık "sonra"da değil) ve araya giren bir değişiklik ezilmez. M4a token'ları (60 sn ömür) `n`/`f` taşımaz: `n` yoksa `max(e)`, `f` yoksa eski `before` yolu. `TOKEN_MAX_LENGTH` 4.000'e çıkar (en kötü durum: iki 200 karakterlik Türkçe metin, testle).
- **D-075 · Details formu:** `GET/POST /applications/{id}/details` (htmx, profil formlarındaki kalıp), alanlar yukarıdaki gibi, `job_url` M4a'daki gibi sadece http(s). Kaydedince event yazmaz, toast "Details saved." (Undo yok; form zaten iptal edilebilir).
- **D-076 · Durum seçici ve mülakat modalı:** Detay sayfasında `<select>` (Saved/Applied/Interview/Offer/Rejected/Archived), klavyeyle kullanılır (D-069'u kapatır). Seçim değişince `POST /applications/{id}/status`: board durumu ise M4a'daki `move` (yeni sütunun en üstü), "Archived" ise `archive`, arşivliden bir duruma geçiş `restore` + `move`. Toast + Undo. **Interview'a geçiş** (board'da sürükleme veya seçici) önce durumu değiştirir, sonra yanıttaki `HX-Trigger: {"open-interview": id}` mülakat modalını açar (tarih, saat, tür, kiminle). "Save interview" → `POST /applications/{id}/interview` (event `interview_scheduled`, toast + Undo), "Skip, I will add it later" sadece kapatır: durum zaten değişmiştir (SPEC kabul kriteri). Detay sayfasında next step kartındaki "Add interview details" aynı modalı açar. Detay sayfasındaki değişiklikler ve Undo `HX-Trigger`'a `application-changed` da ekler; detay sayfasının başlığı, next step kartı ve timeline'ı bu olayla kendini sunucudan yeniler (board'daki `applications-changed` kalıbı).
- **D-077 · Needs attention:** Ana sayfada board ve listenin üstünde, sadece maddesi varsa. Madde = next step tarihi bugün veya geçmişte olan, arşivli olmayan, snooze'lanmamış başvuru. Etiket ve sıra (SPEC §6.3'ün yorumu): önce tarihli işler — **Overdue / Due today** (elle ya da kapanış tarihinden gelen apply_by, custom) ve **Offer** (reply_offer, kendi etiketi) → **Interview** (interview, thank_you) → **Follow up** → **Waiting** (sadece otomatik "eklemeden 7 gün sonra" apply_by: "saved 9 days ago, apply or archive?"); grup içinde eski tarih önce. Needs attention kutusu board'un yenilenen bölgesinin içindedir, değişikliklerden sonra kendini yeniler. İlk 3 + "Show all (n)" (aynı sayfada açılır, sunucu tarafında). Her maddede "Open" (detay sayfası; "Draft a follow-up" gibi AI eylemleri M5+) ve "Snooze". Python'da hesaplanır (en fazla 2.000 başvuru, D-061).
- **D-078 · Detay sayfasında "‹ 1 of n ›":** Aynı durum sütunundaki kartlar arasında (`column_of(user, …)`, `user_id` filtreli), board sırasıyla (arşivli başvuruda arşivliler arasında, en yeni önce). "1 of 10 in Saved".

## 1. Veri modeli
- `Application` + D-072 kolonları, `ApplicationEvent.message` → `Text`. `EVENT_TYPES` += `note_added`, `interview_scheduled`, `next_step_changed`. `NEXT_ACTION_TYPES`, `INTERVIEW_TYPES`.
- Migration `0009_add_application_details.py` (`--autogenerate`, elle gözden geçirilir; NOT NULL `next_action_manual` için `server_default=sa.false()`; Postgres'te `message` tipi değişimi `batch_alter_table` ile; upgrade → downgrade → upgrade + `alembic check`).

## 2. Modüller

| Dosya | İçerik |
|---|---|
| `app/services/clock.py` | `now()`, `today()`, `local(dt)`, `to_utc(date, time)`, `TZ` (D-071); `application_service.today()` buraya taşınır |
| `app/services/next_step_service.py` | `NextStep` (dataclass: type, at, text, why, all_day), `next_step(app, now)`, `TYPE_LABELS`, `choices_for(status)` |
| `app/services/application_service.py` | `set_status` (D-076), `set_interview`, `change_next_step`, `done_next_step`, `add_note`, `update_details`, `snooze`, `needs_attention(user, now)`, `neighbours(user, app)`; `move`/`archive`/`restore` durum değişince next step ve snooze'u temizler; `undo` D-074'e göre genişler |
| `app/services/undo_service.py` | Token v2 (D-079) |
| `app/schemas/application.py` | `InterviewForm`, `NextStepForm`, `NoteForm` (≤2.000), `DetailsForm` (D-075). Tarihler bugün − 1 yıl ile bugün + 2 yıl arasında (9999-12-31 + 10 gün taşmasın); metinler kırpılır, not ve Custom notu boş olamaz |
| `app/routers/applications.py` | Aşağıdaki route'lar |
| `app/templates/applications/` | `detail.html`, `_detail_header.html`, `_details.html`, `_details_form.html`, `_next_step.html`, `_timeline.html`, `_interview_form.html`, `_attention.html`; kart ve liste satırı detay sayfasına link olur |
| `app/static/js/app.js` | `open-interview` olayı, durum seçicinin değişimi (htmx `change`); detay sayfası parçaları `application-changed` ile yenilenir |

### Route'lar (hepsi `require_user`; POST CSRF'li; başkasının / geçersiz id → 404)
| Route | Ne yapar |
|---|---|
| `GET /applications/{id}` | Detay sayfası (arşivli de açılır) |
| `POST /applications/{id}/status` | D-076; toast + Undo; Interview ise `open-interview` |
| `POST /applications/{id}/interview` | Tarih (zorunlu), saat (boşsa tüm gün), tür, kiminle; event + toast + Undo |
| `POST /applications/{id}/next-step`, `/next-step/done` | D-073; toast + Undo |
| `POST /applications/{id}/notes` | Not ekle (event `note_added`); timeline yenilenir |
| `GET/POST /applications/{id}/details` | D-075 |
| `POST /applications/{id}/snooze` | D-074; toast + Undo |
Hız sınırları M4a'daki gibi (yazma route'ları dakikada 60, not 30).

## 3. Ekran (prototip `ApplicationDetail.dc.html`; SPEC kazanır)
- **Başlık:** "← Applications" (board'a, filtresiz), "‹ 1 of 10 in Saved ›", şirket avatarı (baş harfler, renk şirket adından), pozisyon, "Kranich Health · Vienna · Hybrid · saved from LinkedIn today", sağda durum seçici ve "View posting ↗" (`job_url` varsa, `rel="noopener noreferrer" target="_blank"`).
- **Sekmeler:** M4b'de sekme yok: "Apply" / "Progress" M7–M9'da içerikle gelir (D-060: çalışmayan şey gösterilmez); sayfa doğrudan "Job & details" içeriğini gösterir.
- **Job & details:** Details kartı (iletişim, maaş metni, çalışma şekli, kaynak, ilanın kapanışı, notlar; boş alanlar "—"; "Edit") + "Original posting (saved copy)" (daraltılmış, `description_raw` boşsa gizli; düz metin, satır sonları korunur).
- **Sağ panel:** Next step kartı (metin, gerekçe, "Change" → form: tür + tarih + not; "Done"; "Set automatically from the status." otomatikse; "You get a reminder on the day." M11'de eklenir), Timeline (en yeni üstte; "Saved from LinkedIn", "Status: Saved → Applied", "Interview set for Wed 18 Nov, 10:00", "Next step: Follow up on Fri, 20 Nov", "Note: …", "Archived", "Restored"; altında not kutusu + "Add").
- **Board ve liste:** kart ve satırdaki şirket adı detay sayfasına link (kartın tamamı sürüklenebilir kalır).
- **Needs attention** (D-077): sarı kutu, "Needs attention · n", "Most urgent first. Snooze hides an item for 3 days.", maddeler, "Show all (n)".
- Mülakat modalı: "Interview with {Company}", "When is it? We remind you the day before and help you prepare." (hatırlatma M11'de gelir; metin "We use it for your next step." olur, M11'de prototip metnine döner), Date, Time, Type, With (optional), "Skip, I will add it later", "Save interview".

## 4. Testler (freezegun; giriş yapmış istemciyle saat dondurma sorunu için tarihler veritabanına yazılır veya servis seviyesinde test edilir — M4a dersi)
- `test_next_step_service.py`: her kural, sınırlar (kapanıştan 1 gün önce, kapanış yoksa +7, +10, mülakat saati geçmeden/geçince, thank-you Done → +5, Offer tarihli/tarihsiz, Rejected, arşivli), manual override ve durum değişince temizlenmesi, Done (thank-you → +5, follow-up Done → yok, mülakattan önce Interview Done → yok), yeniden planlama temizler, arşiv/restore elle konanı korur; Viyana gece yarısı sınırı (UTC 23:30 = Viyana ertesi gün), Mart ve Ekim saat geçişleri, tüm gün mülakat.
- `test_needs_attention.py`: sıralama ve etiketler (kabul kriteri; elle konan apply_by "Waiting" değil, Offer kendi etiketiyle), bugün/geçmiş/gelecek, snooze gizler ve 3 gün sonra geri gelir, Undo ile döner, ilk 3 + Show all, arşivli ve başka kullanıcının başvurusu yok.
- `test_application_detail.py`: sayfa (başlık, ‹ › komşular, sekmeler, Details, Original posting, link `rel`), kaçışlama, arşivli sayfa, başka kullanıcı 404 (her route), geçersiz id.
- `test_application_status.py`: seçici her durum (klavye yolu = form gönderimi), Archived, arşivliden geri, Interview → `open-interview`, Undo, next step temizlenir.
- `test_interview.py`, `test_next_step_routes.py`, `test_notes.py`, `test_details_form.py`: doğrulama (tarih aralığı, boş not), event, toast + Undo, CSRF, girişsiz. Board'da `PATCH /move` ile Interview'a taşıma da `open-interview` döner.
- `test_undo_v2.py`: Snooze Undo bir kez çalışır (tekrar oynatma yok), araya giren değişiklik ezilmez, sadece kendi alanlarını geri yazar, M4a biçimindeki token hâlâ çalışır, en uzun Türkçe metinlerle token sınırın altında.
- Timeline kabul kriteri: durum, not, mülakat ve next step değişikliklerinin dördü aynı sayfada görünür.
- `test_migrations.py` başı `0009`. Build sonunda `test-writer`.

## 5. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)
| # | Adım | Commit |
|---|---|---|
| 0 | D-071…D-079 `DECISIONS.md`'ye (D-061'e not); SPEC v2.1.12: §5.5 (`snoozed_until`, `posting_closes_at` M4b'de elle, `next_action_*` = elle konan), §6.13 Done kuralı, §6.3 Needs attention etiketleri, §12 › M4b (Details formu, Apply sekmesi M7'de); Fatih claude.ai kopyasını yeniler | `docs: add m4b plan and decisions` |
| 1 | `clock.py` + kolonlar + migration `0009` + `next_step_service` (saf) + testler; CI docker'da `zoneinfo` kontrolü | `feat: add next step rules` |
| 2 | Detay sayfası (salt okunur): başlık, ‹ ›, sekmeler, Details, Original posting, Next step kartı, Timeline; kart ve satırdan link | `feat: show an application's detail page` |
| 3 | Undo token v2 (D-079) + durum seçici + Archived + Undo (D-069 kapanır) | `feat: change the status from the detail page` |
| 4 | Mülakat modalı (board + detay) + Undo | `feat: add interview details` |
| 5 | Next step Change / Done + Undo | `feat: change and finish the next step` |
| 6 | Timeline'a not ekleme | `feat: add notes to the timeline` |
| 7 | Details formu (D-075) | `feat: edit an application's details` |
| 8 | Needs attention + Snooze + Undo (D-074, D-077) | `feat: show what needs attention` |
| 9 | `test-writer`, `security-reviewer`, tarayıcı kontrolü (test hesabıyla), Fatih'in elle testi, README, LEARNING_LOG, CLAUDE.md, PR (`Closes #33`), merge sonrası etiket `v0.4b-m4b` | `test: …`, `docs: …` |

## 6. Riskler
1. **Saat dilimleri:** Veritabanı UTC, ekran Viyana; yaz/kış saati geçişleri. Tek modül (`clock.py`) ve sınır testleri. `zoneinfo` verisi Docker slim imajında yoksa `tzdata` paketi (D-071).
2. **Hesaplanan next step ile listeler:** Needs attention her istekte en fazla 2.000 başvuru için Python'da hesaplanır; `description_raw` ve `notes` yüklenmez. Kabul (D-067 ile aynı).
3. **Undo'nun genişlemesi:** Bir işlem birden çok alanı değiştirir (durum + mülakat + next step temizliği). D-079: her token sadece kendi alanlarını ve "sonra" değerlerini taşır; Undo karşılaştır-ve-yaz yapar. Testler: Snooze → Undo bir kez, durum → mülakat → ilk işlemin Undo'su reddedilir.
4. **Kapsam:** 9 adım; 1–2 haftalık sınırın üst ucu. Details formu (Adım 7) en kolay kesilebilecek parça: süre aşılırsa M5'e taşınır (SPEC §12 ve #33 aynı değişiklikte).
5. **Sürükleme ve link:** Karttaki şirket adı link olunca tıklama ve sürükleme karışabilir. Sortable küçük hareketi tıklama sayar; tarayıcıda kontrol edilir.
6. **Erişilebilirlik:** Durum seçici `<select>`, modal native `<dialog>`, Needs attention butonları gerçek `<button>`.

## 7. Doğrulama
- `uv run pytest`, ruff, CI (Postgres'te migration, Docker'da `zoneinfo`).
- Tarayıcı (test hesabı): karta tıkla → detay → seçiciyle Applied (klavye) → Undo → Interview → modal → kaydet → next step "Interview · Wed 18 Nov, 10:00" → Change → Done → not ekle → Details düzenle → ana sayfada Needs attention → Snooze → Undo.
- Kabul kriterleri issue #33'te tek tek işaretlenir.

## 8. Kapsam dışı
Apply / Progress sekmesinin içeriği (M7–M9), yapılandırılmış ilan (M5), belge kartı ve kilitleme (M4c), bildirimler ve hatırlatma e-postaları (M11), "Draft a follow-up" gibi AI eylemleri (M5+), kullanıcı saat dilimi (M10/M11).
