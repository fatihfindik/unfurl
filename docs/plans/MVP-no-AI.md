# MVP without AI · Teknik plan (Design fazı)

## Context
Fatih (2026-11-20): Anthropic API için şimdilik para ödemek istemiyor (Pro aboneliği API'yi kapsamıyor). Karar: **A — AI'sız MVP**. AI milestone'ları V2'ye ertelenir, ürün AI olmadan tamamlanır, deploy (M13c) ve README (M13d) ile MVP kapanır. AI daha sonra (API anahtarı ya da BYOK ile) geri gelebilir: SPEC'teki AI tasarımı silinmez, "V2 (D-150)" diye işaretlenir.

Bu PR **sadece kapsam**: SPEC, DECISIONS, CLAUDE.md, arayüz metinleri. M8a, M8b, M9a'nın teknik planları kendi Design fazlarında yazılır. Branch `mvp-without-ai` (milestone değil, kapsam işi; WORKFLOW'daki `m{n}-…` kalıbı dışında). GitHub issue açılır, PR onu kapatır.

### Bugünkü durum (envanter)
- **AI kodu yok.** M5 başlamadı: `AIClient`, `FakeAIClient`, `quota_service`, `AIUsage`, BYOK, Settings › AI / Plan, fake door, asistan yok. `app/templates/assistant/`, `app/templates/cover_letters/` boş klasör (silinir).
- **AI'ya dayanan, yaşayan şeyler:** `billing/plans.py:11,17` (`ai_daily_credits`), `.env.example:28-32` (AI env), `ext_api.py:124-127` (`credits_remaining`/`using_byok` = `None`, D-135). Davranış değişmez; yorum "V2 (D-150)" olur.
- **E-posta doğrulaması** bugün e-posta bildirimlerini ve güvenlik e-postalarını açıyor (`notification_service.py:372, :415`, `settings.py:215, :341`): AI'sız da anlamlı, sadece metni değişir.

