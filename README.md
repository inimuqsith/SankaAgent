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
- **Privacy is a disclosure layer.** Every memory entry carries a level 0-4. Ordinary chat stays
  fluid; only sensitive or grave entries are guarded, and the speaker or the subject decides.
- **Actions are explicit.** `shell`, `sandbox`, and internal `settings` changes wait for `/approve`
  unless approval is disabled. Common tools still run automatically in the default `yolo` mode.
- **Config is data.** One `config.yaml` holds host keys plus `engine`, `gateways`, and `soul`; one
  `roles.yaml` holds every role; `SOUL.md` holds the persona. The same documents live in the
  database and are mirrored back to those files.

## Architecture

```mermaid
flowchart LR
  WA[WhatsApp gateway] -->|Inbound| AG[Agent]
  CLI[CLI gateway] -->|Inbound| AG
  AG --> LLM[LLM + tools]
  AG --> MEM[Mem0 semantic memory]
  AG --> PRIV[Privacy policy + auditor]
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

# 2. Scaffold config.yaml, roles.yaml, SOUL.md, and .env
python -m sanka init --dir .

# 3. Fill .env
#    OPENCODE_GO_API_KEY=...
#    SANKA_DATABASE_URL=postgresql+psycopg://sanka:CHANGE_ME@127.0.0.1:5432/sanka

# 4. Create the schema, seed documents from the files, verify
python -m sanka db upgrade
python -m sanka db check

# 5. Enable WhatsApp in the `gateways` section of config.yaml (sanka init disables it),
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
| `config.yaml` | host only | host keys (`database`, `workspace`, `roles_file`, `sandbox`, `web_search`, `mcp`, `sync_documents`) plus `engine`, `gateways`, and `soul` |
| `roles.yaml` | host only, `0600` | every role under one `roles:` mapping: `tools`, `commands`, `users`, `context`, `priority`, `memory_admin` |
| `SOUL.md` / document `persona` | DB + mirror | persona prose (never translated by the engine) |
| `engine` section / document `engine` | DB + `config.yaml` | `llm`, `default_role`, `reply`, `memory` (incl. `privacy`), `approval`, `skills`, `prompt`, `humanize`, `style`, `affect`, `ambient` |
| `gateways` section | `config.yaml` | `whatsapp` (`enabled`, `session_db`, `backlog_seconds`), `cli` (`enabled`, `role`, `user`, `chat_id`, `chat_name`) |
| `soul` section / document `soul` | DB + `config.yaml` | `name`, `language` (`id`/`en`), `pronouns` |
| `role:<name>` documents | DB + `roles.yaml` | the same explicit role bodies |

`config.yaml` and `roles.yaml` stay host-local because they describe the deployment and may hold
phone numbers. The loader never creates directories. `sync_documents: false` disables
seeding/exporting for tests.

**Manual edits:** the runtime reads DB documents. After editing the `engine`/`soul` sections,
`SOUL.md`, or `roles.yaml` by hand, run `python -m sanka config import`. To write the files from the
DB, run `python -m sanka config export`.

## Roles

Roles are explicit and modular; there is no default permission in handlers. Every role names the
tools and commands it may use (`*` works as a glob). Addresses may appear in several roles; their
permissions are unioned and their contexts are ordered by `priority` (descending) then name. A
sender who matches nothing gets `default_role`. `memory_admin: true` is required for global memory
writes and deletes.

```yaml
roles:
  owner:
    name: owner
    description: Pemilik dan admin Sanka
    priority: 100
    users: ["6280000000000", "130799689031758"]
    tools: ["*"]
    commands: ["*"]
    memory_admin: true
    context: |
      Kamu sedang bicara dengan pemilik Sanka.
  guest:
    name: guest
    description: Pengirim tak dikenal
    priority: 0
    tools: [read, web_search, chat_history, memory]
    commands: [help, ping, reset, whoami, status, tools, cari]
    memory_admin: false
```

`sanka init` writes `owner`, `member`, and `guest` examples; edit them with the `settings` tool,
`/language`, or directly plus `sanka config import`.

## Language selector

Sanka always speaks exactly one language at a time, configured in YAML, never in code strings:

```yaml
soul:
  language: id   # catalog code: sanka/lang/<code>.yaml
