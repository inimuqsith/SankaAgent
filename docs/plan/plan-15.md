# Plan 15 — Pencarian dan pembacaan per bagian lampiran

- Status: Done
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Dokumen besar tidak perlu dibaca utuh hanya untuk menemukan satu bagian. Plan ini menambah dua tool baca untuk lampiran WhatsApp:

- `attachment_search` — mencari kata/frasa di seluruh isi lampiran dan mengembalikan offset, label bagian, dan cuplikan konteks.
- `attachment_outline` — mengembalikan struktur dokumen (halaman PDF, judul Markdown, lembar Excel, rentang baris CSV/teks) beserta offset.

`attachment_read` tetap menjadi cara membaca teks, tetapi sekarang memakai ekstraksi penuh yang sama sehingga offset dari hasil pencarian/outline bisa langsung dipakai. Tujuannya menekan token: 50k karakter per panggilan tetap mahal, dan hasil tool ikut dikirim ulang pada turn berikutnya.

## Di luar cakupan

- Embedding/vector search atau LLM untuk memilih bagian. Pencarian deterministik (substring) dulu.
- Cursor persisten antar pesan pengguna. Offset hanya berlaku selama lampiran masih tersimpan.
- Ringkasan map-reduce otomatis.
- Pencarian lintas lampiran atau lintas chat.
- Cache ekstraksi di disk. Cache hanya di memori proses.
- Menaikkan `tools.docs.max_chars` lagi.
- Perubahan pada `doc_read` (dokumen lokal): batas `max_pages`/`max_rows` tetap seperti sekarang.

## Rancangan

### Ekstraksi bersama

`sanka/tools/extract.py` menambah:

- `Section(label, start, end)` dan `DocumentText(text, units, truncated, sections, columns)`.
- `build_document(path, kind) -> DocumentText` untuk `pdf`, `xlsx`, `csv`, `text`.
  - PDF: semua halaman sampai `_MAX_DOC_PAGES = 300`, satu section per halaman (`halaman N`).
  - XLSX: semua lembar sampai `_MAX_DOC_SHEETS = 20`, `_MAX_DOC_ROWS = 5000` baris per lembar, satu section per lembar (`lembar <nama>`). Setiap lembar diberi baris penanda `### Lembar: <nama>` di dalam teks agar batas lembar terlihat.
  - CSV: `_MAX_DOC_ROWS = 5000` baris, section per 200 baris (`baris a-b`).
  - Teks/Markdown: section dari heading `#`–`######`; bila tidak ada heading, section per 200 baris.
  - Total teks dipotong di `_MAX_DOC_CHARS = 2_000_000`; `truncated` menandai pemotongan.
- Cache memori LRU 4 dokumen, kunci `(path, kind, size, mtime_ns)`; tidak ada file cache baru di disk.

Offset section selalu dihitung terhadap teks yang dikembalikan, bukan terhadap teks mentah, sehingga `attachment_read(offset=...)` konsisten.

### Tool lampiran

`sanka/tools/attachments.py`:

- Helper bersama untuk validasi `attachment_id`, chat, pengirim, jenis gambar, dan resolusi path; ketiga tool memakai validasi yang sama.
- `attachment_read`: dulu membaca PDF maksimal 10 halaman dan Excel hanya sheet aktif; sekarang membaca `DocumentText` penuh dengan pagination `offset`/`limit`/`next_offset` yang sudah ada.
- `attachment_search(attachment_id, query, limit=5, context_chars=200)`:
  - substring case-insensitive (regex di-escape, bukan pola pengguna);
  - maksimum 20 hasil, `context_chars` maksimum 1000;
  - keluaran: `total_matches`, `returned`, `truncated` (masih ada hasil lain), `document_truncated`, dan daftar `{offset, length, section, snippet}`.
- `attachment_outline(attachment_id)`:
  - keluaran: `kind`, `characters`, `units`, `truncated`, `columns`, `section_count`, dan maksimum 100 `{label, start, end, characters, preview}`.

### Izin dan wiring

- `sanka/policy.py`: `attachment_search` dan `attachment_outline` = `COMMITTEE_ROLES`, sama seperti `attachment_read`.
- `sanka/tools/registry.py`: cap teks kedua tool mengikuti `tools.docs.max_chars`.
- `sanka/tools/__init__.py`: daftarkan kedua spec.
- `sanka/agent/prompt.py`: untuk dokumen panjang, arahkan model memakai `attachment_outline`/`attachment_search` lebih dulu, lalu `attachment_read` hanya pada bagian relevan. Aturan `next_offset` tetap.
- `sanka/agent/runtime.py`: petunjuk di blok `<attachments>` menyebut kedua tool baru.
- `sanka/agent/handler.py`: setiap prompt hasil tool mengulang pertanyaan pengguna yang sedang diproses (maks 1.000 karakter). Ini menutup kasus model kehilangan pertanyaan setelah beberapa panggilan tool pada sesi OpenCode Go.
- `docs/SECURITY.md`, `docs/ARCHITECTURE.md`, `README.md`, `docs/ADR.md` (ADR-0028), `docs/LOG.md`.

## Langkah

