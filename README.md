# SankaAgent

Sanka is a persistent soul engine: one identity, one memory, many chat surfaces. The engine
(`sanka/engine/`) owns the turn loop, config, memory, emotion, and tools. Gateways only carry
messages in and out; the core never imports `neonize` or `sanka.gateways`.

- **One database = one soul.** Transcripts, Mem0 memory, affect, style, skills, recipes, the
  `engine`/`soul`/`persona`/`role:<name>` documents, approvals, and `engine_lease` live in Postgres.
- **One primary at a time.** A follower stays frozen; `/freeze` or `sanka console --takeover` moves
  the heartbeat through the lease, never through two hearts.
- **Identity is cross-chat.** Semantic memory is queried for `global` + `person:<sender>` +
  `chat:<this chat>` only. Raw transcripts stay per chat.
- **Privacy is a disclosure layer.** Entries carry a level 0-4; ordinary chat stays fluid, and only
  sensitive or grave entries are guarded.
- **Config is data.** `config.yaml` + `roles.yaml` + `SOUL.md` are seeds and mirrors of DB documents.
- **Character is a pattern catalogue.** `sanka/data/patterns.json` holds the runtime moves; `SOUL.md`
  holds the voice. No raw transcripts are shipped.

## Architecture

```mermaid
flowchart LR
  WA[WhatsApp gateway] -->|Inbound| AG[Agent turn]
  CLI[CLI gateway] -->|Inbound| AG
  AG --> LLM[LLM + tool families]
  AG --> PAT[patterns.json + MiniLM]
  AG --> MEM[Mem0 semantic memory]
  AG --> PRIV[Privacy policy + auditor]
  AG --> AFF[Affect + style + vibe]
  AG --> DB[(Postgres + pgvector)]
  AG -->|deliver_*| WA
  AG -->|deliver_*| CLI
  SUP[Supervisor + lease] --> AG
  DB --- SUP
```

The five repo docs are `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`, and `SOUL.md`.

## Quickstart (Postgres + pgvector)

```bash
# 1. Database: Postgres 14+ with pgvector
cp compose.example.yaml compose.yaml          # set a real password
podman compose -f compose.yaml up -d postgres

# 2. Scaffold config.yaml, roles.yaml, SOUL.md, and .env
python -m sanka init --dir .

# 3. Fill .env
#    OPENCODE_GO_API_KEY=...
#    SANKA_DATABASE_URL=postgresql+psycopg://sanka:CHANGE_ME@127.0.0.1:5432/sanka

# 4. Schema + documents + check
python -m sanka db upgrade
python -m sanka db check

# 5. Enable WhatsApp in `gateways`, then run the primary
python -m sanka run

# Local console against the same DB (takes the lease)
python -m sanka console --takeover
```

Requirements: Python 3.11+, Postgres 14+ with `vector`, and the WhatsApp extra for that gateway.
`sentence-transformers` downloads the embedder once (`paraphrase-multilingual-MiniLM-L12-v2`,
384 dims); it powers both memory embeddings and pattern retrieval.

Dependency note: Mem0 pins `protobuf<7` while Neonize needs `protobuf>=7`. Install Neonize first,
then Mem0 without its resolver:

```bash
pip install -e ".[whatsapp,vibe]"
pip install "sentence-transformers>=5.2,<6"
pip install --no-deps "mem0ai>=2.2,<3"
pip install httpx openai posthog pydantic pytz qdrant-client protobuf
```

Without Mem0 the bot still replies and `/status` reports `Memory: unavailable: <reason>`. Without
`sentence-transformers` the pattern selector falls back to keyword scoring.

## Config surfaces

| File / document | Where | Contents |
|---|---|---|
| `config.yaml` | host only | host keys (`database`, `workspace`, `roles_file`, `sandbox`, `web_search`, `mcp`, `sync_documents`) plus `engine`, `gateways`, `soul` |
| `roles.yaml` | host only, `0600` | every role under one `roles:` mapping |
| `SOUL.md` / `persona` | DB + mirror | persona voice, Indonesian, never translated |
| `engine` / `soul` sections | DB + `config.yaml` | LLM, reply, memory + privacy, approval, affect, ambient, language |
| `role:<name>` | DB + `roles.yaml` | `tools`, `commands`, `users`, `context`, `priority`, `memory_admin` |

