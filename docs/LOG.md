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

## 2026-10-20 — Klarifikasi reuse upstream, foto Sanka, README modern
- Plan: persiapan plan-1 (belum ada kode)
- Dilakukan:
  - Mengklarifikasi kebijakan upstream: kode boleh diambil sebagian bila berguna, lalu diubah/diadaptasi; notice MIT dipertahankan dan perubahan ditandai `SANKA-MOD:`. Yang dilarang adalah vendor mentah tanpa adaptasi. Diperbarui di `AGENTS.md`, ADR-0013, `ARCHITECTURE.md`, `SECURITY.md`, dan `README.md`.
  - Menambahkan `assets/sanka.png` (resize 640 px dari foto owner, 196 KB) dan menulis ulang `README.md` dengan header, badge, diagram, tabel fitur, dan tabel dokumentasi.
  - Mengoreksi catatan sesi sebelumnya: gateway tidak harus "ditulis sendiri dari nol"; kode/pola Hermes boleh diadaptasi.
- Hasil tes: PNG valid 639x640; seluruh tautan aset README menunjuk file yang ada; belum ada tes kode karena belum ada kode.
- Temuan: belum ada. Model sesi dan antrean async masih perlu ADR sebelum plan-1.
- Berikutnya: owner meninjau README, ADR-0013/0014, dan sisa ADR-0011, lalu plan-1.

## 2026-10-20 — Plan-1 disusun
- Plan: plan-1
- Dilakukan: menulis `docs/plan/plan-1.md` untuk fondasi inti (config, router, command registry, policy, formatter) yang bisa dites tanpa LLM dan WhatsApp. Mencatat dependency PyYAML dan pytest, serta menunda command yang butuh layanan/sesi.
- Hasil tes: belum ada kode; belum dites.
- Temuan: session dan antrean async tetap menjadi prasyarat plan-2.
- Berikutnya: owner meninjau plan-1. Coding dimulai setelah status diubah menjadi `In Progress`.

## 2026-10-20 — ADR bahasa implementasi diajukan
- Plan: plan-1 (belum dijalankan)
- Dilakukan: menambahkan ADR-0015 (Proposed) yang menetapkan Python 3.12 untuk inti, Node.js LTS + Baileys hanya untuk bridge WhatsApp, dan Bahasa Indonesia untuk pengguna. Plan-1 mencatat runtime yang sama.
- Hasil tes: belum ada kode; belum dites.
- Temuan: dua runtime hanya dibutuhkan saat gateway aktif; inti tetap bisa dites dengan Python saja.
- Berikutnya: owner menyetujui ADR-0015 dan plan-1 sebelum coding.

## 2026-10-20 — Plan-1 fondasi inti diimplementasikan
- Plan: plan-1 (Done)
- Dilakukan:
  - Menambahkan `pyproject.toml` (PyYAML runtime, pytest dev, target Python 3.11–3.13) dan package `sanka/`.
  - Implementasi `models.py`, `config.py` (validasi fail-fast dengan nama key), `router.py` (COMMAND/AGENT/IGNORE, mention grup), `policy.py` (peran, matriks tool, rate limit in-memory), `commands/registry.py`, `commands/basic.py` (`/ping`, `/help`, `/whoami`, `/status`), dan `formatter.py` (buang blok internal, potong teks, pecah rekap).
  - Menulis 59 tes di `tests/` untuk config, router, policy, command, dan formatter.
  - Memperbarui `docs/COMMANDS.md` (status implementasi), `README.md` (status proyek), dan plan-1 (Catatan perubahan + Hasil).
