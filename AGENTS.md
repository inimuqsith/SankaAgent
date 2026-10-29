# AGENTS

Hard rules for changes in this repo:

- Python only. No HTTP gateway, no Node/Baileys, no bridge, no extra server.
- Five docs only: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`, `SOUL.md`. Do not create new `.md` files.
- Core lives in `sanka/engine/` and must not import `sanka.gateways` or `neonize`. Root `sanka/` only holds `__main__.py`, `admin/`, `gateways/`, `vendor/`.
- A gateway is an appendage: transport only through the contract in `sanka/engine/inbound.py` (`Inbound`, `Gateway`, `GatewayError`). Gateway-owned tools/commands live in `sanka/gateways/<name>/tools|commands`, never in core.
- Six tool families only (`shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`). A new tool is a module in `sanka/engine/tools/` with `TOOL = Handler(...)` and a family from `FAMILIES`; the filename must match `name`; never edit the registry.
- A new command is a module in `sanka/engine/commands/` with `COMMAND = Handler(...)`; never edit the dispatcher.
- Permissions come only from roles (`role:<name>` documents / the `roles.yaml` mirror). No handler has default permissions. A missing `tools`/`commands` entry denies everything. Tool permission is checked only at `sanka/engine/tools/__init__.py:execute`.
- Documents `engine`, `soul`, `persona`, and `role:<name>` are the source of truth in the database; `config.yaml` (engine/soul sections), `SOUL.md`, and `roles.yaml` are seeds plus mirrors. Manual edit → `sanka config import`. The loader must never create directories.
- Config layout: one host-local `config.yaml` holds host keys (`database`, `workspace`, `roles_file`, `sandbox`, `web_search`, `mcp`, `sync_documents`) plus `engine`, `gateways`, and `soul`. One `roles.yaml` holds every role. `SOUL.md` holds the persona; there are no separate `engine.yaml`/`gateway.yaml`/`soul.yaml` or `roles/` files. Secrets only in `.env` (env names referenced from YAML); `config.yaml`, `roles.yaml`, `.env`, and `.sanka/` are never committed.
- Storage: SQLAlchemy Core + `psycopg` to Postgres; one `Database` (`sanka/engine/db.py`) and one `MetaData` (`sanka/engine/schema.py`). No `sqlite3`, `PRAGMA`, FTS5, or state files in stores. SQLite is only a test URL.
- Semantic memory only through Mem0 (`sanka/engine/memory/mem0.py`, `mem0ai`). Never revive `MemoryStore`/custom reflection. The action ledger stays `ActionLedger`; transcripts stay per chat.
- Cross-chat identity: query only `global` + `person:<sender>` + `chat:<this chat>`; another person's private memory is never queried and must not leak into another chat.
- Memory carries a numeric privacy level 0-4 (public, team, personal, sensitive/aib, secret). Level 5/credentials are never stored. `owner_id` is the speaker who stated the fact; `subject_id` is who it is about and only that subject may veto it. Level maths, veto precedence, and disclosure verdicts live in `sanka/engine/memory/privacy.py` (deterministic, no LLM). The LLM only labels a provisional level, phrases the confirmation question, and identifies which entries a draft mentions; `sanka/engine/privacy.py` is the only agent-facing hook.
- Disclosure policy: L0-L2 stay fluid everywhere; L3 in a group and L4 ask for confirmation through `approvals`; vetoed entries are never shown and never disclosed. The auditor pass runs only when a risky entry exists.
- User-facing replies follow `soul.language`, which is YAML config (`soul.language` in `config.yaml` / the `soul` document), never a Python string catalog. Only `en` and `id` are supported; `/language en|id` (owner-only) writes the setting. `SOUL.md` stays Indonesian and is not translated.
- Sensitive actions (`shell`, `sandbox`) and internal changes (`settings`) must pass `SafetyPolicy`/`approvals` and `/approve`. Default mode `yolo`; `off` disables approval (document the risk). `/approve` never calls the LLM and re-checks role permissions.
- One primary: `engine_lease`. An instance without the lease stays standby and frozen; `/freeze` and takeover happen only through the lease/DB, never two hearts.
- Prompt scaffolding and model-facing payloads are English; the `SOUL.md` persona and role `description`/`context` stay Indonesian.
- Code must stay minimal but solid. Do not add abstractions without need.
- The test suite is small and focused on repo contracts: routing/format/permission, gateway contract, lease, approval, cross-chat memory, privacy/disclosure, language selection, and the Neonize reply path. Do not re-test library behavior.
- Verify before finishing: `pytest -q` and `python -m sanka selftest`. For gateway/lease changes also smoke `sanka console --takeover`.
