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

## 2026-10-21 — Wave 1 preflight + keputusan izin Google
- Plan: plan-4 + plan-5 (keduanya masih Draft; belum ada kode ditulis)
- Dilakukan:
  - Preflight Wave 1: `git status` bersih; `node -v`/`npm -v` gagal (Node belum terpasang di Fedora 45; tersedia `dnf`, bukan `pacman`); `SANKA_GATEWAY_TOKEN` dan `BRAVE_SEARCH_API_KEY` tidak ada; `.env` (0600) hanya memuat `OPENCODE_GO_API_KEY` (terisi) dan `GOOGLE_SERVICE_ACCOUNT_JSON` (kosong); `config/sanka.yaml` dan direktori sesi WhatsApp belum ada.
  - Menulis draft **ADR-0019** (`Proposed`) untuk akses Google Sheets lewat endpoint resmi + `google-auth`, allowlist berbasis alias, tanpa `google-api-python-client` untuk saat ini.
  - Mencatat keputusan owner 2026-10-21: izin/scope API dibuat luas dan pembatasan ditegakkan di kode. `workspace.scopes` ditambahkan ke rancangan plan-5 dengan default `https://www.googleapis.com/auth/spreadsheets` (baca+tulis); plan-5 tetap hanya memanggil `values.get`. ADR-0019, plan-5 (termasuk Catatan perubahan), dan `docs/SECURITY.md` diperbarui.
- Hasil tes: `.venv/bin/python -m pytest -q` lulus 306 tes (baseline, belum ada kode baru).
- Temuan:
  - Gate Wave 1 belum terpenuhi: plan-4/plan-5 masih `Draft`, ADR-0011 masih `Proposed`, Node 22 belum ada, kredensial gateway/Brave belum ada, `config/sanka.yaml` belum ada. Tidak ada kode Track A/B yang ditulis.
  - Perlu keputusan owner: menyetujui plan + ADR, izin memasang Node 22 (`sudo dnf install nodejs npm` atau nvm), dan konfirmasi apakah cukup adapter REST resmi atau ingin SDK `google-api-python-client`.
- Berikutnya: setelah gate disetujui, ubah status plan/ADR sesuai persetujuan owner, pasang Node 22, lalu jalankan Track A dan Track B paralel. Plan-6/7/8 belum dimulai.

## 2026-10-21 — Wave 1 dieksekusi (Track A + Track B)
- Plan: plan-4 + plan-5 (keduanya `In Progress`; belum `Done` karena smoke WhatsApp/manual belum dijalankan)
- Dilakukan:
  - Track A: membuat `gateway/bridge/**` (config, logger, session, normalize, backoff, ids, deliver, outbound, http, socket, main, helper tes). Baileys dipin `7.0.0-rc13`, pino `9.14.0`, qrcode-terminal `0.12.0`; `package-lock.json` di-commit. Node `v22.23.1`, npm `10.9.8`.
  - Track B: dependency baru (`pypdf`, `openpyxl`, `pandas`, `google-auth`), config alias allowlist + `workspace.scopes`, `sanka/google.py`, `sanka/tools/**`, `/rekap`, `Command.kind`/`dispatch_result`, integrasi `AgentRuntime`/handler/prompt, fixture tanpa jaringan, dan SearXNG (bukan Brave).
  - Integrasi gateway: `gateway/app.py` membangun `ToolRegistry` + `RecapService`; `gateway/dispatcher.py` memakai `dispatch_result`; `gateway/outbound.py` meneruskan `ResponseKind.RECAP`.
  - Dokumentasi: ADR-0011/0019 `Accepted`, ADR-0013 catatan pin Baileys + satu instance, ADR-0019 draft awal diubah sesuai keputusan scope luas, plan-4/5 Hasil + Catatan perubahan, WAVE-1 status, COMMANDS, ROADMAP, SECURITY.
- Hasil tes:
  - `cd gateway/bridge && npm ci && npm test && npm audit --omit=dev` → `124` tes lulus, `0` gagal, `0` vulnerability.
  - `.venv/bin/python -m pytest -q -o addopts=""` → **477 passed**.
  - `.venv/bin/python -m compileall -q sanka core gateway` → exit `0`.
  - Smoke startup bridge: tanpa token → exit `1`; session dir di dalam repo → exit `1`; keduanya dengan pesan yang menyebut env/masalah, tanpa token/JID.
  - `git status --porcelain` tidak memuat `node_modules`, `config/sanka.yaml`, kredensial, atau direktori sesi.
