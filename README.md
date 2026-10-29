# SankaAgent

Sanka is a persistent soul engine: one identity, one memory, many chat surfaces. The engine
(`sanka/engine/`) owns the agent loop, config, memory, emotion, and tools. Gateways are appendages
that only carry messages in and out. WhatsApp is one gateway; a local CLI REPL is another.

- **One database = one soul.** Transcripts, memory, affect, style, skills, recipes, config/soul/role
  documents, approvals, and the engine lease live in Postgres. Any host that points at the same
  `SANKA_DATABASE_URL` sees the same Sanka.
- **One primary at a time.** `engine_lease` makes exactly one instance serve turns. Other instances
  stay in standby, frozen. `/freeze` or a local `sanka console --takeover` moves the heartbeat.
- **Identity is cross-chat.** Semantic memory is stored per person/agent/chat with Mem0 and queried
  across chats for the same person. Raw transcripts stay per chat.
- **Actions are explicit.** `shell`, `sandbox`, and internal `settings` changes wait for `/approve`
  unless approval is disabled. Common tools still run automatically in the default `yolo` mode.
- **Config is data.** `engine`, `soul`, `persona`, and `role:<name>` are documents in the database
  with file mirrors. Editing a file by hand needs `sanka config import`; the runtime reads the DB.

## Architecture

```mermaid
flowchart LR
  WA[WhatsApp gateway] -->|Inbound| AG[Agent]
  CLI[CLI gateway] -->|Inbound| AG
  AG --> LLM[LLM + tools]
  AG --> MEM[Mem0 semantic memory]
  AG --> ACT[ActionLedger]
  AG --> AFF[Affect + style + vibe]
  AG --> SK[Skills + recipes]
  AG --> DB[(Postgres + pgvector)]
  AG -->|deliver_*| WA
  AG -->|deliver_*| CLI
  SUP[Supervisor + lease] --> AG
  DB --- SUP
```

The core (`sanka/engine`) never imports `neonize` or `sanka.gateways`; `tests/test_gateway_contract.py`
proves it with a subprocess import.

## Quickstart (Postgres + pgvector)

```bash
# 1. Database: any Postgres 14+ with pgvector, e.g. the sample compose file
cp compose.example.yaml compose.yaml   # set a real password
podman compose -f compose.yaml up -d postgres

# 2. Scaffold host config and journals
python -m sanka init --dir .

# 3. Fill .env
#    OPENCODE_GO_API_KEY=...
#    SANKA_DATABASE_URL=postgresql+psycopg://sanka:CHANGE_ME@127.0.0.1:5432/sanka

# 4. Create the schema, seed documents from files, verify
python -m sanka db upgrade
python -m sanka db check

# 5. Enable WhatsApp in gateway.yaml (sanka init disables it for local-only scaffolds),
#    then run the primary
python -m sanka run

# Local console against the same DB (takes the lease by default)
python -m sanka console --takeover
```

Requirements: Python 3.11+, Postgres 14+ with `vector`, and the WhatsApp extra for the WhatsApp
gateway. `sentence-transformers` downloads the default embedder once
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dims).

Dependency note: Mem0 pins `protobuf<7` while Neonize needs `protobuf>=7`. Install Neonize first,
then Mem0 without its resolver, and provide Mem0's other dependencies explicitly:

```bash
pip install -e ".[whatsapp,vibe]"
pip install "sentence-transformers>=5.2,<6"
pip install --no-deps "mem0ai>=2.2,<3"
pip install httpx openai posthog pydantic pytz qdrant-client protobuf
```

The `Dockerfile` does exactly this. Without Mem0 the bot still replies and `/status` reports
`Memory: unavailable: <reason>`.

## Config surfaces

| File / document | Where | Contents |
|---|---|---|
| `config.yaml` | host only | `database`, `workspace`, `gateway_file`, `engine_file`, `roles_dir`, `sandbox`, `web_search`, `mcp`, `sync_documents` |
| `gateway.yaml` | host only | `gateways.whatsapp` (`session_db`, `backlog_seconds`), `gateways.cli` (`role`, `user`, `chat_id`, `chat_name`) |
| `engine.yaml` / document `engine` | DB + mirror | `llm`, `default_role`, `reply`, `memory`, `approval`, `skills`, `prompt`, `humanize`, `style`, `affect`, `ambient` |
| `soul.yaml` / document `soul` | DB + mirror | `name`, `language` (`id`/`en`/`auto`/BCP-47), `pronouns` |
| `SOUL.md` / document `persona` | DB + mirror | persona prose (never translated by the engine) |
| `roles/*.yaml` / documents `role:<name>` | DB + mirror | explicit `tools`/`commands`/`users`/`context`/`priority` |

`config.yaml` and `gateway.yaml` stay host-local because they describe the deployment. The loader
never creates directories. `sync_documents: false` disables seeding/exporting for tests.

**Manual edits:** the runtime reads DB documents. After editing `engine.yaml`, `soul.yaml`,
`SOUL.md`, or `roles/*.yaml` by hand, run `python -m sanka config import`. To write the mirrors from
the DB, run `python -m sanka config export`.

