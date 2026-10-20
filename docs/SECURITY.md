# SECURITY

## Model ancaman (ringkas)
- Anggota grup dan pengguna non-owner tidak tepercaya. Siapa pun bisa menulis pesan yang berusaha memanipulasi Sanka (prompt injection).
- Isi spreadsheet, PDF, dan halaman web juga bisa berisi instruksi tersembunyi. Semuanya dianggap data, bukan perintah.
- Nomor WhatsApp yang dipakai dapat diblokir bila perilakunya dianggap spam (gateway tidak resmi). Batasi laju kirim.
- Kebocoran secrets lewat log atau balasan.

## Peran
| Peran | Siapa | Penentuan |
|---|---|---|
| owner | Abdul Muqsith | Nomor di config, diverifikasi di kode |
| panitia | Tim atau panitia terverifikasi | Daftar nomor di config |
| peserta | Selain itu | Default |

## Matriks izin tool (usulan awal, BELUM final, lihat ADR-0011)
Baris `code_run`, `file_write`, dan shell sudah dikonfirmasi owner-only oleh owner pada 2026-10-20. Baris lain masih usulan.

| Tool | owner | panitia | peserta |
|---|---|---|---|
| web_search | ya | ya | ya |
| doc_read / sheet_read | ya | ya | tidak |
| recap | ya | ya | tidak |
| sheet_write (allowlist) | ya | tidak | tidak |
| proposal_send (butuh /approve) | ya | tidak | tidak |
| browser | ya | tidak | tidak |
| code_run, file_write, shell | ya (shell butuh toggle) | tidak | tidak |

Aturan penegakan: tool yang tidak diizinkan tidak dikirim ke LLM sebagai pilihan pada giliran itu, dan eksekusinya ditolak lagi di registry sebagai lapisan kedua. `code_run`, `file_write`, dan shell tidak punya pengecualian: hanya owner. Shell tetap butuh toggle `tools.shell_enabled` di config.

## WhatsApp gateway
- Gateway terbagi dua proses: server HTTP Python (`gateway/`) dan bridge Node/Baileys (plan-4). Keduanya hanya loopback.
- Server menolak start bila `gateway.listen_host` bukan `127.0.0.1`, `::1`, atau `localhost`. Jangan pernah mengekspos port ini, termasuk lewat reverse proxy.
- Setiap request `POST /v1/inbound` wajib membawa `X-Sanka-Token`; nilainya berasal dari environment variable yang ditunjuk `gateway.auth_token_env` dan dibandingkan dengan `hmac.compare_digest`. Token tidak pernah masuk log, respons, atau pesan. `/healthz` sengaja tanpa auth dan hanya berisi metadata (uptime, kedalaman antrean keluar).
- Batas input: body 64 KiB, teks 32.768 karakter, `mentions` ≤ 32, `message_id`/JID ≤ 200 karakter. Pelanggaran dijawab `400`/`413` tanpa memproses. `Content-Type` wajib `application/json`; tanpa `Content-Length` dijawab `411`; `Transfer-Encoding: chunked` ditolak.
- Dedup `message_id` berumur `limits.inbound_dedup_ttl_minutes` (in-memory). Retry bridge untuk id yang sudah diputuskan tidak diproses ulang.
- Log gateway hanya memuat method, path, status, durasi, digest 8 karakter dari `message_id`/`chat_id`, peran, route, dan hasil. Teks pesan, JID utuh, dan token tidak pernah dilog. Traceback hanya ditulis untuk exception tak terduga, tanpa isi pesan.
- Batas laju keluar global (`limits.outbound_per_minute`) serta FIFO per chat melindungi nomor dari pola spam.
- Gateway adalah bagian Sanka. Kode atau pola Hermes boleh diambil bila berguna, lalu diadaptasi; notice MIT dipertahankan dan perubahan ditandai `SANKA-MOD:` (ADR-0013).
- Jalur Baileys adalah jalur tidak resmi. Risiko nomor diblokir dimitigasi dengan rate limit, tanpa broadcast, dan perilaku non-spam.
- Bila risiko itu tidak dapat diterima, pindah ke WhatsApp Cloud API resmi dicatat sebagai ADR baru.
- Sesi WhatsApp disimpan di luar repo, permission 0600, dan tidak pernah dilog.

## Google Workspace
- Gunakan service account yang hanya di-share ke file/folder tertentu.
- Read default. Write hanya ke allowlist di config.
- Kredensial di luar git. API key dan token hanya dari environment variable, `config/secrets.env`, atau `.env` di root repo (0600, di-ignore). `config/sanka.yaml` tidak boleh memuat secrets.

## Browser
- Gunakan profil Chrome khusus Sanka, hanya terlogin ke akun yang memang dibutuhkan. Jangan memakai profil pribadi owner.
- Bila memungkinkan jalankan di mesin/VPS terpisah.

## Self-evolution (belajar)
- Sanka boleh belajar dari semua pengguna (keputusan owner, ADR-0008). Manajemen dan pembersihan skill dilakukan owner bila ada masalah.
- Usulan pendukung (belum disetujui, ADR-0011): catat asal-usul setiap skill (waktu, chat, pemicu) dan sediakan `/skill list` dan `/skill rm <nama>` untuk owner.
- Skill yang dihasilkan adalah prosedur, tidak boleh berisi secrets atau memberi izin baru. Izin tool tetap ditentukan registry, bukan skill.

## Output
- Hanya teks final ke WhatsApp. Reasoning dan trace tool tidak dikirim (kecuali `/verbose` untuk owner).
- Jangan pernah menggemakan secrets atau isi konfigurasi.

## Rate limit
- Batas pesan per pengguna per menit dan per hari (nilai di config). Lebih dari batas: abaikan atau jawab singkat satu kali.
- Batas kirim keluar untuk melindungi nomor WhatsApp.

## Logging
- Log metadata (waktu, peran, nama tool, durasi, kode galat). Jangan log isi pesan pribadi atau secrets.