After editing `engine`/`soul`, `SOUL.md`, or `roles.yaml` by hand: `python -m sanka config import`.
To write files from the DB: `python -m sanka config export`. `sync_documents: false` disables
seeding for tests. The loader never creates directories.

## Character: SOUL.md and the pattern catalogue

`SOUL.md` is the voice: identity, personality, register, behavioural rules, backstory, relationships,
and four dialogue examples. It stays Indonesian and short, and it must not duplicate the moves.

`sanka/data/patterns.json` is the runtime character sheet: conditional moves ("if this, then
that") tagged with a register, a gender target, and toxicity/adult gates. Each turn the engine reads
the last messages, computes the room register, and picks `engine.character.patterns` lines (0-6,
default 0; the production host uses 4). Selection is deterministic: keyword hits first, then cosine
similarity against precomputed MiniLM vectors. The `always` invariants (language lock, length,
question limit, grounding, floor, curse trigger) need no keyword hit.

The move reaches the model inside `## Room patterns (apply the move, never copy the wording)`. `say`
lines are owner-approved exemplars and must come from an owner correction, never from raw private
chat.

Pattern lifecycle:

```bash
python scripts/patterns.py --check                      # schema, gates, duplicates, reachability
python scripts/patterns.py --embed                      # rebuild pattern_vectors.json (384 dims)
python scripts/patterns.py --stats                      # counts per register and gate
python scripts/patterns.py --build-xlsx workspace/review-patterns.xlsx
python scripts/patterns.py --apply-xlsx workspace/review-patterns.xlsx   # 1:1 by id
```

`--apply-xlsx` writes back `konten`, `contoh`, `hindari`, `keys`, and `komentar`, and reports unknown
ids. After editing content, rerun `--check` then `--embed` so the vectors match.

Scenario replay against the live prompt:

```bash
python scripts/replay.py --scenario roast_group --limit 4
python scripts/replay.py --no-regulate --no-judge --limit 6
```

It reads `tests/data/scenarios.json` (turns, per-speaker targets, rubric, bad examples), builds the
turn with `character_block(...)`, applies `regulate`, and writes `workspace/replay-<id>.json` with a
transcript and a `hard_fail` count. It never writes back to the scenarios or the catalogue.

## Roles and permissions

No handler has default permissions; a role that omits `tools`/`commands` denies everything. Tools
come from six families (`shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`); permission is
checked only in `sanka/engine/tools/__init__.py:execute`. Addresses in several roles are unioned, and
contexts are ordered by `priority` then name. `memory_admin: true` is required for global memory
writes. `/approve` never calls the LLM and re-checks role permissions.

### Workspace roots

A role without `*` in `tools` sees only `public/` (shared, read-only) and `scratch/<chat>/`
(writable); every other workspace path stays private. A bare member path resolves under
`scratch/<chat>/`, explicit `public/...` is shared, and `private/...` is denied. Trusted roles (`*`
in `tools`) keep the whole workspace and may write `public/`. The sandbox follows the same rule:
members get `/work/public` read-only and `/work/scratch` writable, trusted roles get `/work`.
Cross-room reads stay allowed, but a reply carrying an eight-word verbatim span from another room is
replaced before sending.

### Google Workspace

`google` is an in-process tool (family `mcp`, no Node server or `mcp__*` grant). One service account
sees every file shared with it; each sender links a Google identity once in a DM through the device
flow (`openid email profile`). `list/read/write` check the file's explicit user permission for that
verified email and reject `anyone`/`domain` link-sharing. Set `GOOGLE_SERVICE_ACCOUNT_PATH`,
`GOOGLE_OAUTH_CLIENT_ID`, and `GOOGLE_OAUTH_CLIENT_SECRET`. The OAuth client is External, type
"TVs and Limited Input devices", scopes `openid email profile` (no Drive/Docs/Sheets user scopes,
so no sensitive-scope review).


## Safety and approvals

| Level | Meaning | Group ceiling | DM ceiling |
|---|---|---|---|
| 0 | public | fluid | fluid |
| 1 | team | fluid | fluid |
| 2 | personal | fluid | fluid |
| 3 | sensitive | confirmation | fluid |
| 4 | secret | confirmation | confirmation |
| 5 | credentials | never stored | never stored |

Levels 0-2 stay fluid everywhere; level 3 in a group and level 4 ask through `approvals`; vetoed
entries are never shown or disclosed, and the subject of a memory (not the speaker) may veto it. The
auditor pass runs only when a risky entry exists.

`shell` and `sandbox` calls and internal `settings` changes pass `SafetyPolicy`/`approvals`; the
default mode is `yolo` (auto-approve), and `off` disables approval checks entirely - document the risk
if you switch. Levels for tone come from `engine.character`: `toxicity` 0-4 and `adult_memes` 0-3,
with a permanent floor (no sexual content with minors, no doxxing, no credible threats, no hate).

The sandbox fails closed unless `sandbox.network` is a dedicated internal network (not `none`,
`bridge`, `host`, or `default`) and `sandbox.proxy` points at the egress proxy. `compose.example.yaml`
runs `sandbox-proxy` (Squid) on the internal `sanka-sandbox` network; `deploy/sandbox-proxy/squid.conf`
blocks private destinations. `sandbox.host_workspace` must be the host path that mirrors
`config.workspace`, with `public/` and `scratch/` present.

## Database, lease, and console

```bash
python -m sanka db check                       # schema and row summary
python -m sanka db backup                      # pg_dump
python -m sanka console --takeover             # local REPL takes the lease
```

`engine_lease` holds exactly one heartbeat. Other instances stay standby and frozen; `sanka console`
without `--takeover` joins as a follower, and the production instance refreezes within about five
seconds of losing the lease.

## Migration from SQLite or the split layout

```bash
python -m sanka config migrate --dir .        # fold the old layout into config.yaml + roles.yaml
python -m sanka db import                     # old .sanka data
python -m sanka memory migrate                # memory_legacy -> Mem0
python -m sanka db upgrade && python -m sanka db check
```

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m sanka selftest
python scripts/patterns.py --check
ruff check . && ruff format --check .
pytest -q --cov=sanka --cov-report=xml
```

The suite is small and covers repo contracts only: routing/format/permission, gateway contract,
lease, approval, cross-chat memory, privacy/disclosure, language selection, pattern selection, the
pattern CLI, and the Neonize reply path. `sonar-project.properties` is ready for SonarCloud; code
must stay at complexity 12 or lower.

## Removed corpus pipeline

The old collection and training toolchain is gone: no fetch/ingest scripts, no Excel review, no
training loop, no downloaded dataset inbox, no `conversation_datasheet.json`. The character data is
the hand-authored catalogue plus `SOUL.md`, and the replay harness is the only evaluation entry
point. A future corpus refresh would be a new tool, not part of this repository.

---

# Bahasa Indonesia

Sanka adalah mesin jiwa yang menetap: satu identitas, satu memori, banyak permukaan chat. Engine
(`sanka/engine/`) mengurus giliran, config, memori, emosi, dan tool. Gateway cuma membawa pesan
masuk dan keluar; core tidak pernah mengimpor `neonize` atau `sanka.gateways`.

- **Satu database = satu jiwa.** Transkrip, memori Mem0, affect, gaya, skill, resep, dokumen
  `engine`/`soul`/`persona`/`role:<name>`, approval, dan `engine_lease` ada di Postgres.
- **Satu primary.** Instance lain standby dan beku; `/freeze` atau `sanka console --takeover`
  memindahkan heartbeat lewat lease, bukan dua jantung.
- **Identitas lintas chat.** Memori semantik hanya ditanya untuk `global` + `person:<pengirim>` +
  `chat:<chat ini>`. Transkrip mentah tetap per chat.
- **Privasi itu lapisan pengungkapan.** Entri membawa level 0-4; obrolan biasa tetap cair.
- **Config adalah data.** `config.yaml` + `roles.yaml` + `SOUL.md` adalah seed dan mirror dokumen DB.
- **Karakter adalah katalog pola.** `sanka/data/patterns.json` menyimpan gerakan runtime; `SOUL.md`
  menyimpan suara. Tidak ada transkrip mentah di repo.

Lima dokumen repo: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`, `SOUL.md`.

## Arsitektur

```mermaid
flowchart LR
  WA[Gateway WhatsApp] -->|Inbound| AG[Giliran agent]
  CLI[Gateway CLI] -->|Inbound| AG
  AG --> LLM[LLM + keluarga tool]
  AG --> PAT[patterns.json + MiniLM]
  AG --> MEM[Memori Mem0]
  AG --> PRIV[Kebijakan privasi + auditor]
  AG --> AFF[Affect + gaya + vibe]
  AG --> DB[(Postgres + pgvector)]
  AG -->|deliver_*| WA
  AG -->|deliver_*| CLI
  SUP[Supervisor + lease] --> AG
  DB --- SUP
```

## Quickstart (Postgres + pgvector)

```bash
# 1. Database: Postgres 14+ dengan pgvector
cp compose.example.yaml compose.yaml          # ganti password
podman compose -f compose.yaml up -d postgres

# 2. Scaffold config.yaml, roles.yaml, SOUL.md, dan .env
python -m sanka init --dir .

# 3. Isi .env
#    OPENCODE_GO_API_KEY=...
#    SANKA_DATABASE_URL=postgresql+psycopg://sanka:CHANGE_ME@127.0.0.1:5432/sanka

# 4. Schema + dokumen + verifikasi
python -m sanka db upgrade
python -m sanka db check

# 5. Aktifkan WhatsApp di `gateways`, lalu jalankan primary
python -m sanka run

# Console lokal ke DB yang sama (mengambil lease)
python -m sanka console --takeover
```

Butuh Python 3.11+, Postgres 14+ dengan `vector`, dan extra WhatsApp untuk gateway itu.
`sentence-transformers` mengunduh embedder sekali (`paraphrase-multilingual-MiniLM-L12-v2`, 384
dimensi); dipakai untuk embedding memori dan pengambilan pola.

Catatan dependensi: Mem0 mem-pin `protobuf<7` sementara Neonize butuh `protobuf>=7`. Pasang Neonize
dulu, lalu Mem0 tanpa resolver-nya:

```bash
pip install -e ".[whatsapp,vibe]"
pip install "sentence-transformers>=5.2,<6"
pip install --no-deps "mem0ai>=2.2,<3"
pip install httpx openai posthog pydantic pytz qdrant-client protobuf
```

Tanpa Mem0 bot tetap menjawab dan `/status` melaporkan `Memory: unavailable: <alasan>`. Tanpa
`sentence-transformers` pemilih pola jatuh ke pencocokan kata kunci.

## Permukaan config

| Berkas / dokumen | Lokasi | Isi |
|---|---|---|
| `config.yaml` | host saja | kunci host (`database`, `workspace`, `roles_file`, `sandbox`, `web_search`, `mcp`, `sync_documents`) plus `engine`, `gateways`, `soul` |
| `roles.yaml` | host saja, `0600` | semua role dalam satu mapping `roles:` |
| `SOUL.md` / `persona` | DB + mirror | suara persona, Indonesia, tidak diterjemahkan |
| bagian `engine` / `soul` | DB + `config.yaml` | LLM, reply, memori + privasi, approval, affect, ambient, bahasa |
| `role:<name>` | DB + `roles.yaml` | `tools`, `commands`, `users`, `context`, `priority`, `memory_admin` |

Setelah mengedit `engine`/`soul`, `SOUL.md`, atau `roles.yaml` manual: `python -m sanka config
import`. Untuk menulis berkas dari DB: `python -m sanka config export`. `sync_documents: false`
mematikan seeding untuk tes. Loader tidak pernah membuat direktori.

## Karakter: SOUL.md dan katalog pola

`SOUL.md` adalah suara: identitas, kepribadian, register, aturan perilaku, latar, hubungan, dan empat
contoh dialog. Isinya tetap Indonesia, pendek, dan tidak menduplikasi gerakan.

`sanka/data/patterns.json` adalah lembar karakter runtime: gerakan bersyarat ("kalau ini, maka
itu") dengan tag register, target gender, dan gate toxicity/adult. Tiap turn engine membaca pesan
terakhir, menghitung register ruangan, lalu memilih `engine.character.patterns` baris (0-6, default 0;
host produksi memakai 4). Pemilihan deterministik: kata kunci dulu, lalu cosine vektor MiniLM yang
dipra-hitung. Pola `always` (kunci bahasa, panjang, batas pertanyaan, grounding, floor, pemicu
umpatan) tidak butuh kata kunci.

Gerakan masuk ke prompt sebagai `## Room patterns (apply the move, never copy the wording)`. Baris
`say` adalah contoh yang disetujui pemilik dan harus berasal dari koreksi pemilik, bukan chat pribadi
mentah.

Siklus hidup pola:

```bash
python scripts/patterns.py --check                      # skema, gate, duplikat, keterjangkauan
python scripts/patterns.py --embed                      # bangun ulang pattern_vectors.json (384 dim)
python scripts/patterns.py --stats                      # hitungan per register dan gate
python scripts/patterns.py --build-xlsx workspace/review-patterns.xlsx
python scripts/patterns.py --apply-xlsx workspace/review-patterns.xlsx   # 1:1 per id
```

`--apply-xlsx` menulis balik `konten`, `contoh`, `hindari`, `keys`, dan `komentar`, serta melaporkan
id yang tidak dikenal. Setelah mengubah konten, jalankan `--check` lalu `--embed` supaya vektor
cocok.

Replay skenario terhadap prompt hidup:

```bash
python scripts/replay.py --scenario roast_group --limit 4
python scripts/replay.py --no-regulate --no-judge --limit 6
```

Skrip membaca `tests/data/scenarios.json` (turn, target per pembicara, rubrik, contoh buruk),
merakit turn dengan `character_block(...)`, menjalankan `regulate`, lalu menulis
`workspace/replay-<id>.json` berisi transkrip dan hitungan `hard_fail`. Skrip ini tidak pernah
menulis balik ke skenario atau katalog.

## Role dan izin

Tidak ada handler yang punya izin default; role yang tidak menyebut `tools`/`commands` menolak
semuanya. Tool berasal dari enam keluarga (`shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`)
dan izin diperiksa hanya di `sanka/engine/tools/__init__.py:execute`. Alamat di beberapa role
digabung, konteks diurutkan menurut `priority` lalu nama. `memory_admin: true` wajib untuk menulis
memori global. `/approve` tidak pernah memanggil LLM dan memeriksa ulang izin role.

### Root workspace

Role tanpa `*` di `tools` hanya melihat `public/` (bersama, read-only) dan `scratch/<chat>/` (bisa
ditulis); path workspace lain tetap privat. Path polos anggota dipetakan ke `scratch/<chat>/`, `public/`
eksplisit berarti bersama, dan `private/...` ditolak. Role tepercaya (`*` di `tools`) tetap memegang
seluruh workspace dan boleh menulis `public/`. Sandbox mengikuti aturan yang sama: anggota mendapat
`/work/public` read-only dan `/work/scratch` yang bisa ditulis, role tepercaya mendapat `/work`. Baca
lintas ruang tetap boleh, tetapi balasan yang memuat delapan kata verbatim dari room lain diganti
sebelum dikirim.

### Google Workspace

`google` adalah tool in-process (keluarga `mcp`, tanpa server Node atau izin `mcp__*`). Satu service
account melihat setiap file yang dibagikan kepadanya; tiap pengirim menautkan identitas Google sekali
lewat DM dengan device flow (`openid email profile`). `list/read/write` memeriksa izin user eksplisit
untuk email terverifikasi itu dan menolak link-sharing `anyone`/`domain`. Isi
`GOOGLE_SERVICE_ACCOUNT_PATH`, `GOOGLE_OAUTH_CLIENT_ID`, dan `GOOGLE_OAUTH_CLIENT_SECRET`. OAuth
client-nya External, tipe "TVs and Limited Input devices", scope `openid email profile` (tanpa scope
Drive/Docs/Sheets milik user, jadi tidak perlu review sensitive scope).

## Keamanan dan approval

| Level | Arti | Plafon grup | Plafon DM |
|---|---|---|---|
| 0 | publik | cair | cair |
| 1 | tim | cair | cair |
| 2 | pribadi | cair | cair |
| 3 | sensitif | konfirmasi | cair |
| 4 | rahasia | konfirmasi | konfirmasi |
| 5 | kredensial | tidak disimpan | tidak disimpan |

Level 0-2 cair di mana saja; level 3 di grup dan level 4 meminta konfirmasi lewat `approvals`; entri
yang diveto tidak pernah ditampilkan atau diungkap, dan yang boleh memveto adalah subjek memori, bukan
pembicaranya. Auditor hanya jalan kalau ada entri berisiko.

`shell`, `sandbox`, dan perubahan internal `settings` melewati `SafetyPolicy`/`approvals`; mode
default `yolo` (otomatis setuju), dan `off` mematikan pemeriksaan approval sepenuhnya - catat
risikonya kalau diubah. Level nada datang dari `engine.character`: `toxicity` 0-4 dan `adult_memes`
0-3, dengan floor permanen (tanpa konten seksual dengan anak, doxing, ancaman kredibel, kebencian).

Sandbox gagal tertutup kecuali `sandbox.network` adalah network internal khusus (bukan `none`,
`bridge`, `host`, atau `default`) dan `sandbox.proxy` menunjuk proxy egress. `compose.example.yaml`
menjalankan `sandbox-proxy` (Squid) di network internal `sanka-sandbox`;
`deploy/sandbox-proxy/squid.conf` memblokir tujuan privat. `sandbox.host_workspace` harus path host
yang mencerminkan `config.workspace`, dengan `public/` dan `scratch/` sudah ada.

## Database, lease, dan console

```bash
python -m sanka db check                       # ringkasan schema dan baris
python -m sanka db backup                      # pg_dump
python -m sanka console --takeover             # REPL lokal mengambil lease
```

`engine_lease` hanya punya satu heartbeat. Instance lain standby dan beku; `sanka console` tanpa
`--takeover` ikut sebagai follower, dan instance produksi membeku lagi sekitar lima detik setelah
kehilangan lease.

## Migrasi dari SQLite atau layout lama

```bash
python -m sanka config migrate --dir .        # lipat layout lama ke config.yaml + roles.yaml
python -m sanka db import                     # data .sanka lama
python -m sanka memory migrate                # memory_legacy -> Mem0
python -m sanka db upgrade && python -m sanka db check
```

## Pengembangan

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m sanka selftest
python scripts/patterns.py --check
ruff check . && ruff format --check .
pytest -q --cov=sanka --cov-report=xml
```

Suite-nya kecil dan hanya menguji kontrak repo: routing/format/izin, kontrak gateway, lease,
approval, memori lintas chat, privasi/pengungkapan, pemilihan bahasa, pemilihan pola, CLI pola, dan
jalur balasan Neonize. `sonar-project.properties` siap untuk SonarCloud; kompleksitas kode maksimal
12.

## Pipeline korpus yang dihapus

Toolchain pengumpulan dan pelatihan lama sudah hilang: tidak ada skrip fetch/ingest, tidak ada review
Excel, tidak ada loop pelatihan, tidak ada inbox dataset unduhan, tidak ada
`conversation_datasheet.json`. Data karakter adalah katalog yang ditulis tangan plus `SOUL.md`, dan
harness replay adalah satu-satunya pintu evaluasi. Kalau nanti perlu menyegarkan korpus, itu tool
baru, bukan bagian dari repo ini.