```

- `/language` prints the current value and the installed codes; `/language <code>` changes it.
- Only the owner role may run it (owner has `commands: ["*"]`; member/guest do not list it).
- The command writes the `soul` document, mirrors `config.yaml`, and hot-reloads.
- `auto`/`in` map to `id`; a code with no catalog is rejected and the reply lists what is installed.
- `/status` shows `Language: <code>`; the Self prompt block and `Reply in ...` line follow it.

Every chat-visible string lives in `sanka/lang/<code>.yaml`; add a language by copying a catalog and
translating its values, and `soul.language` selects which one the bot speaks.

## Privacy levels and disclosure

Memory entries carry a numeric level; credential-like text is never stored.

| Level | Meaning | Group | DM |
|---|---|---|---|
| 0 | public (event info, jokes) | fluid | fluid |
| 1 | team/internal logistics | fluid | fluid |
| 2 | personal, preferences, casual facts | fluid | fluid |
| 3 | sensitive/aib/harmony risk | owner confirmation | owner: fluid |
| 4 | grave secret | owner confirmation | confirmation once |
| 5 | credentials | never stored | never stored |

Rules:

- `owner_id` is the speaker who stated the fact; `subject_id` is the person it is about. Only the
  subject may veto an entry; a veto hides it from context and tool output and blocks disclosure.
- L0-L2 never trigger confirmation or the auditor. Group ceiling is `group_max_level` (2) and DM
  ceiling is `dm_max_level` (4); `ask_level` (4) asks at capture time.
- The LLM only labels a provisional level/subject, phrases the confirmation question in the soul's
  language, and lists which entries a draft mentions. The verdict itself is deterministic
  (`sanka/engine/memory/privacy.py`): vetoed → deny, non-owner group disclosure → deny, unconfirmed
  owner disclosure → ask, otherwise allow.
- The auditor runs only when a risky entry exists; if it fails, the safe default is ask/deny, never
  the unsanitized draft.
- The `memory` tool exposes `level`, `subject`, `confirmed`, `veto`, and `active=false` to unveto;
  `/memori` shows levels, hides vetoed entries, and limits global add/delete to `memory_admin`.

## Gateways

- **WhatsApp** (`sanka/gateways/whatsapp`): Neonize transport, session DB `0600`, media download,
  history sync, contact/LID directory, mention resolution, and `send`/`send_to` tools plus
  `/backfill`.
- **CLI** (`sanka/gateways/cli`): REPL for a local terminal. `sanka console` always enables it, even
  if `config.yaml` disables it, and never starts WhatsApp. Anyone with shell access is treated as
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

| Scope | Mem0 identity | Metadata | Visibility |
|---|---|---|---|
| `person` | `user_id=person:<id>` | `audience=private` | follows that person into other chats |
| `chat` | `run_id=chat:<chat_id>` | `audience=group` | stays in that chat |
| `global` | `agent_id=sanka` | `audience=shared` | shared team facts |

Every turn queries exactly `global` + `person:<sender>` + `chat:<current chat>`. Another person's
private memory is never queried. The prompt tells the model never to reveal one chat's private
memory to a third person. `memory.backend: null` disables Mem0; failures fail open with a reason in
`/status`.

`memory_legacy` stages SQLite-era memories. `sanka memory migrate` maps DMs to `person`, groups to
`chat`, and globals to `global`; it is idempotent, never re-migrates a row, and writes level 2.

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
- privacy/disclosure confirmations (`kind=privacy|disclosure`),
- in `strict` mode, every tool except read-only observation (`read`, `memory`, `skill`,
  `chat_history`, `web_search`).

Flow: the model receives `pending approval #ID`, tells the sender, and stops. `/pending` lists,
`/approve ID` executes with the original role and re-checks permissions, `/deny ID` cancels. Pending
items are bound to `chat_id` + sender, expire after `ttl_seconds` (default 900), and `/approve` never
calls the LLM or changes emotion. An approved `shell` is host access by design; `settings` is the
validated path for config changes.

## Status, settings, audit

`/status` reports mode, lease holder, uptime, surface, model, database, chat/roles, archive size,
memory status, legacy pending, approvals, skills, people/actions, soul/persona mirror, language,
privacy flags, engine mirror, and the last config change. `/whoami` reports roles, chat, surface,
and self handles.

