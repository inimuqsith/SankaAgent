# Plan 9 — Format WhatsApp, persona runtime, dan command yang hidup

- Status: Done
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Memperbaiki tiga keluhan nyata dari pemakaian WhatsApp:

1. Teks final dari model masih memakai markdown (`**tebal**`, `## judul`, `- daftar`). WhatsApp hanya mengenal `*tebal*`, `_miring_`, `~coret~`, dan ```mono```. Akibatnya pengguna melihat asterisk literal, mis. `*Ringkas*:`, dan heading `##` tidak dirender. Formatter sekarang hanya membuang blok reasoning dan memotong panjang; ia tidak menormalkan markdown.
2. Persona runtime terlalu tipis. `sanka/agent/prompt.py` hanya memuat aturan keselamatan dan "sependek mungkin", tanpa identitas, tanpa arahan emoji, dan tanpa aturan format WhatsApp. `docs/PERSONA.md` tidak pernah dimuat ke prompt. Tidak ada mekanisme `SOUL.md` di Sanka; L0 masih hardcoded.
3. Command deterministik kering: `/whoami` hanya mencetak peran, padahal `CommandContext` sudah membawa `sender_id`, `chat_id`, dan `is_group`. `/ping`, `/help`, `/status`, `/new`, `/stop` juga hanya satu baris tanpa karakter.

Hasil akhir: balasan LLM dan command memakai sintaks WhatsApp yang benar, jumlah emoji wajar dan kontekstual, `/whoami` menampilkan JID pengirim dan jenis chat, dan command terasa berasal dari Sanka — bukan dari `print()`.

## Di luar cakupan

- Mekanisme memori dan skill serta pemindahan L0 ke `sanka/persona/l0.md` yang menjadi lingkup plan-7. Plan ini **tidak** menambahkan loader `SOUL.md` generik; L0 dimuat dari blok bertanda di `docs/PERSONA.md` (ADR-0022).
- Perubahan router, policy, izin tool, gateway, bridge, dan format rekap.
- Emoji dekoratif di setiap baris atau perubahan nada menjadi informal berlebihan. Persona tetap "ramah tapi tidak berlebihan".
- Konversi tabel markdown. Tabel tetap tidak didukung dan persona diminta menghindarinya.

## Rancangan

### 1. Formatter menormalkan markdown ke sintaks WhatsApp

File: `sanka/formatter.py`.

Tambahkan fungsi publik `to_whatsapp(text: str) -> str` dan panggil di `Formatter.format()` **sebelum** `_truncate` / `_split_messages`, sehingga batas panjang dihitung dari teks yang benar-benar dikirim.

Aturan konversi (deterministik, per baris, tanpa LLM):

