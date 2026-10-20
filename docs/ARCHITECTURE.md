# ARCHITECTURE

Status: rencana awal. Perubahan besar dicatat lewat ADR.

## Alur pesan

```
WhatsApp
   |
[bridge Node/Baileys]  (plan-4; hanya I/O WhatsApp)
   |  POST /v1/inbound   (loopback + X-Sanka-Token)
[gateway/normalizer]  -> InboundEvent (message_id, InboundMessage, timestamp)
   |
[gateway/dedup]  -> TtlRegistry per message_id
   |
[gateway/gate]   -> teks kosong, stale, allowlist grup, kebijakan DM
   |
[router]
   |-- diawali "/"  --> [command registry] --> hasil deterministik (tanpa LLM)
   |
   |-- tanpa "/":
         grup  : diproses hanya jika di-mention / @sanka / di-reply ke Sanka, selain itu diabaikan
         pribadi: selalu diproses
         |
      [policy] cek peran, rate limit (command di dispatcher, agent di runtime)
         |
      [agent loop]  (harness turunan GenericAgent)
         |  memanggil tool lewat [tool registry] (allowlist per peran)
         |
      [formatter]  hanya teks final, buang reasoning/tool trace, batasi panjang
   |
[gateway/outbound]  -> FIFO per chat, round-robin, batas laju, retry, idempotency_key
   |  POST /v1/send, /v1/typing, /v1/read
[bridge] -> WhatsApp
```

Bridge mendorong pesan masuk (push) dan Python memanggil bridge untuk keluaran; keduanya hanya loopback dan memakai token bersama (ADR-0018). Tidak ada long-poll.

## Komponen

- **gateway**: terbagi dua proses. `gateway/` (Python, plan-3) memegang normalizer, dedup, gate, dispatcher, antrean keluar, server HTTP loopback, peran, rate limit, command registry, agent runtime, dan formatter. Bridge Node/Baileys (plan-4) hanya I/O WhatsApp: push pesan masuk, kirim teks/typing/read, tanpa logika bisnis. Kode atau pola Hermes boleh diadaptasi dengan notice MIT dan penanda `SANKA-MOD:`.
- **router**: memutuskan jalur command vs LLM dan menerapkan aturan mention di grup.
- **command registry**: tiap command punya nama, izin peran, handler, dan teks bantuan. Lihat `COMMANDS.md`.
- **policy**: peran (owner/panitia; pengguna tak tercantum = panitia), matriks izin tool, rate limit per pengguna. Penegakan di kode.
- **agent loop**: loop persepsi, pikir, tool, memori (turunan `agent_loop.py` GenericAgent). Sebisa mungkin tidak diubah, diperluas lewat hook.
- **tool registry**: daftar tool bundle. Tool yang tidak diizinkan untuk peran tertentu tidak muncul di daftar tool yang dilihat LLM pada giliran itu.
- **formatter**: satu-satunya jalan keluar ke WhatsApp. Aturannya: teks final saja, ringkas, batas panjang, kecuali jenis respons "rekap" yang boleh panjang dan dipecah per pesan.
- **memori dan skill**: lapisan memori turunan GenericAgent. Self-evolution aktif, belajar dari semua pengguna, dengan pencatatan asal-usul skill. Lihat ADR dan `SECURITY.md`.

## Gateway Python (plan-3)

Server memakai stdlib `ThreadingHTTPServer`; tidak ada dependency server baru. Modul:

| File | Tanggung jawab |
|---|---|
| `gateway/normalizer.py` | validasi payload, `InboundEvent`, `MalformedPayload` dengan kode stabil |
| `gateway/dedup.py` | `TtlRegistry` in-memory untuk dedup `message_id` dan throttle notice |
| `gateway/gate.py` | teks kosong, stale, allowlist grup, kebijakan DM |
| `gateway/dispatcher.py` | pipeline sinkron cepat + `ThreadPoolExecutor`, jalur command/agent |
| `gateway/outbound.py` | `HttpBridgeClient`, `OutboundQueue` (FIFO per chat, round-robin, batas laju, retry) |
| `gateway/server.py` | handler HTTP, auth token, batas body, pemetaan status |
| `gateway/app.py` | `build_app()` merakit config, secrets, registry, runtime, dispatcher, outbound |
| `gateway/__main__.py` | `python -m gateway`, SIGINT/SIGTERM shutdown |