The `settings` tool (role-granted) reads and changes Sanka itself: `engine`, `host`, `gateway`,
`soul`, `persona`, `role`. `engine`/`soul` map to the `config.yaml` sections and DB documents;
`gateway` edits only the `gateways` mapping; `role` regenerates `roles.yaml`; `host` rejects paths
inside `engine`/`gateways`/`soul`. `set`/`unset`/`write` validate the candidate through the real
loader, write the DB document, export the mirror atomically, back up the file, record
`config_changes`, and hot-reload. Restart-only keys return `ok: saved; restart required ...` instead
of reloading. Secret-looking keys (`api_key`, `password`, `secret`, `token`) are rejected except
`api_key_env`; paths stay inside the known files.

Audit examples:

```sql
select count(*) from sanka_memories;                    -- Mem0 pgvector collection
select scope, kind, count(*) from memory_legacy group by 1,2;
select count(*) from messages;
select * from engine_lease;
select id, kind, status, summary from approvals order by id desc limit 5;
select * from config_changes order by id desc limit 5;
```

## Migration from SQLite or the split config

```bash
# 1. Stop the bot.
# 2. Fold the old layout into config.yaml + roles.yaml (add --dry-run to inspect).
#    A backup lands in config.bak-<timestamp>/; --force removes the old files afterwards.
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
`FAIL`, and the privacy policy matrix still prints PASS.

## Security

- Secrets only in `.env` (referred by name from YAML). `config.yaml`, `roles.yaml`, `.env`, and
  `.sanka/` are ignored by git.
- `roles.yaml`, the session DB, and `.env` are `0600`. `sanka db backup` writes `0600`.
- The database password is redacted from logs, `/status`, and audit rows.
- `settings` never touches `.env` and rejects sensitive key paths.
- Vetoed and L3/L4 entries are hidden from group tool output; a non-owner cannot confirm disclosure.

## Design principles

1. Identity persists beyond a session: windows are per chat, memory is per person/agent/chat.
2. A gateway is an appendage, never the brain; sensors and actuators can be added the same way.
3. Memory has layers: Mem0 for semantic facts, `ActionLedger` for autobiography, affect/style for
   how Sanka feels and talks, skills/recipes for procedures.
4. Safety and privacy are deterministic and auditable (`SafetyPolicy` + `approvals` +
   `PrivacyPolicy`), not a prompt suggestion.
5. Real-time motor control is out of scope for this iteration; the seams (`Gateway`,
   `WorkingMemory`, `SafetyPolicy`, nullable `where`/`sensor_context` metadata) are in place.

## Bahasa Indonesia

Sanka adalah mesin jiwa persisten: satu identitas, satu memori, banyak surface chat. Engine
(`sanka/engine/`) memegang loop agen, config, memori, emosi, dan tool. Gateway hanyalah appendage
yang membawa pesan masuk dan keluar. WhatsApp salah satu gateway; REPL CLI lokal gateway lainnya.

- **Satu database = satu jiwa.** Transkrip, memori, affect, style, skills, recipes, dokumen
  config/soul/role, approval, dan lease engine tinggal di Postgres. Host mana pun yang menunjuk
  `SANKA_DATABASE_URL` yang sama melihat Sanka yang sama.
- **Satu primary pada satu waktu.** `engine_lease` memastikan hanya satu instance melayani turn.
  Instance lain standby dan beku. `/freeze` atau `sanka console --takeover` memindahkan heartbeat.
- **Identitas lintas chat.** Memori semantik disimpan per person/agent/chat lewat Mem0 dan di-query
  lintas chat untuk orang yang sama. Transkrip mentah tetap per chat.
- **Privasi adalah lapisan pengungkapan.** Setiap entri memori punya level 0-4. Obrolan biasa tetap
  cair; hanya entri sensitif atau berat yang dijaga, dan pembicara atau subjek yang memutuskan.
- **Aksi eksplisit.** `shell`, `sandbox`, dan perubahan internal `settings` menunggu `/approve`
  kecuali approval dimatikan. Tool umum tetap berjalan otomatis di mode default `yolo`.
- **Config adalah data.** Satu `config.yaml` memuat kunci host plus `engine`, `gateways`, dan
  `soul`; satu `roles.yaml` memuat semua role; `SOUL.md` memuat persona. Dokumen yang sama hidup di
  database dan dicerminkan kembali ke berkas itu.

### Arsitektur

Diagram yang sama seperti di atas: gateway WhatsApp/CLI memanggil `Agent` lewat `Inbound`; `Agent`
memakai LLM+tools, Mem0, privacy policy/auditor, `ActionLedger`, affect/style/vibe, skills/recipes,
dan Postgres+pgvector. Core tidak pernah mengimpor `neonize` atau `sanka.gateways`
(`tests/test_gateway_contract.py` membuktikannya lewat subprocess import).

### Quickstart (Postgres + pgvector)

```bash
# 1. Database: Postgres 14+ dengan pgvector, mis. compose contoh
cp compose.example.yaml compose.yaml   # isi password sungguhan
podman compose -f compose.yaml up -d postgres