- Hasil tes: `pytest -q` lulus 59 tes (pytest 9.1.1) di Python 3.15; `python -m compileall sanka` lulus. Smoke manual dengan `config/sanka.example.yaml`: routing grup/DM, `/help` per peran, dan rate limit berjalan sesuai rencana. Tidak ada import ke LLM, WhatsApp, Google, Baileys, atau GenericAgent.
- Temuan:
  - Mesin ini hanya punya Python 3.15, sedangkan target ADR-0015/`pyproject.toml` adalah 3.11–3.13. Kode menghindari fitur 3.14+, tetapi tes pada 3.12 belum dijalankan.
  - `config/sanka.yaml` belum dibuat; `config/sanka.example.yaml` masih berisi daftar owner/panitia kosong.
  - Rate limit memakai jendela bergulir (60 detik/24 jam), bukan hari kalender, dan hilang saat restart.
  - ADR-0015 masih `Proposed`; implementasi mengikuti plan-1. Persetujuan owner masih diperlukan.
- Berikutnya: owner meninjau diff plan-1 dan mengisi `config/sanka.yaml`. Plan-2 memerlukan ADR session/antrean sebelum adapter OpenCode Go.

## 2026-10-21 — Plan-2 disusun
- Plan: plan-2 (Draft)
- Dilakukan:
  - Menulis `docs/plan/plan-2.md`: sesi per chat, antrean serial per chat, client OpenCode Go non-streaming, adaptasi `agent_loop.py`, `/new` dan `/stop`, serta tes tanpa WhatsApp/jaringan.
  - Mengajukan ADR-0016 (sesi dan antrean) dan ADR-0017 (adaptasi harness + client sendiri) dengan status `Proposed`.
  - Memeriksa dokumentasi OpenCode Go: base URL `https://opencode.ai/inference/go/openai/v1`, model ID `deepseek-v4.1-flash`, endpoint `chat/completions`, dan kewajiban `x-opencode-session` serta User-Agent sendiri.
- Hasil tes: belum ada kode; belum dites.
- Temuan:
  - Header autentikasi (`Authorization: Bearer`) masih asumsi dan harus diverifikasi dengan kunci nyata di langkah pertama plan-2.
  - Dokumentasi OpenCode Go membatasi pemakaian per 5 jam, minggu, dan bulan per model; ini perlu dipantau setelah agent aktif.
  - `llmcore.py` upstream terlalu besar untuk kebutuhan Sanka; plan-2 hanya mengadaptasi `agent_loop.py` dan menulis client sendiri.
- Berikutnya: owner meninjau plan-2 dan ADR-0015/0016/0017. Coding dimulai setelah status plan menjadi `In Progress`.

## 2026-10-21 — Roadmap plan disusun
- Plan: roadmap plan-1 sampai plan-8
- Dilakukan:
  - Menulis `docs/plan/ROADMAP.md`: 8 plan, prasyarat, kriteria keluar, dan definisi "bot inti hidup" di akhir plan-4.
  - Memisahkan gateway Python (plan-3) dari bridge Baileys (plan-4), tool baca/rekap (plan-5) dari tool tulis/proposal (plan-6), serta menjadikan hardening/deploy plan-8.
  - Menautkan roadmap dari `docs/plan/README.md`.
- Hasil tes: belum ada kode; hanya dokumen. Tidak ada tes yang dijalankan.
- Temuan:
  - Tabel indikatif 6 plan di plan-1 terlalu menggabungkan gateway/bridge dan tool baca/tulis. Roadmap baru menggantikannya.
  - Plan-2 masih `Draft`; coding belum dimulai.
- Berikutnya: owner meninjau roadmap dan plan-2. Plan-2 menjadi `In Progress` setelah disetujui.

