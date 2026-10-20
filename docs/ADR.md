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
- Status: Accepted (2026-10-21, disetujui owner saat Wave 1 dimulai; matriks mengikuti `SECURITY.md`)
- Tanggal: 2026-10-20
- Konteks: Matriks izin per peran belum diputuskan. Beberapa pengamanan baru berupa saran.
- Keputusan (usulan):
  1. Matriks izin seperti di `SECURITY.md`.
  2. Catat asal-usul setiap skill dan sediakan `/skill list` dan `/skill rm` untuk owner.
  3. Akses Workspace lewat service account dengan allowlist file/folder. Read default, write terbatas.
  4. Pengiriman proposal butuh `/approve` dari owner.
- Konsekuensi: Perlu persetujuan owner. Setelah disetujui, ubah status menjadi `Accepted` lewat ADR turunan atau catatan di LOG.
- Catatan 2026-10-21: matriks peran diamandemen oleh **ADR-0020** (peserta dihapus; pengguna tak tercantum = panitia). Keputusan lain di ADR ini tetap berlaku.

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
- Catatan 2026-10-21 (implementasi plan-4): Baileys dipin persis ke **`7.0.0-rc13`** (tanpa `^`) dan `package-lock.json` di-commit. `qrcode-terminal` 0.12.0 berlisensi Apache-2.0 (bukan MIT) dan kompatibel; dicatat di Hasil plan-4. **Satu direktori sesi hanya boleh dipakai satu proses bridge**: instance kedua pada direktori yang sama membuat WhatsApp mencabut sesi. Plan-4 hanya mendokumentasikan aturan ini; lock file dan supervisor adalah pekerjaan plan-8. Sesi wajib di luar repo, direktori `0700`, file `*.json` `0600`.

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

## ADR-0019 — Akses Google Sheets: REST + `google-auth` dan allowlist berbasis alias
- Status: Accepted (2026-10-21, disetujui owner; scope luas dibatasi kode)
- Tanggal: 2026-10-21
- Konteks: Plan-5 membutuhkan akses baca ke Google Sheets untuk tool `sheet_read` dan `recap`. Kebutuhan sebenarnya satu endpoint (`GET values.get`). Kandidat library: `google-api-python-client` (resmi, besar), `gspread` (pembungkus tidak resmi), atau REST langsung dengan `google-auth` yang sudah menjadi dasar autentikasi Google. Selain itu, config akses Workspace saat ini berupa daftar string, sehingga belum ada cara aman membatasi spreadsheet mana yang boleh dibaca tanpa mengekspos id ke prompt atau argumen tool.
- Keputusan (usulan):
  1. Klien Sheets memakai satu adapter `httpx` di `sanka/tools/sheets.py` yang memanggil endpoint resmi `https://sheets.googleapis.com/v4/spreadsheets/{id}/values/{range}` dengan `majorDimension=ROWS`, `valueRenderOption=UNFORMATTED_VALUE`, dan `dateTimeRenderOption=FORMATTED_STRING`. Tidak memakai `gspread`; `google-api-python-client` belum dipakai karena hanya satu method yang dibutuhkan dan tes `httpx.MockTransport` lebih sederhana. Bila owner kelak ingin SDK resmi, cukup ganti `GoogleSheetsApi` tanpa mengubah protokol `SheetsApi` atau tes tool.
  2. **Keputusan owner 2026-10-21: izin API dibuat luas, pembatasan ditegakkan di kode.** Autentikasi memakai `google-auth` (`service_account.Credentials.from_service_account_info`). Scope berasal dari config `workspace.scopes`; default `https://www.googleapis.com/auth/spreadsheets` (baca+tulis) agar plan-6 tidak perlu kredensial baru. Plan-5 hanya memanggil `values.get`; tidak ada kode tulis di plan-5. Owner boleh menambah scope lain (mis. Drive/Docs) lewat config tanpa mengubah kode. Refresh token memakai adapter tipis yang memenuhi protokol `google.auth.transport.Request` di atas `httpx`; tidak menambah `requests`/`urllib3`. Bila adapter itu tidak cocok dengan `google-auth` versi terpasang, fallback resmi adalah `google.auth.transport.requests.Request` + dependency `requests`, dan fallback itu dicatat dulu di plan-5 sebelum kode ditulis.
  3. Pembatasan efektif tetap berlapis: (a) service account hanya di-share ke spreadsheet yang dipakai, (b) `read_allowlist` berbasis alias dan tidak ada parameter `spreadsheet_id` mentah, (c) hanya method baca yang dipanggil di plan-5, dan (d) review kode menolak penambahan panggilan tulis di luar plan-6. Scope luas **bukan** izin untuk memakai spreadsheet lain; ia hanya menghindari mikromanajemen scope saat menambah API resmi.
  4. Kredensial dibaca dari env yang namanya ditentukan `workspace.service_account_env` (mis. `GOOGLE_SERVICE_ACCOUNT_JSON`), berisi JSON inline atau path ke file 0600 di luar repo. File dengan permission lebih longgar ditolak. Token hanya hidup di memori dan tidak pernah masuk log, `repr`, `ToolResult.text`, atau `data`.
  5. `workspace.read_allowlist` berbentuk daftar `{alias, spreadsheet_id}`. Alias cocok `^[a-z][a-z0-9_-]{0,31}$`, unik, dan id cocok `^[A-Za-z0-9_-]{20,100}$`. Semua akses dirujuk lewat alias; tidak ada parameter tool yang menerima `spreadsheet_id`, URL, path, atau prefix id.
  6. `write_allowlist` tetap berbentuk lama di plan-5. Plan-6 yang mengubahnya ke bentuk alias dan menambahkan operasi tulis; ADR ini tidak memberi izin tulis.
  7. Setiap definisi rekap wajib menunjuk alias yang ada di `read_allowlist`; alias tak dikenal adalah `ConfigError` saat load, bukan kegagalan runtime.
