# M6a düzeltme — LinkedIn ve karriere.at'te alanlar · Teknik plan (Design fazı)

## Context
M6a merge edildi (PR #65), elle test 2026-11-20'de Fatih'le yapıldı (`extension/TESTING.md`). 20 adımdan 16'sı geçti, 2'si atlandı (8, 12), 2'si başarısız:
- **Adım 5, LinkedIn:** Sadece pozisyon geldi, o da sayfa başlığının tamamı ("Senior Growth Product Manager | Soda | LinkedIn"); şirket, konum, maaş ve çalışma şekli boş. LinkedIn'in yeni sayfasında `site_hints.js`'teki sınıfların hiçbiri yok: sınıflar rastgele (`bsjmg7`), `h1` yok, JSON-LD yok.
- **Adım 7, karriere.at arama paneli** (`/jobs?keywords=…#10035685`): Şirket "karriere.at" (og:site_name), konum boş. Kayıtlı link `https://karriere.at/jobs`: panelden kaydedilen **her** ilan aynı linke sahip olur, ikinci ilanda yanlış "Already saved" çıkar. İlanın kendi sayfası (`/jobs/10035685`) doğru (JSON-LD var).

Fatih'in isteği: **alanlar doğru dolsun, hem ilan detay sayfasında hem arama sayfasında.**

Sayfalar Claude in Chrome ile incelendi (2026-11-20):
- **LinkedIn**, detay (`/jobs/view/{id}`) ve arama (`/jobs/search-results/?currentJobId={id}`) aynı üst kartı gösteriyor. Kalıcı tutamaklar:
  - Şirket linki `a[aria-label^="Company, "]` (aria-label `Company, Soda.`; sayfada tek).
  - Üst kart: şirket linkinden yukarı çıkınca "Save the job" / "Apply" butonunu içeren ilk kap (3 seviye). Satırları: `Şirket`, `Pozisyon`, `Konum · 1 week ago · 46 applicants`, … , maaş (`120K EUR/yr - 140K EUR/yr`, varsa), `Remote|Hybrid|On-site`, `Full-time`, `Apply`, `Save`.
  - `document.title`: `Pozisyon | Şirket | LinkedIn` (aramada seçili ilana göre güncelleniyor).
  - Açıklama: ilk `[data-testid="expandable-text-box"]` (ikincisi şirket tanıtımı).
  - Bu mantık 7 ilanda denendi (detay + arama; maaşlı/maaşsız; Remote/Hybrid/On-site): hepsinde doğru.
- **karriere.at** arama paneli: içerik JavaScript ile sonradan yükleniyor, panelde JSON-LD yok, `m-jobHeader__*` sınıflarının çoğu yok. İlan numarası adreste `#10035685`. Detay sayfası `/jobs/{id}` aynı origin'de ve tam JSON-LD veriyor (şirket, konum, maaş, son gün, açıklama).

### Kabul kriterleri
- LinkedIn detay ve arama sayfasında: pozisyon (sadece pozisyon), şirket, konum, çalışma şekli, varsa maaş dolu; açıklama ilanın metni.
- karriere.at detay ve arama sayfasında: pozisyon, şirket, konum, varsa maaş ve son gün dolu; kaydedilen link ve "Already saved" kontrolü `karriere.at/jobs/{id}`.
- Sayfa başlığına düşülürse sonundaki " | LinkedIn" / " | karriere.at" gibi site adı atılır.
- StepStone ve diğer siteler bozulmaz (Adım 6 tekrar).
- İş formundaki alanlarda Chrome'un `autocomplete` uyarısı yok.

## Kararlar
- **D-144 · Eklentide LinkedIn ve karriere.at okuma (Fatih onayladı 2026-11-20: karriere.at isteği evet, İngilizce + Almanca LinkedIn, eski satırlar için migration yok):**
  - `extract.js`'e site okuyucuları (`siteReaders`): seçici listesiyle anlatılamayan siteler için küçük fonksiyonlar. Öncelik sırası D-139'daki gibi: JSON-LD → site okuyucusu / seçiciler → sayfa başlığı. `site_hints.js` veri olarak kalır; LinkedIn'in eski sınıfları yedek olarak durur (çıkış yapmış "guest" sayfası hâlâ onları kullanıyor olabilir).
  - **LinkedIn okuyucusu:** şirket aria-label'dan (`Company, ` ve sondaki `.` atılır); üst kart şirket linkinden en fazla 6 seviye yukarı, Save/Apply butonunu içeren ilk kap; pozisyon `document.title`'dan: baştaki bildirim sayısı `(3) ` atılır, sondaki ` | LinkedIn` ve ` | <aria-label'daki şirket>` atılır (pozisyonda ` | ` olabilir, ilk parçayı almak onu keserdi); başlıktaki şirket aria-label'la uyuşmazsa (arama sayfasında başlık geç güncellenmiş olabilir) kartta şirketten sonraki satır; konum pozisyondan sonraki satırın ` · `'dan önceki kısmı; maaş para birimi + rakam içeren kısa satır; çalışma şekli `Remote`→`remote`, `Hybrid`→`hybrid`, `On-site`→`onsite`; açıklama ilk `expandable-text-box`. Bir şey bulunmazsa sessizce boş kalır. İngilizce ve Almanca LinkedIn arayüzü (Fatih): etiketlerin Almanca karşılıkları da (`Unternehmen`, `Vor Ort`, `Speichern`, `Bewerben`…); şirket linki etiketle bulunamazsa üst kısımdaki ilk `/company/` linki.
  - **karriere.at arama sayfası:** sadece host tam `karriere.at` ya da `www.karriere.at` iken, path'in son parçası rakam değilse ve fragment `^#(\d{1,12})$` ise. Eklenti aynı siteden `/jobs/<id>`'yi okur:
    - URL `new URL("/jobs/" + id, location.origin)` (MV3'te content script'in isteği sayfanın origin'inden gider: aynı origin, CORS yok).
    - `fetch(..., { credentials: "omit", redirect: "error" })`: kullanıcının karriere.at çerezleri gönderilmez (§7.2), yönlendirme izlenmez; ayrıca `response.url` aynı origin ve aynı path olmalı.
    - `response.ok` ve `content-type` `text/html` olmalı; 5 sn `AbortController`; en fazla 2 MB, `Content-Length` ve akış okuyucusuyla (önce `text()` çağırmadan).
    - HTML `DOMParser` ile ayrıştırılır (script çalışmaz); `jobPosting()` bu belgeye, aynı 10 script / 200.000 karakter sınırlarıyla uygulanır.
    - Hepsi try/catch içinde: hata olursa extract.js o ana kadar bulduğuyla sonuçlanır, popup alanları atmaz. Bekleme süresince popup'ın "loading" görünümü durur. Bulunan `url` `https://www.karriere.at/jobs/<id>` olur; popup bunu "Already saved" kontrolünde ve kayıtta kullanır. Okuma başarısız olursa: listedeki o ilanın satırından şirket (`.m-jobsListItem__companyName`), link yine `/jobs/<id>`.
    - Gizlilik (§7.2): istek, kullanıcının zaten açık olduğu siteye ve sadece tıklamadan sonra gidiyor; çerez gönderilmez; Unfurl'e giden veri değişmez. `extract.js` hâlâ Unfurl'ü çağırmaz.
  - **Sunucu:** `url_service.canonical_url` karriere.at'te: path'in son parçası rakam değilse ve fragment 1–12 rakamsa → `https://karriere.at/jobs/<fragment>` (arama `/jobs?keywords=…#123` ve `/jobs/python/wien#123` dahil). Detay path'i (`/jobs/123#456`) her zaman kazanır. Rakamsız fragment eskisi gibi atılır. Aynı kural extract.js'te.
  - **Eski satırlar:** Daha önce panelden kaydedilmiş satırların `canonical_url`'i `https://karriere.at/jobs` kalır (D-138: sadece create/update hesaplar). Uygulama yayında değil (M13c), sadece geliştirme verisi: veri migration'ı yok (Fatih onayladı; test kaydını kendisi siler).
  - **SPEC (bu PR'da):** §7.2'ye bir cümle (tıklamadan sonra, aynı iş sitesinde, ilanın kendi sayfasına tek bir çerezsiz GET; Unfurl'e giden veri değişmez); §7.4/§7.5'e site okuyucuları; §8.5'e karriere.at fragment kuralı; sürüm satırı. D-139'a "Güncelleme: D-144" notu. Gizlilik sayfası ve `extension/README.md` aynı cümleyle.
  - **Sayfa başlığı yedeği:** son ` | <site>` parçası atılır; LinkedIn'de `Pozisyon | Şirket` ise ilk parça.
  - `extract.js` async olur (`executeScript` Promise'in sonucunu bekler).
  - Popup'taki iş alanlarına `autocomplete="off"`.
- **Alternatifler:** LinkedIn'in rastgele sınıflarını seçici olarak yazmak (her yayında kırılır); karriere.at panelinin DOM'unu okumak (sonradan yükleniyor, yapısı belirsiz, otomasyonla incelenemedi); eklentiye karriere.at için `host_permissions` verip popup'tan okumak (gereksiz izin; aynı origin'den `fetch` yeterli); arama sayfalarında "ilanı açın" demek (Fatih arama sayfasında da çalışmasını istedi).

## Testler
- `tests/test_url_service.py`: karriere.at `/jobs?keywords=…#10035685` ve `/jobs/python/wien#10035685` → `https://karriere.at/jobs/10035685`; `/jobs#abc` → `https://karriere.at/jobs`; `/jobs/123#456` → `/jobs/123`; 13 rakamlı fragment atılır. LinkedIn `/jobs/search-results/?currentJobId=…` → `/jobs/view/…` (regresyon).
- `tests/test_ext_api.py`: farklı `#id`'li iki panel linki eşleşmez; panel linkiyle kaydedilen ilan detay linkiyle "Already saved" ve tersi.
- Eklenti JS'inin otomatik testi yok (SPEC §13). `tests/test_extension_manifest.py` izinlerin değişmediğini zaten kontrol ediyor (karriere.at için yeni izin yok).
- Elle test (Fatih, `extension/TESTING.md` güncellenir): LinkedIn detay + arama (yeni adım), karriere.at detay + arama (yeni adım), StepStone tekrar; Almanca LinkedIn arayüzü.

## Adımlar
| # | Adım | Commit |
|---|---|---|
| 0 | D-144, D-139 notu, SPEC §7.2/§7.4/§7.5/§8.5 + sürüm, plan | `docs: plan extension site fixes` |
| 1 | `url_service` karriere.at fragment + testler | `fix: karriere.at search links point to the job` |
| 2 | `extract.js` LinkedIn okuyucusu, başlık temizliği, async | `fix: read linkedin's new job page` |
| 3 | karriere.at arama sayfası (fetch + yedek), popup `autocomplete`, TESTING.md | `fix: read karriere.at jobs from the search page` |
| 4 | security-reviewer; PR | |

### Definition of Done
- [ ] ruff, tam test takımı (`TZ=UTC` ile ve onsuz)
- [ ] security-reviewer (özellikle karriere.at `fetch`'i)
- [ ] Fatih'in elle testi: LinkedIn detay + arama, karriere.at detay + arama, StepStone
- [ ] Bug issue (iki başarısız TESTING.md adımı), PR `Closes #…`; CI yeşil
- [ ] SPEC değişti: Fatih claude.ai kopyasını yeniler
- [ ] LEARNING_LOG: M6a'nın maddelerine eklenir (Fatih'in cümleleriyle)
- [ ] Merge sonrası `v0.6a-m6a` tag'i (M6a'nın kabulü bu testle tamamlanır)
