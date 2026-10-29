# TASK

Checklist target refactor soul engine + gateway. Tandai `[x]` hanya setelah jalur nyata diverifikasi.

## Core

- [x] `sanka/engine/` sebagai core otonom; tidak mengimpor gateway/Neonize (`tests/test_gateway_contract.py`).
- [x] `Agent.handle` menerima `Inbound` dan memakai `Gateway` (tanpa protobuf di core).
- [x] `WorkingMemory` = jendela per chat, bukan identitas; `HistoryStore` = arsip.
- [x] Role modular: satu dokumen/file per role, izin eksplisit, multi-role union, tanpa default handler.
- [x] `Soul` (nama/bahasa/pronomina/persona) + `language_line` untuk prompt.
- [x] Prompt scaffolding Inggris; persona Indonesia tetap; gateway hints untuk sintaks transport.
- [x] Blok prompt `Self`, `Roles`, dan `Memory` wajib terlihat; memory tidak pernah dibuang salience.

## Storage

- [x] Satu `Database` SQLAlchemy Core; schema portabel (Postgres runtime, SQLite test).
- [x] Store DB: history, affect, style, vibe, skills, recipes, documents.
- [x] Dokumen `engine`/`soul`/`persona`/`role:<name>` + mirror file (`seed`, `export`, `import`).
- [x] Mem0 OSS untuk memori semantik; scope `person`/`chat`/`global` + metadata privasi.
- [x] Retrieval lintas chat untuk person yang sama; memori orang lain tidak pernah di-query.
- [x] `ActionLedger` autografis; ledger disaring dari rahasia.
- [x] `memory_legacy` + `sanka memory migrate` idempoten (DM/LID → person, grup → chat, global).
- [x] `sanka db upgrade|import|check|backup|restore`; impor SQLite lama + skill/recipe/state.
- [x] Selftest memakai database sementara; Mem0 tidak tersedia = SKIP.

## Gateway

- [x] Kontrak `Gateway`/`Inbound`/`GatewayError`; registry tool+command digabung, duplikat ditolak.
- [x] WhatsApp: ingress, media, directory (kontak/LID/nama), history sync, send/typing/reaction/render.
- [x] `send_to` hanya target eksplisit dari arsip (tanpa fallback kontak tunggal).
- [x] CLI REPL `sanka console`; hanya gateway CLI, role dari `gateway.yaml`.
- [x] Supervisor + `engine_lease`: primary/standby, renew, takeover, `/freeze`, promote kembali.
- [x] `sanka run` default; `backfill` tetap command gateway WhatsApp.
- [x] Partial send tidak menandai pesan sebagai terjawab.

## Safety, settings, self

- [x] `SafetyPolicy` deterministik (`allow|pending|deny`) sebagai seam robotik.
- [x] `approvals` terikat chat+pengirim+TTL; `/pending`, `/approve`, `/deny` tanpa LLM.
- [x] `/approve` mengecek ulang izin role asli; `settings` divalidasi loader sebelum ditulis.
- [x] Tool `settings`: `schema/get/set/unset/write/reload`, mirror atomik, backup, audit `config_changes`, hot reload/restart-only.
- [x] `/status` memuat mode, lease, uptime, database, memory, approvals, skills, soul/engine/config.
- [x] `send_text` kosong = error eksplisit; config loader tanpa `mkdir`; DSN diredaksi.

## Packaging & docs

- [x] `pyproject` script `sanka`, extras `whatsapp`/`vibe`/`memory`/`dev`; Dockerfile + compose pgvector.
- [x] `sanka init` scaffold + `.env` 0600; contoh `*.example.yaml`.
- [x] README Inggris + ringkasan Indonesia, runbook migrasi, audit SQL, approval, memory.
- [x] AGENTS/TASK/LOG diperbarui; lima docs saja.

## Verifikasi rilis

- [x] `pytest -q`.
- [x] `python -m sanka selftest`.
- [x] `sanka init` + `sanka db upgrade` + `sanka db check`.
- [x] Console nyata dengan LLM + `/status` + `/quit` (lease dilepas).
- [x] Dua proses lease (console A/B): takeover, freeze, promote kembali.
- [x] Postgres nyata + pgvector: `db upgrade`, import 386 pesan, sequence reset, merge upsert.
- [x] Mem0 nyata: `sanka memory migrate` 3 baris + add/search/context/forget adapter lewat pgvector.
  Catatan: embedder HF tidak bisa dipasang di Python 3.15 mesin dev; verifikasi memakai server
  embedding OpenAI-compatible deterministik. Produksi memakai HuggingFace (Docker Python 3.12).
- [ ] WhatsApp nyata end-to-end (butuh kredensial/QR produksi).