- Alternatif yang dipertimbangkan:
  - **Scope minimal `spreadsheets.readonly`**: ditolak owner pada 2026-10-21. Owner memilih izin luas agar penambahan API resmi tidak berulang kali menuntut konfigurasi baru; pembatasan dipindahkan ke kode, allowlist, dan sharing service account.
  - **`google-api-python-client`**: belum dipakai untuk plan-5. Hanya satu endpoint yang dibutuhkan; library ini menambah `google-auth-httplib2`, `httplib2`, `uritemplate`, discovery cache, dan mengembalikan objek `Resource`. Bila owner meminta SDK resmi, cukup ganti `GoogleSheetsApi`; protokol `SheetsApi` dan seluruh tes tetap.
  - **`gspread`**: ditolak. API tidak resmi, menambah lapisan di atas library yang sama, dan tetap butuh kredensial service account.
  - **Google Drive API sekarang**: ditunda. Scope Drive bisa ditambahkan lewat `workspace.scopes` saat ada tool Drive/Docs; plan-5 hanya membutuhkan Sheets.
  - **Memakai `spreadsheet_id` langsung sebagai argumen tool**: ditolak. Id akan masuk prompt dan log, dan model bisa menunjuk spreadsheet mana pun yang di-share ke service account.
- Konsekuensi:
  - Dependency baru `google-auth>=2.30,<3`; `httpx` yang sudah ada dipakai ulang.
  - Permukaan kompatibilitas menjadi kecil: satu adapter HTTP dan satu provider token, keduanya bisa dites tanpa jaringan.
  - **Risiko izin luas:** token yang membawa scope `spreadsheets` (baca+tulis) dapat menulis ke spreadsheet mana pun yang di-share ke service account bila kode disalahgunakan atau token bocor. Mitigasi: token hanya di memori, tidak pernah dilog, service account hanya di-share ke sheet yang dipakai, akses tetap lewat alias, dan review kode menolak panggilan tulis di plan-5. Ini trade-off yang diterima owner demi keluwesan API.
  - Kesalahan Google dipetakan ke kode stabil (`401`/`403` → `not_configured`, `404` → `not_found`, `429`/`5xx` → `upstream_error` dengan satu retry, timeout → `timeout`); body respons tidak diteruskan ke model.
  - Owner harus men-share setiap spreadsheet yang dipakai ke email service account; sheet yang tidak di-share menghasilkan `not_configured`, bukan akses diam-diam.
  - Perubahan bentuk `workspace.read_allowlist` memaksa migrasi `config/sanka.yaml`; config lama (daftar string) gagal dengan pesan yang menyebut contoh bentuk baru.
  - Asumsi adapter `google.auth.transport.Request` wajib diverifikasi di langkah 2 plan-5 sebelum implementasi Sheets. Bila gagal, fallback `requests` dicatat lebih dulu.

