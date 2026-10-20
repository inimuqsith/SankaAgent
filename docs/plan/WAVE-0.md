# Wave 0 — Plan-3 Gateway Python

Dokumen ini adalah instruksi orkestrasi untuk **session baru**. Jangan mengerjakan di session lama.
Status: siap dieksekusi. Plan-3 sudah `In Progress`; ADR-0018 sudah `Accepted`.

## Tujuan wave

Menyelesaikan plan-3 saja: gateway Python + normalizer + dispatcher + outbound + server HTTP lokal, dengan bridge palsu.
Tidak menyentuh WhatsApp nyata, tidak memulai plan-4, dan tidak mengubah kontrak ADR-0018.

## Baca dulu

1. `AGENTS.md`
2. `docs/plan/plan-3.md` (sumber kebenaran)
3. `docs/plan/plan-2.md` dan `sanka/agent/runtime.py`
4. `docs/ADR.md` ADR-0018
5. `sanka/config.py`, `sanka/commands/basic.py`, `sanka/policy.py`, `sanka/formatter.py`

## Aturan orkestrasi

- Maksimal 4 subagent kode paralel + 1 parent integrator.
- Satu penulis per file. Subagent **hanya** menulis file yang ditugaskan.
- Parent memegang file bersama: `sanka/config.py`, `config/sanka.example.yaml`, `pyproject.toml`, `docs/*`, `tests/conftest.py`, dan integrasi `gateway/app.py` jika perlu.
- Subagent tidak menjalankan git, tidak commit, tidak menjalankan pytest di tengah jalan. Parent menjalankan suite sekali setelah integrasi.
- Kontrak dari plan-3 dibekukan. Jangan mengubah nama field HTTP, status code, atau signature publik tanpa persetujuan owner.
- Tidak ada import WhatsApp/Baileys di Python. Tidak ada network nyata di tes.

## Pembagian subagent

### Agent A — Normalizer, dedup, gate

File:
- `gateway/__init__.py`
- `gateway/normalizer.py`
- `gateway/dedup.py`
- `gateway/gate.py`
- `tests/test_gateway_normalizer.py`
- `tests/test_gateway_dedup.py`
- `tests/test_gateway_gate.py`

Kontrak: `InboundEvent`, `normalize_inbound()`, `MalformedPayload`, `TtlRegistry`, `GateReason`, `check_gate()`. Ikuti plan-3 bagian Normalizer, Deduplikasi, dan Gate.

### Agent B — Outbound

File:
- `gateway/outbound.py`
- `tests/test_gateway_outbound.py`
- `tests/gateway_fakes.py` (fake bridge; koordinasikan dengan Agent C/D lewat parent)

Kontrak: `BridgeClient` protocol, `HttpBridgeClient`, `OutboundKind`, `OutboundItem`, `OutboundQueue`. Wajib: formatter dipakai, FIFO per chat, round-robin, batas laju, retry 429/503/504/5xx, `409` sukses, typing/read best-effort. Ikuti plan-3 bagian Antrean keluar.

### Agent C — Dispatcher

File:
- `gateway/dispatcher.py`
- `tests/test_gateway_dispatcher.py`

Kontrak: `Dispatcher`, `SubmitOutcome`, `SubmitResult`, `AgentHandler` protocol. Alur persis plan-3: dedup → role → gate → route → rate limit **hanya command** → executor. Jalur agent memakai `AgentRuntime.handle()` dan rate limit milik runtime. Jangan membuat antrean agent kedua.

### Agent D — Server, app, entrypoint

File:
- `gateway/server.py`
- `gateway/app.py`
- `gateway/__main__.py`
- `tests/test_gateway_server.py`
- `tests/test_gateway_e2e.py`

Kontrak: `GatewayServer` (ThreadingHTTPServer), handler HTTP, body cap 64 KiB, `X-Sanka-Token` constant-time, loopback-only, `GET /healthz`, `POST /v1/inbound`, graceful shutdown. `build_app()` merakit config, secrets, command registry, `AgentRuntime`, dispatcher, outbound. Ikuti plan-3 bagian Server HTTP.

### Parent — Config dan integrasi

- Tambah key config plan-3 di `sanka/config.py` + `config/sanka.example.yaml`: `gateway.listen_host`, `listen_port`, `auth_token_env`, `request_timeout_seconds`, `max_inbound_age_seconds`; `access.bot_ids`; `limits.inbound_dedup_ttl_minutes`.
- Tambahkan `gateway*` ke `[tool.setuptools.packages.find] include` di `pyproject.toml`.
- Sediakan `tests/conftest.py` fixture config yang sudah berisi key baru.
- Setelah semua agent selesai: integrasikan import, perbaiki kontrak yang tidak cocok, jalankan seluruh tes, lalu smoke loopback nyata dengan bridge palsu.

## Interface yang harus konsisten

```text
InboundEvent(message_id, message: InboundMessage, timestamp: float)
normalize_inbound(payload: dict) -> InboundEvent      # raises MalformedPayload
check_gate(event, config, now) -> GateReason          # ALLOWED / ...
TtlRegistry(ttl_seconds, clock=...)                    # register/seen
HttpBridgeClient(base_url, token, timeout, transport=None)
OutboundQueue(formatter, bridge, config, clock=...)
Dispatcher(config, registry, limiter, outbound, agent, ...)
submit(event) -> SubmitResult
build_app(config, secrets_path=None) -> GatewayApp
```

Field HTTP: `message_id`, `chat_id`, `sender_id`, `text`, `is_group`, `mentions`, `reply_to_bot`, `timestamp`.
Respons inbound: `202`, `200 duplicate`, `200 ignored`, `400`, `401`, `413`, `415`, `429`, `500`.
Endpoint bridge: `POST /v1/send`, `/v1/typing`, `/v1/read`; `409` = sukses.

## Verifikasi wave

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q sanka core gateway
```

Smoke manual:
- jalankan `python -m gateway` dengan config contoh + token uji;
- `curl 127.0.0.1:8099/healthz`;
- kirim satu `POST /v1/inbound` command;
- pastikan satu `send` tercatat di bridge palsu;
- kirim payload yang sama dua kali, pastikan `duplicate`;
- hentikan server dengan SIGTERM dan pastikan shutdown rapi.

## Definition of done Wave 0

- Semua tes plan-3 lulus dan suite total hijau.
- `compileall` bersih.
- Smoke loopback berhasil, tanpa WhatsApp.
- Tidak ada reasoning/token/isi pesan di log.
- `docs/plan/plan-3.md` bagian Hasil diisi dan status menjadi `Done`; LOG diperbarui.
- Plan-4 **belum** dimulai.

## Kickoff untuk session baru

Ketik:

```text
Baca docs/plan/WAVE-0.md dan kerjakan Wave 0. Gunakan subagent paralel sesuai pembagian file di dokumen itu. Jangan mulai plan-4.
```
