# M8b — Motivasyon mektubu: PDF ve başvuru · Teknik plan (Design fazı)

## Context
M8a mektupları getirdi (`v0.8a-m8a`): model, master/kopya/kilit kuralları (`lineage.py`), editör ve önizleme. M8b (issue #81, SPEC v2.1.33 §12 › M8b, §5.4, §5.4b, §5.5, §6.6, §6.7) mektubu CV'nin M4c'de kazandığı her şeyle buluşturur: başvuruya bağlama, gönderince kilit + `ApplicationDocument`, DACH PDF'i, editörde "← {Başvuru}", "Duplicate" ve "Copy text", Documents sayfasında başvuruya göre gruplama. AI taslağı V2 (D-150); Apply sekmesi ve belge paneli (drawer) M9a.

Kural: **CV'de nasılsa mektupta da öyle** (D-080…D-086). Yeni bir kural sadece mektuba özgü yerlerde (tarih, ön doldurma, dosya adı, "Copy text").

Branch: `m8b-cover-letter-pdf` (`main`, `67fb05a`). Etiket `v0.8b-m8b`. Onaydan sonra plan issue #81'e yorum olarak eklenir. Her commit'ten önce Fatih'e sorulur.

## Kararlar (Adım 0'da `docs/DECISIONS.md`'ye; D-157'den başlar)
- **D-157 · Mektubu başvuruya bağlama (D-080'in aynısı).** `CoverLetter.application_id` (FK → application, `SET NULL`, index) = **soy**: bu başvuru için yapılmış kopyalar. `Application.cover_letter_id` (FK → coverletter, `SET NULL`, **unique**, `use_alter`) = **şu anki** mektup. Master hiç bağlanmaz. Detay sayfasındaki Documents kartına CV bloğunun altına bir **Cover letter** bloğu:
  - Bağlı mektup yoksa: dil başına "Copy my master letter (EN)" (yeni kopya, başlık "{Company} · Cover letter", şirket 200 karaktere sığacak şekilde kesilir) ve "Use an existing copy" (başka başvuruya bağlı olmayan taslak kopyalar).
  - Varsa: başlık · vN, rozet (Draft / Sent · locked), "Open" (`?application={id}`), "PDF", taslaksa "Remove", gönderilmiş başvuruda "Lock as sent".
  - Hepsi toast + Undo. Undo token'ına CV'nin yanında `created_letter`, `linked_letter` anahtarları; `creation_fingerprint` ve `NOTE_FIELDS`'a `cover_letter_id`.
- **D-158 · Başvuru için kopyada ön doldurma (Fatih onayladı).** Master'dan başvuru için kopya oluşunca **boş olan** alanlar ilandan dolar: `recipient_company` ← `application.company`, `recipient_name` ← `contact_name`, `subject` ← dile göre kalıp (`document_labels`: DE "Bewerbung als {job_title}", EN "Application for {job_title}", TR "{job_title} pozisyonu için başvuru"). Dolu alanlara dokunulmaz. Documents sayfasından "New" ile açılan kopyada ön doldurma yok (başvuru yok).
- **D-159 · Gönderince kilit (D-082'nin aynısı, iki belge).** `lock_sent_cv` → `lock_sent_documents`: başvuru Applied, Interview veya Offer'a her geçişte bağlı CV **ve** bağlı mektup taslaksa kilitlenir; durum, kilitler, `ApplicationDocument`'lar ve event'ler **tek transaction**. "Lock as sent" her belge için ayrı (`POST /applications/{id}/cover-letter/send`). Undo: token'da `locked_letters` (`[id, locked_at]` çiftleri); kilit açma sadece `cover_letter_service.unlock_after_undo`'da ve sadece bu Undo'dan (CV'deki tek istisnanın aynısı: içerik değişmez).
  - **`ApplicationDocument.cover_letter_id`** (yeni kolon, FK → coverletter, `SET NULL`, index). `document_id` CV'ye bağlı kalır (yeniden adlandırmak M4c kodunu ve testlerini açar). Her satırda `doc_type`'a göre tam biri dolu.
  - `content_snapshot` = mektubun bütün alanları (sözlük) + `"date"`: kilit anındaki tarih satırı metni.
  - Timeline: "{title} locked: this is what you sent". Kartta gönderilenler "CV sent on {tarih}" / "Cover letter sent on {tarih}".
- **D-160 · Tarih kilitte sabitlenir (D-154'ü tamamlar).** Taslakta tarih satırı bugün (kullanıcının saat diliminde). Kilitli mektupta `locked_at` tarihi, kullanıcının saat diliminde. Yeni kolon yok. Kabul edilen risk: kullanıcı sonradan saat dilimini değiştirirse tarih bir gün kayabilir; gönderilen metnin kesin kopyası `ApplicationDocument.content_snapshot["date"]`'te durur.
- **D-161 · PDF.** Önizlemedeki `pdf/cover_letter.html` aynen kullanılır; WeasyPrint için tam sayfa sarmalayıcı `pdf/_letter_page.html` (CV'deki `_pdf_page.html` gibi, sadece `static/` okunur, D-059). `GET /cover-letters/{id}/pdf` (giriş, `user_id`, 20/dakika, kilitlide de çalışır, hata sayfası CV'deki `pdf_error.html` kalıbı). Dosya adı `CoverLetter_{Ad}_{Şirket?}_{lang}.pdf`: ad `sender_name`'den (gönderilen mektup gönderildiği adla kalır, D-057), şirket bağlı başvurunun şirketi, **yoksa `recipient_company`** (Fatih onayladı); aynı ASCII + `filename*` kuralları. `pdf_service.pdf_filename` öneki ve kişi adını parametre alacak şekilde genelleşir (CV davranışı aynı kalır).
- **D-162 · Editör ve başvuru (D-084 ve D-050'nin aynısı).**
  - Başvuru `letter.application_id`'den; `?application=` sadece onunla aynıysa kabul edilir: "← {Company}" geri linki, "All documents" linki, bilgi bandı "Copy for {Company}, based on {Master}. Your master letter stays unchanged."
  - Versiyon seçici: aynı başvurunun versiyonları + master.
  - Bağlı kopyadan "Save as new version" `application_id`'yi aktarır; eski versiyon başvurunun şu anki mektubuysa yenisi onun yerine geçer (CV `cvs.py:336`).
  - **Duplicate** (`POST /cover-letters/{id}/duplicate`): başvuruya bağlı olmayan yeni taslak "… (copy)".
  - **Copy text** (Fatih onayladı): selamlama + gövde + kapanış + gönderen adı, düz metin olarak panoya. `app.js`'e küçük bir Alpine bileşeni (`navigator.clipboard.writeText`, inline script yok, CSP aynı); kopyalanınca "Copied." toast'ı. Metin sayfadaki gizli bir `<textarea>`'dan okunur (sunucu oluşturur, kaçışlı).
- **D-163 · Documents › Cover letters gruplama ve silme (D-084, D-086).** Kopyalar başvuruya göre gruplanır ("{Company} · {Role}"), bağlı olmayanlar "Not linked to an application". Bağlı bir taslak mektup silinince başvurunun `cover_letter_id`'si boşalır; Undo başvuru hâlâ sahibininse ve `cover_letter_id` hâlâ boşsa yeniden bağlar (`restore_cv` kalıbı).

### plan-reviewer (2026-11-21) ve güncellemeler
Sonuç "Needs changes": 1 Blocker, 7 Should, 6 Nit. Hepsi aşağıdaki maddelerle kapandı; bunlar yukarıdaki kararların yerine geçer.
- **D-158 konu uzunluğu (Blocker):** `job_title` 200 karakter olabilir, konu kalıbıyla 214'e çıkar (Postgres'te 500). Konu 200 karaktere sığacak şekilde `job_title` kesilir (`copy_title` kalıbı). Test: 200 karakterlik pozisyon adı (CI Postgres).
- **D-158 ve Undo:** Ön doldurma kopya oluşturulurken constructor'da yapılır (`stamped`/`save_copy`'den önce), ikinci bir yazma yok; böylece kopya `never_edited` kalır ve "Copy my master letter"ın Undo'su onu siler (test).
- **D-162 versiyon bağı (E1):** Yeni versiyon başvurunun şu anki mektubunun yerine **sadece başvuru gönderilmemişse** geçer (`application.status not in SENT_STATUSES`, `cvs.py:332-338`). Test: gönderilmiş başvuruda yer değiştirmez.
- **D-163 geri yükleme:** `restore_letter`, `restore_cv`'nin üç koşulunu aynen alır (`document_service.py:411-431`): başvuru hâlâ sahibinin ve `cover_letter_id` boşsa **ve** `status == "saved"` ise yeniden bağlar; başvuru artık yoksa `application_id` temizlenir. Her biri için test.
- **Undo alanları (adım 2):** `NOTE_FIELDS`'a `cover_letter_id`, `_from_text`'e `cover_letter_id` durumu (`application_service.py:375`), `undo_change`'e mektup sahiplik kontrolü (`:492-496`).
- **Undo token (adım 3):** `undo_events` (`applications.py:218`) `made_since`'ten `locked_letters`'ı da token'a koyar. `made_since` kayıtları `doc_type`'a göre ayırır.
- **Kilit yolları (adım 3):** Üç yol test edilir: board sürükleme (`PATCH …/move`), durum seçici (`POST …/status`), mülakat modalı (Saved → Interview, `set_interview`); ayrıca arşivden geri alma. Her birinde CV + mektup kilitlenir ve Undo ikisini de taslağa döndürür.
- **PDF linki:** Documents kartındaki ve editördeki "PDF" linki adım 4'te gelir (route ile birlikte).
- **Endpoint ayrıntıları:** `DELETE …/cover-letter` arşivli başvuruda 409; DELETE ve `/send` 60/dk; `letter=` ile master gelirse 422 (CV route'u gibi, `applications.py:891`).
- **D-159:** "Her satırda tam biri dolu" bir kod kuralıdır, veritabanı CHECK'i değil (`SET NULL` ikisini de boşaltabilir). DECISIONS'ta D-082 ve D-083'e "Güncelleme: D-159" notu.
- **D-161:** `pdf_filename` ve `content_disposition` ikisi de genelleşir; ad boşken dosya adı testi (`CoverLetter_{Şirket}_{lang}.pdf`).
- **D-160:** Kilitli mektupta önizleme ve PDF tarihi `ApplicationDocument.content_snapshot["date"]`'ten okunur (varsa); yoksa `locked_at`. Saat dilimi kayması böylece görünmez.
- **Testler:** Erişim matrisi (girişsiz, CSRF'siz 403, garip id, başkasının) yeni route'lar için; hesap silme: bağlı ve kilitli mektuplu kullanıcı (iki yönlü FK + gönderilen kayıt), Postgres'te de.
- **Adım 5 bölündü:** 5a editör ve başvuru (geri link, bant, Duplicate, Copy text, versiyon bağı), 5b Documents sayfası (gruplama, silme/Undo bağı).
- **Kişisel instance:** `0019` kişisel ortama gelmeden önce `scripts/backup_personal.sh` ile yedek (D-155); PR'daki notta yazar.

---

## 1. Paketler
Yok (WeasyPrint ve pypdf zaten var).

## 2. Veri modeli — migration `0019`
| Tablo | Kolon | Not |
|---|---|---|
| `coverletter` | `application_id` | FK → application.id, `SET NULL`, index, nullable |
| `application` | `cover_letter_id` | FK → coverletter.id, `SET NULL`, unique, nullable, açık isimli constraint `fk_application_cover_letter_id_coverletter`, `use_alter=True` |
| `applicationdocument` | `cover_letter_id` | FK → coverletter.id, `SET NULL`, index, nullable |

- Döngüsel FK (application ↔ coverletter): M4c'deki `0010` kalıbı; downgrade önce constraint'leri siler. Up → down → up (SQLite testte, Postgres CI'da) + `alembic check`; `test_migrations.py` → `0019`.
- Dışa aktarma: yeni kolonlar mevcut bölümlerle gelir (test eklenir). Hesap silme: cascade değişmez.

## 3. Modüller
| Dosya | Değişiklik |
|---|---|
| `app/models/cover_letter.py`, `app/models/application.py` | Yeni kolonlar |
| `app/services/cover_letter_service.py` | `copy_for_application` (D-158 ön doldurma), `copies_for_linking`, `duplicate`, `unlock_after_undo`, `delete_letter`/`restore_letter` başvuru bağıyla, `letter_date(letter)` (D-160), `plain_text(letter)` (Copy text), `snapshot(letter)` |
| `app/services/application_service.py` | `link_letter`, `unlink_letter`, `send_letter`; `lock_sent_cv` → `lock_sent_documents`; `made_since` ve `_undo_documents` mektup anahtarlarıyla; `NOTE_FIELDS`, `creation_fingerprint` |
| `app/services/pdf_service.py` | `render_letter_pdf`, genel `pdf_filename` |
| `app/i18n/document_labels.py` | Konu kalıbı (D-158) |
| `app/routers/applications.py` | `POST /applications/{id}/cover-letter` (master'dan kopya veya mevcut kopya), `DELETE /applications/{id}/cover-letter`, `POST /applications/{id}/cover-letter/send`; `documents_context` mektupla |
| `app/routers/cover_letters.py` | `GET /cover-letters/{id}/pdf`, `POST /cover-letters/{id}/duplicate`, editör başvuru bağlamı, versiyonda bağın taşınması |
| `app/routers/cvs.py` | Cover letters sekmesinde gruplama |
| Şablonlar | `applications/_documents.html` (Cover letter bloğu), `cover_letters/editor.html` (geri link, bant, Duplicate, PDF, Copy text), `pdf/_letter_page.html`, `cvs/documents.html` (gruplama) |
| `app/static/js/app.js` | `copyText` Alpine bileşeni |

## 4. Endpoint'ler (hepsi giriş, CSRF, `user_id`; başkasının başvurusu/mektubu 404)
| Metot | Yol | Not |
|---|---|---|
| POST | `/applications/{id}/cover-letter` | `master=` → kopya + bağla; `letter=` → mevcut kopyayı bağla. Arşivli başvuru 409, master/kilitli/başka başvurunun kopyası 409. 60/dk |
| DELETE | `/applications/{id}/cover-letter` | Taslaksa bağı kaldırır; kilitli 409 |
| POST | `/applications/{id}/cover-letter/send` | "Lock as sent": sadece gönderilmiş, arşivsiz başvuruda |
| GET | `/cover-letters/{id}/pdf` | 20/dk; kilitlide de |
| POST | `/cover-letters/{id}/duplicate` | Yeni taslak, bağsız; 100 sınırı |

## 5. Testler
- **Bağlama** (`tests/test_application_letters.py`): master'dan kopya ön doldurmayla (dolu alana dokunmaz), mevcut kopya, Remove, Undo (dokunulmamış kopya silinir, düzenlenmişse sadece bağ kalkar, eski başvurusuna geri bağlanır), arşivli başvuru 409, master/kilitli/başkasının 404/409, aynı mektup iki başvuruya bağlanamaz (unique).
- **Kilit (kabul)** (`tests/test_send_lock_letters.py`): Saved → Applied CV'yi ve mektubu kilitler, iki `ApplicationDocument` (snapshot'ta `"date"`), Undo ikisini de geri alır; Rejected kilitlemez; zaten kilitli ikinci kayıt açmaz; sadece mektup bağlıyken de çalışır; "Lock as sent"; kilitli mektup kaydedilemez/silinemez (kabul).
- **Tarih:** taslakta bugün, kilitlide `locked_at` (saat dilimi: 23:30 UTC Viyana'da ertesi gün; freezegun).
- **PDF (kabul)** (`tests/test_letter_pdf.py`): DE, EN, TR'de pypdf ad soyadı ve tarihi okuyor; dosya adı (başvurulu, `recipient_company`'li, şirketsiz, Türkçe harfler, tırnak/; gibi karakterler); başkasının 404; girişsiz; 20/dk; WeasyPrint hatası 500 değil hata sayfası.
- **Editör:** geri link ve bant (`?application=` başkasının ya da başka başvurunun id'si yok sayılır), Duplicate bağsız, Save as new version bağı taşır, Copy text metni (kaçışlı), versiyon seçici.
- **Documents sayfası:** gruplama; bağlı mektubu silip Undo yeniden bağlar.
- **Göç:** `0019` up/down/up. **Dışa aktarma:** `cover_letter_id` alanları.
- **CV regresyonu:** mevcut M4c testleri değişmeden geçer.
- Sonunda test-writer ve security-reviewer.

## 6. Adımlar (her biri ayrı commit, önce Fatih'e sorulur)
0. `docs: plan M8b` + `docs: SPEC and D-157…D-163 for M8b` (SPEC v2.1.34: başlık; §5.4 `application_id` M8b ✓ ve tarih kuralı; §5.4b `cover_letter_id`; §5.5 `cover_letter_id` notu; §6.6 Duplicate/Copy text/PDF; §6.7 Documents kartında mektup; §12 › M8b).
1. `feat: cover letter links in the database` (modeller, migration 0019, migration ve export testleri).
2. `feat: cover letters on the application page` (Documents kartı: kopya + ön doldurma, kullan, kaldır, Undo; PDF linki yok).
3. `feat: lock the cover letter that was sent` (`lock_sent_documents`, Lock as sent, snapshot, sabit tarih, Undo).
4. `feat: cover letter PDF` (sarmalayıcı, route, dosya adı, pypdf testleri, Documents kartında ve editörde PDF linki).
5a. `feat: cover letter editor with its application` (geri link, bant, Duplicate, Copy text, versiyon bağı).
5b. `feat: cover letters grouped by application` (Documents gruplama, silme/Undo bağı).
6. Review ve kapanış: test-writer, security-reviewer, bulgular; `uv run pytest` (normal ve `TZ=UTC`), ruff; yeşil CI; Chrome'da elle test (8000); PR'da elle test listesi; `LEARNING_LOG`; CLAUDE.md'de M8b ✓; PR "Closes #81"; Fatih SPEC kopyasını v2.1.34 ile yeniler; merge sonrası etiket `v0.8b-m8b`.

## Riskler
- **Üçlü döngüsel FK** (application ↔ coverletter, application ↔ cvdocument): SQLite batch migration sırası; `0010` kalıbı ve açık constraint isimleri.
- **Undo token boyutu:** CV ve mektup aynı anda kilitlenince token büyür; `TOKEN_MAX_LENGTH` aşılırsa Undo'suz toast (mevcut kural). Test: iki belge birlikte kilitlenince token sınırın altında.
- **WeasyPrint önizlemeden farklı çizebilir:** CSS `pdf/_letter_page.html` içinde düzeltilir (M3b'deki gibi); `white-space: pre-line` WeasyPrint'te desteklenir.
- **Kod kopyası:** CV ve mektup için bağlama/kilit kodu iki kez yazılmasın diye ortak parçalar (`made_since`, `_undo_documents`) belge türüne göre döngüye çevrilir; davranış CV için aynı kalır (M4c testleri).

## Fatih'in cevapları (2026-11-21)
1. **PDF adındaki şirket:** bağlı başvurunun şirketi, başvuru yoksa `recipient_company` (öneri kabul).
2. **Başvuru için kopyada ön doldurma:** sadece boş alanlar; şirket, iletişim kişisi, dile göre konu kalıbı (öneri kabul).
3. **"Copy text":** selamlama + gövde + kapanış + ad (öneri kabul).