# 2. Scaffold config.yaml, roles.yaml, SOUL.md, dan .env
python -m sanka init --dir .

# 3. Isi .env
#    OPENCODE_GO_API_KEY=...
#    SANKA_DATABASE_URL=postgresql+psycopg://sanka:CHANGE_ME@127.0.0.1:5432/sanka

# 4. Buat schema, seed dokumen dari berkas, verifikasi
python -m sanka db upgrade
python -m sanka db check

# 5. Aktifkan WhatsApp di bagian `gateways` config.yaml (sanka init mematikan),
#    lalu jalankan primary
python -m sanka run

# Console lokal ke DB yang sama (mengambil lease secara default)
python -m sanka console --takeover
```

Kebutuhan: Python 3.11+, Postgres 14+ dengan `vector`, dan extra WhatsApp untuk gateway WhatsApp.
`sentence-transformers` mengunduh embedder default sekali
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dim).

Catatan dependensi: Mem0 mem-pin `protobuf<7` sementara Neonize butuh `protobuf>=7`. Pasang Neonize
dulu, lalu Mem0 tanpa resolver, dan sediakan dependensi Mem0 lain secara eksplisit:

```bash
pip install -e ".[whatsapp,vibe]"
pip install "sentence-transformers>=5.2,<6"
pip install --no-deps "mem0ai>=2.2,<3"
pip install httpx openai posthog pydantic pytz qdrant-client protobuf
```

`Dockerfile` melakukan hal yang sama. Tanpa Mem0 bot tetap membalas dan `/status` menampilkan
`Memory: unavailable: <alasan>`.

### Permukaan config

| Berkas / dokumen | Lokasi | Isi |
|---|---|---|
| `config.yaml` | host saja | kunci host (`database`, `workspace`, `roles_file`, `sandbox`, `web_search`, `mcp`, `sync_documents`) plus `engine`, `gateways`, dan `soul` |
| `roles.yaml` | host saja, `0600` | semua role dalam satu mapping `roles:`: `tools`, `commands`, `users`, `context`, `priority`, `memory_admin` |
| `SOUL.md` / dokumen `persona` | DB + mirror | prosa persona (tidak diterjemahkan engine) |
| bagian `engine` / dokumen `engine` | DB + `config.yaml` | `llm`, `default_role`, `reply`, `memory` (termasuk `privacy`), `approval`, `skills`, `prompt`, `humanize`, `style`, `affect`, `ambient` |
| bagian `gateways` | `config.yaml` | `whatsapp` (`enabled`, `session_db`, `backlog_seconds`), `cli` (`enabled`, `role`, `user`, `chat_id`, `chat_name`) |
| bagian `soul` / dokumen `soul` | DB + `config.yaml` | `name`, `language` (`id`/`en`), `pronouns` |
| dokumen `role:<nama>` | DB + `roles.yaml` | body role eksplisit yang sama |

`config.yaml` dan `roles.yaml` tetap host-local karena mendeskripsikan deployment dan bisa memuat
nomor telepon. Loader tidak pernah membuat direktori. `sync_documents: false` mematikan
seed/export untuk test.

**Edit manual:** runtime membaca dokumen DB. Setelah mengedit bagian `engine`/`soul`, `SOUL.md`,
atau `roles.yaml` secara manual, jalankan `python -m sanka config import`. Untuk menulis berkas dari
DB, jalankan `python -m sanka config export`.

### Role

Role eksplisit dan modular; tidak ada izin default di handler. Setiap role menyebut tool dan command
yang boleh dipakai (`*` berlaku sebagai glob). Satu alamat boleh muncul di beberapa role; izinnya
di-union dan context-nya diurutkan menurut `priority` (turun) lalu nama. Pengirim yang tidak cocok
mendapat `default_role`. `memory_admin: true` wajib untuk tulis/hapus memori global.

```yaml
roles:
  owner:
    name: owner
    description: Pemilik dan admin Sanka
    priority: 100
    users: ["6280000000000", "130799689031758"]
    tools: ["*"]
    commands: ["*"]
    memory_admin: true
    context: |
      Kamu sedang bicara dengan pemilik Sanka.
  guest:
    name: guest
    description: Pengirim tak dikenal
    priority: 0
    tools: [read, web_search, chat_history, memory]
    commands: [help, ping, reset, whoami, status, tools, cari]
    memory_admin: false
