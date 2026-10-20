# ADR — Architecture Decision Records

Aturan: append-only. Jangan menimpa ADR lama. Untuk mengubah keputusan, tambah ADR baru dan tandai yang lama `Superseded by ADR-XXXX`.
Status: `Proposed` (usulan, belum disetujui owner), `Accepted`, `Superseded`, `Rejected`.

## Template
```
## ADR-XXXX — Judul
- Status:
- Tanggal:
- Konteks:
- Keputusan:
- Konsekuensi:
```

---

## ADR-0001 — Harness berbasis GenericAgent
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Tidak ingin merancang agent dari nol. GenericAgent kecil (inti sekitar 3K baris, loop sekitar 100 baris), punya memori berlapis dan mekanisme skill.
- Keputusan: Pakai GenericAgent sebagai basis arsitektur dan harness, dimodifikasi penuh.
- Konsekuensi: Perlu memahami `agent_loop.py` dan `llmcore.py`. SOP bawaan berbahasa Mandarin, L0 ditulis ulang dalam bahasa Indonesia. Tool bawaan yang berbahaya untuk bot publik (code_run, kontrol browser/JS, file_write) dibatasi lewat registry (ADR-0006, ADR-0007). Modifikasi dicatat di `UPSTREAM.md`.
- Koreksi 2026-10-20: `docs/UPSTREAM.md` dibatalkan. Modifikasi upstream dicatat lewat komentar `SANKA-MOD:` di kode, ADR terkait, dan `docs/LOG.md`.

## ADR-0002 — Gateway WhatsApp dari Hermes Agent saja
- Status: Superseded by ADR-0013
- Tanggal: 2026-10-20
- Konteks: Owner sudah memakai Hermes Agent dan nyaman dengan gateway WhatsApp-nya.
- Keputusan: Ambil hanya sistem gateway WhatsApp dari Hermes. Bagian lain tidak dipakai. Dimodifikasi agar reasoning dan tool trace tidak dikirim ke WhatsApp.
- Konsekuensi: Perlu memetakan batas antara I/O pesan dan pemanggilan agent di `gateway/` Hermes, lalu membuat antarmuka tipis `on_message(msg) -> reply`. Risiko pemblokiran nomor pada jalur tidak resmi perlu dijaga dengan rate limit.

## ADR-0003 — Routing hybrid: "/" vs LLM
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Butuh kepastian untuk perintah rutin dan kemudahan ngobrol bebas.
- Keputusan: Pesan diawali `/` ditangani command registry deterministik. Pesan tanpa `/` ditangani LLM.
- Konsekuensi: Command registry harus bisa dites tanpa LLM. Perlu daftar command dan izin per peran (`COMMANDS.md`).

## ADR-0004 — Di grup hanya merespons mention
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Merespons semua pesan grup akan berisik dan boros kuota.
- Keputusan: Di grup, Sanka memproses pesan tanpa `/` hanya bila di-mention atau dipanggil `@sanka` (dan reply ke Sanka). Di chat pribadi semua pesan tanpa `/` diproses.
- Konsekuensi: Normalizer harus mendeteksi mention dan reply. Perlu tes untuk variasi penulisan nama.

## ADR-0005 — Tool dibundle native, bukan MCP
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Jumlah tool sedikit (sekitar 5 sampai 6). Lebih sederhana dan cepat bila satu proses.
- Keputusan: Tool berupa fungsi Python dalam proses yang sama. Skema input/output eksplisit agar mudah dibungkus MCP nanti.
- Konsekuensi: Jika kelak dibutuhkan agent lain, tambah lapisan MCP tanpa mengubah logika tool.
- Koreksi 2026-10-20: jumlah tool aktual 10 (lihat tabel di `ARCHITECTURE.md`), bukan 5–6. Keputusan tetap: native bundle.

