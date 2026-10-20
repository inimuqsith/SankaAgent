# Plan 1 — Fondasi inti: config, router, command, policy, formatter

- Status: Draft
- Tanggal: 2026-10-20
- Pemilik: Abdul Muqsith

## Tujuan

Membangun inti deterministik Sanka yang bisa dites tanpa LLM dan tanpa WhatsApp: memuat config, membaca pesan masuk, memilih jalur command vs agent, menegakkan peran dan rate limit, menjalankan command dasar, lalu menghasilkan teks final lewat formatter.

Plan ini belum membuat bot yang bisa dijalankan. Tujuannya adalah lapisan yang nanti dipakai oleh agent (plan-2) dan gateway (plan-3).

## Di luar cakupan

- LLM, OpenCode Go, dan integrasi `llmcore.py`.
- Harness GenericAgent dan memori/skill.
- Gateway WhatsApp dan normalizer payload WhatsApp.
- Google Workspace, PDF/Excel, web search, recap, proposal.
- Command yang butuh layanan atau sesi: `/new`, `/stop`, `/rekap`, `/proposal`, `/approve`, `/reject`, `/skill`, `/verbose`.
- Database atau persistensi. Rate limit masih in-memory.

## Rancangan

### Struktur file

```text
pyproject.toml
sanka/
  __init__.py
  config.py
  models.py
  router.py
  policy.py
  formatter.py
  commands/
    __init__.py
    registry.py
    basic.py
tests/
  test_config.py
  test_router.py
  test_policy.py
  test_commands.py
  test_formatter.py
```

### Alur

```text
InboundMessage
   -> router.route()        # COMMAND | AGENT | IGNORE
   -> policy.resolve_role() # owner | panitia | peserta
   -> policy.rate_limit()   # per pengirim
   -> commands.registry     # hanya untuk COMMAND
   -> formatter.format()    # teks final, dipotong/dipecah
```

`route()` tidak mengeksekusi apa pun. `AGENT` hanya mengembalikan hasil routing; penyambungan ke agent terjadi di plan-2.

### Keputusan teknis

Runtime: inti memakai Python 3.12 (rentang 3.11–3.13); bridge WhatsApp memakai Node.js LTS + Baileys dan hanya menangani I/O. Bahasa pengguna: Indonesia. Lihat ADR-0015 (Proposed).

1. **Router**
   - Pesan diawali `/` selalu menjadi command, termasuk di grup, tanpa perlu mention.
   - Di grup, pesan non-command hanya diproses jika `mentions` memuat bot, teks memuat `@sanka` (case-insensitive), atau `reply_to_bot=True`.
   - Di chat pribadi, semua pesan non-command menjadi `AGENT`.
   - Teks kosong atau hanya spasi menjadi `IGNORE`.
   - Command tidak dikenal tetap menjadi `COMMAND`; registry yang membalas petunjuk `/help`.

2. **Peran dan izin**
   - Peran ditentukan dari nomor pengirim di config, bukan dari nama tampilan.
   - Matriks tool disimpan sebagai data di `policy.py`; plan ini belum mengeksekusi tool.
   - Command punya daftar peran yang boleh memakainya.
   - Rate limit dicek sebelum command dijalankan; batas per menit dan per hari. Jam disuntikkan agar bisa dites.

3. **Command dasar**
   - `/ping`, `/help`, `/whoami`, `/status`.
   - `/help` hanya menampilkan command yang boleh dipakai peran pengirim.
   - `/status` menampilkan mode `deterministic` dan nama model dari config, tanpa memanggil LLM.

4. **Formatter**
   - Satu-satunya jalan keluar teks.
   - Membuang blok `<reasoning>`, `<thought>`, dan `<tool_call>`.
   - Respons biasa dipotong di `max_reply_chars`; respons rekap dipecah per paragraf menjadi beberapa pesan.
   - Tidak ada logika LLM di sini.

5. **Config**
   - `load_config(path)` membaca `config/sanka.yaml` dengan `yaml.safe_load`.
   - Config non-secret saja. Secrets tetap dari env dan baru dipakai di plan-2.
   - Validasi gagal cepat dengan pesan yang menyebut key bermasalah.

### Dependency baru

- Runtime: `PyYAML`.
- Dev: `pytest`.

Keduanya dicatat di plan ini sesuai aturan repo.

## Langkah

1. Buat `pyproject.toml`, package `sanka/`, dan konfigurasi pytest.
2. Implementasikan `models.py` dan `config.py` beserta validasinya.
3. Implementasikan `router.py` dan tes tabel routing.
4. Implementasikan `policy.py`: resolusi peran, matriks tool, dan rate limiter dengan fake clock.
5. Implementasikan `commands/registry.py` dan `commands/basic.py`.
6. Implementasikan `formatter.py`.
7. Tulis tes untuk seluruh modul; tidak ada network, LLM, atau WhatsApp.
8. Jalankan `pytest -q` dan `python -m compileall sanka`.
9. Perbarui `docs/COMMANDS.md` bila status command berubah, lalu isi bagian Hasil dan ubah status plan.

## Tes

| Tes | Skenario |
|---|---|
| `test_router.py` | `/ping` di grup tanpa mention tetap command |
| `test_router.py` | pesan grup tanpa mention menjadi `IGNORE` |
| `test_router.py` | `@sanka halo`, mention ID, dan reply ke bot menjadi `AGENT` |
| `test_router.py` | chat pribadi non-command menjadi `AGENT` |
| `test_policy.py` | owner/panitia/peserta dikenali dari config |
| `test_policy.py` | command ditolak untuk peran yang tidak berhak |
| `test_policy.py` | rate limit per menit dan per hari, lalu reset |
| `test_commands.py` | `/help` menyaring command sesuai peran |
| `test_commands.py` | command tidak dikenal membalas petunjuk `/help` |
| `test_formatter.py` | tag reasoning/tool dibuang |
| `test_formatter.py` | respons biasa dipotong; rekap dipecah per paragraf |
| `test_config.py` | config valid dimuat; key/rate tidak valid gagal cepat |

## Kriteria selesai

- `pytest -q` lulus semua.
- `python -m compileall sanka` tidak menghasilkan error.
- Tidak ada import ke LLM, WhatsApp, Google, atau GenericAgent.
- Semua fungsi publik punya type hint.
- `docs/LOG.md` diperbarui dan status plan menjadi `Done`.

## Yang perlu dipahami owner

- **Router hanya memilih jalur.** Ia tidak menjalankan command dan tidak memanggil LLM; ini yang membuat routing bisa dites tanpa model.
- **Policy adalah satu-satunya sumber izin.** Jangan menaruh cek peran di handler command, supaya tidak ada dua kebenaran.
- **Formatter adalah satu-satunya pintu keluar.** Kalau nanti ada jalur balasan baru, wajib lewat formatter agar reasoning tidak bocor.
- **Rate limiter in-memory** hilang saat proses restart. Itu cukup untuk plan ini; persistensi diputuskan nanti.
- **Session belum ada.** Karena itu `/new` dan `/stop` sengaja ditunda ke plan-2.

## Rencana lanjutan (indikatif, belum disetujui)

| Plan | Fokus |
|---|---|
| plan-2 | ADR session + antrean, adapter OpenCode Go, integrasi harness GenericAgent |
| plan-3 | Gateway WhatsApp, normalizer, bridge, aturan mention di level transport |
| plan-4 | Tool Workspace, dokumen, web search, rekap |
| plan-5 | Memori, skill, persona L0 |
| plan-6 | Proposal + `/approve`, hardening, deployment |

## Catatan perubahan

Belum ada.

## Hasil

Belum dikerjakan.
