# AGENTS

Aturan keras untuk perubahan di repo ini:

- Python saja. Tanpa gateway HTTP, tanpa Node/Baileys, tanpa bridge, tanpa server tambahan.
- Empat file docs itu saja: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`. Jangan bikin file `.md` baru.
- Tool tetap enam keluarga: `shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`. Jangan tambah keluarga baru.
- Permission hanya lewat `roles` di `config.yaml`; cek izin tool hanya di `sanka/tools.py:execute`.
- Command baru = modul di `sanka/commands/` dengan `COMMAND = Handler(...)` (name, aliases, description, defaults); jangan edit dispatcher.
- Code wajib seminimal mungkin tapi tetap solid.
- Jangan menumbuhkan suite test; tiga test smoke cukup.
- Rahasia hanya di `.env` (nama env dirujuk dari YAML). `config.yaml` dan `.env` tidak di-commit.
- Verifikasi sebelum selesai: `pytest -q` dan `python -m sanka selftest`.