## ADR-0006 — Izin tool berbasis peran, ditegakkan di kode
- Status: Accepted (prinsip). Detail matriks: lihat ADR-0011.
- Tanggal: 2026-10-20
- Konteks: Prompt saja tidak cukup mencegah penyalahgunaan di grup.
- Keputusan: `code_run`, `file_write` bebas, dan shell hanya untuk owner. Shell juga butuh toggle di konfigurasi. Penegakan di registry tool, bukan hanya prompt.
- Konsekuensi: Tool tak berizin tidak ditawarkan ke LLM pada giliran itu dan ditolak lagi saat eksekusi.
- Koreksi 2026-10-20: `code_run`, `file_write`, dan shell hanya untuk owner. Kata "bebas" pada keputusan di atas berarti bebas bagi owner, bukan untuk peran lain. Shell tetap butuh toggle config.

## ADR-0007 — Tool browser tetap dipertahankan
- Status: Accepted (keputusan). Profil khusus: Proposed.
- Tanggal: 2026-10-20
- Konteks: Owner meminta browser tetap ada. Browser GenericAgent memakai sesi Chrome asli yang menyimpan login.
- Keputusan: Tool browser dipertahankan. Usulan pengamanan: profil Chrome khusus Sanka yang hanya terlogin ke akun yang dibutuhkan, idealnya di mesin terpisah.
- Konsekuensi: Perlu menyiapkan profil dan, bila diterima, memisahkan lingkungan. Siapa saja yang boleh memakai browser ditentukan di ADR-0011.

## ADR-0008 — Self-evolution aktif, belajar dari semua pengguna
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Owner ingin Sanka makin jarang salah lewat belajar dari pengalaman. Agent menyarankan membatasi sumber belajar ke owner/panitia; owner menolak saran itu dan memilih belajar dari semua pengguna, dengan manajemen oleh owner.
- Keputusan: Mekanisme skill/memori GenericAgent diaktifkan dan boleh belajar dari semua pengguna. Owner yang mengurus bila ada masalah.
- Konsekuensi: Ada risiko skill terbentuk dari masukan yang memanipulasi. Mitigasi yang diusulkan (belum disetujui, ADR-0011): catat asal-usul skill, serta `/skill list` dan `/skill rm`.

## ADR-0009 — Jawaban singkat dengan dua lapis
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Pengguna tidak butuh reasoning dan trace tool; instruksi prompt saja sering terlupa.
- Keputusan: Lapis 1: instruksi persona. Lapis 2: formatter di kode hanya meneruskan teks final, menekan progres tool (cukup indikator mengetik), dan membatasi panjang kecuali jenis respons rekap. `/verbose` untuk owner.
- Konsekuensi: Perlu penanda jenis respons "rekap" di pipeline.

## ADR-0010 — Rekap dihitung di kode
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Angka salah pada acara berdampak nyata.
- Keputusan: Agregasi dilakukan di kode. LLM hanya merangkai kalimat dari hasil hitung.
- Konsekuensi: Tool recap harus mengembalikan hasil terstruktur dan teruji.

## ADR-0011 — Matriks izin tool, asal-usul skill, dan akses Workspace
- Status: Proposed
- Tanggal: 2026-10-20
- Konteks: Matriks izin per peran belum diputuskan. Beberapa pengamanan baru berupa saran.
- Keputusan (usulan):
  1. Matriks izin seperti di `SECURITY.md`.
  2. Catat asal-usul setiap skill dan sediakan `/skill list` dan `/skill rm` untuk owner.
  3. Akses Workspace lewat service account dengan allowlist file/folder. Read default, write terbatas.
  4. Pengiriman proposal butuh `/approve` dari owner.
- Konsekuensi: Perlu persetujuan owner. Setelah disetujui, ubah status menjadi `Accepted` lewat ADR turunan atau catatan di LOG.