- Temuan/deviasi:
  - Script `npm test` memakai glob `'test/*.test.js'` karena Node 22.23.1 tidak menerima argumen direktori `test/`.
  - `pandas` dipin `>=2.2,<4` (bukan `<3`) karena Python 3.15 tidak punya wheel pandas 2.x; pandas 3.0.6 dipakai dan tes lulus.
  - `qrcode-terminal` berlisensi Apache-2.0, bukan MIT; kompatibel dan dicatat di plan-4.
  - `gateway/app.py`, `dispatcher.py`, `outbound.py` disentuh di luar daftar awal plan-5 untuk menutup jalur `/rekap` dan tool registry.
  - Token Google nyata, SearXNG nyata, spreadsheet uji, dan QR WhatsApp belum dipakai; semua tes memakai fake/MockTransport.
- Berikutnya: owner menyiapkan `config/sanka.yaml`, `SANKA_GATEWAY_TOKEN`, service account Google yang di-share ke sheet uji, instance SearXNG dengan JSON API, nomor WhatsApp Sanka + ponsel. Lalu pairing/smoke plan-4, dilanjutkan smoke plan-5. Plan-4/5 baru boleh `Done` setelah itu. Plan-6/7/8 belum dimulai.

## 2026-10-21 — ADR-0020: peserta dihapus, default panitia
- Plan: plan-5 (In Progress), kebijakan lintas plan
- Dilakukan:
  - Owner meminta menurunkan friksi: istilah peserta dihapus; nomor yang tidak tercantum mendapat izin panitia. Ditulis **ADR-0020** (`Accepted`), matriks ADR-0011 diamandemen, `SECURITY.md`/`ARCHITECTURE.md`/`COMMANDS.md`/`AGENTS.md`/plan-4/plan-5/WAVE-1/ROADMAP diperbarui.
  - `sanka/policy.py`: `Role` hanya `OWNER` dan `COMMITTEE`; `resolve_role` mengembalikan `COMMITTEE` untuk semua non-owner. `committee_numbers` tetap diterima config untuk label/migrasi tetapi tidak mengubah izin.
  - Tes yang mengandalkan peserta dihapus/diubah: tool visibility committee, command `/status`, `/rekap`, DM pairing, dan injection test. Jumlah tes Python menjadi **470**.
- Hasil tes:
  - `.venv/bin/python -m pytest -q -o addopts=""` → **470 passed**.
  - `.venv/bin/python -m compileall -q sanka core gateway` → exit `0`.
  - Gateway live (config lokal, SearXNG aktif, fake bridge): nomor tak tercantum `628333333333` → `/whoami` `"Peran kamu: panitia."`, `/status` deterministik, `/rekap` `"Belum ada rekap yang terdaftar."`, dan agent berhasil memanggil `doc_read`.
- Temuan:
  - Konsekuensi keamanan: siapa pun yang bisa menjangkau Sanka dapat memakai `web_search`, `doc_read`, `sheet_read`, `recap`, dan `/rekap`. Tool owner-only (`sheet_write`, `proposal_send`, `browser`, `code_run`, `file_write`, `shell`) tetap ditolak.
  - `dm_policy: pairing` tidak lagi membedakan peran karena semua non-owner adalah panitia; gerbang utama yang tersisa adalah `group_policy` allowlist dan rate limit. Dicatat di ADR-0020.
- Berikutnya: pairing QR WhatsApp oleh owner; setelah `connected:true`, smoke DM/grup. Plan-4/5 belum `Done`.

