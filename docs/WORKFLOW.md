# Unfurl — Çalışma Şeklimiz (İteratif SDLC)

> Bu doküman projenin **nasıl** geliştirildiğini anlatır. **Ne** geliştirildiği `docs/SPEC.md`'dedir.
> Claude Code dahil tüm AI ajanları bu süreci takip eder.

---

## 1. Roller

| Kim | Rol | Sorumlulukları |
|---|---|---|
| **Fatih** | Product owner, son karar verici, reviewer, test eden kullanıcı | Kapsamı onaylar, planı onaylar, PR'ları okur ve merge eder, kabul kriterlerini elle test eder |
| **Claude (claude.ai "Unfurl" projesi)** | Tartışma ortağı, öğretmen | Sadece karar tartışması, özet ve retro; kavramları öğretme. Dosya yazmaz: SPEC ve WORKFLOW kopyaları repodaki dosyaların aynasıdır. |
| **Claude Code (terminal)** | Planlayan ve geliştiren | Plan (SPEC'teki milestone bölümü, milestone issue'su), Design (teknik plan), `plan-reviewer`'ı çalıştırma, Build, Test, SPEC/WORKFLOW/DECISIONS güncellemeleri, commit'ler, PR |
| **Subagent'lar** (`.claude/agents/`) | Bağımsız kontrol | `plan-reviewer`: teknik planı Fatih onaylamadan önce SPEC, DECISIONS ve WORKFLOW'a göre inceler. `security-reviewer`: SPEC 10.1 kontrol listesine göre inceleme. `test-writer`: yetki ve hata durumu testleri. |

---

## 2. Bir milestone'un döngüsü

Her milestone (SPEC Bölüm 12) küçük bir SDLC döngüsüdür. Bir milestone **1-2 hafta** sürer. Daha uzun sürecekse Plan fazında bölünür (ör. M3a, M3b).

| # | Faz | Ne yapılır | Nerede | Çıktı (bitti sayılması için) |
|---|---|---|---|---|
| 1 | **Plan** | Kapsam ve kabul kriterleri netleşir, belirsizlikler kapatılır, V1/V2 sınırı kontrol edilir. Bir karar tartışılmak istenirse claude.ai'de konuşulur, sonuç terminalde dosyaya yazılır. | Claude Code (terminal) | SPEC'te net bir M{n} bölümü + GitHub'da milestone issue |
| 2 | **Design** | Veri modeli, endpoint'ler, ekran akışı, testler ve riskler planlanır. Gerekirse prototip güncellenir. **Kod yazılmaz.** Plan `plan-reviewer`'dan geçer; Fatih raporu okur ve sorularını cevaplar. | Claude Code (plan modu) + `plan-reviewer` | `docs/plans/M{n}.md`, Blocker'ı kalmamış `plan-reviewer` raporu, Fatih'in onayı (plan issue'ya yorum olarak) |
| 3 | **Build** | Küçük adımlarla kod. Her adım çalışır durumda kalır ve ayrı commit olur. Her commit'ten önce diff gösterilir ve Fatih'in onayı beklenir. | Claude Code | Feature branch'te commit'ler |
| 4 | **Test** | Otomatik testler + lint, ardından kabul kriterlerinin elle testi | Claude Code + Fatih | Yeşil CI, işaretlenmiş kabul kriterleri |
| 5 | **Review** | Fatih kodu okur, `security-reviewer` çalışır, anlaşılmayan yerler sorulur ve açıklanır | GitHub PR + Claude Code | Onaylanmış ve merge edilmiş PR |
| 6 | **Release** | `main`'e merge ve etiket. M13'ten sonra canlıya deploy. | GitHub (sonra Render) | Etiket: `v0.{n}-m{n}` (ör. `v0.3-m3`) |
| 7 | **Retro** | Ne öğrendim, ne zorladı, süreçte veya SPEC'te ne değişmeli? Tartışma claude.ai'de, dosya değişiklikleri terminalde. | `docs/LEARNING_LOG.md` + claude.ai projesi | 3-5 madde. Gerekirse SPEC/WORKFLOW güncellemesi. |

**claude.ai kopyası:** Repo tek doğruluk kaynağıdır; claude.ai projesindeki SPEC.md ve WORKFLOW.md onun aynasıdır. Bu iki dosyayı değiştiren bir commit yapıldığı anda Fatih claude.ai'deki eski kopyayı siler ve yenisini yükler (merge'ü beklemez).

---

## 3. Kurallar

