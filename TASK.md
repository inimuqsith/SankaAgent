# TASK

Tujuan: bot WhatsApp kecil yang nyala dalam satu proses Python.

- [x] Enam keluarga tool: `shell`, `read`, `write`, `web_search`, `sandbox`, `mcp`.
- [x] `web_search` memakai SearXNG bila `web_search.base_url` diisi, fallback ke backend `ddgs`.
- [x] Permission ala Discord lewat YAML: nomor -> role -> pola tool/command.
- [x] `python -m sanka selftest` lulus: write/read, shell, sandbox, web_search, LLM.
- [x] Routing DM/grup + format balasan diuji lewat event protobuf sintetis dan client palsu.
- [x] Kontrak balasan Neonize dijaga test regresi: wrapper event, target chat, sender LID.
- [x] `python -m sanka`: scan QR sekali, session tersimpan, lalu balas DM nyata (`halo`) dan `@sanka ping` di grup.
- [x] Konteks 20 pesan per chat: nama/nomor/waktu, kutipan, lampiran diunduh, gambar inline lewat MIME,
  teks panjang dipotong dengan penanda `read path=...`, biner diarahkan ke tool `sandbox`.

Di luar cakupan: transkripsi audio/video dan parsing PDF di dalam Sanka (lewat `sandbox`), Google
Docs/Sheets, browser, gateway HTTP, bridge Node.
