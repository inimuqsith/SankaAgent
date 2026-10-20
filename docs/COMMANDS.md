# COMMANDS

Command diawali `/`, diproses deterministik tanpa LLM. Daftar di bawah adalah usulan awal; perubahan dicatat di LOG/ADR.

| Command | Fungsi | Izin |
|---|---|---|
| `/help` | Daftar command yang boleh dipakai pengirim | semua |
| `/ping` | Cek Sanka hidup | semua |
| `/status` | Status ringkas (model aktif, mode) | panitia, owner |
| `/whoami` | JID pengirim, peran, dan jenis/JID chat | semua |
| `/new` | Mulai percakapan baru (reset konteks) | semua |
| `/stop` | Hentikan pekerjaan yang sedang berjalan | pemilik giliran, owner |
| `/rekap <nama>` | Jalankan rekap bernama (sheet yang terdaftar) | panitia, owner |
| `/proposal list` | Lihat proposal menunggu | owner |
| `/approve <id>` / `/reject <id>` | Setujui atau tolak proposal | owner |
| `/skill list` | Daftar skill beserta asal-usul | owner |
| `/skill rm <nama>` | Hapus skill | owner |
| `/verbose on\|off` | Tampilkan jejak tool (debug) | owner |

## Status implementasi

Plan-1 mengimplementasikan `/ping`, `/help`, `/whoami`, dan `/status`. Plan-2 menambahkan `/new` dan `/stop`. Plan-5 menambahkan `/rekap <nama>` (deterministik, tanpa LLM) dengan `Command.kind = RECAP`; command lain di tabel masih rencana.

Sejak ADR-0020 tidak ada peran peserta: owner tetap pemilik nomor di config, dan semua pengguna lain (termasuk yang tidak tercantum) adalah panitia. Karena itu command berizin "panitia, owner" tersedia untuk semua pengirim yang lolos gate grup/DM.

`/rekap` terdaftar hanya jika `RecapService` tersedia. Tanpa definisi `recaps` di config, ia membalas daftar kosong. Angka berasal dari `compute_recap` (pandas), bukan dari model; role gate tetap ada untuk command yang memang owner-only.

Gateway plan-3 sekarang memakai `CommandRegistry.dispatch_result()` sehingga balasan `/rekap` memakai jalur `ResponseKind.RECAP` (dipecah per paragraf, bukan dipotong sebagai `TEXT`). Pemanggil lama `dispatch()` tetap mengembalikan `str`.

## Contoh keluaran

`/whoami` di DM:

```text
👤 Kamu: 628123456789@s.whatsapp.net
🎭 Peran: Panitia
💬 Chat: pribadi
```

`/whoami` di grup menampilkan `💬 Chat: grup (<jid>@g.us)`. Bila `chat_id` tidak tersedia, baris chat dihilangkan.

Semua balasan command memakai sintaks WhatsApp (`*tebal*`, `•`), bukan markdown. Formatter menormalkan keluaran handler yang masih memakai markdown.

## Aturan
- Command tidak dikenal: balas singkat dengan petunjuk `/help`.
- Setiap handler dites tanpa LLM dan tanpa WhatsApp.
- Toggle shell berada di konfigurasi, bukan command.
- Di grup, command tetap diperiksa perannya dari nomor pengirim.