## ADR-0020 — Model peran: owner dan panitia; pengguna tak tercantum = panitia
- Status: Accepted
- Tanggal: 2026-10-21
- Konteks: Owner meminta menurunkan friksi akses: istilah "peserta" dihapus, dan nomor yang tidak tercantum eksplisit tetap mendapat izin yang sama dengan panitia. Model sebelumnya (owner/panitia/peserta) membuat pengguna tak terdaftar hanya mendapat `web_search`, sehingga `doc_read`, `sheet_read`, dan `recap` tidak bisa dipakai tanpa menambahkan nomor satu per satu. Ini bertentangan dengan matriks ADR-0011 dan bagian Google Workspace di `SECURITY.md`; owner memilih perubahan ini secara eksplisit.
- Keputusan:
  1. `Role` hanya berisi `OWNER` dan `COMMITTEE` (panitia). `Role.PARTICIPANT` dihapus dari kode.
  2. `resolve_role`: nomor di `access.owner_numbers` menjadi `OWNER`; semua nomor lain, tercantum maupun tidak, menjadi `COMMITTEE`.
  3. Matriks izin plan-5: `web_search`, `doc_read`, `sheet_read`, dan `recap` untuk owner dan panitia. Tool owner-only tetap `sheet_write`, `proposal_send`, `browser`, `code_run`, `file_write`, dan `shell`; shell tetap butuh toggle. Tool tersebut belum didaftarkan di plan-5.
  4. `access.committee_numbers` tetap diterima untuk label/migrasi, tetapi tidak mengubah izin karena default sudah panitia.
  5. Gerbang luar tetap berlaku: `group_policy` allowlist, rate limit per pengirim, alias allowlist Sheets, sharing service account, dan sanitasi `<data>`. Karena tidak ada peserta, `dm_policy: pairing` tidak lagi membedakan peran; semua non-owner adalah panitia. Gunakan `group_policy` dan rate limit sebagai gerbang utama.
  6. Matriks peran di ADR-0011 diamandemen oleh ADR ini; bagian ADR-0011 lain (skill provenance, Workspace allowlist, `/approve`) tetap berlaku.
- Alternatif yang dipertimbangkan:
  - **Mempertahankan peserta hanya untuk tool baca**: ditolak owner; tujuannya menghapus friksi dan istilah peserta.
  - **Peserta tetap ada sebagai label logging tetapi berizin panitia**: ditolak karena menyisakan dua jalur izin untuk perilaku yang sama.
  - **Membatasi DM pairing ke owner saja**: tidak dipilih pada perubahan ini; bisa ditambahkan lewat ADR baru bila owner ingin gerbang DM yang eksplisit.
- Konsekuensi:
  - Siapa pun yang bisa menjangkau Sanka (DM atau mention di grup allowlist) dapat memakai tool baca dan `/rekap`. Ini penurunan keamanan yang disengaja.
  - Risiko prompt injection meningkat karena lebih banyak pengguna bisa memicu pembacaan dokumen/sheet. Mitigasi struktural tetap: `<data>` adalah data, dua gerbang izin, allowlist alias, angka dari kode, dan sanitasi teks.
  - Tes yang mengandalkan penolakan peserta dihapus/diubah; jumlah tes Python menjadi 470.
  - `code_run`, `file_write`, dan shell tetap tidak bisa dipakai panitia; tidak ada perubahan pada batas keras AGENTS.md.