## 2026-10-21 — Plan-2 dikerjakan, plan-3 sampai plan-8 disusun paralel
- Plan: plan-2 (Blocked), plan-3..8 (Draft), roadmap
- Dilakukan:
  - Menerima instruksi owner untuk mengerjakan plan secara paralel dengan subagent.
  - Plan-2: menambahkan `core/agent_loop.py`, `sanka/secrets.py`, `sanka/sessions.py`, `sanka/queue.py`, `sanka/agent/{client,prompt,handler,runtime}.py`, dan `sanka/commands/session.py`; menambah `/new` dan `/stop`, `httpx`, base URL OpenCode Go, serta validasi `llm.base_url` tidak kosong.
  - Plan-2 dipecah paralel: loop agent, secrets, client HTTP, serta sesi/antrean dikerjakan subagent terpisah, lalu diintegrasikan lewat runtime dan command registry.
  - Menulis draft `plan-3.md` sampai `plan-8.md` lewat enam subagent paralel, lalu menyeragamkan path gateway (`gateway/`), kontrak rate limit, dan nomor ADR (0018 gateway, 0019 Google, 0020 proposal, 0021 memori, 0022 sandbox, 0023 supervisor, 0024 audit).
  - Memperbarui `docs/plan/ROADMAP.md`, `docs/plan/README.md`, `docs/COMMANDS.md`, `README.md`, ADR-0012, dan ADR-0015/0016/0017 menjadi `Accepted`.
- Hasil tes: `pytest -q` lulus 122 tes (62 tes plan-2); `python -m compileall -q sanka core` lulus. Smoke manual dengan client palsu: agent menjawab, `/new` mereset, `/stop` melaporkan idle.
- Temuan:
  - Plan-2 belum bisa `Done`: `OPENCODE_GO_API_KEY` tidak ada, `config/secrets.env` dan `config/sanka.yaml` belum dibuat, sehingga request live dan verifikasi header `Authorization: Bearer` belum dijalankan. Status plan-2 = `Blocked`.
  - Lingkungan hanya Python 3.15; target proyek 3.11–3.13. Tes di 3.12 belum dijalankan.
  - Draft plan-3..8 masih perlu ditinjau owner. Beberapa keputusan saling memengaruhi: plan-5 mengubah bentuk allowlist menjadi alias, plan-6 mengganti nama `proposal_send` menjadi `proposal_prepare`, dan plan-7 membutuhkan keputusan lingkup memori DM vs grup.
  - Eksekusi antarplan tidak bisa benar-benar paralel karena plan-3 bergantung pada plan-2, plan-4 pada plan-3, plan-5 pada plan-4, plan-6 pada plan-5, dan plan-8 pada semuanya. Paralelisasi dilakukan di dalam plan, bukan antarplan.
- Berikutnya: owner mengisi API key + `config/sanka.yaml`, menjalankan live smoke plan-2, lalu meninjau draft plan-3..8. Plan-3 baru masuk `In Progress` setelah plan-2 `Done`.

## 2026-10-21 — Plan-2 diverifikasi live dan selesai
- Plan: plan-2 (Done)
- Dilakukan:
  - Mengunci `.env` ke 0600 (sebelumnya 0644) dan memperluas `sanka.secrets.get_secret` agar urutannya environment → `config/secrets.env` → `.env`.
  - Menambahkan tes fallback `.env` dan memperbarui ADR-0012/0014, `docs/SECURITY.md`, README, plan-2, dan roadmap.
  - Menjalankan live smoke: `AgentRuntime` mengirim satu pesan ke `deepseek-v4.1-flash` lewat `https://opencode.ai/inference/go/openai/v1/chat/completions` dan menerima balasan `ok`.
- Hasil tes: `pytest -q` lulus 123 tes (63 tes plan-2); `python -m compileall -q sanka core` lulus. Live smoke OpenCode Go berhasil; header Bearer, `x-opencode-session`, dan User-Agent diterima.
- Temuan:
  - Plan-2 selesai; status `Done`.
  - `pip install -e .` masih tidak bisa diuji di Python 3.15 (target 3.11–3.13).
  - `.env` berisi `GOOGLE_SERVICE_ACCOUNT_JSON` juga; nilainya belum dipakai sampai plan-5.
- Berikutnya: owner meninjau plan-3; setelah disetujui, plan-3 menjadi `In Progress` dan implementasinya boleh dipecah paralel per modul.

