# TASK

Checklist for the soul-engine + gateway refactor. Tick `[x]` only after the real path is verified.

## Core

- [x] `sanka/engine/` is an autonomous core; it never imports a gateway or Neonize (`tests/test_gateway_contract.py`).
- [x] `Agent.handle` receives `Inbound` and uses `Gateway` (no protobuf in core).
- [x] `WorkingMemory` = per-chat window, not identity; `HistoryStore` = archive.
- [x] Modular roles: one `roles.yaml` block per role, explicit permissions, multi-role union, no handler defaults.
- [x] `Soul` (name/language/pronouns/persona) + `language_line` for the prompt.
- [x] English prompt scaffolding; Indonesian persona preserved; gateway hints for transport syntax.
- [x] Prompt blocks `Self`, `Roles`, and `Memory` stay visible; memory is never dropped by salience.

## Config consolidation

- [x] One host-local `config.yaml`: host keys + `engine` + `gateways` + `soul`; one `roles.yaml`; `SOUL.md` persona.
- [x] `engine.yaml`, `gateway.yaml`, `soul.yaml`, and `roles/` removed; the loader rejects the split layout with a migration hint.
- [x] `sanka init` writes exactly `config.yaml`, `roles.yaml`, `SOUL.md`, `.env`; `roles.yaml`/`.env` are `0600`.
- [x] `sanka config migrate` folds legacy or split layouts into `config.yaml`+`roles.yaml`, backs up old files into `config.bak-<timestamp>/`, and removes them only with `--force`.
- [x] Document mirrors merge engine/soul into `config.yaml` without clobbering host/gateway keys and aggregate every role into `roles.yaml`.
- [x] `settings` tool: `engine`/`gateway`/`soul`/`persona`/`role` map to the combined files; `host` rejects reserved sections.

## Storage

- [x] One SQLAlchemy Core `Database`; portable schema (Postgres runtime, SQLite test).
- [x] DB stores: history, affect, style, vibe, skills, recipes, documents.
- [x] Documents `engine`/`soul`/`persona`/`role:<name>` + file mirrors (`seed`, `export`, `import`).
- [x] Mem0 OSS for semantic memory; scopes `person`/`chat`/`global` + privacy metadata (level/owner/subject/confirmed/vetoed).
- [x] Cross-chat retrieval for the same person; another person's memory is never queried.
- [x] `ActionLedger` autobiographical; the ledger is filtered free of secrets.
- [x] `memory_legacy` + `sanka memory migrate` idempotent (DM/LID → person, group → chat, global).
- [x] `sanka db upgrade|import|check|backup|restore`; imports old SQLite plus skills/recipes/state.
- [x] Selftest uses a temporary database; unavailable Mem0 = SKIP.

## Gateway

- [x] `Gateway`/`Inbound`/`GatewayError` contract; tool+command registries merged, duplicates rejected.
- [x] WhatsApp: ingress, media, directory (contacts/LID/names), history sync, send/typing/reaction/render.
- [x] `send_to` only takes explicit targets from the archive (no single-contact fallback).
- [x] CLI REPL `sanka console`; CLI gateway only, role from `config.yaml`.
- [x] Supervisor + `engine_lease`: primary/standby, renew, takeover, `/freeze`, promote back.
- [x] `sanka run` by default; `backfill` stays a WhatsApp gateway command.
- [x] A partial send never marks the message as answered.

## Privacy and disclosure

- [x] Numeric memory levels 0-4 (public, team, personal, sensitive/aib, secret); level 5/credentials are never stored.
- [x] Deterministic `PrivacyPolicy`: show/disclose matrices with group vs DM ceilings, owner = speaker, subject veto, confirmation state.
- [x] `PrivacyService`: pending confirmations, capture assessment, pre-delivery review; only agent-facing hook.
- [x] Capture asks at `ask_level` (default 4) and stores unconfirmed until the speaker answers; L0-L2 stay fluid.
- [x] Group L3/L4 disclosure asks the owner; non-owner or vetoed disclosure is denied; the auditor runs only when a risky entry exists.
- [x] `memory` tool: level/subject/confirmed/veto, `memory_admin` required for global add/delete; `memori` command filters through the policy.
- [x] Memory context carries `MemoryHit` metadata and filters vetoed entries.

## Language selector

- [x] `soul.language` is YAML config (`config.yaml` `soul` section / `soul` document); only `en` and `id` are supported.
- [x] `/language en|id` (owner-only) writes the soul document, mirrors `config.yaml`, and reloads.
- [x] `language_line` and the Self block follow the configured language; there is no Python string catalog.
- [x] `settings` `file=soul path=language` validates through the same normaliser.

## Safety, settings, self

- [x] Deterministic `SafetyPolicy` (`allow|pending|deny`) as the robotics seam.
- [x] `approvals` bound to chat+sender+TTL; `/pending`, `/approve`, `/deny` without the LLM.
- [x] `/approve` re-checks the original role permissions; `settings` is loader-validated before writing.
- [x] `settings` tool: `schema/get/set/unset/write/reload`, atomic mirror, backup, `config_changes` audit, hot reload/restart-only.
- [x] `/status` reports mode, lease, uptime, database, memory, approvals, skills, soul/engine/config, privacy, and language.
- [x] Empty `send_text` = explicit error; the config loader never `mkdir`s; the DSN is redacted.

## Packaging and docs

- [x] `pyproject` script `sanka`, extras `whatsapp`/`vibe`/`memory`/`dev`; Dockerfile + pgvector compose.
- [x] `sanka init` scaffold + `.env` `0600`; a single `config.example.yaml`.
- [x] English README + full Indonesian section, migration runbook, audit SQL, approval, memory privacy, language.
- [x] AGENTS/TASK/LOG updated; five docs only.

## Release verification

- [x] `pytest -q`.
- [x] `python -m sanka selftest`.
- [x] `sanka init` + `sanka db upgrade` + `sanka db check`.
- [x] Real console with the LLM + `/status` + `/quit` (lease released).
- [x] Two lease processes (console A/B): takeover, freeze, promote back.
- [x] Real Postgres + pgvector: `db upgrade`, 386-message import, sequence reset, merge upsert.
- [x] Real Mem0: `sanka memory migrate` 3 rows + add/search/context/forget through pgvector.
  Note: the HF embedder cannot be installed on the dev machine's Python 3.15; verification used a
  deterministic OpenAI-compatible embedding server. Production uses HuggingFace (Docker Python 3.12).
- [x] Config consolidation: split layout migrated, combined config loads, mirrors round-trip.
- [x] Privacy matrix and `/language` console smoke.
- [ ] Real WhatsApp end-to-end (needs production credentials/QR).
