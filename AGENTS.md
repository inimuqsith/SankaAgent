# AGENTS

Aturan keras untuk perubahan di repo ini:

- Python saja. Tanpa gateway HTTP, tanpa Node/Baileys, tanpa bridge, tanpa server tambahan.
- Lima file docs itu saja: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`, `SOUL.md`. Jangan bikin file `.md` baru.
- Core ada di `sanka/engine/` dan tidak boleh mengimpor `sanka.gateways` atau `neonize`. Root `sanka/` hanya berisi `__main__.py`, `admin/`, `gateways/`, `vendor/`.
- Gateway adalah appendage: transport hanya lewat kontrak di `sanka/engine/inbound.py` (`Inbound`, `Gateway`, `GatewayError`). Tool/command milik gateway tinggal di `sanka/gateways/<name>/tools|commands`, bukan di core.
- Tool tetap enam keluarga (`shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`). Tool baru = modul di `sanka/engine/tools/` dengan `TOOL = Handler(...)` dan keluarga dari `FAMILIES`; nama file harus sama dengan `name`; jangan edit registry.
- Command baru = modul di `sanka/engine/commands/` dengan `COMMAND = Handler(...)`; jangan edit dispatcher.
- Permission hanya dari `roles` (dokumen `role:<name>` / mirror `roles/<name>.yaml`). Tidak ada default izin di handler. `tools`/`commands` yang tidak ditulis = tolak semua. Cek izin tool hanya di `sanka/engine/tools/__init__.py:execute`.
- Dokumen `engine`, `soul`, `persona`, `role:<name>` adalah sumber kebenaran di database; file `engine.yaml`/`soul.yaml`/`SOUL.md`/`roles/` hanya seed + mirror. Edit manual → `sanka config import`. Loader tidak boleh membuat direktori.
- `config.yaml` hanya host-local (database/workspace/sandbox/web_search/mcp/gateway_file/engine_file/roles_dir). `gateway.yaml` host-local. Rahasia hanya di `.env` (nama env dirujuk dari YAML); `config.yaml`, `gateway.yaml`, `engine.yaml`, `soul.yaml`, `roles/`, `.env`, `.sanka/` tidak di-commit.
- Storage: SQLAlchemy Core + `psycopg` ke Postgres; satu `Database` (`sanka/engine/db.py`) dan satu `MetaData` (`sanka/engine/schema.py`). Jangan pakai `sqlite3`, `PRAGMA`, FTS5, atau file state di store. SQLite hanya URL test.
- Memori semantik hanya lewat Mem0 (`sanka/engine/memory/mem0.py`, `mem0ai`). Jangan hidupkan kembali `MemoryStore`/refleksi custom. Ledger aksi tetap `ActionLedger`; transcript tetap per chat.
- Identitas lintas chat: query hanya `global` + `person:<pengirim>` + `chat:<chat ini>`; memori privat orang lain tidak pernah di-query dan tidak boleh bocor ke chat lain.
- Aksi sensitif (`shell`, `sandbox`) dan perubahan internal (`settings`) wajib lewat `SafetyPolicy`/`approvals` dan `/approve`. Mode default `yolo`; `off` mematikan approval (dokumentasikan risikonya). `/approve` tidak memanggil LLM dan mengecek ulang izin role.
- Satu primary: `engine_lease`. Instance tanpa lease standby dan beku; `/freeze` dan takeover hanya lewat lease/DB, bukan dua jantung.
- Prompt scaffolding internal berbahasa Inggris; persona `SOUL.md` tidak diterjemahkan. Bahasa balasan mengikuti `soul.language`.
- Code wajib seminimal mungkin tapi tetap solid. Jangan tambah abstraksi tanpa kebutuhan.
- Suite test kecil dan fokus pada kontrak repo: routing/format/permission, gateway contract, lease, approval, memori lintas chat, dan jalur balasan Neonize. Jangan uji ulang perilaku library.
- Verifikasi sebelum selesai: `pytest -q` dan `python -m sanka selftest`. Untuk perubahan gateway/lease, jalankan juga smoke `sanka console --takeover`.