## 2026-10-21 — Pairing WhatsApp + smoke grup berhasil
- Plan: plan-4 (In Progress), plan-5 (In Progress)
- Dilakukan:
  - Owner memindai QR di terminal sendiri; pairing berhasil (`connection_open`, `pairing_complete`). Sesi tersimpan di luar repo, direktori `0700`, 1.624 file `0600`.
  - Bridge di-restart tanpa QR; `/healthz` `connected:true`.
  - Pesan grup pertama ditolak `group_not_allowed` karena `group_policy: allowlist` dan daftar grup kosong. Untuk smoke, config lokal diubah ke `group_policy: open`.
  - Mention grup masih `not_mentioned`: `bot_ids` yang diambil dari `creds.json` berbeda dari `sock.user` live. Debug sementara menulis mention JID ke `/tmp`; setelah `bot_ids` diisi dari `sock.user` (`me.id`/`me.lid`), router menghasilkan `agent`.
  - Mention grup nyata berhasil: gateway `accepted detail=agent` → `agent_done`, bridge `outbound_sent` ke grup. Dump debug dihapus.
  - `app-state-sync-key-AAAAAMCo.json` baru tertulis 1 detik setelah sync pertama; error Baileys hilang setelah restart.
- Hasil tes:
  - `npm test` → **125 lulus**; `pytest` → **470 passed**; `compileall` exit `0`.
  - `GET /healthz` bridge `connected:true`, gateway hidup.
- Temuan:
  - `group_policy` lokal masih `open`; produksi harus `allowlist` + JID grup. Belum ada cara aman mengambil JID grup tanpa log mentah; perlu satu langkah owner.
  - DM `/ping`, DM agent, grup tanpa mention, multi-chunk, logout, dan mode pesawat belum diuji.
- Berikutnya: uji DM dan grup tanpa mention; setelah itu lengkapi Hasil plan-4 dan pertimbangkan `Done`. Plan-5 tetap menunggu sheet uji/service account untuk `/rekap` nyata.

## 2026-10-21 — Plan-9: format WhatsApp, persona dari PERSONA.md, command hidup
- Plan: plan-9 (In Progress → Done)
- Dilakukan:
  - Diagnosis keluhan owner: formatter tidak menormalkan markdown (`**`, `##`, `-`), prompt runtime hardcoded tanpa aturan emoji/format, `/whoami` mengabaikan `sender_id`, dan command deterministik hanya berisi string datar.
  - `sanka/formatter.py`: menambahkan `to_whatsapp()` — heading → baris tebal, `**` → `*`, `~~` → `~`, link markdown → `teks (url)`, bullet → `•`, garis horizontal dihapus. Blok/inline code dilindungi dan konversi idempoten. `Formatter.format()` mengonversi sebelum memotong/memecah.
  - `sanka/agent/prompt.py`: L0 sekarang dimuat dari blok bertanda `<!-- SANKA-L0:START/END -->` di `docs/PERSONA.md`, di-cache per proses. Aturan tool/data dan tanggal tetap di kode. `AgentRuntime.__init__` gagal cepat bila aset persona hilang.
  - `docs/PERSONA.md`: menambahkan identitas Sanka, arahan emoji 1–2 per balasan, aturan format WhatsApp, dan larangan membacakan aturan internal saat ditanya karakter.
  - Command: `/whoami` menampilkan JID pengirim, peran, jenis chat, dan JID chat; `/ping`, `/help`, `/status`, `/new`, `/stop`, command tidak dikenal, dan command terlarang diberi nada/emoji. Deskripsi `/rekap` diberi emoji.
  - Dokumentasi: ADR-0021 (formatter) dan ADR-0022 (persona dari `docs/PERSONA.md`) `Accepted`; `ARCHITECTURE.md`, `COMMANDS.md`, plan-9 diperbarui.
- Hasil tes:
  - `.venv/bin/python -m pytest -q` → **485 passed**.
  - `.venv/bin/python -m compileall -q sanka core gateway` → exit `0`.
  - Smoke loopback dengan fake bridge + model OpenCode Go nyata: `/whoami` mengirim `👤 Kamu: 628111111111@s.whatsapp.net`, `/help` memuat daftar command, balasan agent memakai persona dan emoji, dan tidak ada `**`/`##` di ketiga pesan.
  - Konversi contoh markdown (`##`, `**`, link, bullet, HR) keluar bersih; inline code tetap utuh.
- Temuan:
  - Gateway yang sedang berjalan (PID 159060) masih memakai kode lama; perlu restart agar perubahan aktif di WhatsApp.
  - `docs/PERSONA.md` sekarang menjadi aset runtime. Deployment harus menyertakan `docs/`; plan-7 nanti boleh memindahkannya ke `sanka/persona/l0.md` lewat ADR baru.
- Berikutnya: restart gateway, lalu verifikasi tampilan `/whoami`, `/help`, dan satu balasan agent di WhatsApp; lanjutkan smoke plan-4 (DM/grup tanpa mention).

