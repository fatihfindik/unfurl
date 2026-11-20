# M8a — Motivasyon mektubu: model ve editör (AI'sız) · Teknik plan (Design fazı)

## Context
D-150 ile MVP AI'sız (PR #83). M8a (issue #80, SPEC v2.1.30 §12 › M8a, §5.4, §6.5, §6.6) mektupları CV'lerle aynı kurallarla getirir: dil başına bir master, ondan kopyalar ve versiyonlar, kilitli belge hiç değişmez, master sadece açık onayla düzenlenir (D-048). Editör: solda form, sağda canlı önizleme (beyaz kâğıt). PDF, başvuruya bağlama, "applied"da kilit, editörde "Duplicate", "All documents" ve "← {Başvuru}" M8b'de (#81; SPEC §12 M8b, D-050: başvuru yokken Duplicate = Save as new version). AI ile taslak V2.

Branch: `m8a-cover-letters` (PR #83'ün üstünde; #83 merge olunca main'e taşınır). Etiket `v0.8a-m8a`. Onaydan sonra plan issue #80'e yorum olarak eklenir. Her commit'ten önce Fatih'e sorulur.

## Kararlar (Adım 0'da `docs/DECISIONS.md`'ye)
- **D-152 · `CoverLetter` modeli ve alanları.** SPEC §5.4 güncellenir: `tone`, `length`, `ai_generated` V2 (AI ile gelir; boş kolon taşımayız); `application_id` M8b'de (CV'deki D-045 kalıbı). Mektup **düz kolonlar**dır, JSON değil (CV'nin iç içe bölümleri yok; form alanları kolonlarla bire bir). Alanlar §2'de. M8b'de `ApplicationDocument.content_snapshot` için kolonlar bir sözlüğe çevrilir (Riskler).
- **D-153 · Ortak soy (lineage) yardımcıları.** Master/kopya/versiyon kuralları (D-045, D-047, D-048, D-050) CV ve mektupta aynı. Modelden bağımsız parçalar yeni `app/services/lineage.py`'ye taşınır ve model sınıfını parametre alır:
  - `current_master`, `versions_in_lineage`, `copy_count`, `next_version`, `save_copy` (`(parent_id, version)` çakışmasında bir kez yeniden dene), `create_master_or_copy` (aynı anda iki "New" isteğinde ikincisi kopya olur; `create_cv` l.252-265'teki yarış kuralı), `never_edited` / `stamped` (D-050 "Nothing changed since this version…"), `restore_checks` (master'ın dili boş mu, kopyanın master'ı var mı, versiyon numarası boş mu; `restore_cv` l.507-522), `check_room(session, model, user, limit)`.
  - **Limit çağrı anında verilir** (`document_service.MAX_CVS_PER_USER` testlerde monkeypatch ediliyor: `test_document_service.py:96`, `test_documents_pages.py:113`, `test_cv_actions.py:136`), varsayılan argüman olarak değil.
  - **Hata sınıfları** (`DocumentLocked`, `MasterNotUnlocked`, `MasterHasCopies`, `TooManyDocuments`, `NothingChanged`) `lineage.py`'de tanımlanır; `document_service` onları aynı isimle import eder (`document_service.DocumentLocked` router'larda çalışmaya devam eder). `lineage.py` hiçbir servisi import etmez (döngüsel import yok).
  - `document_service.never_edited` dışa açık kalır (`application_service.py:586` kullanıyor).
  - Mektuba özel olanlar `app/services/cover_letter_service.py`'de. Alternatif: CV kodunu kopyalamak (aynı kural iki yerde, bir gün ayrışır) veya tek tablo (SPEC §5.3/§5.4 ayrı tablo; CV içeriği JSON).
- **D-154 · Dile göre varsayılanlar ve tarih.** Kelimeler D-055 gereği `app/i18n/document_labels.py`'de (`LABELS`'a `letter_salutation`, `letter_closing`, `letter_subject_hint`): DE "Sehr geehrte Damen und Herren," / "Mit freundlichen Grüßen"; EN "Dear Hiring Team," / "Kind regards,"; TR "Sayın Yetkili," / "Saygılarımla,". Tarih satırı mevcut `full_date` ile (D-055: DE ve TR `21.11.2026`, EN `21 November 2026`; DIN 5008 sayısal tarihi kabul eder, yeni ay sözlüğü gerekmez). M8a'da tarih her zaman **bugün**, kullanıcının saat diliminde `clock.today()` (D-109); M8b'de kilitlenince sabitlenir. Yeni master: gönderen alanları ve `place` profilden, selamlama/kapanış dile göre, konu ve gövde boş (Fatih: boş, placeholder ipucuyla). Yeni kopya: master'ın bütün alanlarını kopyalar. **"Refresh sender from profile"** sadece gönderen alanlarını yeniler ve CV'deki gibi **onay modalı** ister (elle yapılan değişiklikleri ezer, SPEC §6.5); kilitli belgede yok; master'da D-048 onayı.

