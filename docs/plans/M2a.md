# M2a — Profil bölümleri · Teknik plan (Design fazı)

## Context
M1 hesapları ve oturumları getirdi. M2a (issue #20, SPEC §12 › M2a, §5.2, §6.4, D-030) kullanıcının `/profile` sayfasında profilini doldurmasını sağlar: Personal info, Summary, Experience (maddeler dahil), Education, Skills, Languages, Certifications, Projects. Bu veri M3'te CV'nin, M7'de uyum analizinin, M8'de mektubun kaynağıdır. Sıralama, fotoğraf ve profil gücü M2b'de (#21).

Onaydan sonra: plan issue #20'ye yorum olarak eklenir. Branch: `m2a-profile` (açık, `345dea3`). Etiket `v0.2a-m2a`.

Çalışma kuralı: her commit'ten önce diff Fatih'e gösterilir ve onay beklenir; push ve PR da onayla.

Kararlar (Fatih, 2026-11-14):
1. Silmede Undo M4'e kadar yok; kayıt onaysız silinir (D-035).
2. Deneyim maddeleri ayrı kutular + "Add bullet" (SPEC §6.4, prototip).

Yeni kararlar (Adım 0'da `docs/DECISIONS.md`'ye):
- **D-031 · Liste bölümleri için tek route kalıbı:** Experience, Education, Skill, LanguageSkill, Certification ve Project aynı işlemlere sahip. Her biri için ayrı route yazmak yerine `/profile/{section}/...` route'ları ve küçük bir `Section` dataclass'ından oluşan `SECTIONS` sözlüğü kullanılır (kod taslağı §3'te). Gerekçe: ~30 neredeyse aynı route yerine 5 route; testler bölüm listesi üzerinden parametrize edilir. Alternatif: her bölüme ayrı route'lar.
- **D-032 · Profil sahipliği ve `profile_service`:** Alt tablolarda `user_id` yok (SPEC §5.2). Her alt kayıt sorgusu `Profile.user_id == user.id` ile join yapan tek bir yardımcıdan geçer (`get_owned_item`); bulunamazsa 404. Kod `app/services/profile_service.py`'de (SPEC §4 ağacına küçük ek). Profil ilk açılışta boş oluşturulur, e-postası hesap e-postasıyla başlar (SPEC bunu söylemiyor; kullanıcı değiştirebilir). Alternatif: alt tablolara `user_id` eklemek (tekrar eden veri).
- **D-033 · Tarihler ay hassasiyetinde:** Deneyim, eğitim ve sertifika tarihleri `<input type="month">` ile alınır, DB'de ayın 1'i olarak `date` saklanır. Bazı tarayıcılar (masaüstü Safari, Firefox) bu alanı düz metin kutusu gösterir; bu yüzden placeholder "YYYY-MM" ve biçim hatası mesajı var. "Hâlâ devam ediyor" için "I currently work here" / "Ongoing" kutusu; işaretliyse bitiş tarihi boş kaydedilir.
- **D-034 · SQLite'ta foreign key kontrolü açık:** SQLite foreign key kurallarını (ve `ON DELETE CASCADE`'i) varsayılan olarak uygulamaz. `app/db.py` ve test engine'i, her bağlantıda `PRAGMA foreign_keys=ON` çalıştıran bir SQLAlchemy `connect` listener'ı kullanır (tek yardımcı fonksiyon, iki yerde çağrılır). Böylece localde ve testlerde Postgres ile aynı davranış. **Migration'larda kapalı kalır:** listener sadece uygulama ve test engine'lerine eklenir; asla `Engine` sınıfına (her engine'e) ya da `alembic/env.py`'ye eklenmez. Sebep: SQLite'ta tablo değiştiren bir batch migration tabloyu kopyalayıp eskisini siler; foreign key açıkken bu silme ona bağlı satırları da (ör. `user` değişince bütün profilleri) siler. Kodda da yorumla yazılır.
- **D-035 · M2a'da silmede Undo yok:** Silme, SPEC §6.0/2'deki işlemler gibi onay sormaz; ama Undo altyapısı M4'te geldiği için M2a'da silinen profil kaydı geri alınamaz. SPEC değişmez. M4 issue'suna "profile delete undo" maddesi eklenir. Alternatif: M2a'da basit Undo.

---

## 1. Paketler
Yok. SortableJS ve Pillow M2b'de.

---

## 2. Veri modeli — `app/models/profile.py` + migration `0003`

| Tablo | Kolonlar (SPEC §5.2) |
|---|---|
| `profile` | id, user_id (**unique** FK → user, `ON DELETE CASCADE`), headline, summary, email, phone, location, website, linkedin_url, github_url, date_of_birth, nationality, version (int, 0), created_at, updated_at |
| `experience` | id, profile_id (FK, CASCADE, index), sort_order, company, title, location, start_date, end_date (null = present), description, bullets (JSON list[str]) |
| `education` | id, profile_id, sort_order, institution, degree, field, location, start_date, end_date, description |
| `skill` | id, profile_id, sort_order, name, category (null), level (null, 1–5) |
| `language_skill` | id, profile_id, sort_order, language, level (metin: A1, A2, B1, B2, C1, C2, native; D-025'teki `plan` gibi DB enum değil) |
| `certification` | id, profile_id, sort_order, name, issuer, date, url |
| `project` | id, profile_id, sort_order, name, description, url |

- `photo_path` M2b'de gelir (D-025: kolon onu kullanan kodla birlikte).
- `bullets`: SQLAlchemy bir JSON listesinin içindeki değişikliği fark etmez; kod her zaman yeni bir liste atar (yorumla açıklanır).
- `sort_order`: yeni kayıt sona eklenir (`max + 1`). M2a'da sıralama değiştirilemez.
- Migration: `alembic revision --autogenerate -m "add profile tables"` → `0003`; `test_migrations.py` beklenen revizyonu günceller.

### Form alanları ve sınırlar

| Form | Zorunlu | İsteğe bağlı | Sınırlar |
|---|---|---|---|
| Personal info | full_name | headline, email, phone, location, website, linkedin_url, github_url, date_of_birth, nationality | Kısa metin ≤ 200, URL ≤ 500 ve `http(s)://`, e-posta biçimi, doğum tarihi 1900 ile bugün − 14 yıl arası |
| Summary | – | summary | ≤ 5000 |
| Experience | title, company, start_date | location, end_date / ongoing, description, bullets | açıklama ≤ 5000, madde ≤ 300, en fazla 20 madde (boş maddeler atlanır), start ≤ end |
| Education | institution, start_date | degree, field, location, end_date / ongoing, description | start ≤ end |
| Skill | name | category, level (1–5) | |
| Language | language, level | – | level listeden |
| Certification | name | issuer, date, url | |
| Project | name | description, url | |

- Her bölümde en fazla **50 kayıt** (51. ekleme hata mesajı verir).
- Hata mesajları kullanıcı dilinde ve kısa: "Enter a job title.", "Use the format YYYY-MM.", "The start date must be before the end date." Pydantic'in kendi metinleri gösterilmez; şema hataları alan → mesaj sözlüğüne çevrilir.

---

## 3. Modüller

| Dosya | İçerik |
|---|---|
| `app/db.py` | `enable_sqlite_foreign_keys(engine)` (D-034): sadece uygulama engine'i ve test engine'i için çağrılır, migration'lar için asla |
| `app/models/profile.py` (yeni) | 7 tablo |
| `app/schemas/profile.py` (yeni) | Form şemaları (Pydantic) ve `form_errors(exc) -> dict[str, str]` |
| `app/services/profile_service.py` (yeni, D-032) | `get_or_create_profile` (aynı anda iki ilk istek: `IntegrityError` yakalanır, profil tekrar okunur), `get_owned_item`, `add_item`, `update_item`, `delete_item`, `update_personal_info`, `update_summary`. Her yazma `_touch(profile)` ile `version`'ı SQL'de artırır (`Profile.version + 1`, iki istek aynı anda gelse de kayıp yok). |
| `app/routers/profile.py` (yeni) | Route'lar ve `SECTIONS` |
| `app/templates/profile/` | `index.html`, `_personal.html` / `_personal_form.html`, `_summary.html` / `_summary_form.html`, `_section.html` (bir liste bölümünün tamamı), her bölüm için `_item_<section>.html` ve `_form_<section>.html` |
| `app/static/js/app.js` | `Alpine.data("bulletList")` (CSP build, D-005) |
| `app/templates/base.html` | Avatar menüsünde "My profile" linki |

### `SECTIONS` taslağı (D-031)

```python
@dataclass
class Section:
    model: type[SQLModel]       # e.g. Experience
    form: type[BaseModel]       # e.g. ExperienceForm (app/schemas/profile.py)
    title: str                  # "Experience"
    item_template: str          # "profile/_item_experience.html"
    form_template: str          # "profile/_form_experience.html"
    list_fields: tuple = ()     # form fields sent many times, e.g. ("bullets",)

SECTIONS = {
    "experience": Section(Experience, ExperienceForm, "Experience", ..., list_fields=("bullets",)),
    "education": Section(Education, EducationForm, "Education", ...),
    ...
}

async def read_form(request: Request, section: Section) -> dict:
    """The submitted form as a dict: lists for list_fields, single values otherwise."""
    form = await request.form()
    data = {key: form.get(key) for key in form.keys()}
    for name in section.list_fields:
        data[name] = form.getlist(name)
    return data
```

Route `section` adını `SECTIONS`'ta arar; yoksa 404. Form `section.form.model_validate(data)` ile doğrulanır.

### Route'lar (hepsi `require_user`; POST/DELETE CSRF'li)
Kayıt sırası önemli: önce sabit yollar (`/profile/personal`, `/profile/summary`), sonra `/profile/{section}/new`, en son `/profile/{section}/{item_id}`. Aksi halde "new" bir id gibi okunur. `item_id` yolda metin olarak alınır ve `get_owned_item` içinde sayıya çevrilir; çevrilemezse 404 (FastAPI'nin `int` tipi 422 verirdi, oysa "böyle bir kayıt yok" 404'tür).

| Metot | Yol | Döner |
|---|---|---|
| GET | `/profile` | Sayfa (profil yoksa oluşturur) |
| GET | `/profile/personal`, `/profile/personal/edit` | Görünüm / form parçası |
| POST | `/profile/personal` | Kaydet → görünüm parçası. Hata → **200** + hatalı form parçası |
| GET / POST | `/profile/summary`, `/profile/summary/edit` | Aynı kalıp |
| GET | `/profile/{section}/new` | Boş form parçası |
| POST | `/profile/{section}` | Ekle → **bölümün tamamı** (`_section.html`). Hata → 200 + hatalı form |
| GET | `/profile/{section}/{item_id}`, `.../edit` | Kayıt görünümü / form parçası |
| POST | `/profile/{section}/{item_id}` | Güncelle → kayıt görünümü. Hata → 200 + hatalı form |
| DELETE | `/profile/{section}/{item_id}` | Sil → **bölümün tamamı** (200) |

- **Hata neden 200:** htmx 2, 4xx yanıtları sayfaya koymaz (M1b'deki resend dersi). Testler status yerine mesajı ve DB'nin değişmediğini kontrol eder.
- **Ekleme ve silmeden sonra bölümün tamamı döner:** boş bölüm mesajı ("No certifications yet…") ve açık kalan ekleme formu kendiliğinden doğru olur; htmx tarafında tek bir `hx-target` yeter.
- **JavaScript zorunlu:** satır içi düzenleme htmx ile çalışır. htmx olmayan bir istek (ör. adres çubuğundan GET parçası) 303 `/profile`'a gider; JS kapalı kullanım desteklenmez.
- **htmx hata durumu:** CSRF süresi dolması (403) veya sunucu hatası (500) şu an kullanıcıya hiçbir şey göstermez. `app.js`'te `htmx:responseError` olayında genel bir toast: "Something went wrong. Please reload the page." (toast bileşeni M0'dan var).

### Arayüz (prototip `docs/prototype/Profile.dc.html`)
- `page_header("My profile", subtitle="Your source data. Master documents and fit analysis are built from it. Copies you already sent never change.", back_url="/documents", back_label="Documents")`.
- Geniş ekranda iki sütun: solda Personal info, Summary, Experience, Education, Certifications, Projects; sağda Skills, Languages (M2b'de Profile strength kartı buraya). Mobilde tek sütun.
- Prototipteki genel "Save changes" yok: her bölümün kendi Edit / Save / Cancel'ı var (SPEC §6.4). "Import from PDF" yok (M9).
- Personal info: Full name (`User.full_name`), Headline, Email, Phone, Location, Website, LinkedIn, GitHub; açılır "Optional for DACH CVs" bölümünde Date of birth ve Nationality ("These are never sent to the AI and are hidden on CVs by default.").
- **Maddeler (`bulletList`):** sunucu mevcut maddeleri `<input name="bullets">` olarak basar; "Add bullet" bir `<template>`'i kopyalayıp boş kutu ekler, × kutuyu kaldırır. Alpine CSP build `x-data`'ya argüman veremediği için başlangıç verisi HTML'den gelir, JS'ten değil.
- Skills: çipler; çipe tıklayınca küçük form (ad, kategori, seviye); × siler; altta "Add".
- Languages: dil + seviye; seviye değişince kaydedilir (`hx-trigger="change"`); × siler.

---

## 4. Testler

| Dosya | Kapsam |
|---|---|
| `test_profile_models.py` | Profil kullanıcı başına bir tane (unique). Kullanıcı silinince profil ve alt kayıtlar silinir (SQLite'ta, D-034 sayesinde). Bullets JSON listesi gidip geliyor. `get_or_create_profile` iki kez çağrılınca tek profil. |
| `test_profile.py` | `/profile` ilk açılışta profili oluşturur, e-posta dolu; Adım 3'te Personal info ve Summary başlıkları, Adım 4b'de 8 bölüm başlığının hepsi kontrol edilir. Personal info ve Summary: düzenle, kaydet, iptal; full_name `User`'a yazılır. **Her liste bölümü (parametrize):** ekle → listede; düzenle → değişir; sil → yok; her işlem `version` +1; boş durum metni. Maddeler: ekle, sil, boş madde atlanır, 21. madde reddedilir. **Geçersiz girdi (parametrize):** boş zorunlu alan, 201 karakter, `2021-13` / `abc` tarih, başlangıç > bitiş, ongoing + bitiş tarihi (bitiş yok sayılır), `javascript:` URL, seviye `D1`, skill seviyesi 6, gelecekte doğum tarihi → mesaj görünür, DB değişmez. 51. kayıt reddedilir. HTMX'siz GET parça → 303. Route sırası: `/profile/skills/new` form döner; `/profile/skills/abc` 404. Bilinmeyen bölüm 404. |
| `test_profile_access.py` | **Girişsiz:** her route (bölüm × GET new / GET view / GET edit / POST / DELETE, ayrıca personal ve summary) login'e yönlendirir (htmx'te `HX-Redirect`). `test_auth.py`'deki genel test `{` içeren yolları atladığı için bu test açıkça yazılır. **Başka kullanıcı:** B'nin her bölümdeki kaydına A'nın GET, GET edit, POST, DELETE istekleri 404 ve B'nin verisi değişmez. Var olmayan id 404. CSRF'siz POST/DELETE 403. |
| Mevcut | `test_migrations` → `0003`. |

`test-writer` subagent'ı Adım 4b'den sonra yetki ve hata testlerinde eksik var mı diye çalıştırılır.

---

## 5. Adım sırası (her adım ayrı commit, sonrasında `pytest` + `ruff`)

| # | Adım | Commit |
|---|---|---|
| 0 | Plan, D-031…D-035, issue #20'ye yorum, M4 issue'suna (yoksa M4 issue'su açılınca) undo notu | `docs: add m2a technical plan` |
| 1 | SQLite foreign key ayarı (D-034) + modeller + `0003` migration + model testleri | `feat: add profile tables` |
| 2 | `profile_service`, form şemaları, hata mesajları + birim testleri | `feat: add profile service and form validation` |
| 3 | `/profile` sayfası, Personal info, Summary, avatar menüsü linki, htmx hata toast'ı + testler (erişim testleri dahil) | `feat: add profile page with personal info and summary` |
| 4a | `SECTIONS`, genel route'lar; Skills, Languages, Certifications, Projects + testler (erişim dahil) | `feat: add profile list sections` |
| 4b | Experience ve Education, tarihler, `bulletList` + testler (erişim dahil) | `feat: add experience and education with bullets` |
| 5 | `test-writer` kontrolü, eksik testler | `test: fill profile authorization gaps` (gerekirse) |
| 6 | README, elle kabul testi (kısa: ekranlar ve ana akış), `security-reviewer`, LEARNING_LOG | `docs: update readme and learning log for m2a` |
| 7 | CLAUDE.md'de M2a ✓ | `docs: mark m2a as done` |
| 8 | PR (`Closes #20`), merge sonrası etiket `v0.2a-m2a` | — |

---

## 6. Riskler
1. **Generic route'lar (D-031):** `SECTIONS` ilk bakışta sihirli görünebilir. Dataclass alanları ve `read_form` yorumlu; bir bölüm gerekirse ayrı route'a çıkarılabilir.
2. **htmx ve hatalar:** form hataları 200 ile döner; 403/500 için genel toast. Testler mesaj ve DB durumunu kontrol eder.
3. **SQLite foreign key (D-034):** ayar açılınca mevcut testlerde gizli bir foreign key ihlali varsa ortaya çıkar (şu an sadece `user` tablosu var, beklenmiyor).
4. **`type="month"` tarayıcı farkı (D-033):** düz metin olarak çıkan tarayıcılarda biçim hatası mesajı.
5. **Silme geri alınamaz (D-035):** M4'e kadar bilinen sınır.

---

## 7. Doğrulama
1. `uv run pytest`, ruff temiz, `uv run alembic upgrade head` 7 tabloyu oluşturur.
2. Fatih (kısa elle test): avatar menüsü → My profile; her bölüme bir kayıt ekle, düzenle, sil; deneyime 3 madde ekle, birini sil; hatalı tarih mesajı; light/dark ve mobil.
3. `docker compose up --build` → Postgres'te aynı akış.
4. `security-reviewer`; bulgular düzeltilir veya issue olur.

## 8. Kapsam dışı
Sıralama, fotoğraf, profil gücü (M2b, #21). Import from PDF (M9). Documents'taki Profile strength kartı (M3). Asistan önerileri (M12b). Silmede Undo (M4, D-035).

## 9. Açık soru
Yok.
