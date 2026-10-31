# TASK

Checklist for the soul engine, the gateway contract, and the pattern-based character layer. Tick
`[x]` only after the real path is verified.

## Core

- [x] `sanka/engine/` is an autonomous core; it never imports a gateway or Neonize (`tests/test_gateway_contract.py`).
- [x] `Agent.handle` receives `Inbound` and talks back through `Gateway`; five docs only.
- [x] `WorkingMemory` = per-chat window, not identity; `HistoryStore` = archive.
- [x] Modular roles: one `roles.yaml` block per role, explicit permissions, union across matching roles, no handler defaults.
- [x] `Soul` (name/language/pronouns/persona) + `language_line`; English scaffolding, Indonesian persona.
- [x] One host-local `config.yaml` (host keys + `engine` + `gateways` + `soul`) and one `roles.yaml`; `config import`/`export` keep the DB documents and the files in step.
- [x] One `Database` + one `MetaData` (Postgres runtime, SQLite test); documents `engine`/`soul`/`persona`/`role:<name>`.
- [x] Mem0 semantic memory, scopes `person`/`chat`/`global`, cross-chat retrieval for the same person only; `ActionLedger` for actions.
- [x] Supervisor + `engine_lease`: primary/standby, renew, takeover, `/freeze`.

## Character layer

- [x] `sanka/data/patterns.json`: 30-60 conditional moves with `register`, `keys`, `target`, toxicity/adult gates, `say` exemplars, `avoid`, `priority`, `always`, `source`.
- [x] Only the six invariants are `always` (language lock, length, question limit, grounding, floor, curse trigger); they need no keyword hit and never crowd the register moves out of the sort.
- [x] `sanka/data/pattern_vectors.json`: one 384-dim unit vector per pattern for `paraphrase-multilingual-MiniLM-L12-v2`.
- [x] `sanka/engine/patterns.py`: `load_patterns` (cache, corrupt = empty, malformed rows dropped), `room_register`, `select_patterns` (gate filter, keyword hits, cosine, deterministic sort by score/priority/id, 180-char render).
- [x] `sanka/engine/agent.py` selects `config.character.patterns` lines per turn and passes them to `character_block`; the prompt header is `## Room patterns (apply the move, never copy the wording)`.
- [x] `CharacterConfig.patterns` clamped 0..6; `config.yaml`, `config.example.yaml`, and the admin template carry `patterns` (no `examples` left anywhere).
- [x] Keyword fallback keeps the turn working when `sentence-transformers` or the vectors are missing; `--embed` is the only path that needs the model.
- [x] `scripts/patterns.py`: `--check` (schema, gates, duplicates, reachability), `--embed`, `--stats`, `--build-xlsx`, `--apply-xlsx` (1:1 by id, unknown ids reported).
- [x] `SOUL.md` is a character card (identity, personality, voice, rules, backstory, relationships, four examples), ≤3600 chars, no trainer/coach framing, honest about being an AI.
- [x] Level semantics live in `engine.character`: `toxicity` 0-4, `adult_memes` 0-3, `roast_jabs` 1-3, `max_questions` 1-2, plus `regulate` as the deterministic rewrite.
- [x] `target.py`: typing-style gender, avatar only as a fallback, `/gender` override; unsure means gentle and no curse.
- [x] `scripts/replay.py` replays `tests/data/scenarios.json` through the live prompt and writes `workspace/replay-<id>.json` with a hard-fail count.

## Character data contract

- [x] The runtime character sheet is `sanka/data/patterns.json` (plus `pattern_vectors.json`); there is no second source of moves.
- [x] No raw transcripts, private exports, phone numbers, or dataset copies live in the repo or in the prompt.
- [x] Every `say` line comes from an owner-approved correction; `content` is imperative, one line, 10-200 chars.
- [x] Every pattern has a register the selector can reach, so no row is dead weight (`--check` fails otherwise).
- [x] Editing content requires `--check` then `--embed`; the selector ignores vectors whose model/dim do not match.

## Privacy, safety, and settings

- [x] Numeric memory levels 0-4 (public, team, personal, sensitive, secret); level 5/credentials are never stored.
- [x] Deterministic `PrivacyPolicy`: show/disclose matrices, group vs DM ceilings, owner = speaker, subject veto, confirmation state.
- [x] L0-L2 stay fluid; group L3 and L4 ask through `approvals`; the auditor runs only when a risky entry exists.
- [x] Deterministic `SafetyPolicy` (`allow|pending|deny`); approvals bound to chat+sender+TTL; `/approve` re-checks role permissions.
- [x] `settings` tool: `schema/get/set/unset/write/reload`, atomic mirror, backup, audit, hot reload; the loader never `mkdir`s.
- [x] `/status` reports mode, lease, uptime, database, memory, approvals, soul/engine, privacy, and language.

## Gateways

- [x] `Gateway`/`Inbound`/`GatewayError` contract; tool and command registries merged with duplicates rejected.
- [x] WhatsApp: ingress, media, directory, history sync, send/typing/reaction/render; a partial send never marks the message answered.
- [x] CLI REPL `sanka console`; CLI gateway only, role from `config.yaml`.
- [x] `send_to` only takes explicit archive targets, and only after that room's archive was read.

## Packaging and docs

- [x] `pyproject` script `sanka`, extras `whatsapp`/`vibe`/`memory`/`dev`; ruff + coverage config; Dockerfile + pgvector compose.
- [x] `sonar-project.properties` for SonarCloud (sources, tests, exclusions, coverage path).
- [x] English README with a full Indonesian section; TASK/LOG/AGENTS/SOUL are the only other markdown files.
- [x] The corpus toolchain is deleted: fetch/ingest/Excel review/chat review/training scripts, the downloaded dataset inbox, and `conversation_datasheet.json`.

## Verification

- [x] `pytest -q`.
- [x] `python -m sanka selftest`.
- [x] `python scripts/patterns.py --check` and `--embed` (43 patterns, 384 dims).
- [x] Selector smoke: comfort first for a distressed message, no roast pattern at toxicity 1, count 0 returns `[]`, banter returns the roast move first.
- [x] Workbook roundtrip: `--build-xlsx`, edit one `konten` cell, `--apply-xlsx`, `--check` clean.
- [x] `python -m sanka config import` mirrors `patterns` into the `engine` document.
- [x] Replay smoke on a roast scenario with a real key: transcript written, no curse for an unknown target.
- [ ] Real WhatsApp end-to-end (needs production credentials/QR).
- [ ] SonarCloud scan (needs `SONAR_TOKEN` and `sonar-scanner` on the host).