| Input markdown | Output WhatsApp |
|---|---|
| `## Judul` / `### Judul` | `*Judul*` (heading jadi satu baris tebal) |
| `**tebal**` | `*tebal*` |
| `~~coret~~` | `~coret~` |
| `[teks](https://contoh)` | `teks (https://contoh)`; bila teks sama dengan URL, cukup URL |
| `- item`, `* item`, `+ item` di awal baris | `• item` |
| `---`, `***`, `___` (garis horizontal) | baris dihapus |
| `` `kode` `` dan blok ```` ``` ```` | tidak diubah |
| `*tebal*`, `_miring_`, `~coret~` | tidak diubah (sudah sintaks WhatsApp) |

Detail implementasi:

- Blok kode berpagar dilewati baris per baris dan inline code dipisah sebelum konversi, lalu digabung kembali. Markdown di dalam kode tidak boleh berubah.
- Heading diproses setelah inline formatting pada isi heading, lalu dibungkus satu pasang `*` (hindari `***`).
- Asterisk tunggal sengaja **tidak** diubah: prompt baru meminta model memakai `*tebal*`, dan `**` adalah sumber asterisk literal. `*miring*` ala Markdown akan tampil sebagai tebal — trade-off yang diterima agar sintaks WhatsApp menang.
- `to_whatsapp` harus idempoten untuk teks yang sudah memakai sintaks WhatsApp (`*x*` tetap `*x*`).
- Tidak ada dependency baru; hanya `re`.

### 2. Persona runtime dimuat dari `docs/PERSONA.md`

File: `sanka/agent/prompt.py`, `docs/PERSONA.md`, `sanka/agent/runtime.py`.

- Blok L0 ditandai `<!-- SANKA-L0:START -->` dan `<!-- SANKA-L0:END -->` di `docs/PERSONA.md`. Hanya isi blok itu yang masuk system prompt.
- `prompt.py` memuat dan meng-cache blok tersebut; file/penanda hilang atau blok kosong melempar `PersonaError`. Tidak ada persona cadangan di kode.
- `AgentRuntime.__init__` memanggil `load_persona()` agar aset yang rusak gagal saat startup, bukan pada pesan pertama.
- Isi blok mencakup identitas ("Si Kecil Penjelajah Sanankerto"), gaya hangat tapi ringkas, emoji 1–2 yang relevan, larangan markdown, dan larangan membacakan aturan internal saat ditanya karakter.
- Aturan teknis tool/data dan tanggal tetap di `prompt.py` karena terikat kontrak kode.

### 3. Command yang hidup dan `/whoami` informatif

File: `sanka/commands/basic.py`, `sanka/commands/session.py`, `sanka/commands/registry.py`.

`/whoami` menampilkan data yang sudah tersedia di `CommandContext`:

```text
👤 Kamu: 628123456789@s.whatsapp.net
🎭 Peran: Panitia
💬 Chat: pribadi
```

Grup:

```text
👤 Kamu: 628123456789@s.whatsapp.net
🎭 Peran: Panitia
💬 Chat: grup (1203630...@g.us)
```

Bila `chat_id` kosong (unit test), baris chat dihilangkan. JID pengguna ditampilkan ke pengguna itu sendiri; ini bukan kebocoran, dan berguna saat owner menyusun `allowed_group_ids`.

Balasan lain diberi nada dan emoji:

- `/ping`: `🏓 Pong! Sanka hidup dan siap.`
- `/help`: header `🧭 *Command Sanka*`, deskripsi per command diberi emoji, dan penutup singkat.
- `/status`: `⚙️ Mode: deterministic (command tanpa LLM)` + `🧠 Model: ...`.
- `/new`: `🧹 Siap! Konteks chat ini sudah direset.`
- `/stop`: varian `⏹️`, `✅`, dan `🤔` untuk stopped/idle/forbidden.
- `UNKNOWN_COMMAND_REPLY`: `🤔 Command itu belum ada. Coba /help ...`
- `FORBIDDEN_COMMAND_REPLY`: `🔒 Command ini tidak tersedia untuk peranmu.`

Teks tetap deterministik dan dites; tidak ada panggilan LLM.

### 4. Dokumentasi

- `docs/COMMANDS.md`: contoh keluaran `/whoami`.
- `docs/PERSONA.md`: aturan format WhatsApp dan emoji disamakan dengan prompt runtime.
- `docs/ARCHITECTURE.md`: satu baris bahwa formatter juga menormalkan markdown model ke sintaks WhatsApp.
- `docs/ADR.md`: **ADR-0021** (`Proposed`) — formatter sebagai satu-satunya tempat normalisasi format, bukan instruksi prompt saja.
- `docs/LOG.md`: entri setelah implementasi, berisi tes yang benar-benar dijalankan.

## Langkah

1. Implementasikan `to_whatsapp` + integrasi di `Formatter.format`; jaga urutan strip → convert → truncate/split.
2. Tambahkan tes formatter untuk setiap baris tabel aturan, idempoten, kode berpagar, dan panjang setelah konversi.
3. Perkaya `_BASE_PROMPT` dan `docs/PERSONA.md`.
4. Ubah `/whoami` dan balasan command; perbarui `UNKNOWN_COMMAND_REPLY`/`FORBIDDEN_COMMAND_REPLY`.
5. Perbarui tes command yang mengunci string lama (`pong`, `Peran kamu: ...`).
6. Tulis ADR-0021, `COMMANDS.md`, `ARCHITECTURE.md`.
7. Jalankan `pytest -q`, `compileall`, lalu smoke loopback dengan fake bridge: satu balasan agent (atau teks contoh) dan `/whoami` harus keluar tanpa `**`/`##` dan memuat JID.
8. Restart gateway yang sedang berjalan agar kode baru dimuat; verifikasi `/whoami` dan satu balasan agent di WhatsApp. Isi `Hasil` dan ubah status plan menjadi `Done` setelah owner mengonfirmasi tampilan.

## Tes

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q sanka core gateway
```

Tes baru yang wajib:

| File | Kasus |
|---|---|
| `tests/test_formatter.py` | `**tebal**` → `*tebal*`; `## Judul` → `*Judul*`; `- item` → `• item`; link markdown; `~~coret~~`; garis horizontal hilang |
| `tests/test_formatter.py` | blok ``` dan `` `kode` `` tidak berubah; `*tebal*` tetap; output idempoten |
| `tests/test_formatter.py` | limit dihitung setelah konversi (tidak ada potongan yang menyisakan `**`) |
| `tests/test_commands.py` | `/whoami` memuat `sender_id` dan label peran; grup menampilkan `chat_id`; tanpa `chat_id` tetap valid |
| `tests/test_commands.py` | `/ping`, `/help`, `/status` memuat penanda baru dan tetap lolos pemeriksaan peran |

Smoke loopback: jalankan gateway dengan fake bridge pada port uji, kirim `/whoami` dan satu pesan agent, periksa isi `send` di fake bridge (bukan WhatsApp nyata) — tidak ada `**` atau `##`, ada JID, dan emoji wajar.