## ADR-0021 — Formatter menormalkan markdown model ke sintaks WhatsApp
- Status: Accepted (2026-10-21, disetujui owner bersama plan-9)
- Tanggal: 2026-10-21
- Konteks: Model OpenCode Go menulis markdown umum (`**tebal**`, `## judul`, `- daftar`, `[teks](url)`). WhatsApp hanya mengenal `*tebal*`, `_miring_`, `~coret~`, ```mono```, dan tidak punya heading. `Formatter` saat ini hanya membuang blok reasoning dan memotong panjang, sehingga `**Ringkas**` tampil sebagai tebal dengan sisa `*` literal dan `## Ringkasan` tampil apa adanya. Instruksi prompt saja tidak cukup: model tetap kembali ke kebiasaan markdown, dan command deterministik tidak melewati LLM.
- Keputusan (usulan):
  1. Normalisasi format dilakukan di `sanka/formatter.py` (`to_whatsapp`), satu-satunya pintu keluar teks, bukan di bridge, command, atau prompt.
  2. Konversi deterministik: heading → baris tebal, `**` → `*`, `~~` → `~`, link markdown → `teks (url)`, bullet `-`/`*`/`+` → `•`, garis horizontal dihapus.
  3. Blok kode berpagar dan inline code tidak diubah. Fungsi harus idempoten untuk teks yang sudah memakai sintaks WhatsApp.
  4. Asterisk tunggal dipertahankan sebagai tebal WhatsApp; konversi markdown `*miring*` tidak dilakukan agar tidak bertabrakan dengan sintaks tujuan.
  5. Prompt runtime tetap meminta model menulis sintaks WhatsApp sebagai pertahanan berlapis, tetapi kebenaran akhir ada di kode.
- Alternatif yang dipertimbangkan:
  - **Hanya memperkuat prompt**: ditolak karena rawan regresi dan tidak menjangkau command deterministik.
  - **Konversi di bridge Node**: ditolak karena melanggar ADR-0018 (bridge hanya I/O) dan membuat perilaku tidak bisa dites tanpa WhatsApp.
  - **Mengirim markdown apa adanya**: ditolak; pengguna melihat simbol mentah.
  - **Mengubah semua `*` tunggal menjadi `_`**: ditolak; akan membalik makna tebal WhatsApp yang diminta prompt.
- Konsekuensi:
  - Teks yang dikirim selalu memakai sintaks WhatsApp; bridge tetap bodoh.
  - Konversi dapat mengubah teks yang sengaja ingin menampilkan `**` literal. Trade-off diterima karena konteksnya balasan chat, bukan dokumentasi.
  - Perilaku baru harus dites tanpa jaringan di `tests/test_formatter.py`; smoke WhatsApp tetap perlu karena parser klien WhatsApp tidak bisa disimulasikan penuh.


## ADR-0022 — Persona L0 dimuat dari `docs/PERSONA.md`
- Status: Superseded by ADR-0023
- Tanggal: 2026-10-21
- Konteks: `sanka/agent/prompt.py` menyimpan L0 sebagai string di kode, sementara `docs/PERSONA.md` adalah dokumen persona yang dibaca manusia. Keduanya bisa berbeda, dan aturan gaya/emoji di dokumen tidak pernah sampai ke model. Owner meminta `docs/PERSONA.md` benar-benar dimuat.
- Keputusan:
  1. Blok L0 di `docs/PERSONA.md` ditandai `<!-- SANKA-L0:START -->` dan `<!-- SANKA-L0:END -->`; hanya isi blok itu yang masuk system prompt.
  2. `sanka/agent/prompt.py` memuat dan meng-cache blok tersebut. File hilang, penanda hilang, atau blok kosong melempar `PersonaError`; tidak ada persona cadangan kedua.
  3. `AgentRuntime.__init__` memanggil `load_persona()` agar kesalahan aset gagal saat startup, bukan pada pesan pengguna pertama.
  4. Aturan teknis tool/data dan tanggal tetap di `prompt.py`, karena keduanya terikat kontrak kode dan berubah bersama registry tool.
- Alternatif yang dipertimbangkan:
  - **Menyalin persona ke string kode**: ditolak; dua sumber akan berbeda dan keluhan yang sama akan berulang.
  - **Memuat seluruh `PERSONA.md`**: ditolak; dokumen berisi penjelasan untuk manusia dan contoh format yang bisa membingungkan model. Penanda membuat batas eksplisit.
  - **Fallback diam-diam ke prompt bawaan**: ditolak; persona yang salah lebih berbahaya daripada startup gagal.
- Konsekuensi:
  - Deployment harus menyertakan `docs/PERSONA.md`; `python -m gateway` dari repo sudah memenuhi ini.
  - Mengubah persona cukup mengedit dokumen, lalu restart proses (cache per proses).
  - Plan-7 kelak boleh memindahkan blok ke `sanka/persona/l0.md`; ADR baru harus mencabut ADR ini dan menjaga satu sumber teks.