```

`sanka init` menulis contoh `owner`, `member`, dan `guest`; ubah lewat tool `settings`, `/language`,
atau langsung lalu `sanka config import`.

### Pemilih bahasa

Sanka selalu berbicara dalam satu bahasa pada satu waktu, diatur di YAML, bukan di string kode:

```yaml
soul:
  language: id   # catalog code: sanka/lang/<code>.yaml
```

- `/language` menampilkan nilai sekarang dan kode yang terpasang; `/language <kode>` mengubahnya.
- Hanya role owner yang boleh menjalankannya (owner punya `commands: ["*"]`; member/guest tidak
  mencantumkannya).
- Command ini menulis dokumen `soul`, mencerminkan `config.yaml`, dan hot-reload.
- `auto`/`in` dipetakan ke `id`; kode tanpa katalog ditolak dan balasannya menyebut yang terpasang.
- `/status` menampilkan `Language: <kode>`; blok Self dan baris `Reply in ...` mengikutinya.

Setiap teks yang terlihat di chat ada di `sanka/lang/<kode>.yaml`; menambah bahasa berarti menyalin
satu katalog lalu menerjemahkan nilainya, dan `soul.language` memilih katalog yang dipakai.

### Level privasi dan pengungkapan

Entri memori membawa level numerik; teks mirip kredensial tidak pernah disimpan.

| Level | Arti | Grup | DM |
|---|---|---|---|
| 0 | publik (info acara, lelucon) | cair | cair |
| 1 | tim/internal | cair | cair |
| 2 | personal, preferensi, fakta santai | cair | cair |
| 3 | sensitif/aib/risiko harmoni | konfirmasi owner | owner: cair |
| 4 | rahasia berat | konfirmasi owner | konfirmasi sekali |
| 5 | kredensial | tidak pernah disimpan | tidak pernah disimpan |

Aturan:

- `owner_id` adalah pembicara yang menyatakan fakta; `subject_id` adalah orang yang dibicarakan.
  Hanya subjek yang boleh memveto entri; veto menyembunyikannya dari konteks dan output tool serta
  memblokir pengungkapan.
- L0-L2 tidak pernah memicu konfirmasi atau auditor. Plafon grup `group_max_level` (2) dan plafon DM
  `dm_max_level` (4); `ask_level` (4) bertanya saat capture.
- LLM hanya memberi label level/subjek sementara, menyusun pertanyaan konfirmasi dalam bahasa soul,
  dan mendaftar entri yang disebut draft. Keputusannya deterministik
  (`sanka/engine/memory/privacy.py`): veto → tolak, pengungkapan grup oleh non-owner → tolak,
  pengungkapan owner yang belum dikonfirmasi → tanya, selain itu izinkan.
- Auditor hanya berjalan bila ada entri berisiko; kalau gagal, default aman adalah tanya/tolak,
  bukan draft mentah.
- Tool `memory` mengekspos `level`, `subject`, `confirmed`, `veto`, dan `active=false` untuk
  membatalkan veto; `/memori` menampilkan level, menyembunyikan entri yang diveto, dan membatasi
  tambah/hapus global untuk `memory_admin`.

### Gateway

- **WhatsApp** (`sanka/gateways/whatsapp`): transport Neonize, DB sesi `0600`, unduh media, history
  sync, direktori kontak/LID, resolusi mention, tool `send`/`send_to`, dan `/backfill`.
- **CLI** (`sanka/gateways/cli`): REPL terminal lokal. `sanka console` selalu mengaktifkannya,
  meskipun `config.yaml` mematikannya, dan tidak pernah menyalakan WhatsApp. Siapa pun dengan akses
  shell diperlakukan sebagai `cli.role` yang dikonfigurasi (default: `default_role`).
- Menambah gateway: sediakan entry point di grup `sanka.gateways` dan implementasikan protokol di
  `sanka/engine/inbound.py`. Tool/command milik gateway ditemukan dari
  `sanka/gateways/<nama>/tools` dan `commands`; nama duplikat ditolak saat startup.

`gateway.render()` mengubah markdown ke dialek transport; `gateway.prompt_hints()` hanya menambah
sintaks transport (format WhatsApp, mention, surface). Prompt dasar netral transport.

### DB bersama, lease, freeze, console

Hanya pemegang lease yang melayani turn. `sanka run` dan `sanka console` mengambil `engine_lease`;
perpanjangan tiap 5 detik dengan TTL 30 detik. Kehilangan lease (atau `/freeze`) menghentikan
gateway dan loop ambient, melepas lease, dan menjaga proses standby sambil mencoba lagi.

```bash
# Produksi
python -m sanka run
# Lokal, ambil lease; instance produksi membeku dalam ~5 detik
python -m sanka console --takeover
# Periksa pemegang lease
python -m sanka db check
```

`/quit` di console melepas lease; instance produksi mempromosikan dirinya lagi. WhatsApp menyambung
ulang dengan DB sesi yang sama setelah promosi.

### Mesin memori (Mem0)

Mem0 OSS melakukan ekstraksi, dedupe, embedding, dan retrieval. Sanka hanya menyimpan identitas,
`ActionLedger` autografis, dan data affect/style/skill.

| Scope | Identitas Mem0 | Metadata | Visibilitas |
|---|---|---|---|
| `person` | `user_id=person:<id>` | `audience=private` | ikut orang itu ke chat lain |
| `chat` | `run_id=chat:<chat_id>` | `audience=group` | tetap di chat itu |
| `global` | `agent_id=sanka` | `audience=shared` | fakta tim bersama |

Setiap turn hanya me-query `global` + `person:<pengirim>` + `chat:<chat ini>`. Memori privat orang
lain tidak pernah di-query. Prompt memerintahkan model tidak pernah membocorkan memori privat satu
chat ke orang ketiga. `memory.backend: null` mematikan Mem0; kegagalan fail-open dengan alasan di
`/status`.

`memory_legacy` menampung memori era SQLite. `sanka memory migrate` memetakan DM ke `person`, grup
ke `chat`, dan global ke `global`; idempoten, tidak pernah memigrasikan ulang satu baris, dan menulis
level 2.

### Affect, style, skills, recipes

- `AffectStore` menyimpan emosi PAD, trust, relasi, topik, dan drives di Postgres; baris state
  dibaca/ditulis dalam satu transaksi dengan `FOR UPDATE` di Postgres.
- `StyleStore` mengagregasi statistik ritme/diksi dari arsip; isi pesan mentah tidak disimpan di
  sana.
- `SkillStore` menyimpan prosedur (`skills`, `skill_files`, `skill_usage`). Model memuat indeks dan
  body lengkap lewat tool `skill`.
- `RecipeStore` menyimpan command user (`/nama`) sebagai body YAML; langkah `recipe` menjalankan tool
  dengan `{{args}}`, langkah `prompt` satu turn LLM.
- `VibeRunner` menyimpan mood ruangan per chat di tabel `state`.

### Approval dan safety

Default `approval.mode: yolo` membiarkan tool biasa berjalan. Yang butuh `/approve`:

- setiap tool di `approval.sensitive_tools` (default `shell`, `sandbox`, glob diizinkan),
- setiap perubahan internal lewat tool `settings` saat `approval.settings: true`,
- konfirmasi privasi/pengungkapan (`kind=privacy|disclosure`),
- di mode `strict`, setiap tool kecuali observasi read-only (`read`, `memory`, `skill`,
  `chat_history`, `web_search`).

Alur: model menerima `pending approval #ID`, memberi tahu pengirim, lalu berhenti. `/pending`
mendaftar, `/approve ID` mengeksekusi dengan role asli dan mengecek ulang izin, `/deny ID`
membatalkan. Item pending terikat `chat_id` + pengirim, kedaluwarsa setelah `ttl_seconds` (default
900), dan `/approve` tidak pernah memanggil LLM atau mengubah emosi. `shell` yang disetujui berarti
akses host secara desain; `settings` adalah jalur tervalidasi untuk perubahan config.

