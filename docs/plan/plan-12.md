# Plan 12 — Attachment masuk: gambar dan dokumen

- Status: In Progress
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Membuat Sanka benar-benar menerima lampiran WhatsApp, bukan hanya caption-nya.
Bridge mengunduh media, mengunggah byte ke gateway, dan meneruskan metadata lampiran bersama pesan.
Python menyimpan lampiran di luar repo, membatasi ukuran/retensi, lalu memberi agent dua jalur:

- **Gambar** (JPEG/PNG/WebP) dikirim ke model sebagai bagian multimodal `image_url`; model `deepseek-v4.1-flash` sudah terbukti bisa membacanya.
- **Dokumen** (PDF/XLSX/CSV/MD/TXT) dibaca lewat tool baru `attachment_read`, memakai extractor yang sama dengan `doc_read`.

Akhir plan: mengirim `AGENTS.md` atau foto beserta caption ke Sanka menghasilkan jawaban yang benar-benar memakai isi lampiran, dan lampiran tanpa caption di DM tetap diproses.

## Di luar cakupan

- Voice note, audio, video, sticker, GIF, dan transkripsi. Perilakunya tetap seperti sekarang: caption saja.
- OCR lokal, thumbnail, kompresi/resize gambar, dan pembersihan EXIF. Tidak ada dependency baru.
- Format dokumen di luar PDF/XLSX/CSV/MD/TXT (DOCX, PPTX, ODS, ZIP, dan sejenisnya ditolak dengan pesan jelas).
- Menyimpan lampiran ke memori jangka panjang atau konteks grup. `group_context` tetap hanya teks (ADR-0025).
- Lampiran pada command `/...`. Command tetap deterministik dan mengabaikan lampiran.
- Antrean upload persisten, retry lintas restart, dan deduplikasi upload. File yatim dibersihkan oleh retensi.
- Perubahan pada `doc_read` untuk file di `tools.docs.dir`; jalur itu tetap terpisah.

## Prasyarat

- Plan-4 dan plan-5 sudah berjalan di lingkungan nyata; kontrak `/v1/inbound` dipakai bersama.
- Gateway dan bridge berjalan di host yang sama, loopback, dengan `SANKA_GATEWAY_TOKEN` yang sama (sudah menjadi syarat ADR-0018).
- `config/sanka.yaml` mendapat section `attachments` dengan `dir` absolut di luar repo, misalnya `/home/muqsith/.local/share/sanka/attachments`. Contoh config dibiarkan `dir: ""` (fitur mati) agar repo tetap aman.
- Model `deepseek-v4.1-flash` menerima `image_url` data URL. Sudah diprobe pada 2026-10-21 dengan PNG 2x2: respons `200`, jawaban `"Red"`. Probe tidak disimpan sebagai tes otomatis karena memakai API key dan jaringan.
- Tidak ada dependency Python/Node baru. Baileys `7.0.0-rc13` sudah menyediakan `downloadMediaMessage`; Node 22 punya `fetch`/`Buffer`; Python memakai `hashlib`, `base64`, dan extractor yang ada.

## Rancangan

### Alur data

```text
WhatsApp media
    |
    v
[socket.js] deteksi imageMessage/documentMessage
    |
    v
[media.js] downloadMediaMessage -> Buffer (cap ukuran)
    |
    v
[upload.js] POST /v1/attachments (binary + metadata header)
    |
    v
[gateway/server.py] stream ke AttachmentStore -> attachment_id
    |
    v
normalize.js: event {text, attachments:[{attachment_id, kind, mime, name, size}]}
    |
    v
POST /v1/inbound -> normalizer.py -> gate/router -> dispatcher
    |
    +--> gambar: AgentRuntime menyisipkan image_url ke pesan user
    +--> dokumen: agent memanggil attachment_read(attachment_id)
```

Urutan upload sebelum inbound membuat Python tidak perlu menerima byte di jalur JSON.
Bridge tetap hanya I/O; seluruh keputusan produk tetap di Python.

### Kontrak baru: `POST /v1/attachments`

Header wajib:

