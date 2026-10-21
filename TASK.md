# TASK

Tujuan: bot WhatsApp kecil yang nyala dalam satu proses Python.

- [x] Enam keluarga tool: `shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`.
- [x] `web_search` memakai SearXNG bila `web_search.base_url` diisi, fallback ke backend `ddgs`.
- [x] Permission ala Discord lewat YAML: nomor -> role -> pola tool/command.
- [x] `python -m sanka selftest` lulus: write/read, shell, sandbox, web_search, LLM.
- [x] Routing DM/grup + format balasan diuji lewat event protobuf sintetis dan client palsu.
- [x] Kontrak balasan Neonize dijaga test regresi: wrapper event, target chat, sender LID.
- [x] `python -m sanka`: scan QR sekali, session tersimpan, lalu balas DM nyata (`halo`) dan `@sanka ping` di grup.
- [x] Konteks 20 pesan per chat: nama/nomor/waktu, kutipan, lampiran diunduh, gambar inline lewat MIME,
  teks panjang dipotong dengan penanda `read path=...`, biner diarahkan ke tool `sandbox`.
- [x] Arsip pesan SQLite per chat (`history_db`, 0600): semua pesan live + `HistorySync` disimpan,
  20 pesan terakhir dimuat ulang setelah restart, `/reset` hanya mereset jendela, dan tool
  `chat_history` mencari pesan lama hanya di chat aktif.
- [x] Backfill riwayat lama lewat perangkat sementara (`/backfill` atau `python -m sanka backfill`):
  scan QR sekali, hasil sync masuk `history.db`, perangkat sementara logout otomatis.
- [x] Nama chat di `chats.name` dari `HistorySync`/`GroupInfoEvent`; dipakai di header payload,
  `chat_history`, dan ringkasan, sehingga tidak menebak nama dari isi pesan.
- [x] Nama pengirim di-resolve dari kontak WhatsApp (`whatsmeow_contacts` + `lid_map`) dan
  `HistorySync.inlineContacts`; fallback push name lalu ID, termasuk untuk pesan lama.
- [x] Memori jangka panjang bertingkat: indeks kecil di system prompt, fakta/preferensi di
  `memory.db` (global + per chat), arsip mentah tetap di `history.db`; satu tool `memory` dengan
  `scope=chat|global` dan action add/search/list/forget, dipakai owner + member. `memory.auto_extract`
  membuat model mengekstrak fakta/preferensi/prosedur (`kind=rule`) sendiri di latar setelah tiap
  turn. Hanya info eksplisit dan tahan lama; rahasia/tebakan/status sesi tidak boleh disimpan.
- [x] Skill lokal `SKILL.md` dengan frontmatter `name`/`description`: indeks nama+deskripsi di
  prompt, isi penuh dimuat lewat tool `skill` (progressive disclosure); AI memutuskan list/view/read,
  save/overwrite, dan archive (dipindah ke `.archive/`). File pendukung bisa dibaca. Diadaptasi dari
  GenericAgent (L1/L2/L3/L4) dan Hermes (SKILL.md).
- [x] Loop umpan balik: refleksi hanya untuk role dengan tool `memory` dan pesan ≥8 karakter, dengan
  transkrip 8 pesan sebelumnya; blok memori mendahulukan entri yang cocok dengan pesan terakhir lalu
  yang terbaru, plus tanggal update; indeks skill menampilkan jumlah dan tanggal pemakaian dari
  `.usage.json` agar `archive` berbasis bukti.
- [x] Supersede memori: `memory add` menerima `supersedes=<id>` (harus aktif, satu scope, satu kind);
  baris lama ditandai `superseded_by` dan disembunyikan dari prompt/pencarian tanpa dihapus. Refleksi
  menerima blok memori aktif dan memakai jalur yang sama; supersede invalid jatuh ke add biasa. DB
  lama dimigrasi otomatis saat start.
- [x] Pengiriman file hasil kerja: tool `send` dan command `/kirim` (owner + member/panitia)
  mengirim dari workspace ke chat aktif lewat Neonize, dengan batas ukuran dan pencatatan ke
  jendela konteks. Jalur dipilih dari isi file: stiker WebP ≤512 px (transparan/animasi) sebagai
  stiker, JPEG/PNG/WebP lain sebagai foto, sisanya dokumen; caption stiker dikirim sebagai teks.
- [x] Kirim ke grup lain: tool `send_to` (default owner) menerima nama/potongan nama/JID grup yang
  sudah ada di arsip, mengirim teks dan/atau file, menolak target ambigu, dan langsung mencatat
  pesan keluar di jendela + `history_db` chat tujuan. Pesan masuk semua grup sudah dicatat live
  sebelum filter mention, jadi tidak perlu tool fetch terpisah.

## Nanti

- Integrasi penuh (fetch URL, parsing PDF/audio/OCR, calendar/email) — ditunda; sementara lewat
  MCP atau `sandbox`.
- State tugas persisten/crash recovery (job store, resume setelah restart) — opsional, ditunda
  sampai ada kebutuhan tugas panjang.

Di luar cakupan: transkripsi audio/video dan parsing PDF di dalam Sanka (lewat `sandbox`), Google
Docs/Sheets, browser, gateway HTTP, bridge Node.