## 2026-10-21 — Batas Python dilonggarkan
- Plan: plan-2 (Done)
- Dilakukan: mengubah `requires-python` dari `>=3.11,<3.14` menjadi `>=3.11`; memperbarui AGENTS.md §8, ADR-0015 (catatan), dan plan-2.
- Hasil tes: `pip install -e .` berhasil di Python 3.15; import `sanka`, `core`, `httpx`, dan `yaml` berhasil; `pytest -q` tetap 123 tes lulus; `compileall sanka core` lulus.
- Temuan: dependency runtime saat ini (PyYAML, httpx) mendukung 3.15. Target produksi tetap 3.12; tes di 3.12 belum dijalankan.
- Berikutnya: plan-3 menunggu persetujuan owner.

## 2026-10-21 — Wave 0 disiapkan untuk session baru
- Plan: plan-3 (In Progress)
- Dilakukan:
  - Owner menyetujui Wave 0 dan meminta eksekusinya di session baru.
  - Menambahkan ADR-0018 (kontrak HTTP gateway Python ↔ bridge) sebagai `Accepted`.
  - Menulis `docs/plan/WAVE-0.md`: pembagian 4 subagent (normalizer/dedup/gate, outbound, dispatcher, server/app), file ownership, kontrak antarmuka, dan perintah verifikasi.
  - Mengubah plan-3 menjadi `In Progress` dan memperbarui prasyarat/langkah agar tidak meminta persetujuan ulang.
- Hasil tes: belum ada kode baru; belum dites. Implementasi dilakukan di session baru.
- Temuan: Wave 0 tidak membutuhkan WhatsApp, Google, atau systemd; semua bisa dites dengan bridge palsu.
- Berikutnya: buka session baru dan jalankan prompt kickoff di `docs/plan/WAVE-0.md`. Plan-4 tidak boleh dimulai sebelum plan-3 `Done`.

## 2026-10-21 — Plan-3 Wave 0 selesai (gateway Python)
- Plan: plan-3 (Done)
- Dilakukan:
  - Mengerjakan Wave 0 dengan empat subagent paralel sesuai pembagian file di `docs/plan/WAVE-0.md`: normalizer/dedup/gate, outbound + fakes, dispatcher, lalu server/app/entrypoint. Parent memegang config, fixture, integrasi, dan dokumentasi.
  - Menambah key config tervalidasi: `gateway.listen_host` (loopback saja), `listen_port`, `auth_token_env`, `request_timeout_seconds`, `max_inbound_age_seconds`, `access.bot_ids`, `limits.inbound_dedup_ttl_minutes`; memperbarui `config/sanka.example.yaml`, `tests/conftest.py`, dan `pyproject.toml` (`gateway*`).
  - Implementasi `gateway/`: normalizer (`InboundEvent`, `MalformedPayload`, kode reason stabil), `TtlRegistry` (dedup + notice, thread-safe), gate (blank/stale/allowlist grup/DM pairing), `HttpBridgeClient`, `OutboundQueue` (satu worker, FIFO per chat, round-robin, batas laju, retry 429/5xx, `409` sukses), `Dispatcher` (dedup → role → gate → route → rate limit command → executor), `GatewayServer` (`ThreadingHTTPServer`, token constant-time, body 64 KiB, healthz), `build_app()`, dan `python -m gateway`.
  - Mode terdegradasi `agent=None` melayani command dengan balasan `"Fitur AI belum aktif."` untuk jalur agent.
  - Memperbarui `docs/ARCHITECTURE.md` (alur + kontrak gateway), `docs/SECURITY.md` (loopback/token/batas input), `docs/plan/plan-3.md` (Catatan perubahan + Hasil, status `Done`), `docs/plan/ROADMAP.md`, dan `README.md`.
