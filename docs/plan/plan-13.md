# Plan 13 — Konteks balasan, nama tampilan, dan lampiran utuh

- Status: Done
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Memperbaiki tiga keluhan dari pemakaian WhatsApp:

1. Isi `AGENTS.md` yang dikirim sebagai lampiran terpotong di bagian §8, padahal file hanya ~8 KB. Penyebabnya `attachment_read` memakai cap default tool registry 8.000 karakter dan tidak punya mekanisme lanjutan.
2. Payload tidak membawa isi pesan yang dibalas atau nama orang yang di-mention. Model hanya tahu `reply_to_bot=true` dan daftar JID mention.
3. Saat ditanya namanya, Sanka ragu terhadap `pushName` ("itu cuma nama tampilan"). Nama tampilan seharusnya dipakai dengan yakin sebagai nama sapa, tanpa mengklaim verifikasi identitas.

## Di luar cakupan

- OCR, media, voice note, dan perubahan pada pipeline lampiran plan-12.
- Menyimpan riwayat balasan ke long-term memory.
- Perubahan pada group context store selain pemakaian nama tampilan yang sudah ada.
- Resolusi nama kontak dari buku telepon WhatsApp; hanya `pushName` dan cache pesan yang terlihat.

## Rancangan

### Lampiran utuh dan berpaginasi

- `sanka/tools/registry.py`: `attachment_read` memakai `tools.docs.max_chars` (sekarang 50.000), bukan cap default 8.000.
- `sanka/tools/attachments.py`: tambah argumen `offset` dan `limit`; hasil memuat `offset`, `next_offset`, `characters`, `truncated`.
- Default window = `docs.max_chars - 256` (49.744) agar tag `<data>` tidak memotong teks.
- `tools.docs.max_chars` dibatasi maksimum 50.000 di config; nilai lebih besar ditolak.
- Prompt menginstruksikan model melanjutkan dengan `next_offset` sampai null bila pengguna meminta seluruh isi.

### Payload balasan dan mention

- Bridge mengekstrak `contextInfo.quotedMessage` menjadi `quoted_text` (maks 500 karakter) dan `quoted_sender_name` (dari cache `pushName`, fallback `Peserta <4 digit>`; pesan bot menjadi `Sanka`).
- Bridge mengirim `mention_names` sejajar dengan `mentions`.
- Gateway menormalisasi field baru dengan batas yang sama dan memasukkannya ke `InboundMessage`.
- `AgentRuntime` membangun `reply_context` dari quote dan meneruskannya ke `build_system_prompt` bersama `speaker_name` dan `mentioned_names`.
- Prompt menambahkan blok `<reply_context>` berlabel DATA, daftar nama yang disebut, dan nama tampilan pengirim.
- `config/SOUL.md`: nama tampilan adalah nama yang sah untuk dipakai menyapa; jangan ragu, tetapi bukan verifikasi identitas legal.

## Tes

- `attachment_read` dengan offset/limit: potongan pertama, `next_offset`, potongan kedua, `truncated=false`.
- Registry: cap `attachment_read` mengikuti `tools.docs.max_chars`, bukan 8.000.
- Bridge: quote text/name dan mention names masuk event; kontrak field diperbarui.
- Normalizer: field quote/mention divalidasi dan dipetakan.
- Prompt/runtime: nama pengirim, quote, dan mention masuk prompt; tag di-escape.
- Smoke: baca `AGENTS.md` 8.361 karakter sampai baris terakhir tanpa marker `dipotong`; model menjawab "Muqsith" saat ditanya nama.

## Hasil

- Status: **Done** (2026-10-21).
- Python: **560 passed**; bridge: **137 passed**; `compileall` exit `0`.
- Smoke `attachment_read` pada `AGENTS.md`: `characters=8361`, `next_offset=None`, `truncated=False`, teks sampai baris terakhir; hasil registry 8.402 karakter tanpa marker `dipotong`.
- Smoke dokumen 60.000 karakter: panggilan pertama 49.744 karakter (`next_offset=49744`), panggilan kedua 10.256 karakter (`next_offset=None`), total 60.000 tanpa kehilangan teks.
- Smoke model dengan `speaker_name=Muqsith`: jawaban `"Muqsith 🙂"`.
- Restart final: gateway PID 193344, bridge PID 193370, health `ok`, bridge `connected:true`.
- Dokumentasi: ADR-0027, plan-13, SECURITY, ARCHITECTURE, LOG.

## Catatan perubahan

- 2026-10-21: Plan dibuat setelah laporan owner. Plan-12 (attachment) sedang dikerjakan paralel; plan-13 hanya menambah koreksi di atasnya.
