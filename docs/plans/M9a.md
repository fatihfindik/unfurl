# M9a — Apply akışı (AI'sız) · Teknik plan (Design fazı)

## Context
M8b (`v0.8b-m8b`) ile CV ve mektup başvuruya bağlanıyor, gönderince kilitleniyor, PDF'i iniyor; hepsi başvuru detayındaki sağ **Documents** kartında. M9a (issue #82, SPEC v2.1.34 §12 › M9a, §6.7) bunları başvuru detayında bir **akışa** çevirir: **Apply** sekmesi (① Prepare documents → ② Final check), belgeyi sayfadan çıkmadan gösteren **belge paneli (drawer)** ve başvurudan sonra **Progress** görünümü. AI yok (D-150): öneri, `readiness_check`, "Should I apply?", CV içe aktarma V2.

Kural: **yeni iş mantığı yok, mevcut kurallar yeniden kullanılır.** Kopya oluşturma, bağlama, kilit, Undo ve PDF M4c/M8b'deki route ve servislerle aynı; M9a yeni ekran ve tek yeni servis (`final_check_service`) getirir. **Migration yok.**

Branch: `m9a-apply-flow` (`main`, `41ffb07`). Etiket `v0.9a-m9a`. Onaydan sonra plan issue #82'ye yorum olarak eklenir. Her commit'ten önce Fatih'e sorulur.

## Kararlar (Adım 0'da `docs/DECISIONS.md`'ye; D-151 bu fazda yazılır, yeniler D-164'ten başlar)

- **D-151 · Final check: kural tabanlı kontrol listesi.** Her kontrol `ok`, `warn` ya da `missing` döner; `missing` ve `warn` satırında bir **düzeltme linki** (sayfa + çapa) olur. Kontroller kodla, veritabanındaki alanlardan yapılır (PDF çizilmez):

  | id | Kontrol | Durum | Düzeltme linki |
  |---|---|---|---|
  | `cv_linked` | Bu başvuruya bir CV bağlı | yoksa `missing` | Apply › Prepare (`#prepare`) |
  | `cv_contact` | CV'de ad ve e-posta dolu | eksikse `missing` | **Profil** (`/profile`); satırda not: "Then use Refresh from profile in the CV." (bu alanlar CV editöründe düzenlenmez, profilden gelir, `CVEditForm`) |
  | `cv_content` | CV'de görünen (`hidden_sections`'ta olmayan) en az bir deneyim ya da bir özet var | yoksa `warn` | Özet yoksa CV editörü (`/cvs/{id}?application={app}`); deneyim yoksa profil + aynı not |
  | `letter_linked` | Bir mektup bağlı | yoksa `warn` (mektup isteğe bağlı) | Apply › Prepare |
  | `letter_recipient` | Mektubun `recipient_company`'si başvurunun şirketiyle aynı (büyük/küçük harf, boşluk ve sondaki yaygın hukuki ekler yok sayılır: GmbH, AG, KG, SE, Ltd, Inc, e.U., OG) | farklı ya da boşsa `warn` ("başka şirketin adı kalmış olabilir") | Mektup editörü |
  | `letter_body` | Mektup gövdesi boş değil | boşsa `missing` | Mektup editörü |
  | `language` | CV ve mektup aynı dilde | farklıysa `warn` | Apply › Prepare |
  | `posting_open` | İlanın kapanış tarihi geçmemiş (`posting_closes_at`, kullanıcının saat diliminde) | geçmişse `warn` | Job & details (`?tab=details`) |

  Mektup bağlı değilse mektup kontrolleri (`letter_recipient`, `letter_body`, `language`) listelenmez. Başlıkta özet: "Final check · {ok sayısı} of {toplam} passed". **Kontrol listesi hiçbir şeyi engellemez** (bkz. Soru 1): kullanıcı uyarılara rağmen "I have applied" diyebilir. Kilitli bir belgede düzeltme linki gösterilmez (değiştirilemez), satır sadece bilgi verir.