## ADR-0012 — LLM: OpenCode Go
- Status: Accepted (pilihan penyedia). Detail API: terverifikasi 2026-10-21.
- Tanggal: 2026-10-20
- Konteks: Owner memakai inferensi OpenCode Go.
- Keputusan: LLM utama memakai OpenCode Go.
- Konsekuensi: Format API (apakah kompatibel dengan format OpenAI) belum diverifikasi. Diverifikasi di plan-2 sebelum menulis adapter di `llmcore.py`.
- Catatan verifikasi 2026-10-21: dokumentasi OpenCode Go menyebut base URL `https://opencode.ai/inference/go/openai/v1` untuk model `deepseek-v4.1-flash` melalui `POST /chat/completions` (OpenAI-compatible), dengan `x-opencode-session` dan User-Agent sendiri. `config/sanka.example.yaml` sudah memakai base URL itu. Live smoke pada 2026-10-21 berhasil: `Authorization: Bearer <API key>`, `x-opencode-session`, dan User-Agent diterima, dan respons `ok` diterima dari model. Plan-2 menjadi `Done`.
- Catatan 2026-10-20: OpenCode Go adalah langganan nyata ($10/$40 per bulan) yang ditujukan untuk OpenCode dan coding agent sejenis; traffic dipantau untuk penyalahgunaan. Limit per model: 5 jam = 20%, minggu = 50%, bulan = 100% dari kuota bulanan. Klien disarankan mengirim `x-opencode-session` dan user-agent sendiri. Bot WhatsApp 24/7 berisiko kena limit atau dianggap traffic tidak wajar. Wajib diverifikasi di plan-2: base URL, apakah API key Go berlaku di luar OpenCode, dan provider fallback.


## ADR-0013 — Gateway WhatsApp: ambil yang berguna dari Hermes, adaptasi, jangan vendor mentah
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: ADR-0002 memilih "ambil hanya sistem gateway WhatsApp dari Hermes". Audit 2026-10-20 menunjukkan adapter WhatsApp Hermes adalah plugin yang bergantung pada `BasePlatformAdapter` dan core gateway yang besar. Owner mengklarifikasi: bukan berarti tidak mengambil sama sekali; ambil kode yang berguna bila bisa, lalu ubah dan adaptasi. Yang tidak boleh adalah menyalin mentah tanpa perubahan.
- Keputusan:
  1. Kode dan pola Hermes boleh diambil sebagian bila memberi nilai. Setiap bagian yang diambil diubah/diadaptasi, mempertahankan notice MIT, dan ditandai `SANKA-MOD:`.
  2. `gateway/` adalah bagian Sanka sendiri dengan pola bridge: proses terpisah memegang koneksi WhatsApp Web (Baileys, MIT), lalu berkomunikasi dengan proses Python lewat HTTP lokal. Potongan kode bridge, allowlist, atau gating Hermes boleh dipakai ulang setelah diadaptasi.
  3. Perilaku yang diadopsi dari Hermes: reconnect dengan backoff, session persistence di luar repo, long-poll inbound, endpoint kirim/typing, allowlist dan gating grup, deteksi mention/reply, idempotensi kirim, dan rate limit.
  4. Antarmuka ke inti Sanka tetap tipis: `on_message(msg)` menaruh pesan di antrean async per chat; balasan dikirim lewat gateway tanpa membawa reasoning atau trace tool.
  5. Atribusi Hermes dan Baileys dicantumkan di `README.md`. Notice MIT upstream wajib dipertahankan untuk kode yang dipakai ulang.
  6. WhatsApp Cloud API resmi tetap opsi cadangan bila risiko blokir nomor pada jalur tidak resmi tidak dapat diterima. Perpindahan jalur dicatat sebagai ADR baru.
- Konsekuensi: Tidak harus ada dependency ke paket Hermes; kode yang diadaptasi menjadi bagian repo Sanka. Setiap adaptasi dicatat di ADR/LOG dan dicek kepatuhan lisensinya saat review. Risiko pemblokiran nomor pada jalur Baileys tetap ada dan dimitigasi dengan rate limit serta perilaku non-spam.