## Kararlar
- **D-150 · AI'sız MVP.** Fatih (2026-11-20; sorulara cevaplar aşağıda). Ertelenen (V2 › AI): M5 (AI altyapısı, ilan çıkarımı, krediler, BYOK, maliyet raporu), **fake door + Settings › Plan + Settings › AI + "Pro" (krediye dayandığı için)**, M6b, M7, M12a, M12b, M8'in "AI ile oluşturma"sı, M9'un `tailor_cv`, `readiness_check` AI'sı ve `import_cv`'si, **onboarding sihirbazı** (Fatih: V2; "Continue to setup" `/profile`'a gider), **Job & details yapılandırılmış ilan** (Fatih: V2; ilan metni serbest metin kalır), `ai_result` bildirimi. Alternatifler: B sadece BYOK + FakeAIClient (prompt'lar gerçek modelle hiç denenmez), C beklemek. Kabul edilen risk: ürünün ana farkı MVP'de yok; README'de "AI V2'de" diye anlatılır.
- **Yeni MVP sırası:** (bu PR) → **M8a** → **M8b** → **M9a** → M13c → M13d.
  - **M8a — Mektup: model ve editör (AI'sız):** `CoverLetter` modeli, Documents › Cover letters sekmesi (bugün "Coming soon"), CV'lerle aynı master/kopya/kilit kuralları, editör + HTML önizleme, varsayılan mektup dili (D-129), silmede Undo (D-046), dışa aktarma ve hesap silme kapsamı (M13b). Kabul: üç dilde mektup elle yazılıp önizleniyor; master/kopya kuralları CV ile aynı (testler); dışa aktarmada var, hesap silinince gidiyor.
  - **M8b — Mektup: PDF ve başvuru:** DACH PDF şablonu (3 dil), başvuruya bağlama, "applied"da kilit, başvurudan kopya. Kabul: PDF üç dilde düzgün; bağlı mektup applied'da kilitleniyor; kilitli kopya düzenlenemiyor.
  - **M9a — Apply sekmesi (AI'sız, 2 adım; Fatih):** (1) **Prepare documents:** master CV/mektuptan bu iş için kopya oluştur ve bağla (AI önerisi yok); (2) **Final check:** kural tabanlı kontrol listesi, "Download PDFs", "I have applied · mark as applied"; ardından **Progress görünümü** ("Draft a follow-up email" ve uyum açığından öğrenme hedefi V2). Belge paneli (drawer: önizleme, "Open full editor", PDF). Kabul: kopya master'a dokunmadan oluşur (test); kontrol listesi eksikleri gösterir ve her biri düzeltme sayfasına götürür; mark as applied → Progress görünümü + belgeler kilitli.
  - **D-151 · Kural tabanlı Final check** (M9a Design fazında yazılır): `readiness_check` AI'sı yerine sabit kurallar (CV bağlı mı, mektup bağlı mı / isteğe bağlı, iletişim bilgileri dolu mu, belge dili ilan diliyle uyumlu mu). Bu PR'da sadece SPEC'e yer tutucu olarak girer.
- **M13c:** kabul ("CV ve mektup indirmeye kadar") aynen kalır; başlıktaki sıra "AI'dan sonra" → "M9a'dan sonra". Deploy maliyeti (Render ücretsiz katmanı, özellikle Postgres) M13c Design fazında ayrıca konuşulur.

## Adımlar (bu PR)
Her adım ayrı commit (Conventional Commits).

1. **`docs: plan MVP without AI`** — bu plan.
2. **`docs: SPEC and D-150 for the MVP without AI`** — SPEC v2.1.30, DECISIONS.
   - **SPEC kontrol listesi** (her yerde metin silinmez; "V2 (D-150)" notu ya da sütun değişikliği):
     - §1.2:67-74 ürün tanımı: AI cümleleri "V2" olarak.
     - §1.6:104-128 V1/V2 tablosu: Onboarding (AI import, krediler, sihirbaz), CV (tailoring), Cover letter (AI taslağı), Apply flow (Should I apply, AI öneriler, readiness), Chrome extension (AI doldurma, popup fit), Fit analysis, Notifications (`ai_result`), 5 Assistant satırı, Settings (AI, Plan) → V2 sütununa; Cover letter ve Apply flow'un AI'sız kısmı V1'de kalır.
     - §2 tablo (AI maliyet modeli, krediler, BYOK, Pro) → "V2 (D-150)".
     - §5.7–5.9c (AI tabloları: `AIUsage`, `FitAnalysis`, asistan) → başlıkta "V2 (D-150)"; `User.anthropic_api_key_encrypted` satırı V2.
     - §6 giriş (:546 kredi kartı), §6.0/1 (:549), §6.1 (:575 "Continue to setup" → `/profile`; `require_verified` "M7'den itibaren" notu; kayıttaki "AI özellikleri doğrulama olmadan kilitli" → "e-posta bildirimleri doğrulamadan sonra"), §6.5 (:601, :608), §6.6 (:622), §6.7 (:628, :632-633, :638-643 → Apply sekmesi 2 adım, Progress'te AI satırları V2), §6.9 (:657, :659), §6.10 (:666-667), §6.11 (:678 `ai_result`), §6.13 kural 5.
     - §7.4, §7.6, §8 (extract/fit/assistant endpoint'leri) → "V2 (D-150)".
     - §9 AI → bölüm başına "V2 (D-150)".
     - §11 env (AI değişkenleri V2), §13 AI testleri ve eval → V2, §14:1307, §15:1313 (V2 listesine "AI" maddesi), §17.3 (fake door/Pro) → V2.
     - §12: M4b:1165 ("yapılandırılmış ilan M5'te" → V2), M5/M6b/M7/M12a/M12b başına "V2 (D-150)", M6:1202 ve M13c:1273 sıra notları, M8 → M8a + M8b, M9 → M9a (kabul kriterleriyle, yukarıdaki gibi).
   - **DECISIONS:** D-150 (yukarıda). "Güncelleme: D-150" notu: D-088 (:476-480), D-123, D-124 (:662, Settings › Plan/AI M5'teydi), D-135 (:724).
3. **`fix: drop AI promises from the UI`** — sadece metin, davranış değişmez:
   - Doğrulama: `components/verify_banner.html`, `auth/verify_email.html`, `auth/check_inbox.html`, `routers/auth.py:331` → "…so we can send you reminders and account emails." gibi.
   - `auth/layout.html` maddeleri: "Know your fit before you apply", "Turn your gaps into a learning plan" → AI'sız özellikler (ör. "Track every application on one board", "Plan what you learn next").
   - `profile/_strength.html` ("better fit scores and cover letters" → "better CVs and cover letters"), `profile/index.html` alt başlığı ("fit analysis" çıkar), `profile/_personal_form.html` ("never sent to the AI" çıkar; "hidden on CVs by default" kalır).
   - `legal/terms.html:17` ("scores and (later) AI suggestions" → "Reminders are help, not advice."), `legal/privacy.html:28` ("Unfurl has no AI features yet") ve `:60-61` ("What is never sent to AI" bölümü V2'ye kadar kaldırılır, şablonda yorum olarak not).
   - `extension/README.md:46` ("come with M6b" → "V2 (D-150)").
   - Yorumlar: `routers/auth.py:255`, `services/auth_service.py:364`, `legal/privacy.html:3`, `billing/plans.py`, `.env.example` (AI satırları "# V2 (D-150)").
   - Boş `app/templates/assistant/`, `app/templates/cover_letters/` silinir (M8a kendi klasörünü açar).
4. **`test: no AI wording on the sign-up pages`** — mevcut testlerdeki beklenen metinler güncellenir; verify banner, verify sayfası, check inbox, `/verify-email` POST sonucu (`auth.py:331`) ve giriş/kayıt sayfasında "AI" geçmediğini kontrol eden test.
5. **`docs: milestones after D-150`** — CLAUDE.md: M5, M6b, M7, M12a, M12b → "V2 (D-150)"; M8 → M8a + M8b, M9 → M9a; "M13c Deploy (after M9a)"; "M6b … V2".

## Definition of Done
- [ ] `uv run pytest` (normal ve `TZ=UTC`), `ruff check`, `ruff format --check` temiz
- [ ] Elle kontrol: kayıt → check inbox → verify banner → verify sayfası → giriş sayfası ve profil; ekran görüntüleri PR'da
- [ ] security-reviewer: davranış değişmediği için atlanır (sadece metin ve doküman); PR'da gerekçesiyle yazılır
- [ ] GitHub: bu iş için issue (`Closes #`); M8a, M8b, M9a için yeni issue'lar; AI issue'larına (#64 E2 vb.) `v2` etiketi
- [ ] Fatih: claude.ai SPEC kopyasını v2.1.30 ile yeniler
- LEARNING_LOG: milestone değil, eklenmez

## Fatih'in cevapları (2026-11-20)
- Onboarding sihirbazı: **V2**.
- Apply sekmesi: **2 adım** (Prepare documents → Final check).
- M8: **ikiye bölünür** (M8a, M8b).
- Yapılandırılmış ilan: **V2**.
- Fake door / Settings › Plan / Pro: krediye dayandığı için **V2** (Claude karar verdi; reviewer sorusu 2).