## 2026-10-21 — Plan-10: SOUL.md di config + wrapper gateway
- Plan: plan-10 (In Progress → Done)
- Dilakukan:
  - Persona L0 dipindah dari blok bertanda `docs/PERSONA.md` ke `config/SOUL.md`. `sanka/agent/prompt.py` sekarang punya `load_soul()` yang membaca file utuh, men-trim, dan melempar `PersonaError` bila hilang/kosong. `AgentRuntime.__init__` tetap fail-fast.
  - `docs/PERSONA.md` menjadi dokumen manusia dan hanya merujuk ke `config/SOUL.md`.
  - Membuat `scripts/sanka` (executable) dengan `start`, `stop`, `restart`, `status`, `logs`, dan `bridge`. Gateway berjalan detached dengan `nohup`, log `logs/gateway.log`, PID `logs/gateway.pid`; wrapper memuat `.env` tanpa mencetak secret.
  - `scripts/sanka` dapat mengadopsi gateway yang dijalankan manual: PID listener port 8099 ditemukan lewat `ss` dan ditulis ke PID file. Ini memperbaiki kasus restart saat PID file belum ada.
  - Dokumentasi: ADR-0023 `Accepted`, ADR-0022 `Superseded by ADR-0023`, README bagian "Menjalankan", ARCHITECTURE, PERSONA, plan-10.
- Hasil tes:
  - `.venv/bin/python -m pytest -o addopts="" -q` → **486 passed**.
  - `.venv/bin/python -m compileall -q sanka core gateway` → exit `0`.
  - `scripts/sanka status` → gateway PID 164708 diadopsi, health `ok`, bridge `connected:true`.
  - `scripts/sanka restart` → gateway berhenti, PID baru 167088, `/healthz` `{"status":"ok"}` dengan uptime 0, PID file dan listener `ss` cocok.
  - Smoke sebelumnya (plan-9) tetap berlaku untuk format/persona; gateway baru memuat `config/SOUL.md` saat startup.
- Temuan:
  - Percobaan pertama wrapper membuat proses kedua karena PID file tidak ada sementara port 8099 masih dipakai gateway manual; proses baru gagal `Address already in use`. Sudah diperbaiki dengan resolusi PID listener dan pengecekan port sebelum spawn.
  - Bridge tetap proses terpisah; `scripts/sanka bridge` menjalankannya di foreground karena butuh QR/sesi.
- Berikutnya: owner menguji `/whoami` dan satu balasan agent di WhatsApp. Plan-4/5 tetap menunggu smoke DM/grup.

## 2026-10-21 — Revisi plan-10: wrapper mengurus gateway + bridge
- Plan: plan-10 (revisi, tetap Done)
- Dilakukan:
  - Owner menolak wrapper yang hanya mengurus gateway. `scripts/sanka` diperluas: target `all|gateway|bridge` untuk `start`, `stop`, `restart`, `logs`; `status` menampilkan keduanya; `pair` menjalankan bridge foreground untuk QR.
  - Wrapper memuat `.env` dan `config/secrets.env` (token bridge), menyimpan `logs/bridge.pid` dan `logs/bridge.log`, mengadopsi proses manual lewat PID listener `ss`, dan memverifikasi PID file benar-benar listener port.
  - Start bridge menunggu `"connected":true` hingga 30 detik; bila gagal, mencetak petunjuk log/pairing dan 20 baris log terakhir.
  - Dokumentasi: ADR-0024 `Accepted` (menggantikan keputusan 5 ADR-0023), README, ARCHITECTURE, plan-10.
- Hasil tes:
  - `bash -n scripts/sanka` → OK.
  - `scripts/sanka start bridge` → PID 168377, `connected:true` (~3 detik).
  - `scripts/sanka restart bridge` → PID 168513, `connected:true`.
  - `scripts/sanka restart` (all) → gateway PID 168619, bridge PID 168647; health gateway `ok`, bridge `connected:true`.
  - `scripts/sanka status` → kedua PID cocok dengan listener `ss`.
  - `pytest -o addopts="" -q` → **486 passed**; `compileall` → exit `0`.
