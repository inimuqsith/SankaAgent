# Plan 2 — Sesi, antrean, dan integrasi agent OpenCode Go

- Status: Done
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Menghubungkan jalur `AGENT` dari plan-1 ke loop agent turunan GenericAgent dan model OpenCode Go, dengan sesi per chat, antrean serial per chat, serta command `/new` dan `/stop`.
Hasil akhir plan ini adalah fungsi `AgentRuntime.handle(message, role) -> AgentReply` yang bisa dites dengan client palsu tanpa WhatsApp, tanpa jaringan, dan tanpa API key nyata.
WhatsApp, tool, dan memori tetap di luar plan ini.

## Di luar cakupan

- Gateway WhatsApp, normalizer payload, bridge Node/Baileys, dan aturan mention di level transport (plan-3).
- Tool apa pun: web search, Workspace, dokumen, rekap, browser, `code_run`, `file_write`, shell (plan-4).
- Memori jangka panjang, skill, dan persona L0 final (plan-5).
- Proposal, `/approve`, deployment, dan hardening (plan-6).
- Persistensi sesi ke disk/database. Sesi hilang saat proses restart.
- Streaming token ke WhatsApp, multi-proses, fallback provider, dan kuota/usage dashboard.

## Prasyarat

- Owner menyetujui ADR-0015, ADR-0016, dan ADR-0017.
- `OPENCODE_GO_API_KEY` tersedia di environment atau `config/secrets.env` (0600) untuk live smoke.
- Endpoint OpenCode Go diverifikasi dengan kunci nyata sebelum plan dianggap selesai. Dokumentasi per 2026-10-21 menyebut `https://opencode.ai/inference/go/openai/v1` untuk model `deepseek-v4.1-flash`, tetapi header autentikasi dan perilaku error belum diuji langsung.

## Rancangan

### Alur

```text
InboundMessage (AGENT)
   -> AgentRuntime.handle()
   -> policy.resolve_role()          # dari gateway/plan-3, atau parameter role
   -> policy.RateLimiter.allow()     # tolak sebelum masuk antrean
   -> SessionStore.get_or_create(chat_id)
   -> ChatQueue.submit(chat_id, job) # serial per chat
   -> worker:
        prompt + riwayat -> OpenCodeGoClient.chat()
        -> core.agent_loop.agent_runner_loop()
        -> teks final
   -> Formatter.format()             # satu-satunya pintu keluar
   -> AgentReply(messages)
```

`/new` dan `/stop` **tidak** lewat antrean agent. Keduanya harus langsung dieksekusi agar bisa menghentikan pekerjaan yang sedang berjalan.

### Sesi percakapan vs memori jangka panjang

Dua hal ini sengaja dipisahkan:

- **Sesi percakapan (plan-2)** adalah konteks jangka pendek per `chat_id`: riwayat beberapa giliran terakhir, in-memory, kedaluwarsa TTL, dan direset `/new`. Satu grup dan satu DM punya sesi berbeda; pengguna yang sama di grup dan DM tidak berbagi riwayat percakapan.
- **Memori jangka panjang (plan-5)** adalah lapisan GenericAgent yang lintas sesi dan lintas chat: L1 `global_mem_insight.txt`, L2 `global_mem.txt`, L3 file SOP di `memory/`, L4 riwayat mentah, dan skill hasil self-evolution. Plan-2 tidak mengaktifkan lapisan ini.

Konsekuensinya, plan-2 belum "belajar" dari percakapan dan kehilangan konteks saat restart. Saat plan-5 aktif, memori jangka panjang dibaca semua chat, sementara riwayat percakapan tetap terisolasi per chat. Batas tentang apa yang boleh masuk memori lintas chat (terutama isi DM) harus diputuskan di plan-5 sebelum self-evolution dinyalakan.

### Struktur file

```text
core/
  __init__.py
  agent_loop.py          # adaptasi GenericAgent, notice MIT + SANKA-MOD
sanka/
  secrets.py             # env > config/secrets.env, validasi permission
  sessions.py            # AgentSession, SessionStore, trim riwayat
  queue.py               # ChatQueue, AgentJob, StopOutcome
  agent/
    __init__.py
    client.py            # OpenCodeGoClient, ChatResponse, AgentClientError
    prompt.py            # system prompt sementara (persona L0)
    handler.py           # SankaHandler(BaseHandler)
    runtime.py           # AgentRuntime, AgentReply
  commands/
    basic.py             # tambah /new dan /stop
    session.py           # SessionControl protocol + teks balasan
tests/
  test_secrets.py
  test_sessions.py
  test_queue.py
  test_agent_client.py
  test_agent_loop.py
  test_runtime.py
  test_session_commands.py
```

