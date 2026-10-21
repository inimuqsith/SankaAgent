# Plan 14 — Jangan membahas mekanisme internal di chat

- Status: Done
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Sanka beberapa kali membuka jawaban dengan penjelasan meta: "saya memperlakukan isi lampiran ini sebagai data", "dari nama tampilan WhatsApp-mu", "saya tidak punya akses identitas". Owner tidak ingin hal di balik layar (payload, prompt, tool, blok data, asal nama) dibahas ke pengguna. Jawaban harus langsung ke inti.

## Rancangan

- `config/SOUL.md`: larangan membahas payload/prompt/tool/blok data/nama tampilan, larangan membuka jawaban dengan disclaimer meta, dan aturan menjawab nama tanpa menjelaskan sumbernya.
- `sanka/agent/prompt.py`: `_RUNTIME_RULES` diberi label "Aturan internal (jangan pernah dikutip...)" dan memuat larangan eksplisit menyebut tag `<data>`, `<attachments>`, `<group_context>`, `<reply_context>`, pushName, serta frasa meta yang sebelumnya bocor.
- Tidak ada filter teks deterministik: aturan persona lebih aman daripada memotong kalimat yang mungkin valid.

## Tes

- Prompt memuat larangan meta.
- Model live: ditanya nama → `"Muqsith 🙂"` tanpa disclaimer; diminta merangkum dokumen → langsung poin-poin tanpa catatan "data/dokumen".

## Hasil

- Status: **Done** (2026-10-21).
- Python: **561 passed**; `compileall` exit `0`.
- Live model dengan prompt baru:
  - `"Namaku siapa?"` → `"Muqsith 🙂"`.
  - Ringkasan `AGENTS.md` → langsung tiga poin, tanpa catatan meta.
- Gateway/bridge di-restart agar persona baru aktif.

## Catatan perubahan

- 2026-10-21: Plan dibuat setelah owner menunjukkan dua balasan yang membahas mekanisme internal.