## ADR-0014 — Konfigurasi: satu YAML non-secret, secrets lewat env
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Dokumen sebelumnya menyebut dua sumber config (`sanka.yaml` dan `mykey.py`) sehingga rawan duplikasi dan kebocoran secrets.
- Keputusan:
  1. `config/sanka.yaml` adalah satu-satunya sumber konfigurasi non-secret: model, daftar owner/panitia, allowlist Workspace, toggle shell, rate limit, batas panjang jawaban, session TTL.
  2. Secrets (API key, token, kredensial service account, sesi WhatsApp) hanya dari environment variable atau `config/secrets.env` dengan permission 0600. Keduanya di-ignore git.
  3. `config/sanka.example.yaml` dan `config/secrets.example.env` disediakan sebagai contoh, tanpa nilai rahasia.
  4. Adapter Sanka memetakan YAML + env ke bentuk `cfg` yang diharapkan `llmcore.py`; tidak ada `mykey.py` duplikat.
- Konsekuensi: Startup harus gagal cepat bila env wajib tidak ada. Owner hanya perlu mengelola satu file config non-secret.
- Catatan 2026-10-21: `.env` di root repo juga diterima sebagai fallback setelah environment dan `config/secrets.env`. Urutannya: environment, `config/secrets.env`, lalu `.env`; semua file wajib 0600 dan tetap di-ignore git. Alasan: owner memakai `.env` untuk pengembangan lokal.

## ADR-0015 — Bahasa implementasi: Python untuk inti, Node.js hanya untuk bridge WhatsApp
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Inti Sanka memakai GenericAgent yang ditulis dengan Python, dan tool bundle direncanakan sebagai fungsi Python dalam satu proses. Di sisi lain, jalur WhatsApp Web yang dipakai Hermes adalah bridge Node.js dengan Baileys. Perlu ditetapkan bahasa mana yang dipakai di bagian mana agar tidak ada dua implementasi inti.
- Keputusan (usulan):
  1. Inti Sanka (router, command, policy, formatter, agent, tool, tes) memakai **Python 3.12**, dengan rentang 3.11–3.13. Hindari 3.14 karena dependency GenericAgent.
  2. Bridge WhatsApp memakai **Node.js LTS + Baileys** sebagai proses terpisah. Bridge hanya I/O: terima/kirim pesan, typing, media, dan session. Tidak ada logika bisnis Sanka di Node.
  3. Bahasa yang dilihat pengguna adalah **Bahasa Indonesia** (lihat `PERSONA.md`). Identifier dan komentar kode tetap bahasa Inggris.
  4. Dependency Python dikelola lewat `pyproject.toml`; dependency bridge lewat `package.json` di direktorinya sendiri.
- Alternatif yang dipertimbangkan:
  - **Rust untuk inti**: ditolak. GenericAgent, `llmcore.py`, memori, dan tool bundle sudah Python; memakai Rust berarti menulis ulang basis yang sudah dipilih, atau menempelkan Python lewat FFI. Bot ini I/O-bound dan latensinya didominasi LLM, jadi keunggulan performa Rust tidak memberi nilai sebanding dengan biaya rewrite. Rust tetap boleh dipakai nanti untuk komponen kecil yang terbukti bottleneck, bukan untuk inti.
  - **Python saja + WhatsApp Cloud API resmi**: satu bahasa, tetapi butuh akun Meta Business, webhook publik, dan biaya percakapan. Dicatat sebagai opsi cadangan di ADR-0013.
  - **Semua Node.js**: ditolak karena harness GenericAgent, memori, dan tool sudah Python; menulis ulang berarti membuang basis yang sudah dipilih.
- Konsekuensi: deployment membutuhkan dua runtime (Python dan Node) hanya saat gateway aktif. Bagian inti tetap bisa dites dengan Python tanpa Node. Jika kelak pindah ke Cloud API, bridge Node bisa dihapus tanpa mengubah inti.
- Catatan 2026-10-21: batas atas `<3.14` dicabut. Sanka hanya mengadaptasi `agent_loop.py`, bukan dependency GenericAgent, dan dependency runtime (`httpx`, PyYAML) mendukung Python 3.15. `requires-python` menjadi `>=3.11`; target produksi tetap 3.12. Editable install dan tes dijalankan di 3.15.