- **D-164 · Sekmeler.** Başvuru detayında iki sekme: **Apply** (durum `saved` iken) / **Progress** (`applied`, `interview`, `offer`, `rejected`) ve **Job & details**. Sekme adresle seçilir: `/applications/{id}` (varsayılan ilk sekme), `/applications/{id}?tab=details`. Sekmeler düz linktir (`<a>`, `aria-current`), JS gerekmez. `application-changed` ile sayfanın kendini yeniden çekmesi (`hx-get`) bulunduğu sekmenin adresini kullanır, yani değişiklikten sonra sekme kaybolmaz. Sağ panel (Next step, Documents, Timeline) iki sekmede de aynı kalır (SPEC §6.7). Arşivli başvuruda ilk sekme arşivden önceki duruma göre seçilir ve işlemler pasiftir ("Restore it to prepare documents.").
- **D-165 · Apply sekmesi akışı.**
  - **① Prepare documents:** CV ve mektup için ayrı birer satır. Bağlı değilse M8b'deki formlar (dil başına "Copy my master CV (EN)", "Use an existing copy"; mektup için aynısı); bağlıysa "{Başlık} · vN", rozet, **"Preview & edit"** (drawer'ı açar). Aynı route'lar (`POST /applications/{id}/cv`, `…/cover-letter`), aynı toast + Undo.
  - Adım **CV bağlanınca tamamlanmış** sayılır (mektup isteğe bağlı) ve daralır: "✓ Prepare documents · CV v1, cover letter v1" + "Show".
  - **② Final check:** CV bağlanana kadar pasif (soluk, "Prepare your CV first."). Sonra D-151 listesi, altında "Ready? Send it yourself, then come back" kutusu (prototip metni): PDF indirme linkleri ("CV (PDF)", "Cover letter (PDF)") ve **"I have applied · mark as applied"**.
  - "Mark as applied" mevcut `POST /applications/{id}/status` route'una `status=applied` gönderir: durum, kilitler, `ApplicationDocument`'lar, toast + Undo tek yerden (D-159). Sayfa `application-changed` ile yenilenir ve **Progress** görünür.
- **D-166 · Belge paneli (drawer).** "Preview & edit" sağdan açılan bir `<dialog>` açar: hazır ama henüz kullanılmayan `components/drawer.html` makrosu (SPEC §6.0/4; native `<dialog>`, Esc ile kapanır, odak içeride). Önizleme için genişliği 520 px'ten büyütülür (CV A4 önizlemesi sığsın diye, ör. `max-w-[760px]`). İçerik `GET /applications/{id}/drawer?doc=cv|cover_letter` ile htmx'ten gelir: CV / Cover letter sekmeleri (sadece bağlı olanlar), belge adı · vN · rozet, **önizleme** (editördeki önizlemenin aynısı: CV için `pdf_service.cv_context` + şablon, mektup için `preview_context` + `pdf/cover_letter.html`), "Open full editor" (`?application={id}`), "Download PDF". Panel her zaman **salt okunur** (düzenleme tam editörde; panelde düzenleme ve "Edit with assistant" V2). Kilitli belgede "Sent · locked" rozeti ve "This is what you sent." notu.
- **D-167 · Progress görünümü.**
  - **"Applied on {applied_at}"** kartı ve gönderilen belgeler: başvurunun şu anki CV'si ve mektubu, rozetiyle; her birinde "Preview" (drawer) ve "PDF". Kilitli değilse (ör. belge Applied'dan sonra bağlandıysa) M8b'deki "Lock as sent".
  - **"What happens next":** durum `applied` iken: follow-up tarihi (next step'ten, D-099; ertelenmiş ya da elle değişmişse onun tarihi) ve **"I got an interview"** butonu (durum seçicinin yaptığını yapar: `status=interview`, mülakat modalı açılır). `interview`: mülakatın tarihi ve saati (next step'ten); `offer` ve `rejected`: kısa bir cümle. "Draft a follow-up email" V2 (gösterilmez).
  - **"How you applied"** (daraltılmış): timeline'daki ilgili olaylardan üretilir, yeni kayıt yok: belge bağlama olayları ("Cover letter: …"), "Applied" durumu, kilit olayları, tarihleriyle. Final check sonucu saklanmaz (Soru 3).

