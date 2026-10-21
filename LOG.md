# LOG

## 2026-10-21

- Rewrite sederhana: satu proses Python (Neonize 0.5.2), tanpa gateway, tanpa bridge Node.
- Enam keluarga tool: `shell`, `read`, `write`, `web_search`, `sandbox` (Podman), `mcp`.
- Permission ala Discord: `roles` di YAML, pola glob, cek di satu choke point `tools.execute`.
- Riwayat percakapan in-memory (30 pesan/chat), `/reset` membersihkan.
- Tiga test smoke: role/glob, routing DM/grup, format balasan WhatsApp.
- Docs dipangkas jadi empat file: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`.
- Verifikasi: `pytest -q` 3 lulus; `python -m sanka selftest` PASS write/read, shell, sandbox
  (Podman 6.1.4, image python:3.12-slim), web_search, LLM (butuh header `x-opencode-session`
  dan User-Agent sendiri — tanpa itu OpenCode Go membalas 400 MissingSessionID).
- Tool calling nyata diuji ke model: prompt "baca selftest.txt" memicu tool `read` lalu jawaban
  berisi isi file. MCP diuji dengan server stdio lokal (`mcp__demo__tambah` -> `42`).
- Belum: pairing WhatsApp nyata (butuh scan QR dari ponsel owner).

## 2026-10-21 (registry handler)

- `sanka/registry.py`: satu bentuk `Handler` (name, aliases, description, usage, parameters,
  defaults per role, pattern) + `Registry` (nama/alias, deteksi duplikat, pola izin).
- Command pindah ke `sanka/commands/` dan di-import otomatis (`pkgutil`): `ping`, `help`, `reset`
  dengan alias `/p`, `/h` `/bantuan`, `/r` `/clear`. `/help` membaca metadata registry.
- Tool memakai `Handler` yang sama; `execute` tetap satu choke point (izin, alias, error, potong).
- Role tanpa `tools:`/`commands:` di YAML memakai default handler; daftar eksplisit tetap menang.
- Verifikasi: `pytest -q` 3 lulus, `selftest` PASS semua, `/p`, `/h`, `/r`, command tak dikenal,
  role tanpa daftar (default), dan modul command drop-in terdeteksi tanpa mengubah dispatcher.
