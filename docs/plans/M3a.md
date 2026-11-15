# M3a — CV belgeleri ve editör · Teknik plan (Design fazı)

## Context
M2 profili tamamladı (`v0.2b-m2b`). M3a (issue #27, SPEC v2.1.6 §12 › M3a, §5.3, §6.5, D-042) kullanıcının profilinden CV belgeleri oluşturmasını sağlar: dil başına bir master, ondan kopyalar ve versiyonlar, kilitli belgelerin hiç değişmemesi, solda form + sağda canlı HTML önizlemeli editör. PDF, diğer iki şablon ve DE/TR başlıklar M3b'de (#28). Başvurular M4'te geldiği için kopyalar henüz başvuruya bağlanmaz.

Branch: `m3a-cv-documents` (D-042; `f07eb9a` üzerinde). Etiket `v0.3a-m3a`. Onaydan sonra plan issue #27'ye yorum olarak eklenir.

Çalışma kuralı: her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push ve PR da onayla.

### Fatih'in kararları (2026-11-15, plan-reviewer sonrası)
1. **Master'ı düzenlemek:** master editörde salt okunur açılır; "Edit master" butonu bir onaydan sonra düzenlemeyi açar ("Changes only affect new copies, not existing ones."). SPEC §9.8 ile uyumlu; CLAUDE.md kuralına bu istisna yazılır (D-048).
2. **Kopyası olan master silinemez:** "This master has {n} copies. Delete the copies first." Sahipsiz kopya oluşmaz (D-047).
3. **M4'e kadar CV silme onay modalıyla:** SPEC §6.0/2'ye geçici istisna; M4'te Undo gelince kalkar (D-046).
4. **Editör kapsamı:** belge adı, headline, summary, bölüm göster/gizle, deneyim maddesi seçimi. Deneyim metinleri profilden (Refresh from profile) veya M12'de asistandan (D-049).
5. **`default_doc_language` ertelenir:** New CV modalında dil her seferinde seçilir (varsayılan EN); kolon Settings sayfasıyla gelir. D-025 güncellenir.

### Yeni kararlar (Adım 0'da `docs/DECISIONS.md`'ye; her numara tek karar)
- **D-043 · CV içeriği bir Pydantic şeması (`CVContent`):** `CVDocument.content` JSON'u `app/schemas/cv.py`'deki `CVContent` ile okunur ve yazılır: `schema_version` (1), personal (ad, headline, e-posta, telefon, konum, linkler), summary, experience (bullet'lar `{text, selected}`), education, skills, languages, certifications, projects, `hidden_sections`. Snapshot = profilin o anki hali (`snapshot_from_profile`). Fotoğraf, doğum tarihi ve uyruk M3a'da snapshot'a girmez (M3b); `show_photo`/`show_birth_date` kolonları şimdiden gelir (SPEC §5.3). Alternatif: içeriği ilişkisel tablolara kopyalamak.
- **D-044 · Önizleme ve PDF aynı şablonu kullanır:** SPEC §4 ağacına uygun olarak `app/templates/pdf/cv_classic.html` sadece CV'yi çizer; stili Tailwind değil düz CSS `app/static/css/cv_classic.css`, `.cv-paper` altında kapsamlı, satır içi `style=`/`<style>` yok (CSP). Editör önizlemesi bu şablonu parça olarak koyar; M3b'de WeasyPrint aynı şablonu ve CSS'i kullanır. CSS sadece editörde yüklenir: `base.html`'e `{% block head_extra %}`. Kâğıt koyu temada da beyaz (SPEC §6.0). Alternatif: `<iframe srcdoc>`.
- **D-045 · Başvuru yokken versiyon kuralları:** Soy (lineage) = aynı `parent_id` (M3a'da hep o dilin master'ı). Master'ın `version`'ı 0. Yeni versiyon numarası **her yerde** `max(soydaki version) + 1`; unique index `(parent_id, version)` + `IntegrityError` olursa bir kez yeniden dene. New CV: o dilde master yoksa master; varsa master'ın soyunda yeni kopya (profilden snapshot). Save as new version / Duplicate as new version: içeriği kopyalayıp aynı soyda yeni taslak. Duplicate: aynı içerik, başlık "{başlık} (copy)", aynı soyda yeni taslak. M4'te `application_id` gelince soy (parent, application) olur; kolon M4'te (D-025 kalıbı).
- **D-046 · M4'e kadar CV silme onay modalıyla:** M0'daki `modal` bileşeni, "Delete this CV? This can't be undone." SPEC §6.0/2 modalı hesap silmeye ayırır; Undo M4'te geldiği için geçici istisna (D-035'teki profil satırlarından farkı: bir belge çok daha büyük kayıp). M4'te Undo ile değişir.
- **D-047 · Kopyası olan master silinemez:** `delete_cv` master'ın soyunda kopya varsa reddeder ("This master has {n} copies. Delete the copies first."). `parent_id` FK yine `ON DELETE SET NULL` (savunma için), ama kurala göre sahipsiz kopya oluşmaz.
- **D-048 · Master sadece açık onayla düzenlenir:** Master editörde salt okunur açılır; "Edit master" + onay modalı → o sayfada form açılır (sunucu tarafında: master'a POST ancak formda `edit_master=confirmed` alanı varsa kabul edilir, yoksa 409). Refresh from profile master'da da aynı onayı ister. Mevcut kopyalar etkilenmez (test). CLAUDE.md'deki "never edit a master … in place" kuralına istisna eklenir: "…except when the user explicitly unlocks the master in the editor". SPEC §9.8 ile uyumlu; asistan (M12) master'ı asla değiştirmez.
- **D-049 · M3a editör kapsamı:** Editörde belge adı, headline, summary, bölüm göster/gizle ve deneyim maddesi seçimi düzenlenir. Deneyim, eğitim vb. metinleri profilde düzenlenip Refresh from profile ile gelir veya M12'de asistanla. Alternatif: tüm snapshot'ı düzenlenebilir yapmak (büyük form, M3a'yı şişirir).
- **D-025 güncellemesi:** `default_doc_language` M3'ten Settings sayfasına (milestone'u Settings planlanınca) ertelendi.

---

## 1. Paketler
Yok. (WeasyPrint M3b'de, D-014.)

## 2. Veri modeli — `app/models/cv.py` + migration `0005`

| Kolon | Tip | Not |
|---|---|---|
| id | int PK | |
| user_id | FK → user, CASCADE, index | Her sorgu bununla filtrelenir (CLAUDE.md) |
| title | str(200) | ör. "CV – Master (EN)" |
| language | str(2) | `de`/`en`/`tr` (DB enum değil, D-025'teki `plan` gibi) |
| template | str(20), default `classic` | M3a'da sadece classic |
| content | JSON | `CVContent` (D-043) |
| show_photo, show_birth_date | bool, default False | M3b'de kullanılır |
| source_profile_version | int | Snapshot anındaki `Profile.version` |
| is_master | bool | |
| parent_id | FK → cvdocument.id, ON DELETE SET NULL, nullable | |
| version | int | Master 0, kopyalar 1, 2, … |
| status | str(10), default `draft` | `draft` / `locked` |
| locked_at | datetime, nullable | |
| created_at / updated_at | datetime | |

- **Kullanıcı ve dil başına tek master:** kısmi unique index `(user_id, language)`, koşul kolon ifadesinden kurulur (`sqlite_where=CVDocument.is_master.is_(true())`, `postgresql_where` aynı), ham metin değil (SQLite 0/1, Postgres boolean). Aynı anda iki "New CV" isteği iki master üretemez; ikinci istek `IntegrityError` yakalayıp kopya olarak kaydedilir. Postgres doğrulaması CI `docker` job'unda (D-012).
- **Soyda tek versiyon numarası:** unique index `(parent_id, version)` (D-045).
- **Sınır:** kullanıcı başına en fazla `MAX_CVS_PER_USER = 100` belge; aşılırsa "You can have up to 100 CVs. Delete one to add another." (M2a'daki 50 satır kalıbı).
- **Tarihler:** `created_at`/`updated_at`/`locked_at` `app/models/base.py`'deki `utcnow` ile yazılır, okurken `as_utc` (SQLite naive tarih); "updated 2 days ago" bunun üzerinden.
- `application_id` M4'te (D-045).
- Migration: `alembic revision --autogenerate -m "add cv documents"` → `0005`; `test_migrations.py` → `0005`; upgrade → downgrade → upgrade + `alembic check` (D-011).

## 3. Modüller

| Dosya | İçerik |
|---|---|
| `app/models/cv.py` | `CVDocument`, `CV_LANGUAGES`, `CV_STATUSES` (şablonlar `entitlements.allowed_templates()` üzerinden, CLAUDE.md) |
| `app/schemas/cv.py` | `CVContent` ve alt modeller; `NewCVForm` (title: kırpılır, 1–`SHORT_TEXT`; language ∈ de/en/tr; template ∈ `allowed_templates(user)`); `CVEditForm` (title 1–`SHORT_TEXT`, headline ≤ `SHORT_TEXT`, summary ≤ `LONG_TEXT`, `hidden_sections` ⊆ 8 bilinen bölüm, seçili bullet anahtarları `"<deneyim sırası>-<madde sırası>"` biçiminde, ikisi de mevcut aralıkta, en fazla 50 × 20); sabitler `schemas/profile.py`'den; `form_errors` (M2a kalıbı) |
| `app/services/document_service.py` | `snapshot_from_profile(session, user) -> CVContent`; `get_owned_cv(session, user, id)` (404); `list_cvs(session, user)`; `create_cv(session, user, title, language)`; `duplicate(...)`; `save_as_new_version(...)`; `refresh_from_profile(...)`; `update_content(...)`; `delete_cv(...)`; `lock(session, cv)` (master'ı reddeder: sadece kopyalar kilitlenir, SPEC §5.3); `versions_in_lineage(...)`. Değiştiren her fonksiyon önce `_ensure_draft(cv)` çağırır → kilitliyse `DocumentLocked`. Master ve kilit kuralları sadece burada (CLAUDE.md: "never edit a master or a locked version in place" kuralı burada uygulanır; master taslak olduğu için düzenlenebilir, kilitli asla). |
| `app/routers/cvs.py` | SPEC §4'teki ad; `/documents` ve `/cvs/...` route'ları. `/documents` şimdiki "Coming soon" sayfasının yerini alır. |
| `app/templates/cvs/` | `documents.html` (sekmeler, Masters, Copies, strength kartı, "New CV" modalı), `_cv_row.html`, `editor.html`, `_form.html`, `_preview.html` |
| `app/templates/pdf/cv_classic.html` | CV şablonu (D-044), M3b'de PDF de bunu kullanır |
| `app/static/css/cv_classic.css` | Kâğıt stili (A4 oranı, serif başlıklar), sadece editör sayfası yükler (`head_extra`) |
| `app/templates/base.html` | `{% block head_extra %}{% endblock %}` |

### Route'lar (hepsi `require_user`, POST/DELETE CSRF'li)

| Metot | Yol | Davranış |
|---|---|---|
| GET | `/documents` | CVs sekmesi (varsayılan) |
| GET | `/documents?tab=letters` | Cover letters: "Coming soon" boş durumu (M8) |
| POST | `/cvs` | New CV (title, language, template). Hata → modal formu mesajla. 100 belge sınırı → mesaj. Başarı → 303 `/cvs/{id}` |
| GET | `/cvs/{id}` | Editör |
| POST | `/cvs/{id}` | Otomatik kaydet (`hx-trigger="input changed delay:500ms"`, `hx-sync="this:replace"`) → önizleme parçası; form hatası → 200 + mesaj (OOB), içerik değişmez. Kilitli → 409. Master → `edit_master=confirmed` yoksa 409 (D-048) |
| POST | `/cvs/{id}/versions` | Save as new version / Duplicate as new version → 303 yeni belge |
| POST | `/cvs/{id}/duplicate` | Duplicate → 303 yeni belge |
| POST | `/cvs/{id}/refresh` | Refresh from profile (onay modalından) → 303 aynı belge; kilitli → 409 |
| DELETE | `/cvs/{id}` | Sil (onay modalından) → `HX-Redirect: /documents`; kilitli → 409; kopyası olan master → 200 + mesaj, silinmez (D-047) |

- `{id}` metin alınır, sayıya çevrilemezse 404 (M2a'daki `get_owned_item` kalıbı).

### Arayüz (prototip `Documents.dc.html`, `CVEditor.dc.html`)
- **Documents:** başlık "Documents", alt başlık "Master documents stay clean. Each application gets its own copy." Sekmeler CVs / Cover letters. **Masters:** dil başına kart ("MASTER · EN", başlık, "Classic template · updated 2 days ago"). **Copies for applications:** "Not linked to an application" grubu; satır: başlık · v{n}, dil, şablon, "based on Master CV (EN)", rozet Draft / Sent · locked, güncellenme (SPEC §6.5'in istediği sütunlar; bağlı başvuru M4). Alt not M3a için: "Copies will be linked to applications once application tracking arrives. Sent copies are locked, so you always know what you sent." Boş durum: "No CVs yet. Create your first master CV from your profile." Sağda Profile strength kartı (M2b'deki şablon) + "Complete my profile" (`/profile`). Sağ üstte "New CV" (modal: başlık, dil, şablon; şablon M3a'da sadece Classic).
- **Editör (/cvs/{id}):** `page_header` ("← Documents", belge adı, rozet), "All documents" linki, **versiyon seçici** (`<details>` menüsü, JS yok: aynı soydaki belgeler + master, her biri link), butonlar: Save as new version, Duplicate, Refresh from profile (onay modalı), Delete (onay modalı). Bilgi bandı: master → "This is your master CV (EN). New copies start from it." + "Edit master" butonu (onay modalı, D-048); kopya → "Copy based on Master CV (EN). Your master CV stays unchanged."; kilitli → "Sent · locked. You can't change a sent CV. Duplicate it as a new version to make changes." + "Duplicate as new version". Gizlenen butonlar: Tailor to a job (M7), Edit with assistant (M12), Download PDF (M3b).
- **Form (sol):** Title, Headline, Summary, Sections (8 bölüm için göster/gizle checkbox'ları), Experience bullets (her deneyimin maddeleri checkbox), alt notu "Changes here only affect this CV, not your main profile." Kilitliyse tüm alanlar `disabled`.
- **Önizleme (sağ):** "Live preview · A4", beyaz kâğıt, classic şablon, başlıklar İngilizce (DE/TR M3b).
- Mobilde form üstte, önizleme altta.

## 4. Testler

| Dosya | Kapsam |
|---|---|
| `test_cv_models.py` | Kullanıcı + dil başına tek master (unique index); `(parent_id, version)` tekil; `parent_id` FK `SET NULL`; SQLite'tan okunan naive `updated_at` UTC sayılır; kullanıcı silinince CV'ler silinir; `CVContent` JSON gidip geliyor |
| `test_cv_snapshot.py` | Snapshot profildeki tüm bölümleri ve `sort_order`'ı alır; `source_profile_version` = profil versiyonu; **CV'yi düzenlemek profili değiştirmez**; profili değiştirmek mevcut CV'yi değiştirmez; fotoğraf/doğum tarihi/uyruk snapshot'ta yok |
| `test_document_service.py` | İlk CV master, ikinci aynı dilde kopya (parent = master, v1, v2…), başka dilde yeni master; **kopyayı düzenlemek master'ı değiştirmez**; **master'ı düzenlemek mevcut kopyaları değiştirmez** (SPEC §5.3); onaysız master düzenleme reddedilir; aynı v1'den iki kez Save as new version → v2 ve v3 (aynı numara yok); 101. belge reddedilir; `lock` master'ı reddeder; kopyası olan master silinemez, kopyalar silinince silinebilir; Save as new version / Duplicate kuralları (D-045); Refresh from profile içeriği yeniler; `lock` sonrası update/refresh/delete `DocumentLocked`, içerik aynı; kilitli belgeden Duplicate as new version çalışır ve yeni taslak düzenlenebilir; kopyasız master silinince o dilde sonraki yeni CV master olur; aynı anda iki master denemesi (IntegrityError) kopyaya düşer |
| `test_documents_pages.py` | `/documents` listeleri (Masters, Copies grubu, rozetler, boş durum), Cover letters "Coming soon", strength kartı; New CV formu hataları (boş başlık, geçersiz dil) → mesaj; editör sayfası bölümleri, gizli butonlar yok; otomatik kaydet → önizleme güncel, DB güncel, profil aynı; form hataları → mesaj, DB aynı; gizlenen bölüm önizlemede yok; seçilmeyen bullet önizlemede yok; kilitli editörde alanlar `disabled` ve "Duplicate as new version" var; kilitli belgeye POST/refresh/DELETE → 409 ve DB aynı; kullanıcı metni escape |
| `test_documents_access.py` | Girişsiz her route → login (htmx'te `HX-Redirect`); başka kullanıcının CV'sine GET/POST/versions/duplicate/refresh/DELETE → 404 ve veri aynı; `abc` id → 404; CSRF'siz POST/DELETE → 403 |
| `test_pages.py` (mevcut) | `/documents` artık "Coming soon" değil |
| `test_migrations.py` | `0005` |

`test-writer` Adım 5'ten sonra çalışır.

## 5. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)

| # | Adım | Commit |
|---|---|---|
| 0 | Plan, D-043…D-049, D-025 güncellemesi, CLAUDE.md'ye master istisnası (D-048), issue #27'ye yorum; CLAUDE.md değiştiği için Fatih claude.ai kopyasını kontrol eder | `docs: add m3a technical plan` |
| 1 | `CVDocument` + `0005`, `CVContent`, `snapshot_from_profile` + model/snapshot testleri | `feat: add cv document model and profile snapshot` |
| 2 | `document_service` (oluştur, master kuralı, versiyonlar, refresh, sil, kilit, sahiplik) + testler | `feat: add document service with master, versions and locks` |
| 3 | Documents sayfası (sekmeler, Masters, Copies, New CV modalı, strength kartı) + testler | `feat: add documents page with cv lists` |
| 4 | Editör: sayfa, form, otomatik kaydet, classic önizleme + CSS + testler | `feat: add cv editor with live preview` |
| 5 | Versiyon seçici, bilgi bandı, kilitli görünüm, Save as new version / Duplicate / Refresh / Delete butonları + testler | `feat: add cv versions, refresh and delete` |
| 6 | `test-writer` kontrolü | `test: fill m3a authorization gaps` (gerekirse) |
| 7 | README, kısa sıralı elle test, `security-reviewer`, LEARNING_LOG | `docs: update readme and learning log for m3a` |
| 8 | CLAUDE.md'de M3a ✓ | `docs: mark m3a as done` |
| 9 | PR (`Closes #27`), CI yeşil, merge sonrası etiket `v0.3a-m3a` | — |

## 6. Riskler
1. **Önizleme isteği sayısı:** her tuş vuruşunda değil, 500 ms bekleyip bir istek (`delay:500ms`); yine de kaydetme ve önizleme aynı istekte. Hızlı yazarken son istek kazanır (`hx-sync="this:replace"`).
2. **Önizleme CSS'i ile Tailwind çakışması:** `classic.css` sadece `.cv-paper` altında; Tailwind'in `preflight`'ı başlık ve liste stillerini sıfırlıyor, CV CSS'i bunları açıkça verir.
3. **JSON içeriğin şeması zamanla değişir:** `CVContent` her okumada doğrulanır; yeni alanlar varsayılan değerle gelir (eski belgeler bozulmaz). Şema sürümü alanı (`schema_version: 1`) şimdiden eklenir.
4. **Fotoğraf ve kilitli belgeler (M3b'ye not):** profil fotoğrafı değişince eski dosya silinir (M2b). Kilitli bir CV fotoğraf gösterecekse, M3b fotoğrafı belgeye kopyalamalı; M3b planına not.
5. **Master'ı düzenlemek:** D-048 ile çözüldü (salt okunur + "Edit master" onayı). İlk taslakta SPEC §6.5'in master düzenlemeye izin verdiği yazıyordu; bu yanlıştı (plan-reviewer), SPEC §1.2 ve §9.8 tersini söylüyor.

## 7. Doğrulama
1. `uv run pytest`, ruff temiz, `alembic upgrade head` `cvdocument` tablosunu oluşturur.
2. Fatih (kısa, sıralı elle test, Chrome + `localhost:8000`): Documents → New CV (EN) → master; tekrar New CV (EN) → kopya v1; editörde summary değiştir → önizleme güncellenir, profil aynı kalır; bir bölümü gizle, bir bullet'ı kaldır → önizlemede yok; Save as new version → v2; Delete (onay) → listeden gider; light/dark (kâğıt beyaz kalır) ve mobil.
3. `security-reviewer`; bulgular düzeltilir veya issue olur.

## 8. Kapsam dışı
PDF, modern/compact şablon, DE/TR başlıklar, fotoğraf ve doğum tarihi gösterimi (M3b, #28). Başvuruya bağlama ve "applied" ile kilitleme (M4). Silmede Undo (M4). Tailor to a job (M7). Cover letters (M8). Edit with assistant (M12). Versiyon karşılaştırma ve geri yükleme (V2).