## ADR-0016 — Sesi per chat dan antrean serial per chat
- Status: Accepted
- Tanggal: 2026-10-21
- Konteks: Jalur `AGENT` butuh konteks percakapan dan harus menangani pesan beruntun. Dua pesan dari chat yang sama tidak boleh menulis riwayat bersamaan, sedangkan chat berbeda sebaiknya tidak saling menunggu. Command `/new` dan `/stop` juga butuh batas yang jelas.
- Keputusan (usulan):
  1. Satu sesi per `chat_id` (grup dan DM terpisah), disimpan in-memory: riwayat pesan, `session_id` stabil untuk header OpenCode Go, `updated_at`, dan status berjalan. Tidak ada persistensi ke disk di plan-2; restart menghapus semua sesi.
  2. Sesi kedaluwarsa setelah `limits.session_idle_ttl_minutes` tanpa aktivitas. Pembersihan lazy saat sesi diakses, bukan timer global.
  3. Riwayat dipangkas dari pesan tertua bila melewati batas karakter (60.000 karakter untuk plan-2). Pesan `assistant` di awal hasil pangkasan dibuang agar riwayat dimulai dari `user`.
  4. Setiap chat punya antrean FIFO sendiri dan dieksekusi satu per satu. Chat berbeda boleh berjalan paralel. Jumlah chat aktif dibatasi (16); bila penuh, pengirim menerima balasan "sedang sibuk" alih-alih menunggu tanpa batas.
  5. Rate limit per pengirim dari `policy.RateLimiter` dicek sebelum pekerjaan masuk antrean.
  6. `/stop` hanya menghentikan pekerjaan di chat pengirim. Boleh dilakukan pengirim pekerjaan itu atau owner. Pekerjaan yang masih antre dibuang; panggilan HTTP yang sedang berjalan tidak diputus paksa, hasilnya dibuang setelah selesai (kooperatif). Ini batas jujur untuk plan-2.
  7. `/new` menghentikan pekerjaan berjalan, membuang antrean chat itu, menghapus riwayat, dan membuat `session_id` baru agar cache prompt lama tidak terbawa.
- Alternatif yang dipertimbangkan:
  - **Satu antrean global**: ditolak karena satu rekap panjang memblokir semua chat.
  - **Tanpa antrean, proses paralel per pesan**: ditolak karena riwayat sesi bisa saling menimpa dan urutan balasan kacau.
  - **Broker eksternal (Redis/RabbitMQ)**: ditolak untuk plan-2; belum ada kebutuhan multi-proses.
- Konsekuensi: `/stop` tidak menghemat kuota untuk request yang sudah terkirim. Persistensi dan pemulihan sesi setelah restart ditunda sampai gateway/deployment membutuhkannya.

## ADR-0017 — Integrasi harness: adaptasi `agent_loop.py`, client OpenCode Go sendiri
- Status: Accepted
- Tanggal: 2026-10-21
- Konteks: GenericAgent menyediakan loop agent kecil (132 baris) tetapi `llmcore.py` besar (1.270 baris) dan berisi banyak protokol yang tidak dipakai Sanka (Claude native, Codex, mixin, mykey). OpenCode Go menyediakan endpoint OpenAI-compatible.
- Keputusan (usulan):
  1. Salin `agent_loop.py` dari GenericAgent commit `e6b1bcec92e7789eb80975980b5fd47a2abea860` ke `core/agent_loop.py`, dengan notice MIT `Copyright (c) 2025 lsdefine`, lalu adaptasi minimal yang ditandai `SANKA-MOD:`.
  2. Tidak memakai `llmcore.py`. Client ditulis sendiri di `sanka/agent/client.py` dengan `httpx`, terbatas pada `POST {base_url}/chat/completions` mode non-streaming.
  3. Client mengirim `Authorization: Bearer <API key>`, `x-opencode-session: <session_id>` per chat, dan User-Agent `sanka-agent/<versi>`. API key tidak pernah masuk log atau pesan.
  4. Respons dipetakan ke `ChatResponse(content, tool_calls, usage)`. Hanya `content` yang boleh keluar; `reasoning_content` dan trace tool dibuang.
  5. Plan-2 tidak mendaftarkan tool apa pun. `tool_calls` sudah diurai tetapi belum dieksekusi; tool masuk lewat registry di plan-4.
  6. Retry hanya untuk 429 dan 5xx, maksimal 3 kali dengan backoff eksponensial dan menghormati `Retry-After`. Error 4xx lain langsung gagal dengan pesan yang tidak memuat API key atau isi pesan pengguna.
  7. Dependency baru: `httpx` (runtime) dan `respx` atau `httpx.MockTransport` untuk tes tanpa jaringan. Tes memakai `MockTransport`, jadi tidak perlu dependency tambahan.
