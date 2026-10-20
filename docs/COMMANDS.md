# COMMANDS

Command diawali `/`, diproses deterministik tanpa LLM. Daftar di bawah adalah usulan awal; perubahan dicatat di LOG/ADR.

| Command | Fungsi | Izin |
|---|---|---|
| `/help` | Daftar command yang boleh dipakai pengirim | semua |
| `/ping` | Cek Sanka hidup | semua |
| `/status` | Status ringkas (model aktif, mode) | panitia, owner |
| `/whoami` | Peran pengirim | semua |
| `/new` | Mulai percakapan baru (reset konteks) | semua |
| `/stop` | Hentikan pekerjaan yang sedang berjalan | pemilik giliran, owner |
| `/rekap <nama>` | Jalankan rekap bernama (sheet yang terdaftar) | panitia, owner |
| `/proposal list` | Lihat proposal menunggu | owner |
| `/approve <id>` / `/reject <id>` | Setujui atau tolak proposal | owner |
| `/skill list` | Daftar skill beserta asal-usul | owner |
| `/skill rm <nama>` | Hapus skill | owner |
| `/verbose on\|off` | Tampilkan jejak tool (debug) | owner |

## Status implementasi

Plan-1 mengimplementasikan `/ping`, `/help`, `/whoami`, dan `/status`. Plan-2 menambahkan `/new` dan `/stop`. Semua command tersebut ada di `sanka/commands/` dan dites tanpa LLM/WhatsApp. Command lain di tabel masih rencana.

## Aturan
- Command tidak dikenal: balas singkat dengan petunjuk `/help`.
- Setiap handler dites tanpa LLM dan tanpa WhatsApp.
- Toggle shell berada di konfigurasi, bukan command.
- Di grup, command tetap diperiksa perannya dari nomor pengirim.