| Header | Isi |
|---|---|
| `X-Sanka-Token` | token bersama, sama seperti `/v1/inbound` |
| `Content-Type` | `application/octet-stream` |
| `Content-Length` | wajib; chunked ditolak `411` |
| `X-Sanka-Chat-Id` | JID chat |
| `X-Sanka-Sender-Id` | JID pengirim |
| `X-Sanka-Message-Id` | id pesan WhatsApp |
| `X-Sanka-Kind` | `image` atau `document` |
| `X-Sanka-Mimetype` | MIME dari WhatsApp, sudah dinormalkan |
| `X-Sanka-Filename-B64` | nama file UTF-8, base64url; boleh kosong untuk gambar |
| `X-Sanka-Sha256` | opsional; bila ada harus cocok dengan hash body |

Respons:

| Status | Body | Arti |
|---|---|---|
| `201` | `{"attachment_id":"<32 hex>","size":n,"sha256":"..."}` | tersimpan |
| `400` | `{"error":"invalid_metadata"}` | header/JID/id tidak valid |
| `401` | `{"error":"unauthorized"}` | token salah |
| `411` | `{"error":"length_required"}` | tanpa Content-Length |
| `413` | `{"error":"payload_too_large"}` | melebihi `attachments.max_bytes` |
| `415` | `{"error":"unsupported_media"}` | kind/MIME/ekstensi tidak didukung |
| `503` | `{"error":"attachments_disabled"}` | `attachments.dir` kosong |
| `500` | `{"error":"internal"}` | gagal menulis |

Body ditulis streaming ke file sementara di `attachments.dir`, di-hash sambil dibaca, lalu di-`rename` atomik.
Byte lampiran tidak pernah masuk log; log hanya `attachment_id` (hash), kind, ukuran, status, dan durasi.

### Kontrak inbound yang diperluas

Field baru, opsional dan kompatibel dengan bridge lama:

```json
{
  "attachments": [
    {
      "attachment_id": "0123456789abcdef0123456789abcdef",
      "kind": "document",
      "mime": "text/markdown",
      "name": "AGENTS.md",
      "size": 8192
    }
  ],
  "attachment_errors": ["download_failed"]
}
```

Aturan validasi:

- `attachments` maksimal 4 entri; `attachment_id` harus 32 hex huruf kecil; `kind` hanya `image`/`document`.
- `mime`, `name`, dan `size` harus cocok dengan metadata yang tersimpan untuk `attachment_id`.
- Metadata harus terikat pada `chat_id`, `sender_id`, dan `message_id` yang sama; lampiran milik chat lain ditolak `attachment_unknown`.
- `attachment_errors` hanya berisi kode pendek: `download_failed`, `upload_failed`, `too_large`, `unsupported`.
- Payload lama tanpa dua field ini tetap valid.

`text` tetap caption/teks apa adanya. Bridge tidak menambahkan penanda buatan.

### Penyimpanan lampiran

Config baru:

```yaml
attachments:
  dir: ""                         # absolut, di luar repo; kosong = fitur mati
  max_bytes: 25000000             # per file
  max_per_message: 4
  max_image_bytes: 8000000        # per gambar yang dikirim ke model
  retention_minutes: 1440
  max_total_bytes: 536870912      # kuota direktori
```

Direktori dibuat `0700`; setiap lampiran menjadi dua file `0600`:

```text
<dir>/<id>.bin    # byte asli, tanpa ekstensi agar tidak bisa dieksekusi
<dir>/<id>.json   # metadata: chat_id, sender_id, message_id, kind, mime,
                  # name, size, sha256, created_at
```

- Nama file tidak pernah dipakai sebagai path; hanya label tampilan.
- Retensi dijalankan saat startup dan setelah setiap penyimpanan. Kuota total dijaga dengan menghapus lampiran tertua lebih dulu.
- `AttachmentStore` memakai lock untuk operasi metadata/cleanup.
- ID dibuat dari `secrets.token_hex(16)`; tidak dapat ditebak dan tidak berurutan.

### Deteksi dan validasi media di bridge

| Isi WhatsApp | Kind | Aturan |
|---|---|---|
| `imageMessage` | `image` | MIME `image/jpeg`, `image/png`, `image/webp`; magic bytes diperiksa di gateway |
| `documentMessage` | `document` | PDF, XLSX, CSV, MD, TXT; MIME atau ekstensi nama file menentukan; PDF/XLSX diperiksa magic bytes |
| media lain | — | caption tetap diteruskan seperti sekarang |