### Definition of Ready (başlamak için)
- [ ] Milestone'un kapsamı SPEC'te yazılı
- [ ] Kabul kriterleri ölçülebilir şekilde yazılı
- [ ] Bağımlı olduğu milestone'lar bitmiş
- [ ] Açık soru kalmamış (kalan varsa `DECISIONS.md`'ye varsayım olarak yazılmış)

### Definition of Done (bitti saymak için)
- [ ] Tüm testler geçiyor (`uv run pytest`)
- [ ] Lint ve format temiz (`uv run ruff check . && uv run ruff format --check .`)
- [ ] CI yeşil
- [ ] Kabul kriterlerini Fatih elle denedi
- [ ] `security-reviewer` çalıştı, bulgular kapatıldı veya issue'ya dönüştü
- [ ] PR merge edildi ve etiket atıldı
- [ ] `CLAUDE.md`'deki milestone listesi işaretlendi
- [ ] `docs/LEARNING_LOG.md` güncellendi
- [ ] SPEC.md veya WORKFLOW.md değiştiyse claude.ai projesindeki kopya, değişiklik commit'lendiği anda yenilendi

### Tek doğruluk kaynağı
- Ürün kararları → `docs/SPEC.md` (değişiklik notu başlıktaki versiyon satırına)
- Mimari kararlar ve varsayımlar → `docs/DECISIONS.md` (tarih, karar, gerekçe, alternatifler)
- Sohbette alınıp bir dosyaya yazılmayan karar **geçersiz sayılır**.

### Kapsam kontrolü
- Yeni fikirler hemen kodlanmaz. Önce GitHub'da `idea` etiketiyle issue olur.
- Plan fazında etiketlenir: `v1` (MVP'ye girer), `v2` (sonraya) veya `wontfix`.
- Bir milestone'un ortasında kapsam büyümez. Gerekirse yeni bir milestone açılır.

### Git düzeni
- `main` her zaman çalışır durumdadır. Doğrudan `main`'e commit atılmaz.
- Milestone başına bir branch: `m{n}-{kısa-ad}` (ör. `m3-cv-builder`).
- Commit mesajları İngilizce ve [Conventional Commits](https://www.conventionalcommits.org/) formatında: `feat: add master CV versioning`, `fix: ...`, `test: ...`, `docs: ...`, `refactor: ...`
- Milestone başına bir PR. Şablon: `.github/pull_request_template.md`.

### Takip
- **GitHub Issues:** her milestone bir issue (kabul kriterleri checklist olarak), ayrıca bug ve fikirler.
- **GitHub Project panosu:** Backlog → Ready → In progress → Review → Done
- Etiketler: `milestone`, `bug`, `idea`, `v1`, `v2`, `tech-debt`, `learning`

---

## 4. Ritim

| Ne zaman | Ne | Nerede |
|---|---|---|
| Haftanın başı | Bu haftanın milestone'u: Plan + Design + `plan-reviewer` (tartışılacak karar varsa önce claude.ai) | Claude Code |
| Hafta içi | Build + Test (küçük adımlar, sık commit) | Claude Code |
| Milestone sonu | Review + Release | GitHub PR + Claude Code |
| Haftanın sonu | Kısa retro (15 dk) | `LEARNING_LOG.md` + claude.ai projesi |

---

## 5. Claude Code ile çalışırken

Her milestone için şu sırayla ilerle:

1. `CLAUDE.md`, `docs/SPEC.md` (ilgili bölümler) ve `docs/WORKFLOW.md` dosyalarını oku.
2. **Plan:** SPEC §12'deki milestone bölümünü ve milestone issue'sunu hazırla ya da kontrol et; açık soruları Fatih'e sor. SPEC değişirse Fatih'e claude.ai kopyasını yenilemesini hatırlat.
3. **Design:** Plan modunda teknik plan çıkar (dosyalar, modeller, endpoint'ler, testler, riskler) ve `docs/plans/M{n}.md`'ye yaz. Kod yazma.
4. **plan-reviewer:** Planı `plan-reviewer` subagent'ına incelet. Raporu ve soruları Fatih'e ver; Blocker'lar kapanınca Fatih planı onaylar. Plan issue'ya yorum olarak eklenir.
5. **Build:** Branch aç, küçük adımlarla uygula, her adımdan sonra testleri ve ruff'ı çalıştır. Her commit'ten önce diff'i göster ve Fatih'in onayını bekle (auto modda da). Push ve PR da onayla.
6. Bitince Definition of Done listesini tek tek kontrol et ve PR açıklamasını şablona göre yaz.
7. `security-reviewer` subagent'ını çalıştır.
8. `docs/LEARNING_LOG.md` için Fatih'in maddelerini al (kendi cümleleriyle) ve ekle.

**Durdur ve sor:** SPEC'te olmayan bir karar gerekiyorsa, bir bağımlılık eklemek gerekiyorsa veya kapsam büyüyorsa devam etme. Fatih'e sor ya da varsayımını `DECISIONS.md`'ye yazıp açıkça belirt.

---

## 6. Retro şablonu (`docs/LEARNING_LOG.md`)

```markdown
## M{n} — {title} ({date})
**What I learned**
- ...
**What was hard**
- ...
**What I will do differently**
- ...
**SPEC / WORKFLOW changes?**
- Yes/No: ...
```