## Roles

Roles are explicit and modular; there is no default permission in handlers. Every role names the
tools and commands it may use (`*` works as a glob). Addresses may appear in several roles; their
permissions are unioned and their contexts are ordered by `priority` (descending) then name. A
sender who matches nothing gets `default_role`.

```yaml
name: owner
description: Owner and admin
priority: 100
users: ["6280000000000", "130799689031758"]
tools: ["*"]
commands: ["*"]
context: |
  You are talking to Sanka's owner.
```

Role files live in `roles/` (and the `role:<name>` documents). `sanka init` writes `owner`,
`member`, and `guest` examples; edit them with the `settings` tool or directly.

## Gateways

- **WhatsApp** (`sanka/gateways/whatsapp`): Neonize transport, session DB `0600`, media download,
  history sync, contact/LID directory, mention resolution, and `send`/`send_to` tools plus
  `/backfill`.
- **CLI** (`sanka/gateways/cli`): REPL for a local terminal. `sanka console` always enables it, even
  if `gateway.yaml` disables it, and never starts WhatsApp. Anyone with shell access is treated as
  the configured `cli.role` (default: `default_role`).
- Adding a gateway: expose an entry point in group `sanka.gateways` and implement the protocol in
  `sanka/engine/inbound.py`. Gateway-owned tools/commands are discovered from
  `sanka/gateways/<name>/tools` and `commands`; duplicate names are rejected at startup.

`gateway.render()` converts markdown to the transport dialect; `gateway.prompt_hints()` adds
transport syntax only (WhatsApp formatting, mentions, surface). The base prompt is transport-neutral.

## Shared DB, lease, freeze, console

Only the lease holder serves turns. `sanka run` and `sanka console` acquire `engine_lease`;
renewals happen every 5 seconds with a 30 second TTL. Losing the lease (or `/freeze`) stops the
gateways and ambient loop, releases the lease, and keeps the process in standby, retrying.

```bash
# Production
python -m sanka run
# Local, take the lease; the production instance freezes within ~5 seconds
python -m sanka console --takeover
# Inspect the holder
python -m sanka db check
```

`/quit` in the console releases the lease; the production instance promotes itself again. WhatsApp
reconnects with the same session DB after promotion.

## Memory engine (Mem0)

Mem0 OSS performs extraction, dedupe, embedding, and retrieval. Sanka stores only the identities,
the autographical `ActionLedger`, and affect/style/skill data.

| Scope | Mem0 identity | Metadata | Privacy |
|---|---|---|---|
| `person` | `user_id=person:<id>` | `audience=private` | follows that person into other chats |
| `chat` | `run_id=chat:<chat_id>` | `audience=group` | stays in that chat |
| `global` | `agent_id=sanka` | `audience=shared` | shared team facts |

Every turn queries exactly `global` + `person:<sender>` + `chat:<current chat>`. Another person's
private memory is never queried. The prompt tells the model never to reveal one chat's private
memory to a third person. `memory.backend: null` disables Mem0; failures fail open with a reason in
`/status`.

`memory_legacy` stages SQLite-era memories. `sanka memory migrate` maps DMs to `person`, groups to
`chat`, and globals to `global`; it is idempotent and never re-migrates a row.

## Affect, style, skills, recipes

- `AffectStore` keeps PAD emotion, trust, relationships, topics, and drives in Postgres; the state
  row is read/written under one transaction with `FOR UPDATE` on Postgres.
- `StyleStore` aggregates rhythm/diction statistics from the archive; raw message content is not
  stored there.
- `SkillStore` keeps procedures (`skills`, `skill_files`, `skill_usage`). The model loads an index
  and full bodies via the `skill` tool.
- `RecipeStore` keeps user commands (`/nama`) as YAML bodies; `recipe` steps run tools with
  `{{args}}`, `prompt` steps are one LLM turn.
- `VibeRunner` keeps room mood per chat in the `state` table.

## Approvals and safety

Default `approval.mode: yolo` lets ordinary tools run. These need `/approve`:

- every tool in `approval.sensitive_tools` (default `shell`, `sandbox`, globs allowed),
- every internal change through the `settings` tool when `approval.settings: true`,
- in `strict` mode, every tool except read-only observation (`read`, `memory`, `skill`,
  `chat_history`, `web_search`).

Flow: the model receives `pending approval #ID`, tells the sender, and stops. `/pending` lists,
`/approve ID` executes with the original role and re-checks permissions, `/deny ID` cancels. Pending
items are bound to `chat_id` + sender, expire after `ttl_seconds` (default 900), and `/approve` never
calls the LLM or changes emotion. An approved `shell` is host access by design; `settings` is the
validated path for config changes.

## Status, settings, audit

`/status` reports mode, lease holder, uptime, surface, model, database, chat/roles, archive size,
memory status, legacy pending, approvals, skills, people/actions, soul/persona mirror, engine
document, and the last config change. `/whoami` reports roles, chat, surface, and self handles.

