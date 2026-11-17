# M4c — Başvuruda belgeler · Teknik plan (Design fazı)

## Context
M4b detay sayfasını ve next step'i getirdi (`v0.4b-m4b`). M4c (issue #34, SPEC v2.1.12 §12 › M4c, §5.3, §5.4b, §6.5, §6.7) CV'leri başvurulara bağlar, başvuru gönderilince bağlı CV'yi kilitler ve "bu şirkete tam olarak ne gönderdim?" sorusunu cevaplar. M3'ten ertelenenler (Duplicate, "All documents", PDF adında şirket) ve silmede Undo (D-035, D-046) burada kapanır.

Branch: `m4c-application-documents` (`main`, `d6649bb`). Etiket `v0.4c-m4c`. Plan issue #34'e yorum olarak eklenir.

### Bu plan nasıl onaylandı
Fatih 2026-11-17 gecesi "bana sorman gereken her şeyi kendin yap, PR merge dahil" dedi. Bu plandaki ürün kararlarını **Claude, Fatih'in yerine** verdi; her biri "(Claude karar verdi)" diye işaretli. Fatih sabah gözden geçirir; değiştirmek istediği karar bir sonraki milestone'da ya da küçük bir PR'la düzeltilir. plan-reviewer her zamanki gibi çalışır.