- Hasil tes: `pytest -o addopts="" -q` lulus **306 tes** (plan-1/plan-2 tetap hijau; 183 tes gateway baru). `python -m compileall -q sanka core gateway` lulus. Smoke loopback nyata dengan `python -m gateway` + bridge palsu: `/healthz` `200`, `POST /v1/inbound` `/ping` `202` dan satu `POST /v1/send` (`pong`, key `smoke-1:0`), kiriman duplikat `200 duplicate`, token salah `401`, `SIGTERM` exit `0`.
- Temuan:
  - Dedup in-memory hilang saat restart; perlindungan duplikat kirim tetap di `idempotency_key` bridge (plan-4). Tercatat sebagai risiko plan-3.
  - `429 overloaded` membuang entri dedup agar retry bridge bisa diterima; tanpa itu retry selalu menjadi `duplicate`.
  - Notice rate limit memakai bucket 10 menit di registry notice 24 jam karena dispatcher hanya menerima satu `TtlRegistry`.
  - `httpx` mencatat URL request pada level INFO; entrypoint menurunkannya ke WARNING agar log tetap metadata gateway.
  - Tes pada Python 3.12 belum dijalankan (mesin ini Python 3.15).
- Berikutnya: plan-4 (bridge Node + Baileys) mengikuti ADR-0018. Belum dimulai.

## 2026-10-21 — Wave 1 disiapkan
- Plan: plan-4 (Draft; belum dieksekusi)
- Dilakukan:
  - Menulis `docs/plan/WAVE-1.md`: tujuan, prasyarat, pembagian 4 subagent + parent untuk bridge Node/Baileys, kontrak modul, API `test/helpers.js`, perintah verifikasi, dan smoke manual.
  - Menautkan WAVE-1 dari langkah 1 `docs/plan/plan-4.md`.
  - Mencatat bahwa Node.js/npm belum terpasang di workstation (perintah `node -v`/`npm -v` gagal), sehingga pemasangan Node 22 LTS menjadi langkah parent sebelum scaffold.
- Hasil tes: belum ada kode bridge; tidak ada tes yang dijalankan.
- Temuan:
  - Plan-4 masih `Draft`; Wave 1 belum boleh dieksekusi sampai owner menyetujui plan-4 dan statusnya menjadi `In Progress`.
  - Manual pairing/QR dan smoke WhatsApp nyata membutuhkan nomor Sanka + ponsel owner; tidak bisa dilakukan otomatis oleh agent.
- Berikutnya: owner meninjau plan-4 + WAVE-1. Setelah disetujui, jalankan Wave 1 di session baru; plan-5 belum dimulai.

## 2026-10-21 — Wave 1 diperluas menjadi dua track
- Plan: plan-4 + plan-5 (keduanya masih Draft)
- Dilakukan:
  - Menulis ulang `docs/plan/WAVE-1.md` menjadi dua track paralel: Track A plan-4 bridge Node/Baileys dan Track B plan-5 tool baca/rekap. File kedua track tidak bertabrakan (`gateway/bridge/**` vs file Python).
  - Menjelaskan kenapa plan-6/7/8 tidak ikut: plan-6 memakai registry/allowlist plan-5 dan menulis file yang sama; plan-7 menimpa prompt/runtime dan menunggu keputusan memori owner; plan-8 menerima semua plan.
  - Mencatat override prasyarat plan-5 ("plan-4 Done" → "plan-4 In Progress + kontrak `/v1` beku"), dengan syarat plan-5 tidak boleh `Done` sebelum smoke WhatsApp plan-4 berhasil. Override dicatat di plan-5 Catatan perubahan.
  - Menautkan WAVE-1 dari langkah 1 plan-5; menambahkan `tests/test_config.py` ke kepemilikan parent Track B.
- Hasil tes: belum ada kode baru; tidak ada tes yang dijalankan.
- Temuan:
  - Track B tetap terblokir sampai ADR-0011 dan ADR akses Sheets `Accepted`, kredensial Google/Brave tersedia, dan Node 22 terpasang untuk Track A.
  - Gate Track B tidak bisa dilewati dengan handler palsu; bila belum siap, jalankan Track A saja.
- Berikutnya: owner menyetujui plan-4, plan-5, ADR-0011, dan ADR Sheets. Setelah itu jalankan Wave 1; plan-6 belum dimulai.