---

## 1. Paketler
Yok.

## 2. Veri modeli — `app/models/cover_letter.py` + migration `0018`

| Kolon | Tip | Not |
|---|---|---|
| id | int PK | |
| user_id | FK → user, CASCADE, index | Her sorgu bununla filtrelenir |
| title | str(200) | ör. "Cover letter – Master (DE)" |
| language | str(2) | `CV_LANGUAGES` ile aynı |
| sender_name, sender_phone | str(200), nullable | Profilden (D-154) |
| sender_email | str(320), nullable | |
| sender_address | str(500), nullable | Çok satır: profilden şehir gelir, isteyen sokak adresini ekler (Fatih) |
| recipient_company, recipient_name | str(200), nullable | |
| recipient_address | str(500), nullable | Çok satır |
| place | str(200), nullable | Tarih satırındaki şehir |
| subject | str(200), default "" | |
| salutation, closing | str(200) | Dile göre varsayılan |
| body | `Text`, default "" | Uygulamada en fazla 10.000 karakter (Pydantic) |
| source_profile_version | int | |
| is_master, parent_id (FK → coverletter.id, SET NULL), version, status, locked_at | CV ile aynı | |
| created_at / updated_at | datetime | |

- Kısmi unique index `(user_id, language)` `is_master` iken (`uq_coverletter_master_per_language`), unique `(parent_id, version)`; CV'deki gibi kolon ifadesiyle (SQLite + Postgres; CI `postgres-tests`).
- Sınır: `MAX_LETTERS_PER_USER = 100` ("You can have up to 100 cover letters. Delete one to add another.").
- Migration `0018 add cover letters`; `test_migrations.py` → `0018`; upgrade → downgrade → upgrade + `alembic check`.
- **Aynı adımda** `export_service.SECTIONS`'a `Section("cover_letters", CoverLetter, "user")` (yoksa `test_export.py:63` kırılır).
- `models/trash.py:24` yorumuna `cover_letter` türü.

## 3. Modüller (SPEC §4 adlarıyla)

