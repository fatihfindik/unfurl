# Unfurl — Learning Log

> Her milestone sonunda 3-5 madde (WORKFLOW §6). Kendi cümlelerimle.

---

## M0 — Proje iskeleti (2026-11-10)

**Öğrendiklerim**
- Git akışı: Branch açıyoruz, kod yazıp kendi bilgisayarımda commit ediyoruz, push ile GitHub'a gönderiyoruz. PR açıyoruz: burada CI testleri çalışıyor ve ben review ediyorum. Sonra main'e merge ediyoruz.
- Farklı testler farklı riskleri yakalıyor. Otomatik testler sadece yazılmış senaryoları kontrol ediyor. Bu yüzden testler geçerken bile ben UX testinde hover sorununu, Docker testinde kapanma sorununu buldum.
- Sunucu istek bekleyen bir çağrı merkezi gibi: açıkken site var, kapanınca yok. FastAPI beyin, kararları veriyor. Veritabanı sadece veriyi tutuyor.
- SQLite tek bir dosya, geliştirmede hızlı ama aynı anda tek yazmaya izin veriyor. Postgres ayrı bir sunucu: çok kullanıcıyı kaldırıyor ve uygulama yeniden kurulsa da veri kaybolmuyor. Bu yüzden canlıda Postgres.
- `fastapi dev` günlük geliştirme için, hızlı. Docker canlıya benzer ortamda prova. Uygulamanın kapanma sinyalini duymadığını (exit 137) sadece Docker testi gösterdi.

**Zorlandığım yerler**
- Aynı anda çok adım ve terminal sekmesi olunca karıştı; adım adım ilerleyince düzeldi.
- Sunucuyu Claude Code arka planda açınca değişiklikler görünmedi; neyin nerede çalıştığını anlamak zaman aldı.
- Docker, veritabanı ve sunucu kavramları önce havada kaldı; somut örnek ve benzetmelerle oturdu.

**Bir dahakine farklı yapacağım**
- Bir sürüm etiketini kullanmadan önce gerçekten var olduğunu kontrol etmek (ilk CI çalışması `setup-uv@v10` etiketi olmadığı için kırıldı).
- Sunucuyu her zaman kendi terminalimde açarım: sol sekme Claude Code, sağ sekme sunucu.
- "Devam et" yerine açık talimat veririm: "push et" / "push etme, önce göster".
- Her oturumun başında modu kontrol ederim (manual / auto).

**SPEC / WORKFLOW değişikliği gerekiyor mu?**
- SPEC: v2.1.1 (odak halkası tüm öğelerde) ve v2.1.2 (tema seçici tek ikon + dropdown). claude.ai projesindeki kopya merge'den sonra yenilenecek.
- WORKFLOW: Hayır.