- Alternatif yang dipertimbangkan:
  - **Vendor `llmcore.py` utuh**: ditolak karena menambah 1.270 baris yang tidak terpakai, memperbesar diff upstream, dan membawa asumsi provider lain.
  - **OpenAI SDK resmi**: ditolak untuk plan-2 karena kontrol header `x-opencode-session` dan penanganan error lebih mudah dilakukan langsung dengan `httpx`.
  - **Streaming token**: ditunda. Plan-2 memakai non-streaming agar satu request = satu respons utuh; streaming baru berguna saat gateway perlu typing/partial (plan-3).
- Konsekuensi: client menjadi permukaan kompatibilitas yang harus dites; perubahan format API OpenCode Go cukup diperbaiki di satu file. Latensi terasa sampai respons selesai, tetapi tidak ada token yang dikirim ke WhatsApp secara parsial.


## ADR-0018 — Kontrak HTTP gateway Python ↔ bridge WhatsApp
- Status: Accepted
- Tanggal: 2026-10-21
- Konteks: Plan-2 sudah menyelesaikan agent core. Diperlukan batas yang jelas antara proses Python yang memegang routing/policy/agent/formatter dan proses Node yang memegang koneksi WhatsApp. Tanpa kontrak tetap, plan-3 dan plan-4 tidak bisa dikerjakan atau dites secara terpisah.
- Keputusan:
  1. Bridge **mendorong** pesan masuk ke Python lewat `POST /v1/inbound`; Python tidak long-poll. Bridge tetap boleh retry dengan `message_id` yang sama, dan Python membalas `duplicate` untuk id yang sudah diproses.
  2. Python memanggil bridge lewat `POST /v1/send`, `/v1/typing`, dan `/v1/read`. `send` memakai `idempotency_key`; `409` berarti item itu sudah pernah diterima dan dianggap sukses.
  3. Semua endpoint hanya menerima koneksi loopback dan token bersama `X-Sanka-Token` dari env yang namanya ditentukan `gateway.auth_token_env`. `/healthz` hanya metadata.
  4. Python adalah pemilik normalizer, allowlist grup/DM, peran, rate limit, command registry, agent runtime, dan formatter. Bridge tidak memutuskan bisnis apa pun dan tidak memformat teks.
  5. Server Python memakai stdlib `ThreadingHTTPServer`, batas body 64 KiB, dan validasi loopback saat start. Tidak ada dependency server baru.
  6. Semua keluaran melewati `Formatter`; `reasoning`, trace tool, dan isi pesan tidak masuk log.
- Alternatif yang dipertimbangkan:
  - **Python long-poll bridge**: ditolak untuk plan-3 karena bridge sudah punya koneksi persisten dan bisa retry sendiri; push + idempotensi lebih sederhana.
  - **Bridge memanggil agent langsung / memegang config**: ditolak karena menduplikasi policy di Node.
  - **FastAPI/uvicorn**: ditolak karena satu klien loopback tepercaya tidak membutuhkan ASGI/routing tambahan.
- Konsekuensi: Plan-4 wajib mengikuti kontrak ini; perubahan endpoint/status harus lewat ADR baru. Plan-3 bisa dites penuh dengan bridge palsu tanpa WhatsApp. Token adalah rahasia dan tidak boleh masuk repo/log.