The `settings` tool (role-granted) reads and changes Sanka itself: `engine`, `host`, `gateway`,
`soul`, `persona`, `role`. `set`/`unset`/`write` validate the candidate through the real loader,
write the DB document, export the mirror atomically, back up the file, record `config_changes`, and
hot-reload. Restart-only keys return `ok: disimpan; restart required ...` instead of reloading.
Secret-looking keys (`api_key`, `password`, `secret`, `token`) are rejected except `api_key_env`;
paths stay inside the known files.

Audit examples:

```sql
select count(*) from sanka_memories;                    -- Mem0 pgvector collection
select scope, kind, count(*) from memory_legacy group by 1,2;
select count(*) from messages;
select * from engine_lease;
select id, kind, status, summary from approvals order by id desc limit 5;
select * from config_changes order by id desc limit 5;
```

## Migration from SQLite

```bash
# 1. Stop the bot.
# 2. Split the old config
python -m sanka config migrate --dir .
# 3. Point SANKA_DATABASE_URL at Postgres, then
python -m sanka db upgrade
# 4. Import the old .sanka data (history, affect, style, skills, recipes, legacy memory)
python -m sanka db import --sqlite .sanka
# 5. Move staged memories into Mem0
python -m sanka memory migrate
# 6. Copy host-local files: whatsapp.db, workspace attachments, .env
# 7. Verify and start
python -m sanka db check
python -m sanka run
```

`db import` refuses while a fresh lease exists (`--force` overrides). `db backup --out file.dump`
uses `pg_dump --format=custom`; `db restore file.dump --yes` uses `pg_restore --clean --if-exists`.
Back up the host-local session DB and workspace separately.

## Development and tests

```bash
pytest -q
python -m sanka selftest          # temporary SQLite DB for stores; real LLM if the key exists
python -m sanka console --takeover
```

Tests use SQLite file URLs and fake gateways/LLMs; runtime uses Postgres. `sanka selftest` never
touches the production database. If Mem0/pgvector is unavailable, its memory check is `SKIP`, not
`FAIL`.

## Security

- Secrets only in `.env` (referred by name from YAML). `config.yaml`, `gateway.yaml`, `engine.yaml`,
  `soul.yaml`, `roles/`, `.env`, and `.sanka/` are ignored by git.
- Session DB and `.env` are `0600`. `sanka db backup` writes `0600`.
- The database password is redacted from logs, `/status`, and audit rows.
- `settings` never touches `.env` and rejects sensitive key paths.
- Role files may contain phone numbers; keep the mirrors private.

## Design principles

1. Identity persists beyond a session: windows are per chat, memory is per person/agent/chat.
2. A gateway is an appendage, never the brain; sensors and actuators can be added the same way.
3. Memory has layers: Mem0 for semantic facts, `ActionLedger` for autobiography, affect/style for
   how Sanka feels and talks, skills/recipes for procedures.
4. Safety is deterministic and auditable (`SafetyPolicy` + `approvals`), not a prompt suggestion.
5. Real-time motor control is out of scope for this iteration; the seams (`Gateway`,
   `WorkingMemory`, `SafetyPolicy`, nullable `where`/`sensor_context` metadata) are in place.

## Bahasa Indonesia

Sanka adalah mesin jiwa persisten: satu identitas, satu database, banyak surface chat. Core ada di
`sanka/engine/`; gateway hanya membawa pesan masuk/keluar. WhatsApp satu gateway, CLI gateway lain.

Quickstart:

```bash
cp compose.example.yaml compose.yaml      # isi password
python -m sanka init --dir .              # config.yaml, gateway.yaml, engine.yaml, soul.yaml, roles/, SOUL.md
# isi .env: OPENCODE_GO_API_KEY dan SANKA_DATABASE_URL
python -m sanka db upgrade
python -m sanka run                       # primary
python -m sanka console --takeover        # console lokal, mengambil lease
```

| Berkas / dokumen | Isi |
|---|---|
| `config.yaml` | host: database, workspace, sandbox, web_search, mcp |
| `gateway.yaml` | whatsapp (session_db, backlog_seconds), cli (role, chat_id) |
| `engine.yaml` / dokumen `engine` | llm, default_role, memory, approval, prompt, affect, ambient |
| `soul.yaml` / dokumen `soul` | nama, bahasa, pronomina |
| `SOUL.md` / dokumen `persona` | persona |
| `roles/*.yaml` / dokumen `role:<nama>` | izin eksplisit per role |

Setelah mengedit berkas secara manual, jalankan `python -m sanka config import`; runtime membaca
dokumen DB. Hanya satu instance memegang lease; instance lain standby. `/freeze` atau
`sanka console --takeover` memindahkan lease. Memori semantik memakai Mem0 dengan scope
`person`/`chat`/`global`: ingatan tentang seseorang ikut ke chat lain, rahasia grup tetap di grup,
dan memori privat orang lain tidak pernah di-query. `shell`, `sandbox`, dan perubahan `settings`
menunggu `/approve` (default `yolo`); `/pending`, `/approve`, `/deny` tersedia sesuai role.
Verifikasi: `pytest -q` dan `python -m sanka selftest`.