- `fileLength` WhatsApp dipakai sebagai pre-check; byte hasil unduhan dicek ulang.
- Nama file di-`sanitize`: buang path separator/karakter kontrol, batasi 128 karakter, jangan biarkan kosong.
- Bila unduhan gagal: event tetap dikirim dengan `attachment_errors:["download_failed"]`.
- Bila ukuran melebihi batas: jangan unduh; kirim `too_large`.
- Bila unggahan gagal: kirim `upload_failed`.
- Bridge memproses pesan dalam loop `onMessagesUpsert` secara berurutan agar urutan FIFO tidak berubah. Unduhan besar memperlambat pesan berikutnya; ini diterima karena batas 25 MB dan antrean pesan WhatsApp biasanya kecil.

### Python: gate, router, runtime

- `gateway/gate.py`: teks kosong **dan** tidak ada `attachments` → `blank_text`. Teks kosong dengan lampiran lolos.
- `sanka/router.py`: teks kosong dengan lampiran di DM → `AGENT`; di grup tetap wajib mention/reply. Caption yang menyebut `@sanka` tetap bekerja.
- `sanka/models.py`: tambah `InboundAttachment` dan field `attachments`/`attachment_errors` pada `InboundMessage` (default kosong, jadi tes lama aman).
- `sanka/agent/runtime.py` menyusun isi pesan user:
  - Selalu ada satu part teks: teks asli, ditambah blok `<attachments>` berisi ID/kind/nama/MIME/ukuran dan instruksi memakai `attachment_read` untuk dokumen.
  - Untuk setiap gambar (maksimal `max_per_message`), baca byte dari store setelah cek chat, lalu tambahkan part `{"type":"image_url","image_url":{"url":"data:<mime>;base64,..."}}`.
  - Bila tidak ada teks sama sekali, part teks berisi `"Pengguna mengirim lampiran tanpa teks."` agar API tidak menerima pesan kosong.
  - Bila ada `attachment_errors`, tambahkan kalimat jujur bahwa sebagian lampiran gagal diterima beserta kodenya; model tidak boleh mengaku menerima file yang gagal.
  - History sesi menyimpan teks + daftar lampiran (bukan base64). Gambar dari giliran lama tidak dikirim ulang; ini disengaja agar history tidak membengkak.
- `core/agent_loop.py`: longgarkan tipe `initial_user_content` menjadi `str | list[dict[str, Any]]`; tidak ada perubahan logika loop.
- `sanka/agent/handler.py`: tidak berubah; `attachment_read` terpasang otomatis lewat registry.

### Tool `attachment_read`

- Input: `{"attachment_id": "<32 hex>"}`.
- Hanya dokumen; gambar tidak perlu tool karena sudah terlihat model. Memanggil tool untuk gambar → `invalid_arguments`.
- Store mengecek ID ada, belum kedaluwarsa, dan `chat_id` sama dengan `ToolContext.chat_id`. Pelanggaran → `not_found`.
- Ekstraksi memakai fungsi PDF/XLSX/CSV/teks yang sekarang ada di `sanka/tools/docs.py`; fungsi dipindah ke modul bersama agar tidak ada dua implementasi. Batas halaman/baris/karakter memakai `tools.docs.max_pages`, `max_rows`, `max_chars`.
- Output text dibungkus registry seperti tool lain (`<data>`), jadi tetap DATA, bukan instruksi.
- Izin: `COMMITTEE_ROLES` di `TOOL_PERMISSIONS` (sama dengan `doc_read`), ditambah tes bahwa peran owner dan panitia bisa, peran lain tidak ada.

### Wiring

- `build_app` membuat `AttachmentStore` dari config dan meneruskannya ke `GatewayServer`, `Dispatcher`, `AgentRuntime`, dan registry tool.
- `GatewayServer` meneruskan store ke HTTP handler untuk endpoint upload; `MAX_BODY_BYTES` JSON tetap 64 KiB, batas lampiran terpisah.
- `ToolContext` mendapat field `attachments: AttachmentStore | None = None` agar tes lama tidak perlu diubah.
- Bila `attachments.dir` kosong: upload menjawab `503`, runtime tidak menyisipkan gambar, `attachment_read` menjawab `not_configured`, dan bridge tetap mengirim teks/caption. Tidak ada crash saat startup.