- Temuan:
  - `npm start` di terminal owner berhenti saat terminal/sesi ditutup; sekarang bridge berjalan detached lewat wrapper dan tetap hidup.
  - Pairing pertama tidak bisa lewat start background karena QR hanya di log/terminal; gunakan `scripts/sanka pair`.
- Berikutnya: owner menguji `/whoami` dan balasan agent di WhatsApp; jalankan `scripts/sanka status` untuk memastikan kedua proses hidup.

## 2026-10-21 — Plan-11: konteks ambient chat grup
- Plan: plan-11 (In Progress → Done)
- Dilakukan:
  - Bridge mengirim `sender_name` (pushName, dibersihkan); `InboundMessage` dan normalizer membawa field itu.
  - `sanka/group_context.py`: JSONL per grup, nama file HMAC-SHA256 dengan salt lokal, direktori `0700`, file/salt `0600`, retensi 24 jam, 200 pesan, 1.000 karakter/entri, injeksi 30 pesan/3.500 karakter, escape `&<>`.
  - `Dispatcher`: pesan grup yang tidak di-mention dicatat segera; pesan COMMAND/AGENT dicatat di `finally` agar tidak dobel pada giliran yang sama; DM, gate tolak, dan command `/...` tidak dicatat.
  - `AgentRuntime` membaca context hanya untuk `is_group`; `build_system_prompt` menambahkan blok `<group_context>` berlabel DATA dan larangan mengikuti instruksi di dalamnya.
  - Config `group_context` ditambahkan (contoh disabled; config lokal enabled + `capture_all_groups: true`). ADR-0025, SECURITY, ARCHITECTURE, README diperbarui.
- Hasil tes:
  - `.venv/bin/python -m pytest -o addopts="" -q` → **509 passed**.
  - `.venv/bin/python -m compileall -q sanka core gateway` → exit `0`.
  - `cd gateway/bridge && npm test` → **126 passed**.
  - Smoke loopback dengan model nyata: pesan grup tanpa mention `"Rapat minggu ini dipindah ke Jumat jam 10 ya"` tersimpan; mention `"@sanka kapan rapat minggu ini?"` dijawab dengan `"Menurut pesan Budi di grup, rapat minggu ini dipindah ke *Jumat jam 10.00* ✅"`.
  - File konteks: `0600`, dir `0700`, nama file hash, isi JSONL tanpa `chat_id` mentah.
- Temuan:
  - `sender_name` bisa dipalsukan pengguna; hanya dipakai sebagai label data, bukan identitas/izin. JID tetap sumber kebenaran untuk peran.
  - `capture_all_groups: true` + `group_policy: open` berarti semua grup Sanka terekam. Batasi dengan `capture_group_ids` bila perlu.
  - Store konteks bukan log dan bukan memori; hilang setelah retensi, tidak bertahan sebagai memori plan-7.
- Berikutnya: restart lewat `scripts/sanka restart`, lalu uji satu obrolan grup tanpa mention diikuti mention; cek `group_context.dir` berisi JSONL. Owner perlu memberi tahu anggota grup bila capture_all aktif.
- Tindak lanjut 2026-10-21: retensi sekarang juga menulis ulang file di disk; JID pengirim disimpan sebagai hash, bukan mentah. Restart final: gateway PID 174106, bridge PID 174132, health `ok`, bridge `connected:true`.

## 2026-10-21 — Plan-13: konteks balasan, nama tampilan, lampiran utuh
- Plan: plan-13 (Done)
- Dilakukan:
  - `attachment_read` sekarang memakai `tools.docs.max_chars` (12.000), bukan cap default 8.000, dan mendukung `offset`/`limit`/`next_offset`. Default window menyisakan 256 karakter untuk tag `<data>`.
  - Bridge mengekstrak `quoted_text`, `quoted_sender_name`, dan `mention_names`; cache `pushName` per JID dipakai untuk nama quote. Gateway menormalisasi field baru ke `InboundMessage`.
  - Prompt menerima `speaker_name`, `reply_context`, dan `mentioned_names`; quote dibungkus `<reply_context>` dan di-escape. `config/SOUL.md` menyatakan nama tampilan sah dipakai menyapa tanpa ragu.
  - ADR-0027, plan-13, SECURITY, ARCHITECTURE, dan plan-3 diperbarui.