### Status, settings, audit

`/status` melaporkan mode, pemegang lease, uptime, surface, model, database, chat/role, ukuran
arsip, status memori, legacy pending, approval, skill, orang/aksi, mirror soul/persona, bahasa, flag
privasi, mirror engine, dan perubahan config terakhir. `/whoami` melaporkan role, chat, surface, dan
handle bot.

Tool `settings` (sesuai izin role) membaca dan mengubah Sanka sendiri: `engine`, `host`, `gateway`,
`soul`, `persona`, `role`. `engine`/`soul` memetakan ke bagian `config.yaml` dan dokumen DB;
`gateway` hanya mengedit mapping `gateways`; `role` meregenerasi `roles.yaml`; `host` menolak path di
dalam `engine`/`gateways`/`soul`. `set`/`unset`/`write` memvalidasi kandidat lewat loader asli,
menulis dokumen DB, mengekspor mirror secara atomik, membackup berkas, mencatat `config_changes`,
dan hot-reload. Kunci khusus restart mengembalikan `ok: saved; restart required ...` alih-alih
reload. Kunci mirip rahasia (`api_key`, `password`, `secret`, `token`) ditolak kecuali
`api_key_env`; path tetap di dalam berkas yang dikenal.

Contoh audit:

```sql
select count(*) from sanka_memories;                    -- koleksi pgvector Mem0
select scope, kind, count(*) from memory_legacy group by 1,2;
select count(*) from messages;
select * from engine_lease;
select id, kind, status, summary from approvals order by id desc limit 5;
select * from config_changes order by id desc limit 5;
```