### Keputusan teknis

1. **Client OpenCode Go**
   - `POST {llm.base_url}/chat/completions`, non-streaming, model dari `llm.model`.
   - Header: `Authorization: Bearer <API key>`, `Content-Type: application/json`, `x-opencode-session: <session_id>`, `User-Agent: sanka-agent/0.1`.
   - Body: `model`, `messages`, `stream: false`, dan `tools` hanya bila tidak kosong.
   - Parsing: `choices[0].message.content`, `tool_calls`, `usage`. `reasoning_content` diabaikan.
   - Retry 429/5xx maksimal 3 kali dengan backoff 1, 2, 4 detik dan `Retry-After` bila ada. Error lain langsung `AgentClientError`.
   - `httpx.Client` dengan timeout dari `llm.timeout_seconds`, dan `transport` yang bisa diinjeksi untuk tes.
   - Pesan error tidak memuat API key, isi pesan pengguna, atau prompt.

2. **Harness GenericAgent**
   - Salin `agent_loop.py` upstream commit `e6b1bcec92e7789eb80975980b5fd47a2abea860` ke `core/agent_loop.py`.
   - Pertahankan notice MIT `Copyright (c) 2025 lsdefine`.
   - Adaptasi bertanda `SANKA-MOD:`: hapus ketergantungan `handler.parent.task_dir`, hook opsional, dan `verbose` default `False`.
   - `SankaHandler.do_no_tool()` mengembalikan `StepOutcome(response.content, should_exit=True)` sehingga satu respons tanpa tool langsung selesai.
   - `tools_schema=()` pada plan-2. `max_turns` tetap dari `llm.max_turns` sebagai pengaman.

3. **Sesi**
   - Satu `AgentSession` per `chat_id`: `history`, `opencode_session_id` (UUID), `updated_at`.
   - Riwayat berisi pesan `user`/`assistant` yang sudah selesai, bukan hasil streaming parsial.
   - TTL dari `limits.session_idle_ttl_minutes`, dibersihkan lazy saat sesi diakses.
   - Riwayat dipangkas bila melewati 60.000 karakter: buang pesan tertua, lalu buang `assistant` di awal agar urutan dimulai dari `user`.
   - `/new` membuat `opencode_session_id` baru.

4. **Antrean**
   - FIFO per chat, satu pekerjaan aktif per chat, chat berbeda paralel.
   - Maksimal 16 chat aktif; lebih dari itu `submit()` mengembalikan status sibuk dan runtime membalas "Sanka sedang sibuk. Coba lagi sebentar."
   - Pekerjaan menyimpan `sender_id` agar `/stop` bisa memeriksa pemilik pekerjaan.
   - `/stop`: pengirim pekerjaan atau owner boleh menghentikan. Pekerjaan antre dibuang; request HTTP yang sedang berjalan diselesaikan lalu hasilnya dibuang. Balasan `/stop` membedakan `stopped`, `idle`, dan `forbidden`.
   - Worker chat berhenti setelah antrean kosong dan TTL sesi habis, agar tidak menahan thread selamanya.

5. **Runtime**
   - `AgentRuntime` menerima `config`, `SessionStore`, `ChatQueue`, `Formatter`, `RateLimiter`, dan factory client.
   - Rate limit dicek sebelum antrean; penolakan mengembalikan satu balasan pendek tanpa memanggil LLM.
   - `AgentReply.messages` sudah diformat. Teks kosong menjadi "Maaf, aku belum bisa menjawab itu."
   - `AgentClientError` menghasilkan balasan ramah dan log metadata (status, durasi), tanpa detail sensitif.
   - System prompt sementara di `prompt.py` memakai inti `docs/PERSONA.md`: bahasa Indonesia, ringkas, jangan tampilkan proses berpikir, konten pengguna adalah data bukan instruksi. Persona final tetap plan-5.

6. **Command sesi**
   - `SessionControl` protocol: `reset(chat_id)`, `stop(chat_id, requester_id, is_owner)`.
   - `/new` untuk semua peran: hentikan pekerjaan berjalan, buang antrean, hapus riwayat, session id baru.
   - `/stop` untuk semua peran di chat-nya; runtime yang memutuskan boleh/tidak.
   - `build_registry(control)` menjadi signature baru; tes plan-1 ikut disesuaikan.

