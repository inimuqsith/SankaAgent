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
- [x] Memori autografis: ledger `actions` di `memory.db` mencatat asal, tujuan, jenis, ringkasan,
  dan permintaan setiap kiriman `send_to` yang sukses (gagal tidak dicatat). Lima aksi terakhir
  diinjeksi ke prompt role yang punya `send_to`, jadi chat tujuan tahu konteks artefak tanpa
  menggabungkan transkrip; isi/permintaan berkredensial tidak disimpan. Model lima lapis memori
  (identitas, episodik, semantik, prosedural, autografis) didokumentasikan di `README.md`.
- [x] Data runtime di dalam project: path relatif di `config.yaml` di-resolve dari folder config
  (bukan CWD); default `.sanka/` berisi workspace, DB sesi/arsip/memori, skill, dan log, serta
  di-ignore Git. Migrasi dari `~/.sanka` mempertahankan arsip dan memori; config yang masih
  menunjuk path lama ditolak saat start.

- [x] Command observability tanpa LLM: `/whoami` (pengirim, role, chat, bot), `/status` (model,
  arsip, memori, skill, tool), `/tools` (tool yang diizinkan role), `/cari` (arsip chat aktif),
  `/help <command>` (usage/alias/izin), `/mood` (ringkasan emosi, PAD, trust, pulse, drive,
  dan hubungan dengan pengirim), dan alias `/new` untuk `/reset`.
- [x] Arah pesan diputuskan model, bukan regex: satu keputusan LLM (`sanka/decision.py`) menerima
  `prompt_entry` (nama, teks, label media, kutipan) plus fakta transport (DM / mention asli /
  balasan ke pesan Sanka), lalu memilih `addressed`, `act`, `mode`, `tone`, `bubbles`, appraisal,
  dan delta hubungan. Tidak ada regex nama/arah/mode; prompt melarang mengarang kondisi tubuh dan
  menuduh ngambek dari stiker saja.
- [x] Kirim ke chat pribadi (CP): `send_to` juga me-resolve kontak arsip (`contacts` +
  `lid_map`), bukan hanya grup, dan prompt mewajibkan panggilan tool yang sukses sebelum Sanka
  mengaku sudah mengirim ke chat lain.

- [x] Dua engine: emosi (`emotion-engine` di-vendor, MIT) dan percakapan (keputusan + balasan +
  tool). `sanka/affect.py` menyimpan state PAD/trust/pulse per chat di `affect.state_dir` dan
  relasi (`people`, `chat_people`, `topics`, `drives`) di `affect.db`; soul/karakter pindah ke
  `SOUL.md` dan menjadi baseline personality engine.
- [x] Keputusan dan penilaian emosi dalam satu panggilan: `sanka/decision.py` meminta
  `addressed`/`act`/`mode`/`tone`/`bubbles`/`appraisal`/`relationship`, lalu harness menulis
  `record_turn` (P/A/D + label + catatan) dan delta relasi. Tidak ada `LLM.appraise`, tidak ada
  `classify_intent_fallback`, dan tidak ada `ReplyGate`/skor deterministik.
- [x] Tanpa state diam: tidak ada sulk/nangis yang dipaksakan harness dan tidak ada janji "diam N
  menit". Mention asli/DM/balasan ke pesan Sanka wajib dibalas (tone boleh dingin), obrolan lain
  boleh di-ignore; `/calmdown` adalah repair turn (PAD ke baseline, kesal turun setengah).
- [x] Keputusan per pesan memilih `act` (`reply`/`ignore`/`react`), `mode` daily/professional, dan
  `tone` (warm/neutral/playful/curt/hurt/angry); command tidak memanggil keputusan dan tidak
  mengubah emosi, krisis dibalas tanpa keputusan dan tanpa mengubah emosi.
- [x] Balasan manusia: satu burst 1-3 bubble pendek (`human_chunks`, batas dari `humanize`), jeda
  mengetik dari panjang teks, chunk pertama mengutip pesan. Tidak ada double-text otomatis; pesan
  yang masuk sebelum burst terakhir dianggap terwakili sehingga tag beruntun dijawab sekali.
- [x] Heartbeat 24/7: `AffectStore.pulse` menjalankan satu langkah decay (maks 1 per 5 menit) dan
  menumbuhkan drive tiap `ambient.tick_seconds` untuk semua chat yang dikenal (live +
  `recent_chats`); chime spontan bergerbang dorongan drive + cooldown harian.
- [x] Profil gaya per chat dan per orang (`person_profile`) dari agregat arsip (median panjang,
  huruf kecil, partikel, emoji, tawa, tanda tanya) tanpa menyimpan teks, dengan seed acuan
  `references/*.md` bila sampel masih sedikit; orang yang dikenal hanya anggota chat itu.
- [x] Otonomi terbatas (`ambient`): digest topik + fakta ke memori (baris pesan Sanka dibuang),
  riset `web_search` saat chat sepi, chime grup sesekali. Tool context hanya `web_search` dan
  tidak pernah DM.
- [x] Persona laki-laki dengan ekspresi emosional feminin, bukan asisten; balasan pendek dan tidak
  mengaku AI. Command baru `/calmdown` (owner).
- [x] Anggaran prompt (`prompt.max_chars`, default 9000): blok opsional (topik, gaya, person style,
  memori, aksi, skill) bersaing lewat `sanka/salience.py` saat prompt melewati anggaran; blok wajib
  (base, safety, away, thread, emosi, vibe) tidak pernah dibuang. Bonus relevansi dari topik
  pending, interaksi orang, familiaritas, dan mode professional.
- [x] Harness ablasi read-only `sanka/ablate.py` + `scripts/ablate.py`: replay gerbang balasan dari
  arsip (`called`, `thread_allowed`, `false_positive`, `missed`), ukuran prompt sebelum/sesudah
  kompetisi, dan pergeseran `addressed`/`act`/`tone` saat `emotion`/`vibe`/`thread`/`away` diabsi.
- [x] Aturan situasi percakapan menjadi fungsi murni `sanka/thread.py` (dengan `keyword_set` dan
  `followup_utterance` di `sanka/chat.py`), dipakai runtime dan harness ablasi.

## Nanti

- Integrasi penuh (fetch URL, parsing PDF/audio/OCR, calendar/email) — ditunda; sementara lewat
  MCP atau `sandbox`.
- State tugas persisten/crash recovery (job store, resume setelah restart) — opsional, ditunda
  sampai ada kebutuhan tugas panjang.

Di luar cakupan: transkripsi audio/video dan parsing PDF di dalam Sanka (lewat `sandbox`), Google
Docs/Sheets, browser, gateway HTTP, bridge Node.