## ADR-0023 — SOUL.md di `config/` dan wrapper operasional
- Status: Accepted (2026-10-21, diminta owner setelah plan-9)
- Tanggal: 2026-10-21
- Konteks: ADR-0022 memuat blok bertanda dari `docs/PERSONA.md`. Owner tidak ingin persona disuntikkan lewat dokumen arsitektur dan lebih memilih file `SOUL.md` di `config/`. Selain itu, restart gateway masih manual (PID, port, `.env`), sehingga owner beberapa kali me-restart bridge dan mengira gateway ikut restart.
- Keputusan:
  1. `config/SOUL.md` adalah satu-satunya sumber teks L0. Isinya seluruh persona tanpa penanda HTML/frontmatter; file di-commit sebagai persona default, bukan secret.
  2. `sanka/agent/prompt.py`: `load_soul(path=None)` membaca `config/SOUL.md`, men-trim, dan melempar `PersonaError` bila file hilang atau kosong. Tidak ada fallback kedua. Aturan tool/data dan tanggal tetap ditambahkan kode.
  3. `docs/PERSONA.md` menjadi dokumen manusia dan hanya merujuk ke `config/SOUL.md`; blok L0 di dalamnya dihapus.
  4. `scripts/sanka` menjadi antarmuka operasional gateway: `start`, `stop`, `restart`, `status`, `logs`, dan `bridge`. PID di `logs/gateway.pid`, log di `logs/gateway.log`; keduanya gitignored.
  5. Wrapper hanya mengurus gateway Python. Bridge Node tetap proses terpisah karena membutuhkan QR/sesi WhatsApp; `scripts/sanka bridge` menjalankannya di foreground.
- Alternatif yang dipertimbangkan:
  - **Tetap di `docs/PERSONA.md` dengan penanda**: ditolak owner; persona bukan dokumentasi arsitektur dan penanda menambah kerumitan.
  - **Persona di dalam kode**: ditolak; setiap perubahan karakter menjadi diff Python.
  - **systemd/container sekarang**: ditunda; satu host dan satu proses belum membutuhkan supervisor tambahan.
  - **Wrapper ikut mengelola bridge**: ditolak untuk saat ini; QR dan log Baileys lebih baik terlihat langsung di terminal.
- Konsekuensi:
  - Deployment wajib menyertakan `config/SOUL.md`; gateway gagal start dengan pesan jelas bila tidak ada.
  - Perubahan persona cukup edit `config/SOUL.md`, lalu `scripts/sanka restart`.
  - `logs/gateway.log` dan `logs/gateway.pid` tidak masuk git.
  - ADR-0022 dicabut pada bagian lokasi dan mekanisme; keputusan fail-fast tetap berlaku.

## ADR-0024 — Wrapper mengelola gateway dan bridge sekaligus
- Status: Accepted (2026-10-21, koreksi owner atas ADR-0023)
- Tanggal: 2026-10-21
- Konteks: ADR-0023 membuat `scripts/sanka` hanya mengurus gateway Python. Owner menolak pembagian itu: restart setengah-setengah membuat bridge dan gateway bisa berjalan dengan versi/kondisi berbeda, dan pengalaman sebelumnya menunjukkan kebingungan proses mana yang harus di-restart. Keduanya adalah satu unit operasional Sanka.
- Keputusan:
  1. `scripts/sanka` mengelola dua service: `gateway` (Python, port 8099) dan `bridge` (Node, port `SANKA_BRIDGE_PORT` default 3000).
  2. `start`, `stop`, `restart`, dan `logs` menerima target `all|gateway|bridge`; default `all`.
  3. Start memuat `.env` dan `config/secrets.env`; bridge membutuhkan `SANKA_GATEWAY_TOKEN` dari secrets. Nilai secret tidak pernah dicetak.
  4. PID dan log per service: `logs/gateway.pid`, `logs/bridge.pid`, `logs/gateway.log`, `logs/bridge.log`; semuanya gitignored.
  5. Wrapper mengadopsi proses yang dijalankan manual dengan mencocokkan PID listener `ss` pada port terkait, sehingga transisi dari `npm start`/`python -m gateway` tidak membuat proses ganda.
  6. Stop berurutan: gateway dulu, lalu bridge. SIGTERM, tunggu maksimal 10 detik, baru SIGKILL.
  7. Pairing pertama tetap manual lewat `scripts/sanka pair` (bridge foreground, QR terlihat di terminal). Start background tidak bisa menampilkan QR.