- Hasil tes:
  - `.venv/bin/python -m pytest -o addopts="" -q` → **559 passed**.
  - `cd gateway/bridge && npm test` → **137 passed**.
  - `compileall` → exit `0`.
  - Smoke `attachment_read` pada `AGENTS.md`: `characters=8361`, `next_offset=None`, `truncated=False`, baris terakhir ada; hasil registry 8.402 karakter tanpa marker `dipotong`.
  - Smoke model dengan `speaker_name=Muqsith`: jawaban `"Muqsith 🙂"`.
  - Restart final: gateway PID 190212, bridge PID 190238, health `ok`, bridge `connected:true`.
- Catatan: `git status` mungkin memuat perubahan plan-12/attachment dari agent paralel; plan-13 hanya menambah koreksi di atasnya.
- Tindak lanjut 2026-10-21: `tools.docs.max_chars` dinaikkan ke **50.000** (maksimum config), window default 49.744, dan prompt memerintahkan melanjutkan `next_offset` sampai null. Tes: **560 passed**, bridge **137 passed**; smoke 60.000 karakter terbagi 49.744 + 10.256 tanpa kehilangan. Restart: gateway PID 193344, bridge PID 193370.

## 2026-10-21 — Plan-12: lampiran masuk (gambar + dokumen)
- Plan: plan-12 (In Progress; menunggu smoke WhatsApp nyata)
- Dilakukan:
  - Menambahkan `sanka/attachments.py`: `AttachmentStore` menyimpan byte di luar repo (`0700`/`0600`), ID 32 hex, sidecar metadata, ikatan `chat_id`/`sender_id`/`message_id`, retensi 24 jam, kuota total, magic-byte check gambar/PDF/XLSX.
  - `gateway/server.py`: endpoint `POST /v1/attachments` dengan token yang sama, body streaming, `Content-Length` wajib, batas `attachments.max_bytes`, sha256 opsional.
  - `gateway/normalizer.py`/`gate.py`/`sanka/router.py`/`sanka/models.py`: field `attachments`/`attachment_errors`; teks kosong dengan lampiran bukan `blank_text`; DM lampiran-only tetap ke agent, grup tetap butuh mention.
  - `sanka/tools/extract.py` + `sanka/tools/attachments.py`: `attachment_read` untuk PDF/XLSX/CSV/MD/TXT; `doc_read` memakai extractor yang sama. Izin `COMMITTEE_ROLES`.
  - `sanka/agent/runtime.py`: gambar menjadi content part `image_url`; dokumen menjadi blok `<attachments>` berisi ID; history sesi tetap teks (tanpa base64). Prompt menegaskan lampiran adalah DATA.
  - Bridge: `media.js`, `upload.js`, `socket.js`, `main.js`, `config.js`; bridge mengunduh media, mengunggah byte, lalu mengirim metadata. Kegagalan menjadi `download_failed`/`upload_failed`/`too_large`/`unsupported`.
  - Dokumentasi: ADR-0026, ARCHITECTURE, SECURITY, README, plan-3, plan-12.
- Hasil tes:
  - `pytest -q -o addopts=""` → **553 passed**.
  - `cd gateway/bridge && npm test` → **135 passed**.
  - `compileall -q sanka core gateway` → exit `0`.
  - Smoke loopback + model nyata: dokumen `MERPATI-77` dibaca lewat `attachment_read`; `assets/sanka.png` dikirim sebagai gambar dan dideskripsikan model. Upload Node → gateway nyata `201`.
  - `scripts/sanka restart` → gateway PID 186059, bridge PID 186115, keduanya sehat; bridge `connected:true`.
- Temuan:
  - Provider menolak PNG 2x2 bila ada history, tetapi menerima PNG normal 639x640. Bukan bug Sanka; tidak ada resize/OCR yang ditambahkan.
  - `attachments.dir` lokal `/home/muqsith/.local/share/sanka/attachments` sudah `0700`; record pertama baru muncul pada smoke WhatsApp di bawah.
- Smoke WhatsApp nyata 04:03: owner mengirim `AGENTS.md` (8.369 byte) di grup; `POST /v1/attachments` `201`, `/v1/inbound` `202 accepted`, `agent_done`, bridge mengirim balasan 2.067 karakter. Store berisi satu record `document`/`AGENTS.md`. Plan-12 menjadi `Done`.