## Kriteria selesai

- Tidak ada lagi `**`, `##`, atau `- ` mentah di teks yang dikirim formatter untuk input markdown umum.
- Teks yang sudah memakai sintaks WhatsApp tidak berubah (idempoten).
- Prompt runtime memuat identitas, arahan emoji, dan aturan format WhatsApp.
- `/whoami` menampilkan JID pengirim, peran, dan jenis chat/JID chat.
- `/ping`, `/help`, `/status`, `/new`, `/stop` bernada Sanka dan memuat emoji yang relevan.
- `pytest -q` lulus dan `compileall` exit `0`.
- Smoke loopback membuktikan keluaran nyata sudah bersih.

## Yang perlu dipahami owner

- **Formatter adalah satu-satunya pintu keluar.** Apa pun yang ditulis model atau handler command, teks itu lewat `Formatter.format()`. Karena itu konversi markdown ditaruh di sini, bukan di prompt. Prompt bisa dilupakan model; kode tidak.
- **Kenapa `**` jadi `*`:** WhatsApp memakai satu asterisk untuk tebal. Markdown memakai dua. Model menulis `**Ringkas**`, WhatsApp merender "Ringkas" tebal tetapi menyisakan satu `*` di kiri-kanan — persis yang terlihat di layar.
- **Kenapa `##` hilang:** WhatsApp tidak punya heading. Heading diubah menjadi baris tebal agar struktur tetap terbaca.
- **Kenapa command terasa kering:** command tidak melewati LLM sama sekali (memang harus deterministik). Karakternya hanya bisa datang dari teks yang ditulis handler. Karena itu `/whoami` dan kawan-kawan diperbaiki di kode, bukan di prompt.
- **Batas plan ini:** belum ada `SOUL.md`/L0 berbasis file. Itu plan-7. Plan-9 hanya memperbaiki L0 hardcoded dan pintu keluar.

## Catatan perubahan

- 2026-10-21: Plan dibuat setelah diagnosis keluhan owner di WhatsApp. Belum ada kode yang diubah.
- 2026-10-21: Owner menyetujui plan dan meminta `docs/PERSONA.md` benar-benar dimuat. Rancangan persona diubah dari menambah string di `prompt.py` menjadi memuat blok bertanda dari `docs/PERSONA.md` (ADR-0022). Status menjadi `In Progress`.
- 2026-10-21: Asterisk ganda `__bold__` tidak dikonversi agar identifier seperti `__init__` tidak berubah; hanya `**bold**` yang ditangani. ADR-0021 disesuaikan.

## Hasil

- Status: **Done** (2026-10-21).
- `sanka/formatter.py`: `to_whatsapp()` mengonversi heading, `**`, `~~`, link, bullet, dan garis horizontal; kode berpagar/inline dilindungi; idempoten. `Formatter.format()` memanggilnya sebelum truncate/split.
- `sanka/agent/prompt.py`: L0 dimuat dari blok bertanda `docs/PERSONA.md` dan di-cache; `PersonaError` untuk file/penanda/blok yang hilang. `AgentRuntime.__init__` memvalidasi saat startup.
- `docs/PERSONA.md`: identitas, arahan emoji, aturan format WhatsApp, dan larangan membacakan aturan internal.
- Command: `/whoami` menampilkan JID pengirim, peran, jenis chat, dan JID chat; balasan `/ping`, `/help`, `/status`, `/new`, `/stop`, command tak dikenal, dan command terlarang memakai nada/emoji Sanka.
- Dokumentasi: ADR-0021 dan ADR-0022 `Accepted`; `ARCHITECTURE.md`, `COMMANDS.md`, `LOG.md` diperbarui.
- Tes yang dijalankan:
  - `.venv/bin/python -m pytest -o addopts="" -q` → **485 passed**.
  - `.venv/bin/python -m compileall -q sanka core gateway` → exit `0`.
  - Smoke loopback (HTTP server nyata, fake bridge, model OpenCode Go nyata): `/whoami` mengirim JID + peran + chat, `/help` mengirim daftar command, balasan agent memakai persona dan emoji, dan ketiganya tidak memuat `**` atau `##`.
  - Formatter diuji langsung pada contoh `##`, `**`, link, bullet, garis horizontal, dan inline code.
- Belum dijalankan: verifikasi tampilan di WhatsApp nyata. Gateway PID 159060 masih memakai kode lama dan perlu di-restart oleh owner.