1. Tambah tipe `Section`/`DocumentText`, `build_document`, dan cache di `sanka/tools/extract.py`.
2. Refaktor `sanka/tools/attachments.py` memakai loader bersama; tambah `attachment_search` dan `attachment_outline`.
3. Daftarkan tool di policy, registry, dan `build_tools`.
4. Perbarui prompt, runtime hint, dan handler agar pertanyaan pengguna ikut pada setiap turn tool.
5. Tambah tes: heading, halaman PDF, lembar XLSX, offset di luar window baca, batas hasil, validasi chat/pengirim, store mati, dan pengulangan pertanyaan.
6. Jalankan pytest, compileall, smoke skrip, smoke model live, lalu restart gateway/bridge.

## Tes

```bash
.venv/bin/python -m pytest -o addopts="" -q
.venv/bin/python -m compileall -q sanka core gateway
```

Smoke:

- Dokumen teks 60.000 karakter dengan penanda di posisi ~55.000: `attachment_search` menemukan offset penanda; `attachment_read(offset=offset)` mengembalikan penanda itu. Ini membuktikan offset konsisten dan pencarian menjangkau teks di luar window pertama.
- `attachment_outline` pada `AGENTS.md` mengembalikan heading dengan offset yang cocok dengan isi `attachment_read`.
- Model live (loopback): minta "cari X di lampiran" dan pastikan model memakai `attachment_search`, bukan membaca 50k pertama lalu menyerah.

## Kriteria selesai

- Tiga tool lampiran terdaftar, berizin panitia, dan tereksekusi lewat registry.
- Pencarian menjangkau seluruh teks hasil ekstraksi, bukan hanya window pertama.
- Offset dari `attachment_search`/`attachment_outline` bisa langsung dipakai `attachment_read` tanpa pergeseran.
- Semua tes Python lama tetap lulus; tes baru membuktikan perilaku di atas.
- ADR-0028, LOG, dan dokumen terkait diperbarui; gateway/bridge di-restart dengan tool baru aktif.

## Yang perlu dipahami owner

- **Offset adalah kontrak internal**: teks diekstrak sekali, lalu semua tool memakai string yang sama. Kalau ekstraksi berubah (mis. urutan halaman), offset lama tidak berlaku lagi. Cache dikunci dengan ukuran dan `mtime` file, jadi file yang sama tidak diekstrak ulang dalam satu proses.
- **Pencarian substring, bukan semantik**: "biaya" tidak akan menemukan "anggaran". Ini disengaja agar murah dan bisa diprediksi; model bisa mencoba beberapa kata kunci.
- **Batas ekstraksi tetap ada**: 300 halaman, 20 lembar × 5.000 baris, 2 juta karakter. `truncated`/`document_truncated` memberi tahu model bahwa masih ada isi yang tidak terindeks, sehingga ia tidak boleh mengaku sudah memeriksa seluruh dokumen.
- **Kenapa tidak menaikkan 50k saja**: setiap hasil tool dikirim ulang pada turn berikutnya. Pencarian mengirim ratusan karakter, bukan puluhan ribu.

## Catatan perubahan

- 2026-10-21: Plan dibuat setelah owner memilih menerapkan `attachment_search`/section-aware reading sebagai alternatif menaikkan batas karakter. Owner langsung meminta "tolong terapkan ini", sehingga plan berstatus In Progress tanpa menunggu persetujuan terpisah.

## Hasil

- Status: **Done** (2026-10-21).
- Python: **569 passed**; bridge: **137 passed**; `compileall` exit `0`.
- Smoke tool deterministik:
  - Dokumen Markdown 66.545 karakter: `attachment_outline` melaporkan 701 section dan 100 section pertama (batas keluaran), `attachment_search` menemukan `PENANDA-UNIK-55K` di offset 66.516 pada section `Bagian Akhir`, dan `attachment_read(offset=66.516)` mengembalikan kalimat penanda. Offset pencarian dan pembacaan konsisten.
  - XLSX dua lembar: outline mengembalikan `lembar Laporan` dan `lembar Arsip`, kolom `Nama | Nominal`, dan pencarian `arsip` cocok di section `lembar Arsip`. Pembacaan XLSX dari store tanpa ekstensi berhasil (sebelumnya gagal karena openpyxl menebak format dari path).
  - Cap registry: dengan `tools.docs.max_chars=200`, teks `attachment_search` 199 karakter dan `attachment_outline` 200 karakter, keduanya bertanda `dipotong`.
- Smoke model live (OpenCode Go, runtime nyata):
  - Dokumen 66.570 karakter dengan penanda di akhir. Model memanggil `attachment_search` lalu `attachment_read`/`attachment_outline` dan menjawab `Rp7.000.000 (7 juta rupiah)` tanpa membaca seluruh dokumen.
  - Sebelum pengulangan pertanyaan di handler, beberapa jawaban membuka dengan "belum ada pertanyaan" setelah turn tool. Setelah handler mengulang pertanyaan, jawaban langsung menjawab pertanyaan.
  - Variasi yang masih muncul: model kadang menyarankan mengirim ulang lampiran meski `attachment_id` ada di hasil tool. Tidak menghalangi jawaban; dicatat sebagai temuan, bukan bug tool.
- Restart: gateway PID 204257, bridge PID 204283, health `ok`, bridge `connected:true`.