### Migrasi dari SQLite atau config terpisah

```bash
# 1. Hentikan bot.
# 2. Lipat layout lama ke config.yaml + roles.yaml (tambah --dry-run untuk melihat).
#    Backup masuk ke config.bak-<timestamp>/; --force menghapus berkas lama setelahnya.
python -m sanka config migrate --dir .
# 3. Arahkan SANKA_DATABASE_URL ke Postgres, lalu
python -m sanka db upgrade
# 4. Impor data .sanka lama (history, affect, style, skills, recipes, memori legacy)
python -m sanka db import --sqlite .sanka
# 5. Pindahkan memori bertahap ke Mem0
python -m sanka memory migrate
# 6. Salin berkas host-local: whatsapp.db, lampiran workspace, .env
# 7. Verifikasi dan jalankan
python -m sanka db check
python -m sanka run
```

`db import` menolak selama lease baru masih ada (`--force` menimpa). `db backup --out file.dump`
memakai `pg_dump --format=custom`; `db restore file.dump --yes` memakai
`pg_restore --clean --if-exists`. Backup DB sesi host-local dan workspace secara terpisah.

### Pengembangan dan test

```bash
pytest -q
python -m sanka selftest          # DB SQLite sementara untuk store; LLM nyata bila kunci ada
python -m sanka console --takeover
```

Test memakai URL file SQLite dan gateway/LLM palsu; runtime memakai Postgres. `sanka selftest` tidak
pernah menyentuh database produksi. Bila Mem0/pgvector tidak tersedia, cek memorinya `SKIP`, bukan
`FAIL`, dan matriks kebijakan privasi tetap PASS.

### Keamanan

- Rahasia hanya di `.env` (nama env dirujuk dari YAML). `config.yaml`, `roles.yaml`, `.env`, dan
  `.sanka/` diabaikan git.
- `roles.yaml`, DB sesi, dan `.env` bermode `0600`. `sanka db backup` menulis `0600`.
- Password database diredaksi dari log, `/status`, dan baris audit.
- `settings` tidak pernah menyentuh `.env` dan menolak path kunci sensitif.
- Entri yang diveto dan L3/L4 disembunyikan dari output tool grup; non-owner tidak bisa
  mengonfirmasi pengungkapan.

### Prinsip desain

1. Identitas bertahan melampaui sesi: window per chat, memori per person/agent/chat.
2. Gateway adalah appendage, bukan otak; sensor dan aktuator bisa ditambah dengan cara yang sama.
3. Memori berlapis: Mem0 untuk fakta semantik, `ActionLedger` untuk autobiografi, affect/style untuk
   cara Sanka merasa dan bicara, skills/recipes untuk prosedur.
4. Safety dan privasi deterministik serta auditabel (`SafetyPolicy` + `approvals` +
   `PrivacyPolicy`), bukan saran prompt.
5. Kontrol motorik real-time di luar cakupan iterasi ini; seam-nya (`Gateway`, `WorkingMemory`,
   `SafetyPolicy`, metadata `where`/`sensor_context` nullable) sudah tersedia.
