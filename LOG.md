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

## 2026-10-21 (balasan & pairing)

- Bug live: `client.reply_message(chunk, ev.Message)` melempar `AttributeError: Info` karena Neonize
  butuh wrapper event (`Info` + `Message`) untuk menentukan chat tujuan dan quote. Fix: oper `ev`
  utuh di `sanka/wa.py`.
- Test regresi `tests/test_reply.py` (3): DM memakai wrapper + quote ID/participant/teks, grup
  membalas ke JID grup bukan sender, pengirim LID memakai `SenderAlt`. Test memakai
  `NewAClient.build_reply_message` asli; hanya `send_message` diganti perekam — revert fix =
  3 test gagal dengan traceback yang sama.
- Aturan test di `AGENTS.md` dilonggarkan: suite tetap kecil, fokus kontrak repo, bukan menguji
  ulang perilaku library.
- Verifikasi: `pytest -q` 6 lulus; `python -m sanka selftest` PASS; owner mengonfirmasi balasan
  berhasil di DM maupun grup setelah pairing nyata.

## 2026-10-21 (tool auto-discovery)

- `sanka/tools.py` jadi package `sanka/tools/`: satu modul per tool (`read`, `write`, `shell`,
  `web_search`, `sandbox`, `mcp`) dengan `TOOL = Handler(...)` plus `family`; `pkgutil` meng-import
  otomatis seperti command. `ToolContext`, `execute`, dan `FAMILIES` tinggal di `__init__.py`.
- `sanka/registry.py`: `validate()` menegakkan kontrak handler (nama = nama file, pola nama/alias,
  deskripsi, `fn`/`pattern`, `family`, skema `parameters`, `defaults`). `discover()` mengumpulkan
  semua error plugin lalu raise `RegistryError` sekali; duplikat nama/alias juga diagregasi.
  `Registry.patterns` menerima `None` (default handler) beda dari `()` (tolak semua).
- `python -m sanka` membungkus import first-party: plugin invalid keluar dengan satu baris
  `sanka: handler tidak valid: ...` dan exit 1, bukan traceback. `run_bot` mencatat daftar
  command/tool hasil discovery dan role yang memakai wildcard.
- `RoleConfig.tools`/`.commands` menjadi `tuple | None`: field tidak ditulis = default handler,
  `[]` = tolak semua. `sanka/mcp.py` memberi `family="mcp"` pada handler dinamis.
- Test baru `tests/test_registry.py` (4): handler bawaan, tool drop-in, agregasi error, izin `[]`.
- Verifikasi: `pytest -q` 10 lulus; `python -c` menampilkan daftar command/tool hasil discovery;
  tool drop-in `echo_tmp` terdeteksi tanpa edit core; plugin invalid (`COMMAND = 1`, `TOOL = 1`)
  exit 1 dengan satu pesan; `python -m sanka selftest` PASS write/read, shell, sandbox,
  web_search, LLM, `SELFTEST OK`.

## 2026-10-21 (status mengetik)

- `sanka/wa.py`: pesan yang lolos `should_process` langsung mengirim presence `COMPOSING`
  (media `TEXT`) ke JID chat asal, disegarkan tiap `TYPING_REFRESH` (8 detik) lewat task kecil
  selama LLM/tool berjalan, lalu `PAUSED` di blok `finally` — termasuk saat handler gagal.
- Presence bersifat kosmetik: exception-nya ditelan dan dicatat `log.debug`, jadi balasan tetap
  terkirim walau server menolak update presence.
- Enum dari `neonize.utils.enum` (`CHAT_PRESENCE_COMPOSING`/`_PAUSED`, `..._MEDIA_TEXT`), bukan
  enum proto: kode Go neonize memakai `state.value` sebagai indeks slice, dan enum proto (1/2)
  akan out-of-range.
- Test `tests/test_reply.py` (2 baru): urutan `COMPOSING` -> balasan -> `PAUSED` ke JID grup, dan
  grup tanpa mention tidak menghasilkan presence sama sekali.
- Verifikasi: `pytest -q` 12 lulus; smoke refresh (LLM 90 ms, interval 20 ms) menghasilkan 5
  `COMPOSING` tanpa task tertinggal; presence yang selalu raise tidak menggagalkan balasan;
  `python -m sanka selftest` PASS.
