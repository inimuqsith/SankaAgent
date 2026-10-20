# Plan 10 — Wrapper operasional dan SOUL.md di `config/`

- Status: Done
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Dua hal yang diminta owner setelah plan-9:

1. Perintah singkat untuk start/stop/restart/status/log gateway, supaya tidak perlu mengingat PID dan flag `python -m gateway` manual.
2. Persona tidak lagi disuntikkan lewat blok bertanda di `docs/PERSONA.md`. Teks L0 dipindah ke `config/SOUL.md` sebagai aset runtime yang bisa diedit tanpa menyentuh kode atau dokumen arsitektur.

Hasil akhir: `scripts/sanka restart` menyalakan gateway dengan aman, dan `config/SOUL.md` adalah satu-satunya sumber persona runtime.

## Di luar cakupan

- systemd unit, container, atau supervisor produksi. Wrapper menjalankan kedua proses dengan `nohup`, bukan supervisor penuh.
- Perubahan router, command, formatter, policy, atau tool.
- Enkripsi atau versioning khusus `SOUL.md`.
- Rotasi sesi WhatsApp atau penanganan QR otomatis; pairing tetap manual.

## Rancangan

### `config/SOUL.md`

- Berisi seluruh teks L0 (tanpa penanda HTML, tanpa frontmatter). Isi dipindahkan dari blok `SANKA-L0` di `docs/PERSONA.md` yang dibuat plan-9.
- File di-commit sebagai persona default; bukan secret. Owner boleh mengeditnya langsung.
- `sanka/agent/prompt.py`: `load_soul(path=None)` membaca `config/SOUL.md`, men-trim, dan melempar `PersonaError` bila file hilang/kosong. Tidak ada fallback kedua.
- `AgentRuntime.__init__` tetap memanggil loader saat startup agar kesalahan aset gagal cepat.
- Aturan teknis tool/data dan tanggal tetap ditambahkan `prompt.py`.
- `docs/PERSONA.md` tetap dokumen manusia; blok runtime dihapus dan diganti rujukan ke `config/SOUL.md`.

### `scripts/sanka`

Skrip Bash executable, dijalankan dari mana saja (path dihitung dari lokasi skrip).

| Subcommand | Perilaku |
|---|---|
| `start [all\|gateway\|bridge]` | Muat `.env` + `config/secrets.env`; jalankan proses dengan `nohup`; tulis PID; tunggu `/healthz`. Default `all`. |
| `stop [all\|gateway\|bridge]` | Gateway dulu, lalu bridge; SIGTERM, tunggu 10 detik, baru SIGKILL; hapus PID file. |
| `restart [all\|gateway\|bridge]` | `stop` lalu `start` untuk target. |
| `status` | PID dan `/healthz` gateway + bridge. |
| `logs [all\|gateway\|bridge]` | `tail -f` log yang dipilih; default keduanya. |
| `pair` / `bridge` | Jalankan bridge di foreground untuk scan QR pertama. |

- PID: `logs/gateway.pid` dan `logs/bridge.pid`; log: `logs/gateway.log` dan `logs/bridge.log` (gitignored).
- Wrapper mengadopsi proses yang dijalankan manual dengan mencocokkan PID dari `ss` pada port yang sesuai.
- Bridge butuh `SANKA_GATEWAY_TOKEN`; wrapper memuat `config/secrets.env` tanpa mencetak nilainya.
- Start bridge menunggu `"connected":true` hingga 30 detik; bila belum, beri petunjuk pairing.
- Tidak mencetak isi `.env`, token, atau JID.
- Gagal start: tampilkan 20 baris log terakhir dan exit non-zero.

### Dokumentasi

- `README.md`: bagian "Menjalankan" dengan contoh wrapper dan catatan `config/SOUL.md`.
- `docs/ARCHITECTURE.md`: persona dari `config/SOUL.md`.
- `docs/PERSONA.md`: rujukan sumber runtime.
- `docs/ADR.md`: **ADR-0023** (`Accepted`) — SOUL.md di `config/`, mencabut bagian lokasi/penanda ADR-0022.
- `docs/LOG.md`: entri plan-10.