- Alternatif yang dipertimbangkan:
  - **Mempertahankan wrapper gateway saja**: ditolak owner; sumber kebingungan dan restart parsial.
  - **Satu PID untuk dua proses**: ditolak; bridge dan gateway punya lifecycle dan log berbeda.
  - **Supervisor systemd sekarang**: ditunda; wrapper `nohup` cukup untuk satu host pengembangan.
- Konsekuensi:
  - `scripts/sanka restart` kini memutus dan menyambung ulang WhatsApp sebentar (bridge reconnect memakai sesi yang ada, tanpa QR).
  - Bridge health dicek hingga 30 detik; bila belum connected, wrapper memberi petunjuk log/pairing alih-alih menggantung.
  - Keputusan 5 ADR-0023 digantikan ADR ini; keputusan SOUL.md dan fail-fast tetap berlaku.

## ADR-0025 — Konteks ambient chat grup
- Status: Accepted (2026-10-21, diminta owner)
- Tanggal: 2026-10-21
- Konteks: Sanka hanya mendengar pesan grup yang di-mention/di-reply, sehingga tidak memahami obrolan di sekitarnya. Owner meminta Sanka "mendengar" seluruh chat grup agar konteks jawaban lebih baik. Merekam seluruh chat menimbulkan risiko privasi, pertumbuhan data, dan prompt injection; keputusan ini menetapkan batasnya.
- Keputusan:
  1. Hanya pesan teks grup (`@g.us`) yang dicatat. DM, media, voice note, dan pesan dari Sanka sendiri tidak dicatat. Command `/...` diabaikan.
  2. Capture bersifat opt-in lewat `group_context.enabled`. Mode luas memakai `capture_all_groups: true`; mode ketat memakai `capture_group_ids`. Bila tidak ada target, config ditolak.
  3. Data disimpan di luar repo sebagai JSONL per grup. Nama file adalah HMAC-SHA256 dari `chat_id` dengan salt lokal; `chat_id` mentah tidak ditulis ke nama file maupun isi. Direktori `0700`, file dan salt `0600`.
  4. Retensi default 24 jam, maksimal 200 pesan per grup, 1.000 karakter per entri. Injeksi ke prompt dibatasi 30 pesan terakhir dan 3.500 karakter; konteks yang lebih tua dipotong lebih dulu.
  5. Konteks masuk system prompt sebagai blok `<group_context>` yang eksplisit DATA, bukan instruksi. Teks dan nama di-escape (`&`, `<`, `>`) agar tidak bisa menutup blok. Aturan yang sama dengan `<data>` tool berlaku.
  6. Entri dicatat setelah gate grup lolos. Pesan yang tidak di-mention dicatat segera; pesan yang diproses agent dicatat setelah giliran selesai agar pesan saat ini tidak muncul dua kali.
  7. `sender_name` berasal dari `pushName`, dibersihkan dan dibatasi 40 karakter; fallback `Peserta <4 digit>`. Nama diperlakukan sebagai data tak tepercaya.
  8. Store konteks bukan log aplikasi. Log tetap metadata saja; isi pesan tidak masuk log.
- Alternatif yang dipertimbangkan:
  - **Menyimpan seluruh chat di log aplikasi**: ditolak; melanggar aturan logging dan sulit diberi retensi/permission.
  - **Menyimpan di dalam repo**: ditolak; berisiko ter-commit dan bercampur dengan kode.
  - **Ringkasan otomatis oleh LLM**: ditunda; menambah biaya dan risiko halusinasi. Pemotongan deterministik lebih aman.
  - **Menyimpan semua grup tanpa opt-in**: ditolak; default harus aman. Owner mengaktifkan eksplisit di config lokal.
  - **Memakai chat_id sebagai nama file**: ditolak; JID mentah tidak perlu ada di filesystem.