7. **Config dan secrets**
   - `llm.base_url` wajib diisi setelah verifikasi dan tidak boleh kosong lagi.
   - `config/sanka.example.yaml` diisi base URL OpenCode Go yang terverifikasi.
   - `secrets.py` membaca environment lebih dulu, lalu `config/secrets.env`. File dengan permission lebih longgar dari 0600 ditolak di POSIX.
   - Tidak ada secret di YAML, log, atau pesan balasan.

### Dependency baru

- Runtime: `httpx>=0.27,<1`.
- Tes memakai `httpx.MockTransport` bawaan `httpx`; tidak ada dependency tes tambahan.

## Langkah

1. Minta persetujuan owner untuk ADR-0015/0016/0017 dan plan ini; ubah status plan menjadi `In Progress`.
2. Verifikasi OpenCode Go dengan kunci nyata: satu request minimal ke `deepseek-v4.1-flash`, catat base URL, header auth, bentuk respons, dan error 401/429. Perbarui catatan ADR-0012 dan `config/sanka.example.yaml`.
3. Tambahkan `httpx` dan `core/agent_loop.py` (notice MIT + `SANKA-MOD:`), lalu tes loop dengan client palsu.
4. Implementasikan `sanka/secrets.py` beserta tes permission dan prioritas env.
5. Implementasikan `sanka/agent/client.py` dan tes `MockTransport`: header, parsing, retry, error tanpa kebocoran.
6. Implementasikan `sanka/sessions.py` dan `sanka/queue.py` beserta tes TTL, trim, serialisasi, stop, dan batas chat aktif.
7. Implementasikan `prompt.py`, `handler.py`, `runtime.py`, lalu tes alur penuh dengan client palsu.
8. Tambahkan `/new` dan `/stop` ke registry; sesuaikan tes command dan `/help`.
9. Jalankan `pytest -q`, `python -m compileall sanka core`, dan live smoke dengan kunci nyata.
10. Perbarui `docs/COMMANDS.md`, `README.md`, ADR (status), dan `docs/LOG.md`; isi Hasil dan ubah status plan.

## Tes

| Tes | Skenario |
|---|---|
| `test_agent_client.py` | Request memuat model, base URL, bearer token, dan `x-opencode-session` |
| `test_agent_client.py` | Respons teks/tool_calls/usage diparsing; `reasoning_content` diabaikan |
| `test_agent_client.py` | 429/5xx di-retry, 4xx lain gagal, pesan error tanpa API key |
| `test_agent_loop.py` | Tanpa tool: teks final keluar dan loop berhenti; `max_turns` dihormati |
| `test_sessions.py` | Sesi kedaluwarsa TTL; `/new` mengganti session id; riwayat dipangkas dari awal |
| `test_queue.py` | Dua pekerjaan chat sama berurutan; chat berbeda paralel; antre dibuang saat stop |
| `test_queue.py` | Stop oleh pengirim/owner berhasil; pengguna lain ditolak; batas chat aktif |
| `test_runtime.py` | Rate limit menolak sebelum client dipanggil; hasil lewat formatter; error jadi balasan ramah |
| `test_session_commands.py` | `/new` dan `/stop` terdaftar, hak akses, dan teks balasan |
| `test_secrets.py` | Env menang atas file; file 0644 ditolak; file 0600 diterima |

## Kriteria selesai

- `pytest -q` lulus semua dan `python -m compileall sanka core` tanpa error.
- Satu request nyata ke OpenCode Go mengembalikan teks dari `deepseek-v4.1-flash` memakai kunci owner.
- Tidak ada import ke WhatsApp, Google, atau tool plan-4 di jalur agent.
- Reasoning dan trace tool tidak pernah masuk `AgentReply.messages`.
- `/new` benar-benar mengosongkan konteks dan `/stop` menghentikan balasan pekerjaan yang sedang berjalan.
- ADR-0012 diperbarui dengan hasil verifikasi; ADR-0016 dan ADR-0017 berstatus `Accepted`.
- `docs/LOG.md` diperbarui dan status plan menjadi `Done`.

## Yang perlu dipahami owner