### Kararlar (Adım 0'da `docs/DECISIONS.md`'ye; D-080'den başlar)
- **D-080 · Bağlama (Claude karar verdi):** `CVDocument.application_id` (FK, nullable, `ON DELETE SET NULL`) ve `Application.cv_id` (FK, nullable, `ON DELETE SET NULL`). Bir başvurunun **bir** CV'si olur; bir kopya en fazla **bir** başvuruya aittir. Master hiçbir zaman bağlanmaz (sadece kopyalar gönderilir, SPEC §5.3). Detay sayfasında **Documents** kartı:
  - Bağlı CV yoksa: dil başına "Copy my Master CV (EN)" (master'dan yeni kopya oluşturur ve bağlar; başlık "{Company} · CV") ve "Use an existing copy" (bu kullanıcının başka başvuruya bağlı olmayan taslak kopyaları).
  - Bağlı CV varsa: başlık, rozet (Draft / Sent · locked), "Open" (editör, `?application={id}`), "PDF", taslaksa "Unlink".
  - Gönderilenler (`ApplicationDocument`): "Sent on {tarih}" satırları.
  Hepsi toast + Undo (D-079 token v2'ye `cv_id` eklenir; kopya oluşturmanın Undo'su kopyayı da siler). Başka kullanıcının CV'si/başvurusu 404 (`get_owned_cv` + `get_owned_application`).
- **D-081 · Versiyon numarası master başına kalır (Claude karar verdi):** SPEC "aynı başvuru için 1, 2, 3" diyor; D-045'teki unique index `(parent_id, version)` master başına numaralıyor. Index'i değiştirmek bütün M3 kurallarını yeniden açar. Numara master başına kalır, ekranda başvurunun adıyla gösterilir ("Kranich Health · v3"). SPEC §5.3'e not düşülür.
- **D-082 · Gönderilince kilitle (Claude karar verdi):** Bir başvuru **Saved'den** Applied, Interview veya Offer'a geçince (board, seçici, mülakat kaydı) bağlı taslak CV kilitlenir ve `ApplicationDocument(application_id, doc_type="cv", document_id, content_snapshot=cv.content, sent_at=now)` oluşur; timeline'a `document_linked` event'i ("CV v3 locked: this is what you sent"). Zaten gönderilmiş (Applied/Interview/Offer) bir başvuruya CV bağlamak da hemen kilitler. Undo kilidi açar ve `ApplicationDocument`'ı siler (token: `locked_cv_ids`, `sent_ids`; Undo sadece CV hâlâ o başvuruya bağlıysa ve başka bir belge kaydı eklenmediyse çalışır). Kilit `document_service.lock` ile (kurallar tek yerde).
- **D-083 · `ApplicationDocument` tablosu:** SPEC §5.4b alanları + `user_id` (her sorgu `user_id` ile filtrelenir), `document_id` `ON DELETE SET NULL` (kilitli belge silinemez ama savunma). `content_snapshot` JSON. Kapak mektubu M8'de aynı tabloyu kullanır (`doc_type`).
- **D-084 · Editör ve Documents sayfası:** Editör `?application={id}` ile açılırsa (sahipliği kontrol edilir; değilse yok sayılır) geri link "← {Company}", ayrıca "All documents" linki ve bilgi bandı "Copy for {Company}, based on {Master}. Your master CV stays unchanged." Editörde **Duplicate** geri gelir (D-050): başvuruya bağlı olmayan yeni taslak "… (copy)". Documents sayfasında kopyalar başvuruya göre gruplanır ("{Company} · {Role}"), bağlı olmayanlar "Not linked to an application" grubunda.
- **D-085 · PDF adında şirket (D-057'yi tamamlar):** Bağlı CV'nin adı `CV_{Ad}_{Şirket}_{lang}.pdf`; aynı ASCII + `filename*` kuralları, şirket adı en fazla 40 karakter.
- **D-086 · Silmede Undo: `DeletedItem` tablosu (Claude karar verdi):** Silinen satırın tüm kolonları JSON olarak `DeletedItem(user_id, kind, item_id, payload, deleted_at)`'e yazılır, sonra satır silinir. Undo aynı id ile geri ekler (`kind`: cv, experience, education, skill, language, certification, project). Token sadece `DeletedItem` id'sini taşır; Undo sahipliği (`user_id`) ve türü kontrol eder, bir kez çalışır. Silinen veri 1 saat sonra temizlenir (her yeni silmede eskiler silinir; hesap silinince cascade). CV'nin bağlı olduğu başvuru `cv_id`'si de geri bağlanır. Alternatif: `deleted_at` ile yumuşak silme (bütün sorgulara filtre gerekir, bir tanesi unutulursa silinen veri görünür); token'da içeriği taşımak (CV fotoğrafıyla çok büyük). CV silmedeki onay modalı kalkar (D-046 kapanır). Editörden silince Documents'a gidilir ve toast orada görünür: oturumda tek seferlik bir "flash" (sadece mesaj + küçük token).

### plan-reviewer (2026-11-17) ve verilen cevaplar
Sonuç "Needs changes": 3 Blocker, 9 Should, 3 Nit; hepsi aşağıdaki "Güncellemeler" ile kapandı. Reviewer'ın Fatih'e soruları Claude tarafından cevaplandı:
1. Zaten gönderilmiş bir başvuruya CV bağlamak **kilitlemez**: CV taslak kalır, Documents kartında "Lock as sent" butonu çıkar (gerçekte gönderileni düzeltebilmek için).
2. Versiyon numarası master başına kalır (D-081).
3. PR, Fatih'in elle testinden önce merge edilir ve etiketlenir: Fatih 2026-11-17 gecesi açıkça "PR merge etmek dahil" dedi (D-087). PR'a elle test listesi eklenir; Fatih sabah dener, sorun çıkarsa küçük bir düzeltme PR'ı açılır.

### Güncellemeler (plan-reviewer sonrası; yukarıdaki kararların yerine geçer)
- **D-080 değişmez kural:** `CVDocument.application_id` = **soy**: bu başvuru için yapılmış kopyalar (birden çok versiyon olabilir). `Application.cv_id` = **şu anki** CV (bir tane). Bağlı bir kopyadan "Save as new version" / "Duplicate as new version" yeni taslağa `application_id`'yi aktarır; kilitli bağlı CV'den "Duplicate as new version" yeni taslağı şu anki `cv_id` yapar (gönderilmiş versiyon `ApplicationDocument`'ta kalır). "Duplicate" (başka başvuru için) `application_id` aktarmaz. `application.cv_id` üzerinde unique index. "{Company} · CV" başlığında şirket adı 200 karaktere sığacak şekilde kesilir.
- **D-082 tetik:** Başvuru Applied, Interview veya Offer'a **her geçişte** (nereden olursa olsun) bağlı CV **taslaksa** kilitlenir. Zaten gönderilmiş başvuruya bağlanan CV taslak kalır; "Lock as sent" butonu aynı kilitlemeyi elle yapar. Durum değişikliği, kilit, `ApplicationDocument` ve event **tek transaction**: `document_service.lock(..., commit=False)`.
- **D-082 Undo:** Token `locked_cv_ids` + her birinin `locked_at`'i ve `sent_ids` taşır. Undo sadece hâlâ kilitli ve `locked_at`'i token'dakiyle aynı olan CV'lerin kilidini açar; `ApplicationDocument`'ları `user_id` + `application_id` + satır sayısı kontrolüyle siler. Kilit açma fonksiyonu sadece `document_service`'tedir ve sadece bu Undo'dan çağrılır. **CLAUDE.md'deki "kilitli versiyon asla düzenlenmez" kuralına bu tek istisna:** içerik değişmez, yanlışlıkla yapılan gönderim 60 sn içinde geri alınır. CLAUDE.md'yi Claude değiştirmez; Fatih'e not olarak bırakılır.
- **D-080 Undo:** Kopya oluşturmanın Undo'su kopyayı sadece hâlâ taslak, hiç düzenlenmemiş (`never_edited`) ve hâlâ bağlıysa siler; yoksa sadece bağı kaldırır. Bağlama/ayırma `document_linked` event'i yazar, böylece "add job"un Undo'su araya giren bir bağlamayı ezmez (`creation_fingerprint`'e `cv_id` de eklenir).
- **D-086 geri yükleme yeni id ile:** Satır **yeni bir id** ile geri eklenir (SQLite'ın id'yi yeniden kullanma sorunu yok). Profil satırı: aynı alanlar ve sıra, bölüm sınırı (`MAX_ITEMS_PER_SECTION`) yeniden kontrol edilir. CV: master ise o dilde başka master varsa "Nothing to undo"; kopya ise master'ı hâlâ yoksa "Nothing to undo"; versiyon numarası boşsa eskisi, değilse `max+1`; `MAX_CVS_PER_USER` kontrolü; her şey `IntegrityError` → "Nothing to undo". Başvuru bağı sadece `application.cv_id` hâlâ boşsa ve başvuru hâlâ sahibinin ise geri kurulur.
- **D-086 saklama süresi:** Silinen veri `UNDO_SECONDS` + 60 sn (2 dakika) tutulur; her silme, geri yükleme ve Undo isteğinde eskiler temizlenir; `user_id` FK `ON DELETE CASCADE`. Ayrı token tuzu (`undo-delete`), giriş + CSRF, `DeletedItem` `user_id` + `kind` ile aranır. Flash toast'ta başlık 60 karaktere kesilir, flash sadece tam sayfa GET'te okunur.
- **D-084 editör:** Başvuru `cv.application_id`'den alınır; `?application=` sadece onunla aynıysa kabul edilir. Versiyon seçici: aynı başvurunun versiyonları + master.
- **D-083:** `ApplicationDocument.application_id` `ON DELETE CASCADE`. **D-085:** Kilitli CV'nin PDF adında şirketin bugünkü adı kullanılır (kabul).
- **D-087 · Gece yetkisi:** Fatih 2026-11-17'de M4c için ürün kararlarını, PR merge'ünü ve etiketi Claude'a bıraktı. WORKFLOW'daki "Fatih merge eder" ve "kabul kriterlerini Fatih elle dener" maddeleri bu milestone için ertelendi: elle test sabah yapılır, gerekirse düzeltme PR'ı açılır.
- **Migration `0010`:** Açık isimli FK'ler (`fk_application_cv_id_cvdocument`, `fk_cvdocument_application_id_application`), döngü için `use_alter=True`; downgrade önce constraint'leri siler. Up → down → up SQLite'ta (test) ve Docker Postgres'te (CI docker job'u zaten tüm migration'ları çalıştırır).

## 1. Veri modeli ve migration `0010`
- `cvdocument.application_id`, `application.cv_id` (ikisi de FK, `SET NULL`, index'li), `applicationdocument`, `deleteditem`. `--autogenerate`, elle gözden geçirme, up → down → up + `alembic check`. Döngüsel FK (application ↔ cvdocument) için `use_alter` gerekebilir (risk 1).

## 2. Modüller
| Dosya | İçerik |
|---|---|
| `app/models/application.py`, `app/models/cv.py`, `app/models/trash.py` | Kolonlar ve tablolar |
| `app/services/document_service.py` | `copy_for_application`, `link`, `unlink`, `copies_for_linking`; `delete_cv` → `DeletedItem`; `restore_cv` |
| `app/services/application_service.py` | Durum değişikliğinde kilit + `ApplicationDocument` (D-082), Undo genişler |
| `app/services/trash_service.py` | `remember(user, kind, row)`, `restore(user, item_id)`, `purge_old()` |
| `app/services/profile_service.py` | `delete_item` → trash; `restore_item` |
| `app/services/pdf_service.py` | Şirketli dosya adı |
| `app/routers/applications.py` | `POST /applications/{id}/cv` (link/copy), `DELETE /applications/{id}/cv` (unlink), Documents kartı |
| `app/routers/cvs.py`, `app/routers/profile.py` | Silme → toast + Undo; `POST /undo/delete` |
| Şablonlar | `applications/_documents.html`, editör başlığı, Documents sayfası grupları, flash toast |

## 3. Testler
`test_application_documents.py` (bağlama, kopya, unlink, başka kullanıcı, kilitli), `test_send_lock.py` (kabul: Saved → Applied kilitler + snapshot, Undo ikisini geri alır, Rejected kilitlemez, zaten kilitli ikinci kez kayıt açmaz), `test_editor_application.py` (geri link, bilgi bandı, `?application=` başkasının id'si yok sayılır, Duplicate), `test_pdf_company.py` (kabul), `test_delete_undo.py` (kabul: profil satırı ve CV aynı içerikle geri gelir; bir kez; başka kullanıcı; 1 saat sonra temizlik; kilitli CV silinemez), `test_documents_grouping.py`. Sonunda `test-writer`.

## 4. Adım sırası
| # | Adım | Commit |
|---|---|---|
| 0 | D-080…D-086, SPEC v2.1.13 (§5.3 not, §5.4b `user_id`, §12 › M4c), plan | `docs: add m4c plan and decisions` |
| 1 | Modeller + migration `0010` | `feat: add application documents and trash tables` |
| 2 | Documents kartı: bağla, kopya oluştur, unlink + Undo | `feat: link cvs to applications` |
| 3 | Gönderince kilitle + `ApplicationDocument` + Undo | `feat: lock the cv that was sent` |
| 4 | Editör: geri link, bilgi bandı, All documents, Duplicate; Documents sayfası grupları; PDF adında şirket | `feat: show documents with their application` |
| 5 | Silmede Undo (profil satırı ve CV), flash toast, onay modalı kalkar | `feat: undo deletes` |
| 6 | test-writer, security-reviewer, tarayıcı kontrolü (ayrı test sunucusu), README, LEARNING_LOG, CLAUDE.md, PR, merge, etiket | `test: …`, `docs: …` |

## 5. Riskler
1. **Döngüsel FK** (application.cv_id ↔ cvdocument.application_id): SQLite batch migration ve Postgres sırası. Gerekirse `use_alter=True` ve migration'da tabloları sırayla ekleme.
2. **Silinen veriyi geri eklemek:** aynı id ile insert Postgres'te sorun değil (sequence ilerlemiş durumda); profil `version`'ı geri eklemede de artar.
3. **Kilit ve Undo:** kilitli bir belgenin kilidini sadece kilitleyen işlemin Undo'su açar (60 sn, aynı token); başka hiçbir yol yok. CLAUDE.md kuralı ("kilitli versiyon asla düzenlenmez") korunur: içerik değişmez, sadece yanlışlıkla yapılan durum değişikliği geri alınır.
4. **Kapsam:** 6 adım; tarayıcı kontrolü geçici test sunucusuyla.

## 6. Kapsam dışı
Kapak mektubu (M8), Prepare documents ve Final check (M9), belge çekmecesi (drawer, M9), versiyon karşılaştırma (V2).
