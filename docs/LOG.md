# LOG

Append-only. Satu entri per sesi kerja, entri terbaru di paling bawah.
Isi: tanggal, plan, yang dilakukan, hasil tes, masalah/temuan, langkah berikutnya. Jangan mengarang hasil.

---

## 2026-10-20 — Sesi desain awal
- Plan: (belum ada kode; persiapan plan-1)
- Dilakukan:
  - Membahas rancangan: hybrid router "/" vs LLM, basis GenericAgent, gateway WhatsApp dari Hermes.
  - Membuat dokumen awal: AGENTS.md, README.md, docs/ARCHITECTURE.md, SECURITY.md, PERSONA.md, COMMANDS.md, UPSTREAM.md, ADR.md, dan rencana plan-1 sampai plan-6.
  - Mencatat keputusan awal di ADR-0001 sampai ADR-0012.
- Hasil tes: tidak ada (belum ada kode).
- Temuan:
  - Format API OpenCode Go belum diverifikasi.
  - Keterikatan gateway Hermes dengan agent-nya belum diperiksa; folder `gateway/` belum dibaca.
  - Matriks izin tool per peran dan beberapa pengamanan (ADR-0011) masih usulan.
- Berikutnya: owner meninjau dokumen dan ADR-0011, lalu memulai plan-1.

## 2026-10-20 — Koreksi dokumen dan inisialisasi repo
- Plan: persiapan plan-1 (belum ada kode)
- Dilakukan:
  - Audit dokumen terhadap upstream GenericAgent dan Hermes Agent.
  - `git init` (branch `main`), menambah `LICENSE` MIT, `.gitignore`, `config/sanka.example.yaml`, `config/secrets.example.env`, dan `docs/plan/README.md`.
  - Clone referensi (shallow) ke `references/genericagent` dan `references/hermes-agent`; folder di-ignore git. Commit upstream yang dipelajari: GenericAgent `e6b1bcec92e7789eb80975980b5fd47a2abea860`, Hermes `0319e113c9602b86ffda350e931cb4b035e1d5d8`.
  - Memperbaiki kontradiksi izin: `code_run`, `file_write`, dan shell hanya owner.
  - Menetapkan satu sumber config: YAML non-secret + secrets dari env/`config/secrets.env` (ADR-0014).
  - Mengoreksi jumlah tool menjadi 10.
  - Menambah ADR-0013 (gateway diimplementasikan sendiri, bukan copy Hermes) dan menandai ADR-0002 superseded.
  - Menambah catatan verifikasi OpenCode Go di ADR-0012.
- Hasil tes: `git status` bersih dari `references/` setelah `.gitignore`; file lisensi upstream diperiksa (MIT, copyright 2025). Belum ada tes kode karena belum ada kode.
- Temuan:
  - Adapter WhatsApp Hermes bergantung pada core gateway yang besar; menyalinnya tidak tipis. Karena itu gateway ditulis sendiri.
  - Baileys adalah jalur tidak resmi; risiko blokir nomor tetap ada.
  - OpenCode Go membatasi traffic per 5 jam/minggu/bulan dan memantau penyalahgunaan.
  - Model sesi dan antrean async belum diputuskan; perlu ADR sebelum plan-1.
  - Koreksi entri sebelumnya: `docs/UPSTREAM.md` dibatalkan dan plan-1..6 belum pernah dibuat di disk.
- Berikutnya: owner meninjau ADR-0013/0014 dan sisa ADR-0011, lalu plan-1.