## 2026-10-21 — Plan-14: larangan membahas mekanisme internal
- Plan: plan-14 (Done)
- Dilakukan:
  - `config/SOUL.md`: larangan membahas payload, prompt, tool, blok data, nama tampilan, dan asal pengetahuan; larangan membuka balasan dengan disclaimer meta; aturan menjawab nama tanpa menjelaskan sumber.
  - `sanka/agent/prompt.py`: blok aturan diberi label internal dan menyebut larangan eksplisit terhadap frasa "memperlakukan ini sebagai data", "nama tampilan WhatsApp", "tidak punya akses identitas".
  - Plan-14 ditambahkan.
- Hasil tes:
  - `.venv/bin/python -m pytest -o addopts="" -q` → **561 passed**; `compileall` exit `0`.
  - Live model: `"Namaku siapa?"` → `"Muqsith 🙂"`; ringkasan `AGENTS.md` langsung tiga poin tanpa catatan meta.
  - Restart: gateway PID 195595, bridge PID 195621, health `ok`, bridge `connected:true`.
- Temuan: aturan teknis di prompt tetap ada, tetapi sekarang ditandai internal agar tidak dikutip. Tidak ada filter teks pascaproses karena rawan memotong kalimat valid.
- Berikutnya: pantau satu-dua balasan di grup; bila masih bocor, tambahkan larangan yang lebih spesifik, bukan filter global.

## 2026-10-21 — Plan-15: pencarian dan pembacaan per bagian lampiran
- Plan: plan-15 (Done)
- Dilakukan:
  - `sanka/tools/extract.py`: `DocumentText`/`Section`, `build_document()` dengan cache memori LRU 4 dokumen; batas 300 halaman PDF, 20 lembar × 5.000 baris XLSX, 5.000 baris CSV, 2 juta karakter. Section: halaman PDF, heading Markdown, lembar XLSX, rentang 200 baris untuk CSV/teks.
  - `sanka/tools/attachments.py`: loader bersama untuk validasi ID/chat/pengirim/jenis; `attachment_search` (substring case-insensitive, offset + label bagian + cuplikan, `total_matches`/`truncated`) dan `attachment_outline` (section + offset + preview + kolom). `attachment_read` sekarang memakai ekstraksi penuh; teks hasil search/outline memuat `attachment_id`.
  - XLSX dari store dibaca lewat stream binary karena file tidak berekstensi; semua lembar dibaca dengan penanda `### Lembar: <nama>`.
  - Policy, registry cap (`tools.docs.max_chars`), wiring tool, prompt, dan runtime hint diperbarui. `SankaHandler` mengulang pertanyaan pengguna pada setiap prompt hasil tool.
  - Dokumentasi: ADR-0028, ARCHITECTURE, SECURITY, README, plan-15.
- Hasil tes:
  - `.venv/bin/python -m pytest -o addopts="" -q` → **569 passed**.
  - `cd gateway/bridge && npm test` → **137 passed**.
  - `compileall -q sanka core gateway` → exit `0`.
  - Smoke tool: dokumen 66.545 karakter, search offset 66.516 section `Bagian Akhir`, read pada offset itu mengembalikan penanda; XLSX dua lembar memetakan `lembar Laporan`/`lembar Arsip` dan search menemukan teks di lembar kedua; cap registry 200 karakter menghasilkan 199/200 karakter bertanda `dipotong`.
  - Smoke model live: model memanggil `attachment_search` + `attachment_read`/`attachment_outline` dan menjawab `Rp7.000.000` dari dokumen 66.570 karakter.
  - Restart: gateway PID 204257, bridge PID 204283, health `ok`, bridge `connected:true`.
- Temuan:
  - Sebelum handler mengulang pertanyaan, beberapa jawaban setelah turn tool membuka dengan "belum ada pertanyaan". Setelah perubahan, jawaban langsung menjawab.
  - Model kadang tetap menyarankan mengirim ulang lampiran meski `attachment_id` ada di hasil tool; tidak menghalangi jawaban.
  - Pencarian substring tidak menemukan sinonim; model perlu mencoba kata kunci lain. Batas ekstraksi ditandai `truncated`/`document_truncated`.
- Berikutnya: pantau pemakaian nyata; bila dokumen besar sering butuh beberapa kata kunci, pertimbangkan multi-keyword search, bukan menaikkan batas karakter.