| Metode | Path | Auth | Catatan |
|---|---|---|---|
| `GET` | `/healthz` | tidak | `{"status","uptime_seconds","outbound_pending"}`, metadata saja |
| `POST` | `/v1/inbound` | ya | push dari bridge, body ≤ 64 KiB, `Content-Type: application/json` |

Respons inbound: `202 accepted`, `200 duplicate`, `200 ignored` + `reason`, `400 malformed_payload`, `401 unauthorized`, `411 length_required`, `413 payload_too_large`, `415 unsupported_media_type`, `429 overloaded`, `500 internal`. `reason` normalizer: `not_an_object`, `invalid_json`, `message_id_invalid`, `chat_id_invalid`, `sender_id_invalid`, `text_invalid`, `text_too_long`, `is_group_invalid`, `is_group_mismatch`, `mentions_invalid`, `reply_to_bot_invalid`, `timestamp_invalid`. `reason` gate/router: `blank_text`, `stale`, `dm_not_allowed`, `group_not_allowed`, `not_mentioned`, `rate_limited`.

Python → bridge: `POST /v1/send` (`chat_id`, `text`, `idempotency_key`; `409` = sudah terkirim = sukses), `/v1/typing` (`composing`/`paused`), `/v1/read` (`chat_id`, `message_id`). Retry hanya untuk error koneksi/timeout/`429`/`503`/`504`/`5xx`, maksimal 3 percobaan.

Keputusan yang perlu diingat:

- Dedup in-memory; restart menghapusnya. Idempotensi kirim yang sesungguhnya ada di bridge lewat `idempotency_key` (plan-4).
- `submit()` tidak pernah menunggu LLM. Pekerjaan command/agent berjalan di pool 8 worker; pengiriman di satu worker antrean keluar.
- Rate limit command dicek di dispatcher, rate limit agent di `AgentRuntime`; tidak ada pemeriksaan ganda.
- Semua keluaran melewati `Formatter`, termasuk balasan command.
- `dm_policy: pairing` pada plan-3 berarti allowlist + satu notice per 24 jam, bukan alur tukar kode.

## Tool bundle (rencana, 10 tool)

| Tool | Fungsi | Catatan |
|---|---|---|
| web_search | Cari informasi di web | |
| browser | Browser asli (TMWebdriver) | Profil Chrome khusus Sanka |
| sheet_read | Baca Google Sheets | Allowlist file |
| doc_read | Baca PDF, Excel, dokumen lain | |
| recap | Hitung dan susun rekap dari data | Hitung di kode, LLM hanya menarasikan |
| sheet_write | Tulis terbatas ke Workspace | Hanya allowlist |
| proposal_send | Kirim proposal ke owner | Butuh `/approve` |
| code_run, file_write, shell | Kendali sistem | Owner saja, shell butuh toggle config |

## Rekap spreadsheet
1. Tool membaca data mentah.
2. Kode (pandas atau sejenis) menghitung agregat.
3. LLM menerima hasil hitung dan hanya merangkai kalimat/format.
4. Formatter memecah hasil panjang menjadi beberapa pesan.

## Konfigurasi
Satu file konfigurasi non-secret (`config/sanka.yaml`, contoh di `config/sanka.example.yaml`) memuat: model, daftar owner/panitia, allowlist Workspace, toggle shell, batas panjang jawaban, rate limit, session TTL. Secrets (API key, token, kredensial service account, sesi WhatsApp) hanya dari environment variable atau `config/secrets.env` dengan permission 0600. Keduanya di-ignore git. Adapter memetakan YAML + env ke `cfg` yang dipakai `llmcore.py`; tidak ada `mykey.py` duplikat.