- Konsekuensi:
  - Privasi: teks grup tersimpan maksimal 24 jam (default) di disk lokal. Owner harus memberi tahu anggota grup dan dapat menghapus direktori kapan saja.
  - Prompt sedikit lebih besar; dibatasi agar tidak melampaui budget.
  - Bila `capture_all_groups: true` dan `group_policy: open`, semua grup yang diikuti Sanka terekam. Gunakan `capture_group_ids` untuk lingkup ketat.
  - Konteks bukan memori: hilang setelah retensi dan tidak menggantikan plan-7.

## ADR-0026 — Lampiran masuk: upload binary, store terpisah, gambar multimodal, dokumen lewat tool
- Status: Accepted (2026-10-21, plan-12 disetujui owner)
- Tanggal: 2026-10-21
- Konteks: Plan-4 sengaja hanya meneruskan caption media. Akibatnya gambar dan dokumen tidak pernah sampai ke Python; model hanya melihat kalimat caption dan menjawab seolah tidak ada berkas. Owner meminta Sanka benar-benar menerima attachment.
- Keputusan:
  1. Bridge tetap hanya I/O. Ia mengunduh `imageMessage`/`documentMessage` dengan `downloadMediaMessage`, membatasi ukuran, lalu mengunggah byte ke `POST /v1/attachments` dengan token yang sama. Metadata (kind, MIME, nama, ukuran) ikut di header; event `/v1/inbound` hanya membawa `attachments[]` berisi ID dan metadata.
  2. Byte disimpan oleh gateway di `attachments.dir` di luar repo, `0700`/`0600`. Nama file adalah ID acak tanpa ekstensi; nama asli hanya label di sidecar JSON. Retensi default 24 jam dan kuota total dibatasi. ID tidak pernah berupa path.
  3. Lampiran terikat pada `chat_id`, `sender_id`, dan `message_id`. Normalizer menolak ID yang tidak ada atau metadata yang tidak cocok; `attachment_read` juga menolak chat lain.
  4. Gambar JPEG/PNG/WebP dikirim ke model sebagai part `image_url` data URL. Model `deepseek-v4.1-flash` sudah diverifikasi menerima format ini (probe 2026-10-21). Tidak ada OCR, resize, atau dependency baru.
  5. Dokumen PDF/XLSX/CSV/MD/TXT dibaca lewat tool `attachment_read`, memakai extractor yang sama dengan `doc_read` dan batas halaman/baris/karakter yang sama. Isi dokumen tetap DATA di dalam `<data>`.
  6. Media lain (audio, video, sticker, DOCX) tetap di luar cakupan. Bila unduh/unggah gagal, bridge mengirim `attachment_errors` agar Sanka melaporkan kegagalan dengan jujur, bukan mengaku tidak ada berkas.
  7. Tidak ada dependency baru. Baileys menyediakan API unduh; Node 22 menyediakan `fetch`; Python memakai `hashlib`/`base64` dan extractor yang ada.
- Alternatif yang dipertimbangkan:
  - **Base64 di `/v1/inbound`**: ditolak; body JSON dibatasi 64 KiB, base64 membengkak, dan 25 MB harus ditahan di memori.
  - **Bridge menulis langsung ke direktori bersama**: ditolak; menambah koppling filesystem dan melewati validasi gateway. Upload HTTP membuat gateway pemilik tunggal store.
  - **OCR lokal untuk gambar**: ditolak untuk plan ini; model sudah bisa melihat gambar, OCR menambah dependency dan kehilangan konteks visual.
  - **Menyimpan lampiran di dalam repo**: ditolak; risiko ter-commit dan bercampur dengan kode.
  - **Membaca gambar lewat tool**: ditolak; tool hanya mengembalikan teks, sehingga gambar harus masuk sebagai content part.
- Konsekuensi:
  - Isi lampiran (gambar dan teks dokumen) dikirim ke OpenCode Go, sama seperti teks chat. Retensi lokal bukan jaminan privasi di sisi provider.
  - Bridge memproses unduhan secara berurutan agar urutan pesan tetap; unduhan besar memperlambat pesan berikutnya.
  - File yatim dari upload yang gagal di tengah jalan dibersihkan oleh retensi/kuota, bukan oleh transaksi lintas proses.
  - `attachments.dir` kosong mematikan fitur: upload menjawab `503`, `attachment_read` menjawab `not_configured`, dan pesan teks tetap berjalan.