## Langkah

1. Tulis ADR-0026 (`Proposed` → `Accepted` saat plan disetujui) yang mencatat pilihan: upload binary terpisah, store di luar repo, model vision untuk gambar, tool untuk dokumen, retensi 24 jam, dan batas ukuran.
2. `sanka/config.py`: tambah `AttachmentsConfig`, parsing/validasi (`dir` absolut di luar repo, batas angka), update `config/sanka.example.yaml` dan dokumentasi konfigurasi.
3. `sanka/attachments.py`: implementasikan `AttachmentStore` (save stream, metadata, read for chat, cleanup retensi/kuota, mode file/direktori). Tes unit memakai `tmp_path`.
4. `gateway/server.py`: tambah rute `POST /v1/attachments` beserta validasi header, streaming body, batas ukuran, dan respons. Tes HTTP memakai server sungguhan di port 0.
5. `gateway/normalizer.py`: parse/validasi `attachments` dan `attachment_errors`, cek metadata ke store. Tambah `InboundAttachment` di `sanka/models.py`.
6. `gateway/gate.py` dan `sanka/router.py`: sesuaikan aturan blank text dan routing lampiran.
7. `sanka/tools/docs.py`: ekstrak fungsi baca ke modul bersama; tambah `sanka/tools/attachments.py` dengan `attachment_read`; daftarkan di `sanka/tools/__init__.py` dan `sanka/policy.py`.
8. `sanka/agent/runtime.py` + `core/agent_loop.py`: bangun content parts multimodal, blok metadata lampiran, penanganan error lampiran, dan history teks. Teruskan store ke `ToolContext`.
9. Bridge: `normalize.js` mengekspos deskriptor media; modul `media.js`/`upload.js` mengunduh dan mengunggah; `socket.js` mengaitkannya sebelum `deliverer.enqueue`. Update config bridge bila perlu (URL upload diturunkan dari `SANKA_INBOUND_URL`).
10. Tes bridge: fixture media (image/document/oversize/download gagal/upload gagal) dengan downloader/uploader palsu; pastikan tidak ada byte/nama file di log.
11. Perbarui `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `README.md`, dan catatan kontrak di `docs/plan/plan-3.md`. Tambah entri `docs/LOG.md` saat selesai.
12. Smoke loopback dengan fake bridge + model nyata: kirim event berisi gambar dan dokumen, pastikan model menjawab memakai isi lampiran. Lalu minta owner mengirim satu foto dan satu `AGENTS.md` lewat WhatsApp.
13. Isi bagian Hasil, ubah status plan menjadi `Done` hanya setelah smoke WhatsApp nyata berhasil.

## Tes

### Otomatis

- `pytest -q -o addopts=""`:
  - `AttachmentStore`: simpan/ambil, mode `0600`/`0700`, ID invalid, chat lain ditolak, retensi menghapus file+metadata, kuota membuang yang tertua, file yatim diabaikan.
  - Normalizer: payload lama tetap valid; attachments valid; ID/kind/MIME/size salah ditolak; metadata tidak cocok dengan chat/sender/message ditolak; maksimal 4.
  - Gate/router: teks kosong tanpa lampiran → `blank_text`; teks kosong dengan lampiran DM → `agent`; grup tanpa mention → `not_mentioned`; caption mention → `agent`.
  - Server upload: 201, 400, 401, 411, 413, 415, 503; body tidak melebihi batas; hash cocok/tidak cocok; byte tidak masuk log.
  - `attachment_read`: PDF/XLSX/CSV/MD/TXT, truncation, dokumen hilang, gambar ditolak, chat lain ditolak, store mati → `not_configured`.
  - Runtime: pesan gambar menjadi content parts dengan data URL; dokumen menjadi blok metadata + tool; history hanya teks; error lampiran masuk prompt; API key tidak bocor.
  - E2E fake bridge: event dengan lampiran → balasan agent keluar lewat outbound.
- `cd gateway/bridge && npm test`:
  - Normalize media descriptor untuk image/document/wrapper; media tanpa caption + lampiran tetap menghasilkan event.
  - Downloader palsu: sukses, gagal, oversize; uploader palsu: sukses, 401, 429 lalu sukses, 500.
  - Event tetap terkirim saat unduh/unggah gagal dengan `attachment_errors`.
  - Log tidak memuat nama file, MIME mentah, atau isi byte.
- `.venv/bin/python -m compileall -q sanka core gateway`.

### Manual

1. DM: kirim `AGENTS.md` dengan caption `@Sanka ringkas file ini` → balasan menyebut isi file, bukan "belum ada berkas".
2. DM: kirim `assets/sanka.png` dengan caption `@Sanka ini gambar apa?` → balasan mendeskripsikan gambar.
3. DM: kirim gambar tanpa caption → Sanka tetap memproses.
4. Grup: kirim dokumen tanpa mention → tidak ada balasan; kirim dengan caption mention → dibalas.
5. Kirim file > 25 MB dan file `.docx` → balasan jelas bahwa format/ukuran tidak didukung; tidak ada crash.
6. Restart gateway dalam 24 jam, lalu minta Sanka membaca ulang lampiran yang sama lewat history/ID → masih bisa (selama sesi masih hidup).
7. Periksa `attachments.dir`: `0700`, file `0600`, tidak ada file di dalam repo; setelah retensi, file lama hilang.
8. Periksa log gateway/bridge: tidak ada nama file, isi lampiran, JID mentah, atau token.

## Kriteria selesai

- Sanka menerima dan memakai isi dokumen PDF/XLSX/CSV/MD/TXT dari WhatsApp.
- Sanka menerima dan memahami gambar JPEG/PNG/WebP lewat model vision.
- Lampiran tanpa caption di DM tidak lagi dibuang sebagai `blank_text`.
- Kegagalan unduh/unggah/format/ukuran dilaporkan jujur oleh Sanka, bukan "belum ada berkas".
- Byte lampiran disimpan di luar repo, `0700`/`0600`, dengan retensi dan kuota; tidak ada path dari wire.
- Lampiran terikat pada chat/sender/message; tidak bisa dibaca lintas chat.
- Log bebas dari isi lampiran, nama file, JID mentah, dan token.
- Semua tes Python dan Node lulus; smoke WhatsApp nyata berhasil.
- ADR-0026, ARCHITECTURE, SECURITY, README, plan-3, dan LOG diperbarui.

## Yang perlu dipahami owner

1. **Kenapa upload terpisah, bukan base64 di `/v1/inbound`.** Body JSON dibatasi 64 KiB dan base64 membengkak 33%. Endpoint binary memungkinkan streaming ke disk tanpa menahan 25 MB di memori dan tanpa mengubah kontrak pesan teks.
2. **Kenapa ID, bukan path.** Model dan bridge tidak pernah menyebut path filesystem. Gateway yang memetakan ID ke file, sehingga path traversal tidak mungkin lewat prompt atau argumen tool.
3. **Kenapa gambar langsung ke model, dokumen lewat tool.** Model vision bisa melihat gambar tanpa OCR, tetapi dokumen panjang harus dipotong dan dibaca bertahap sesuai batas halaman/baris. Tool juga menjaga agar isi dokumen tetap ditandai sebagai DATA.
4. **Batas keamanan.** Lampiran tetap data tak tepercaya. MIME tidak dipercaya mentah (magic bytes untuk gambar/PDF/XLSX), ekstensi asing ditolak, file disimpan tanpa ekstensi, dan `attachment_read` mengecek chat.
5. **Privasi.** Isi lampiran (gambar dan teks dokumen) dikirim ke OpenCode Go, sama seperti teks chat saat ini. Retensi lokal 24 jam adalah batas penyimpanan, bukan jaminan privasi di sisi provider.
6. **Yang belum ada.** Voice note, video, DOCX, OCR, resize, dan EXIF stripping. Jangan menganggap plan ini membuat Sanka bisa membaca semua jenis file.

## Catatan perubahan

- 2026-10-21: draft awal. Keputusan model vision diambil setelah probe langsung ke OpenCode Go (`deepseek-v4.1-flash` menjawab warna dari PNG data URL), sehingga OCR tidak diperlukan dan tidak ada dependency baru.

## Hasil

Belum dijalankan. Plan masih `Draft` dan menunggu persetujuan owner.