### Fatih'e sorular
1. **Final check engellesin mi?** Önerim: hayır, sadece uyarır. Gerçek hayatta mektupsuz başvuru da olur, portal başka bir şey isteyebilir; durum seçici zaten her şeye izin veriyor. Tek kural: CV bağlanmadan Final check adımı açılmaz. (Alternatif: `missing` varsa "Mark as applied" pasif.)
2. **"Download PDFs":** (a) **iki ayrı link** "CV (PDF)" ve "Cover letter (PDF)" (önerim: portallar çoğunlukla ayrı dosya istiyor, kod hazır), (b) tek birleşik PDF (CV + mektup, `pypdf` ile), (c) ikisi birden.
3. **"How you applied":** önerim timeline'dan üretmek (yeni kolon yok). Alternatif: "Mark as applied" anında Final check sonucunu ("5 of 6 passed") timeline'a bir olay olarak yazmak. Bu, durum seçici ya da board'dan Applied'a geçişte olmaz.
4. **Kontrol listesi** (D-151 tablosu): eklemek ya da çıkarmak istediğin kontrol var mı? Bilerek dışarıda bıraktıklarım: sayfa sayısı (PDF çizmek gerekir, her açılışta ~1 sn), anahtar kelime ve dil tespiti (AI, V2).

