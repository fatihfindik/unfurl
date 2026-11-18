# M13 — bölme önerisi (Plan fazı, gece taslağı)

> **Durum:** **Fatih onayladı (2026-11-18).** Bölme ve önerilen cevapların hepsi kabul edildi; soru 6'da öneri (a): Claude taslak yazar, Fatih bir uzmana inceletir. Soru 7'nin cevabını Claude verdi (D-124). SPEC §12 v2.1.20'de güncellendi (D-123, D-124). Not: #46 sonradan D-122 numarasını aldığı için bölme D-123, Settings › Account D-124 oldu.

## Neden bölmek
SPEC §12 M13 dört farklı işi bir araya koyuyor: GDPR, cilalama, deploy ve portföy README'si. Tek milestone WORKFLOW'daki 1–2 haftalık sınırı aşar (D-024, D-098 gibi). Bir kısmı deploy olmadan yapılabilir. M11b'den sonraki sıra #39, sonra M13'ün deploy dışı parçalarıydı (D-088). #39 ve #10 (güvenlik header'ları) bu gece bitti; #15, #16 ve #22 de.

## Öneri

### M13a — Cilalama: hata sayfaları, boş durumlar, yükleme göstergeleri
- **Hata sayfaları:** 404 ve 500, tam sayfa ve htmx için ayrı. Stack trace yok (`DEBUG=false` ile test). 500 yanıtı da güvenlik header'larını alır (D-120'deki kabul edilen sınırlama kapanır).
- **Boş durumlar:** her listede; çoğu zaten var, eksikler tamamlanır.
- **Yükleme göstergesi:** htmx isteklerinde `.htmx-indicator` (input.css'te hazır) ve çift tıklamaya karşı `hx-disabled-elt`.
- **Önceki kararlardan M13'e bırakılanlar:**
  - Çıkışta `session_version` artar (D-105 notu).
  - #46'da karar: duraklatılmış hedefin görevleri hatırlatmada kalsın mı (D-119).
- **Kabul:**
  - Bilinmeyen bir adres ve bir sunucu hatası, uygulamanın kendi sayfasını header'larla gösterir; htmx isteğinde toast çıkar.
  - Her listenin boş durumu var.
  - Çıkıştan sonra eski oturum çerezi işe yaramaz.

### M13b — GDPR: yasal sayfalar, veri dışa aktarma, hesap silme, Settings › Account
- **Yasal sayfalar:** gizlilik sayfası (alt işleyenler, çerezler; tema çerezi dahil), şartlar, Impressum. Şu an yer tutucular (`legal/layout.html` "TODO: legal review"). Hosting ve e-posta sağlayıcısının gerçek adları M13c'de doldurulur.
- **Export my data:** tek bir JSON dosyası.
  - İçerik: hesap, profil, CV'ler ve versiyonları, başvurular ve eventleri, Learning, bildirimler, tercihler.
  - Hariç: şifre hash'i, token'lar, `DeletedItem`.
  - Bir test, `user_id`'li her tablonun ya dışa aktarmada ya da açık bir hariç listesinde olduğunu kontrol eder. AI milestone'larının tabloları böylece unutulmaz.
- **Delete my account:**
  - Şifre onayıyla: tüm satırlar cascade ile silinir, fotoğraf dosyaları da silinir, oturum kapanır, onay e-postası gider.
  - Cascade Postgres'te test edilir (D-034'ten beri sadece SQLite'ta test edildi).
- **Settings › Account (SPEC §6.9; §12'de sahibi yoktu, kapsama ekleniyor, D-124):** ad ve şifre değiştirme. Şifre değişince diğer oturumlar kapanır (`session_version`, D-021). E-posta değiştirme ayrı bir PR (issue açılır): doğrulama akışını ve token'ları (D-027) değiştirir.
- **`M13b.md`'de cevaplanacak açık sorular:**
  - Silmedeki şifre kontrolüne rate limit ve lockout
  - CSRF
  - Dışa aktarmada `no-store` ve boyut
  - Silmeden önce e-posta adresini saklamak
  - Doğrulanmamış adrese onay e-postası gitsin mi
  - Zamanlayıcı çalışırken silinen kullanıcı
  - `AIUsage`, `WaitlistSignup`, `UpgradePromptEvent` ve profil fotoğrafı dışa aktarılsın mı
- **Kabul:**
  - Dışa aktarma, hariç listesindekiler dışında kullanıcının her tablosunu içeriyor (test).
  - Hesap silme Postgres'te bütün satırları ve fotoğraf dosyasını siliyor, oturumu kapatıyor ve onay e-postasını gönderiyor (outbox testi).
  - Yasal sayfalarda gerçek metin var.

### M13c — Deploy
- **Altyapı:** Render (Docker, Frankfurt, Postgres) ve production env.
- **Güvenlik ve ağ:**
  - HSTS
  - Proxy header'ları ve `APP_BASE_URL` kontrolü (#18)
  - `List-Unsubscribe` header'ı
- **Zamanlayıcı:** tek instance'ta çalışır. Zamanlayıcı sorgusu SQL'de tarih aralığıyla daraltılır (D-073 notu).
- **Tek instance olduğu sürece** #17 açık kalır ve belgelenmiş bir sınırlama olarak durur.
- **Kontroller:** yasal sayfalarda gerçek sağlayıcı adları; SPEC §10.1 kontrol listesi madde madde işaretlenir.
- **Eklenti:** prod URL (M6'ya bağlı).
- **Kabul (SPEC'teki):** Canlı URL'de kayıttan ilan kaydına, CV ve mektup indirmeye kadar tüm akış çalışıyor. Mektup M8'e bağlı olduğundan M13c AI milestone'larından sonra gelir (D-088).

### M13d — Portföy README'si
Ekran görüntüleri, mimari şema, "What I learned". Canlı URL gerektiği için M13c'nin son adımı da olabilir. **Kabul:** README'de canlı URL, şema ve ekran görüntüleri var.

## Açık sorular (Fatih için, Claude'un ve plan-reviewer'ın önerisiyle)
1. **Bölme uygun mu?** Öneri: evet, yukarıdaki dört parça.
2. **Dışa aktarmada henüz olmayan veriler** (mektuplar, fit analizleri, sohbetler)? Öneri: M13b var olanı aktarır; otomatik tablo testi yenileri yakalar.
3. **Hesap silmede Undo?** Öneri: hayır. Şifre onayı ve açık bir uyarı yeterli; `DeletedItem` da cascade ile gider (D-086).
4. **Settings › Account M13b'de mi?** Öneri: ad ve şifre M13b'de (ayrı karar, D-124), e-posta değiştirme ayrı bir PR.
5. **Sıra:** Öneri: M13a → M13b → AI milestone'ları (API anahtarı gelince) → M6 → M13c → M13d. M13c'den önce bir Postgres test çalıştırması (CI job ya da `docker compose`); Postgres'e özel kod (D-121'deki `FOR UPDATE`, cascade) henüz orada test edilmedi.
6. **Yasal metinleri kim yazacak?** Fatih mi yazar, yoksa Claude taslak çıkarıp Fatih mi inceletir? Bu özellikle Avusturya'daki Impressum için önemli.
7. **Settings'in Documents (varsayılan belge dili) ve Plan bölümleri** hangi milestone'a ait? SPEC §6.9'da var, §12'de sahipleri yok.

## Bir sonraki adım
Fatih onaylarsa: SPEC §12'de M13 bölümü dörde bölünür (SPEC sürümü artar, D-123 bölme, D-124 Settings bölümleri), milestone issue'ları açılır ve M13a için teknik plan (`docs/plans/M13a.md`) yazılıp plan-reviewer'a verilir.