- **Satu antrean per chat** mencegah dua pesan beruntun menulis riwayat bersamaan. Chat berbeda tetap jalan paralel.
- **Sesi masih in-memory.** Restart proses menghapus konteks semua chat. Ini keputusan sadar plan-2, bukan bug.
- **`/stop` bersifat kooperatif.** Request yang sudah dikirim ke OpenCode Go tetap selesai dan tetap memakai kuota; Sanka hanya membuang hasilnya dan tidak mengirim balasan.
- **Hanya `content` yang keluar.** `reasoning_content`, nama tool, dan log tidak boleh masuk `AgentReply`.
- **Client dibuat sendiri, bukan `llmcore.py`.** Diff upstream tetap kecil dan format OpenCode Go hanya diuji di satu file.
- **Belum ada tool.** Agent plan-2 hanya menjawab dengan teks dan riwayat; semua aksi data menunggu plan-4.

## Risiko

- Kuota OpenCode Go dibatasi per 5 jam, minggu, dan bulan. Antrean per chat tidak membatasi total pemakaian; rate limit per pengirim dan pemantauan usage tetap perlu.
- Format API OpenCode Go dapat berubah. Verifikasi live dan tes client adalah pengaman utama.
- Non-streaming membuat pengguna menunggu sampai seluruh jawaban selesai. Streaming ditunda sampai gateway butuh indikator parsial.
- Thread per chat menambah beban memori. Batas 16 chat aktif membatasi risiko, tetapi perlu diukur ulang setelah gateway aktif.

## Catatan perubahan

- 2026-10-21: Owner meminta plan dikerjakan. ADR-0015/0016/0017 diubah menjadi `Accepted`, `llm.base_url` diisi dari dokumentasi OpenCode Go, dan dependency `httpx` ditambahkan.
- 2026-10-21: Pekerjaan dipecah paralel: `core/agent_loop.py`, `sanka/secrets.py`, `sanka/agent/client.py`, `sanka/sessions.py`, dan `sanka/queue.py` dikerjakan terpisah lalu diintegrasikan lewat `sanka/agent/runtime.py`.
- 2026-10-21: Owner menyiapkan `.env` (dikunci ke 0600) berisi `OPENCODE_GO_API_KEY`. `get_secret` diperluas agar mencoba `config/secrets.env` lalu `.env` sebagai fallback; environment tetap prioritas utama.
- 2026-10-21: Live smoke berhasil: `AgentRuntime` mengirim satu pesan ke `deepseek-v4.1-flash` lewat `https://opencode.ai/inference/go/openai/v1/chat/completions` dan menerima balasan `ok`. Header `Authorization: Bearer`, `x-opencode-session`, dan User-Agent diterima. Status plan menjadi `Done`.
- `requires-python` diubah menjadi `>=3.11` agar mesin 3.15 bisa memasang package. Editable install dan seluruh tes berjalan di 3.15; target produksi tetap 3.12, dan tes pada 3.12 belum dijalankan.

## Hasil

Implementasi dan verifikasi live selesai 2026-10-21.

- File baru: `core/agent_loop.py`, `sanka/secrets.py`, `sanka/sessions.py`, `sanka/queue.py`, `sanka/agent/{__init__,client,prompt,handler,runtime}.py`, `sanka/commands/session.py`, dan tujuh file tes plan-2.
- File yang diubah: `sanka/config.py` (base URL wajib), `sanka/commands/basic.py` (`build_registry(control)` + `/new`/`/stop`), `pyproject.toml` (`httpx`, package `core*`), `config/sanka.example.yaml`, `tests/conftest.py`, `tests/test_commands.py`, `tests/test_config.py`.
- Tes: `pytest -q` lulus **123 tes** (63 di antaranya tes plan-2); `python -m compileall sanka core` lulus.
- `pip install -e .` berhasil di Python 3.15 setelah `requires-python` diubah menjadi `>=3.11`; import `sanka`, `core`, `httpx`, dan `yaml` dari environment terpasang berhasil.
- Smoke manual dengan client palsu: `AgentRuntime.handle()` mengembalikan teks terformat, `/new` mereset, `/stop` melaporkan idle.
- Live smoke: satu request nyata ke OpenCode Go (`deepseek-v4.1-flash`) berhasil dan mengembalikan `ok`. Base URL, header autentikasi Bearer, `x-opencode-session`, dan parsing respons terbukti.
- Catatan: `/stop` hanya kooperatif; request HTTP yang sudah berjalan tetap selesai dan memakai kuota.