### plan-reviewer (2026-11-23) ve güncellemeler
Sonuç "Needs changes": 1 Blocker, 7 Should, 5 Nit. Hepsi aşağıdaki maddelerle kapandı; bunlar yukarıdaki kararların yerine geçer.
- **Düzeltme linkleri (Blocker):** CV'de ad, e-posta ve deneyim profilden gelir, editörde düzenlenmez. `cv_contact` ve deneyim eksikliği profile gider ve "Refresh from profile" notunu taşır; `cv_content` gizli bölümleri saymaz (tablo güncellendi).
- **Drawer bileşeni:** yeni makro yazılmaz, `components/drawer.html` kullanılır (genişlik parametresi). Açılış için yeni JS yazılmaz: mevcut `data-dialog-load` / `openLoadedDialog` (`app.js:417`) kullanılır, izin listesine `/applications/` eklenir. Böylece 404'te panel kapanır, toast nedeni söyler. Drawer `<dialog>`'u `#application-page`'in **dışında** durur (mülakat modalı gibi), yenilemede kapanmaz.
- **Tekrarlanan id'ler:** CV ve mektup satırları bir makroya taşınır (`id_prefix` ile, `text_field` kalıbı); Prepare adımı ve Documents kartı aynı makroyu farklı önekle çağırır. Test: sayfadaki bütün `id`'ler tekil.
- **Önizleme CSS'i ve `preview_context`:** `detail.html`'in `head_extra`'sına `cv_base.css`, üç şablon CSS'i ve `cover_letter.css` eklenir. Mektubun `preview_context`'i `routers/cover_letters.py:69`'dan `cover_letter_service.preview_context`'e taşınır (router → router import'u olmasın; davranış aynı, mevcut testler geçer).
- **Başkasının başvurusu (kabul):** Apply sekmesinin çağırdığı bütün route'lar (`POST …/cv`, `…/cover-letter`, `…/status`, `GET …/drawer`) için başka kullanıcının başvurusuyla tek, parametreli bir test: 404 ve hiçbir şey değişmiyor.
- **Kenar durumlar (D-164/D-167'ye eklenir, her biri test):**
  - `saved → rejected` (`applied_at` boş): Progress sekmesi "Applied on" kartı olmadan, "You did not apply to this job." cümlesiyle.
  - `applied → saved` (durum seçicisiyle geri): Apply sekmesi döner; kilitli belge "Sent · locked" olarak kalır, kaldırılamaz ve yerine başkası bağlanamaz (M8b `has_sent`); Final check'te o belgenin düzeltme linki yok; "Mark as applied" tekrar çalışır, zaten kilitli belge için yeni kilit ya da ikinci `ApplicationDocument` oluşmaz (test).
  - Arşivli başvuru: sekme arşivden önceki duruma göre; Prepare formları ve "Mark as applied" **gösterilmez**, yerine "Restore it to prepare documents." Route'lar mevcut davranışını korur (bağlama 409).
- **What happens next:** sabit "Follow up" yazılmaz; next step'in kendi başlığı ve tarihi gösterilir (`next_step_service`, elle değiştirilmiş adım da doğru adıyla).
- **Adım sırası (Nit):** 2. ve 3. adımda belge linki "Open" (editör); "Preview & edit" (drawer) 4. adımda gelir.
- **`#prepare` testi (Nit):** GET yerine sayfada `id="prepare"` olduğu doğrulanır.
- **Eski yorum ve daraltma (Nit):** `detail.html:5-8`'deki "No Apply / Progress tab until M7–M9" yorumu güncellenir. Daraltılmış Prepare adımının açık/kapalı hali yenilemede kaybolur: kabul edildi (adım tamamlandıysa kapalı başlar).
- **SPEC kopyası (Nit):** Fatih claude.ai'daki SPEC kopyasını adım 0'ın SPEC commit'inden sonra yeniler (WORKFLOW §2), sonda değil.
- **Soru 2 notu:** (b) ve (c) birleşik PDF için `pypdf`'i çalışma zamanı bağımlılığı yapar (bugün sadece dev, D-058); seçilirse DECISIONS'a gerekçe yazılır. (a) için gerek yok.

## 1. Paketler
Yok.

## 2. Veri modeli
Değişiklik yok, migration yok. `applied_at`, `ApplicationDocument`, `cv_id`, `cover_letter_id`, timeline olayları mevcut.

## 3. Modüller
| Dosya | Değişiklik |
|---|---|
| `app/services/final_check_service.py` (yeni) | `Check` dataclass (`id`, `status`, `title`, `detail`, `fix_url`, `fix_label`) ve `run(session, user, application) -> list[Check]`. Sadece okur; her sorgu `user_id` ile. |
| `app/routers/applications.py` | `detail_context`: `tab`, `first_tab` ("apply"/"progress"), `checks`, `prepare_done`, `progress` (applied_at, follow-up tarihi, "How you applied" olayları). `GET /applications/{id}?tab=…`, yeni `GET /applications/{id}/drawer?doc=…` |
| `app/services/application_service.py` | `history_of(session, application)`: "How you applied" için timeline olaylarını seçen küçük yardımcı |
| Şablonlar | `applications/detail.html` (sekmeler, `hx-get` sekmeli adres), yeni `_apply.html` (iki adım), `_final_check.html`, `_progress.html`, `_drawer.html` (içerik); `components/drawer.html` makrosu kullanılır (genişlik parametresi eklenir) |
| `app/static/js/app.js` | `openLoadedDialog` izin listesine `/applications/` (yeni dinleyici yok) |
| `app/services/cover_letter_service.py` | `preview_context` router'dan buraya taşınır |

## 4. Endpoint'ler (hepsi giriş, `user_id`; başkasının başvurusu 404)
| Metot | Yol | Not |
|---|---|---|
| GET | `/applications/{id}?tab=details` | Mevcut sayfa, sekme parametresi; bilinmeyen değer = ilk sekme |
| GET | `/applications/{id}/drawer?doc=cv\|cover_letter` | Panel içeriği (HTML parçası). Bağlı belge yoksa ya da başkasınınsa 404; `doc` bilinmiyorsa 422. 60/dk |

Diğer bütün işlemler mevcut route'lar (bağlama, kaldırma, durum, Lock as sent, PDF).

## 5. Testler
- **Final check** (`tests/test_final_check.py`, birim): her kontrol için ok / warn / missing; mektup yokken mektup kontrolleri yok; şirket adı karşılaştırması (büyük/küçük harf, boşluk, "GmbH" farkı → warn); kapanış tarihi saat dilimiyle (freezegun); kilitli belgede düzeltme linki yok; başka kullanıcının belgesine işaret eden bozuk satır kullanılmaz.
- **Apply sekmesi** (`tests/test_apply_tab.py`): saved başvuruda Apply, applied'da Progress; `?tab=details`; CV yokken Final check pasif; CV bağlanınca adım daralır; **kopya oluşturma master'a dokunmuyor (kabul)**; her düzeltme linki doğru sayfayı açıyor (kabul: linke GET → 200 ve doğru sayfa); arşivli başvuruda işlemler pasif.
- **Mark as applied (kabul):** butonla Applied → Progress görünüyor, CV ve mektup kilitli, `ApplicationDocument` iki tane; Undo → tekrar Apply, belgeler taslak.
- **Drawer** (`tests/test_document_drawer.py`): CV ve mektup önizlemesi, kilitli belgede "Sent · locked" ve düzenleme yok, bağlı belge yoksa 404, başkasının başvurusu 404 (kabul), girişsiz → login, bilinmeyen `doc` 422, 60/dk.
- **Progress:** "Applied on" tarihi, follow-up tarihi, "I got an interview" → interview ve modal, "How you applied" olayları; interview/offer/rejected metinleri.
- **Erişim matrisi:** yeni GET'ler için girişsiz, başkasının, garip id.
- Sonunda test-writer ve security-reviewer.

## 6. Adımlar (her biri ayrı commit, önce Fatih'e sorulur)
0. `docs: plan M9a` + `docs: SPEC and D-151, D-164…D-167 for M9a` (SPEC v2.1.35: başlık; §6.7 sekmeler, Apply, drawer, Progress; §12 › M9a). Bu commit'ten sonra Fatih claude.ai SPEC kopyasını v2.1.35 ile yeniler.
1. `feat: tabs on the application page` (Apply/Progress | Job & details, sekmeli yenileme; Apply ve Progress şimdilik iskelet).
2. `feat: prepare documents step` (CV ve mektup satırları makroya, mevcut formlar, "Open" linki, adımın daralması).
3. `feat: final check` (servis, kontrol listesi, PDF linkleri, Mark as applied).
4. `feat: document drawer` (`preview_context` servise taşınır, route, panel, önizleme CSS'i; Prepare ve Progress'te "Open" yerine "Preview & edit").
5. `feat: progress view` (Applied on, gönderilenler, What happens next, How you applied).
6. Review ve kapanış: test-writer, security-reviewer, `uv run pytest` (normal ve `TZ=UTC`), ruff, yeşil CI, Chrome'da elle test, `LEARNING_LOG`, CLAUDE.md'de M9a ✓, PR "Closes #82", merge sonrası etiket `v0.9a-m9a`.

## Riskler
- **Sekme ve yenileme:** `application-changed` sonrası `hx-get` sekmesiz adres çekerse kullanıcı Job & details'ten Apply'a atlar. Adres şablonda sekmeyle yazılır; test: details sekmesindeyken durum değişince sekme kalır.
- **Aynı bilgi iki yerde:** Prepare adımı ile sağdaki Documents kartı aynı işlemleri sunar. Bilinçli (SPEC §6.7 kartı istiyor); ikisi aynı route'ları ve aynı alt şablonları kullanır, böylece farklılaşmazlar.
- **Drawer önizlemesi:** CV şablonlarının CSS'i önizleme için yazıldı; dar panelde ölçeklenmesi gerekebilir (editördeki önizleme kabının sınıfları kullanılır).
- **Final check kalitesi:** AI'sız kontroller zayıf (D-150'de kabul edilmiş risk). Liste kısa ve açık tutulur; şirket karşılaştırması hukuki ekleri yok sayar ve sadece `warn` verir (D-158 şirket adını zaten aynen doldurur, fark nadirdir).

## Fatih'in cevapları (2026-11-23)
1. **Final check engellemez:** sadece uyarır; Final check adımı CV bağlanınca açılır (öneri kabul).
2. **Download PDFs:** iki ayrı link, "CV (PDF)" ve "Cover letter (PDF)"; yeni bağımlılık yok (öneri kabul).
3. **How you applied:** timeline olaylarından üretilir, yeni kayıt yok (öneri kabul).
4. **Kontrol listesi:** D-151'deki 8 kontrol olduğu gibi (öneri kabul).