## Langkah

1. Pindahkan teks L0 ke `config/SOUL.md`, hapus blok bertanda dari `docs/PERSONA.md`.
2. Ganti `load_persona` menjadi `load_soul` (cutover, tanpa alias) dan sesuaikan `runtime.py`.
3. Perbarui `tests/test_prompt.py`: path default `config/SOUL.md`, file kosong/hilang ditolak, prompt memuat persona.
4. Buat `scripts/sanka` dan `chmod +x`.
5. Tes wrapper: `status`, `restart`, lalu `status` lagi; pastikan `/healthz` ok dan PID berubah.
6. Perbarui README, ARCHITECTURE, PERSONA, ADR, LOG, plan-10.

## Tes

```bash
.venv/bin/python -m pytest -o addopts="" -q
.venv/bin/python -m compileall -q sanka core gateway
scripts/sanka status
scripts/sanka restart
scripts/sanka status
```

Kriteria tambahan: `curl -s http://127.0.0.1:8099/healthz` mengembalikan `status: ok`; `logs/gateway.pid` menunjuk proses yang hidup; `config/SOUL.md` termuat di prompt.

## Kriteria selesai

- `config/SOUL.md` ada, ter-commit, dan menjadi satu-satunya sumber L0.
- `docs/PERSONA.md` tidak lagi memuat teks L0.
- `scripts/sanka start|stop|restart|status|logs` bekerja.
- `pytest` lulus dan gateway hidup setelah `scripts/sanka restart`.

## Yang perlu dipahami owner

- **Kenapa `config/`:** persona adalah aset operasional, bukan kode. Mengedit `config/SOUL.md` tidak mengubah Python dan tidak memerlukan commit kode.
- **Kenapa fail-fast:** kalau `SOUL.md` hilang, Sanka lebih baik gagal start daripada diam-diam memakai persona lama yang ada di kode.
- **Kenapa wrapper:** gateway dan bridge adalah dua proses. Wrapper hanya mengurus gateway Python; bridge tetap dijalankan terpisah karena butuh QR/sesi WhatsApp.

## Catatan perubahan

- 2026-10-21: Plan dibuat atas permintaan owner setelah plan-9. Status langsung `In Progress` karena permintaan eksplisit.
- 2026-10-21: Owner menolak wrapper yang hanya mengurus gateway. Lingkup diperluas ke lifecycle bridge (start/stop/restart/status/logs) dan `pair`; ADR-0024 ditambahkan. Rancangan dan kriteria diperbarui.

## Hasil

- Status: **Done** (2026-10-21).
- `config/SOUL.md` dibuat dari teks L0 plan-9, tanpa penanda. `docs/PERSONA.md` hanya merujuk ke file itu.
- `sanka/agent/prompt.py`: `load_soul()` membaca `config/SOUL.md`, men-trim, `PersonaError` untuk file hilang/kosong. `AgentRuntime.__init__` memanggilnya saat startup.
- `scripts/sanka` executable dengan `start|stop|restart|status|logs` untuk target `all|gateway|bridge`, plus `pair`.
- Tes yang dijalankan:
  - `pytest -o addopts="" -q` → **486 passed**.
  - `compileall` → exit `0`.
  - `scripts/sanka start bridge` → PID 168377, `connected:true` dalam ~3 detik.
  - `scripts/sanka restart bridge` → PID 168513, `connected:true`.
  - `scripts/sanka restart` (all) → gateway PID 168619, bridge PID 168647, health `ok`, bridge `connected:true`.
  - PID file, listener `ss`, dan health cocok untuk kedua proses.
  - Verifikasi final setelah perbaikan resolusi PID: `start` saat hidup tidak spawn duplikat; `restart` semua → gateway PID 169419 dan bridge PID 169445, keduanya health `ok`, bridge `connected:true`.
- Temuan: versi pertama wrapper hanya mengurus gateway dan sempat membuat proses kedua saat PID file tidak ada; diperbaiki dengan resolusi PID listener. Versi kedua diperluas ke bridge setelah owner menolak wrapper setengah.
- Belum dijalankan: verifikasi tampilan di WhatsApp oleh owner.