| Dosya | İçerik |
|---|---|
| `app/services/lineage.py` (yeni) | D-153 |
| `app/services/document_service.py` | `lineage`'ı çağırır; dışa açık isimler aynı (router ve testler değişmez) |
| `app/services/cover_letter_service.py` (yeni) | `get_owned_letter` (yoksa veya başkasınınsa 404), `list_letters`, `create_letter(title, language)`, `save_as_new_version` (kilitliden de; "Nothing changed" kuralı), `update_letter(form, master_unlocked)`, `refresh_sender`, `delete_letter` (kilitli değil, kopyası olan master değil; trash + Undo), `restore_letter`, `lock` (M8b kullanır; M8a'da testler) |
| `app/schemas/cover_letter.py` (yeni) | `CoverLetterForm` (Pydantic): uzunluk sınırları, boşlukları kırpma |
| `app/i18n/document_labels.py` | D-154 kelimeleri |
| `app/routers/cover_letters.py` (yeni) | Aşağıdaki endpoint'ler |
| `app/templates/cover_letters/` | `editor.html`, `_form.html`, `_preview.html`, `_new_letter_form.html` |
| `app/templates/pdf/cover_letter.html` + `app/static/css/cover_letter.css` | Önizleme (M8b'de PDF de) |
| `app/routers/cvs.py` | `/documents?tab=letters` listesi ve "New cover letter" |
| `app/routers/undo.py` | `kind == "cover_letter"` → `restore_letter` |

## 4. Endpoint'ler (hepsi giriş ister, CSRF'li, `user_id` ile filtreli)

| Method | Path | Ne yapar |
|---|---|---|
| POST | `/cover-letters` | New cover letter modalı (başlık, dil; varsayılan `default_doc_language`, D-129) → editöre |
| GET | `/cover-letters/{id}` | Editör |
| POST | `/cover-letters/{id}` | Otomatik kaydetme (htmx `input delay:500ms`), önizlemeyi döner. Kilitli 409, onaysız master 409 (D-048). **Hata davranışı CV ile aynı** (`cvs.py:255-276`): geçersiz alan kayıtlı değerini korur ve hatası gösterilir, diğer alanlar kaydedilir; hiçbir alan geçerli değilse `nothing_saved` |
| POST | `/cover-letters/{id}/versions` | Save as new version / Duplicate as new version (kilitliden de) |
| POST | `/cover-letters/{id}/refresh` | Gönderen alanlarını profilden (onay modalından sonra) |
| DELETE | `/cover-letters/{id}` | Sil, `/documents?tab=letters`'a döner, toast + Undo |

- **Rate limit:** CV ile aynı, sadece DELETE `60/minute` (otomatik kaydetmeye limit konmaz: yazarken dakikada 120 isteğe çıkabilir). Gövde sınırı Pydantic uzunluk kontrolleri; form `request.form(max_fields=50)`.

## 5. Ekranlar
- **Documents › Cover letters** (`/documents?tab=letters`): "Coming soon" kalkar. CV sekmesinin aynısı: Masters (dil başına kart, silme butonu), Copies (M8a'da tek grup "Not linked to an application"), "New cover letter" butonu ve modalı, boş durum ("No cover letters yet. Create your first master letter."). Sağda Profile strength kartı.
- **Editör** (`/cover-letters/{id}`; düzen ve önizleme sırası `docs/prototype/CoverLetters.dc.html`'den. Prototipteki sol AI paneli V2; prototipte önizleme doğrudan düzenlenebilir, SPEC §6.6 "form + canlı önizleme" der, SPEC geçerli. "Copy text", "Download PDF" M8b'de):
  - Başlık kalıbı "← Documents", durum rozeti (Master / Draft / Sent · locked), versiyon seçici (soydaki versiyonlar + master).
  - Bilgi bandı, mektup metinleriyle: master "This is your master letter. New copies start from it."; kopya "Copy based on Master cover letter (DE). Your master letter stays unchanged."; kilitli "Sent · locked. Duplicate as new version to change it."
  - Solda form: belge adı; Sender (ad, e-posta, telefon, adres) + "Refresh from profile" (onay modalı); Recipient (şirket, kişi, adres); şehir; konu; selamlama; gövde; kapanış. Butonlar: Save as new version, Delete; kilitliyse form salt okunur + "Duplicate as new version"; master'da "Edit master" + onay (D-048).
- **Önizleme** (D-044 kalıbı): `pdf/cover_letter.html` + `cover_letter.css` (`.letter-paper` altında; satır içi stil yok, CSP). DACH sırası: gönderen (sağ üst), alıcı (sol), "{şehir}, {tarih}" (sağ), konu (kalın), selamlama, gövde, kapanış, ad. Gövde **`|safe` olmadan**: boş satırlar paragraf, tek satır sonları `white-space: pre-line` ile. Koyu temada da beyaz kâğıt.

## 6. Metinler (GDPR şeffaflığı, SPEC §10.2)
Saklanan / dışa aktarılan / silinen verileri sayan metinlere "cover letters" eklenir: `legal/privacy.html:14`, `legal/terms.html:8`, `settings/_delete_account_form.html:8`, `emails/account_deleted.txt:5` (test: gizlilik ve silme metinleri).

## 7. Testler (`tests/test_cover_letters.py`, `tests/test_lineage.py`)
- **Soy:** ilk mektup master; ikincisi aynı dilde kopya (v1, v2); başka dilde yeni master; aynı anda iki "New" → biri master biri kopya; `(parent_id, version)` çakışması yeniden denenir; "Nothing changed since this version…" (D-050).
- **Master:** onaysız POST 409; `edit_master=confirmed` ile kaydeder; mevcut kopyalar değişmez.
- **Kilit:** `status="locked"` mektuba POST/refresh/delete 409; "Duplicate as new version" yeni taslak açar, kilitli aynen kalır.
- **Silme:** kopyası olan master silinmez (mesaj); taslak silinir, `/documents?tab=letters`'a döner, Undo aynı içerikle geri getirir; master'ı yokken kopya geri gelmez; dil master'ı varken eski master geri gelmez.
- **Erişim matrisi** (`ROUTES` listesi, `test_m3a_gaps.py` kalıbı): girişsiz 303 / HX-Redirect, garip id 404, başkasının mektubu 404, CSRF'siz 403.
- **Form:** body 10.001 karakter → hata, diğer alanlar kaydedilir ve body eski değerinde kalır; boş başlık hatası; varsayılan dil `default_doc_language`.
- **Önizleme:** DE/EN/TR varsayılan selamlama/kapanış ve tarih biçimi; saat dilimi: 23:30 UTC'de Europe/Vienna kullanıcısı ertesi günü görür (freezegun); `<script>` metin olarak kaçışlı; şablonda `style=` yok; paragraflar.
- **Dışa aktarma** `cover_letters` içerir; **hesap silinince** mektuplar gider; gizlilik ve silme metinleri "cover letters" der.
- **Sınır:** 100. mektuptan sonra mesaj (limit monkeypatch ile).
- **Refaktör:** mevcut CV testleri değişmeden geçer.
- **Güncellenecek eski testler:** "Coming soon" bekleyenler (`test_m3a_gaps.py:40-60`, `test_empty_states.py:43`, `test_documents_pages.py:67`).

## 8. Adımlar (her biri ayrı commit, önce Fatih'e sorulur)
0. `docs: plan M8a` + `docs: SPEC and D-152…D-154 for M8a`: SPEC v2.1.31 (başlık satırı); §4 dosya adları; §5.4 tablo (yeni kolonlar; tone/length/ai_generated "V2 (D-150)"; application_id M8b); §5.3/D-086 trash türü; §6.5 Cover letters sekmesi; §6.6 editör alanları ("bir başvuru seç" M8b'ye, Generate V2); §12 M8a/M8b (Duplicate ve "All documents" M8b'de kalır).
1. `refactor: shared lineage helpers for documents` (D-153; davranış değişmez; tam test + ruff).
2. `feat: cover letter model` (model, migration 0018, migration testi, export bölümü).
3. `feat: cover letter rules` (servis, şema, etiketler + testler).
4. `feat: cover letters on the Documents page` (sekme, modal, `POST /cover-letters`, silme + Undo, eski "Coming soon" testleri).
5. `feat: cover letter editor and preview` (editör, önizleme, kaydet, versiyon, refresh, master onayı).
6. `docs: cover letters in the privacy and deletion texts` (+ testler).
7. Review ve kapanış: test-writer, security-reviewer, bulgular; `uv run pytest` (normal ve `TZ=UTC`), `ruff check`, `ruff format --check`; yeşil CI; **elle test listesi** PR'da (Fatih ya da Claude Chrome'da); `LEARNING_LOG` (3–5 madde); CLAUDE.md'de M8a ✓; PR "Closes #80"; Fatih claude.ai SPEC kopyasını v2.1.31 ile yeniler; merge sonrası etiket `v0.8a-m8a`.

## Riskler
- Lineage refaktörü çalışan CV koduna dokunuyor → ayrı commit, önce tam test; davranış değişirse geri alınır ve D-153 "kopyala" seçeneğine döner.
- Önizleme ve PDF aynı şablon: M8b'de WeasyPrint farklı çizerse CSS orada düzeltilir (M3b'deki gibi).
- **M8b işi:** `ApplicationDocument.document_id` FK'si `cvdocument.id`'ye bağlı (`application.py:166-168`); mektup için ya ayrı kolon ya FK'siz kimlik gerekir. `content_snapshot` için mektup kolonlarından sözlük. Tarihin kilitte sabitlenmesi.

## Fatih'in cevapları
1. Gönderen adresi: **mektupta çok satırlı alan** (`sender_address`, profilden şehir gelir; profil değişmez).
2. Yeni master'ın gövdesi: **boş, placeholder ipucuyla**; selamlama ve kapanış dile göre dolu.
- Duplicate ve "All documents" M8b'de (SPEC ve D-050'ye uygun), "Refresh from profile" onay modalıyla (CV ile aynı): Claude karar verdi.
