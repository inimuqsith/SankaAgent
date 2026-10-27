# AGENTS

Aturan keras untuk perubahan di repo ini:

- Python saja. Tanpa gateway HTTP, tanpa Node/Baileys, tanpa bridge, tanpa server tambahan.
- Lima file docs itu saja: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`, `SOUL.md`. Jangan bikin file `.md` baru.
- Tool tetap enam keluarga: `shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`. Jangan tambah keluarga baru.
- Permission hanya lewat `roles` di `config.yaml`; cek izin tool hanya di `sanka/tools/__init__.py:execute`.
- Command baru = modul di `sanka/commands/` dengan `COMMAND = Handler(...)` (name, aliases, description, defaults); jangan edit dispatcher.
- Tool baru = modul di `sanka/tools/` dengan `TOOL = Handler(...)` dan `family` dari `FAMILIES`; nama file harus sama dengan `name`; jangan edit registry.
- Code wajib seminimal mungkin tapi tetap solid.
- Suite test kecil dan fokus pada kontrak repo: smoke routing/format/permission dan jalur balasan Neonize. Jangan uji ulang perilaku library.
- Rahasia hanya di `.env` (nama env dirujuk dari YAML). `config.yaml` dan `.env` tidak di-commit.
- Verifikasi sebelum selesai: `pytest -q` dan `python -m sanka selftest`.
