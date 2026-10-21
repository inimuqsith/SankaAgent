# TASK

Tujuan: bot WhatsApp kecil yang nyala dalam satu proses Python.

- [x] Enam keluarga tool: `shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`.
- [x] Permission ala Discord lewat YAML: nomor -> role -> pola tool/command.
- [x] `python -m sanka selftest` lulus: write/read, shell, sandbox, web_search, LLM.
- [x] Routing DM/grup + format balasan diuji lewat event protobuf sintetis dan client palsu.
- [x] Kontrak balasan Neonize dijaga test regresi: wrapper event, target chat, sender LID.
- [x] `python -m sanka`: scan QR sekali, session tersimpan, lalu balas DM nyata (`halo`) dan `@sanka ping` di grup.

Di luar cakupan: lampiran/media, Google Docs/Sheets, browser, gateway HTTP, bridge Node.
