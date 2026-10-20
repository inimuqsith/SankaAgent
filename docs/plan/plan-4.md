# Plan 4 — Bridge WhatsApp Web (Node.js + Baileys)

- Status: Draft
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Menghidupkan jalur WhatsApp nyata untuk Sanka lewat satu proses Node.js LTS yang memegang koneksi WhatsApp Web dengan Baileys.
Bridge hanya melakukan I/O: menerima event WhatsApp, menormalkannya menjadi fakta transport, mengirim HTTP POST ke Python (plan-3), lalu melayani endpoint kirim/typing/read dari Python.
Semua keputusan produk — peran, allowlist, mention gating, rate limit per pengirim, pemotongan teks, dan isi balasan — tetap di Python.
Akhir plan ini: satu DM dan satu grup nyata bisa memakai Sanka, dan restart bridge menyambung ulang tanpa pairing ulang.

## Di luar cakupan

- Logika bisnis apa pun di Node: peran, allowlist, keputusan mention, rate limit per pengirim, format/pemotongan teks, panggilan LLM.
- Media: unduh, unggah, transkripsi, OCR, thumbnail. Plan-4 hanya meneruskan caption (atau teks kosong) dan tidak menyentuh byte media.
- Endpoint media, edit pesan, poll, lokasi, reaction, sticker, grup metadata, dan pembuatan grup.
- WhatsApp Cloud API resmi (opsi cadangan ADR-0013, butuh ADR baru).
- Antrean persisten untuk pesan masuk, database, dan replay setelah restart. Antrean bridge in-memory dan hilang saat proses mati.
- Supervisor, systemd unit, container, health-check produksi, backup, dan redaksi log menyeluruh (plan-8). Plan-4 hanya menyiapkan `GET /healthz` minimal untuk smoke manual.
- Perubahan pada normalizer, policy, dan outbound queue Python (milik plan-3).

## Prasyarat

- Plan-2 `Done` dan plan-3 `Done`; kontrak HTTP `/v1` di plan-3 sudah beku.
- Node.js 22 LTS (minimum 22.11) tersedia bersama `npm`. Versi Node yang benar-benar dipakai dicatat di bagian Hasil.
- Nomor WhatsApp khusus Sanka (bukan nomor pribadi owner) dan satu ponsel yang bisa memindai QR.
- `SANKA_GATEWAY_TOKEN` tersedia di environment atau `config/secrets.env` (0600). Nilai yang sama dipakai kedua arah.
- Direktori sesi di luar repo sudah ditentukan, misalnya `~/.local/share/sanka/whatsapp-session`.
- Kontrak `/v1` sudah dicatat sebagai ADR oleh plan-3 (plan-3 mengusulkan nomor ADR-0018). Plan-4 **tidak** mengajukan ADR baru: pemilihan Node+Baileys ada di ADR-0015, sesi di luar repo dan secrets di ADR-0014, pola bridge di ADR-0013. Pin versi Baileys dan aturan satu-instance dicatat sebagai catatan di ADR-0013 saat implementasi, bukan ADR baru.

Kontrak yang dipegang plan ini, dari plan-3 (nama field verbatim):

| Arah | Endpoint | Isi |
|---|---|---|
| Bridge → Python | `POST http://{gateway.listen_host}:{gateway.listen_port}/v1/inbound` | `message_id`, `chat_id`, `sender_id`, `text`, `is_group`, `mentions`, `reply_to_bot`, `timestamp` (epoch detik) |
| Python → bridge | `POST /v1/send` | `{chat_id, text, idempotency_key}` → `200 {message_id}`; kunci selesai → `409` |
| Python → bridge | `POST /v1/typing` | `{chat_id, state: "composing"\|"paused"}` → `204` |
| Python → bridge | `POST /v1/read` | `{chat_id, message_id}` → `204` |
| Kedua arah | header `X-Sanka-Token` | token bersama dari env `SANKA_GATEWAY_TOKEN` |

Jawaban Python yang harus dihormati bridge: `202 accepted` dan `200 duplicate`/`ignored` = selesai, jangan kirim ulang; `400`/`401`/`413`/`415`/`404` = buang event dan catat kode; `429`/`5xx`/koneksi putus/timeout = ulangi maksimal 3 kali dengan backoff default 1s, 2s, 4s, atau memakai `retry_after_ms` di body/header `Retry-After` bila ada (dibatasi 30 detik).

Python juga tetap penegak utama batas laju keluar: `limits.outbound_per_minute` dicek sebelum `POST /v1/send`. Pacing di bridge adalah lapisan pertahanan kedua, bukan pengganti.

## Rancangan

### Posisi proses dan batas tanggung jawab

```text
WhatsApp Web  <--Baileys-->  [bridge Node 127.0.0.1:3000]
                                   |  POST /v1/inbound (X-Sanka-Token)
                                   v
                             [gateway Python 127.0.0.1:8099]
                                   |  router -> policy -> command/agent -> formatter
                                   |  POST /v1/send | /v1/typing | /v1/read
                                   v
                             [bridge Node] -> WhatsApp
```

Yang **tidak boleh** ada di Node (ditegakkan lewat review, bukan hanya niat):

- Menentukan siapa owner/panitia/peserta, atau membaca daftar nomor dari config.
- Memutuskan sebuah pesan grup layak diproses; bridge hanya melaporkan `mentions[]` dan `reply_to_bot` sebagai fakta.
- Menghitung rate limit per pengirim, menolak perintah, atau membalas penolakan.
- Memotong, menggabungkan, atau memformat teks balasan. Python mengirim chunk final; bridge mengirim apa adanya.
- Memanggil LLM, membaca `config/sanka.yaml`, atau menyimpan riwayat chat.

Bridge hanya memegang empat hal: koneksi WhatsApp, normalisasi fakta transport, batas aman transport (token, ukuran body, pacing, idempotensi), dan siklus hidup proses.

### Struktur file

```text
gateway/
  bridge/
    package.json           # private, type=module, pin Baileys, script start/test
    package-lock.json      # wajib di-commit
    src/
      main.js              # wiring: config -> logger -> session -> socket -> http
      config.js            # loadConfig(env) + validasi fail-fast
      logger.js            # pino + redaksi + maskJid() + hashId()
      session.js           # prepareSessionDir(), assertOutsideRepo(), enforcePerms()
      normalize.js         # normalizeMessage(msg, ctx) -> InboundEvent | null
      backoff.js           # nextDelay(attempt), classifyDisconnect(statusCode)
      ids.js               # createBoundedMap({ttlMs, max}) untuk dedupe & idempotensi
      deliver.js           # createInboundDeliverer({post, clock, logger})
      outbound.js          # createOutbound({transport, clock, logger, limits})
      http.js              # createServer({token, outbound, status}) dengan node:http
      socket.js            # createSocketManager({config, deliverer, logger})
    test/
      helpers.js           # fakes bersama: jam, logger, transport, HTTP test server
      config.test.js
      logger.test.js
      session.test.js
      normalize.test.js
      backoff.test.js
      ids.test.js
      deliver.test.js
      outbound.test.js
      http.test.js
```

Tidak ada `express`. Router HTTP-nya empat endpoint dengan `node:http`, sehingga dependency yang perlu diaudit tetap kecil dan body limit bisa ditegakkan sebelum parsing.

### Config bridge (env saja)

Node **tidak** membaca `config/sanka.yaml`; itu milik Python (ADR-0014). Bridge juga tidak mem-parse `config/secrets.env` (tanpa `dotenv`): file itu dimuat oleh perintah start atau supervisor (`set -a; . config/secrets.env; set +a`), lalu diwariskan sebagai environment. Tabel di bawah adalah satu-satunya permukaan konfigurasi bridge, dan nilai yang harus sama dengan YAML Python disebut eksplisit.

| Env | Default | Wajib | Catatan |
|---|---|---|---|
| `SANKA_GATEWAY_TOKEN` | — | ya | Harus sama dengan `gateway.auth_token_env` di Python. Bridge menolak start bila kosong. |
| `SANKA_INBOUND_URL` | `http://127.0.0.1:8099/v1/inbound` | tidak | Harus cocok dengan `gateway.listen_host/listen_port`. Hanya host loopback yang diterima. |
| `SANKA_BRIDGE_PORT` | `3000` | tidak | Harus cocok dengan `gateway.bridge_url`. |
| `SANKA_BRIDGE_SESSION_DIR` | `~/.local/share/sanka/whatsapp-session` | tidak | Wajib di luar repo. |
| `SANKA_BRIDGE_OUTBOUND_PER_MINUTE` | `20` | tidak | Cermin `limits.outbound_per_minute`. Python tetap penegak utama; ini batas pertahanan kedua. |
| `SANKA_BRIDGE_READ_RECEIPTS` | `0` | tidak | Cermin `gateway.send_read_receipts`. |
| `SANKA_BRIDGE_LOG_LEVEL` | `info` | tidak | `debug` hanya untuk diagnosis lokal. |
| `SANKA_BRIDGE_PAIR_ONLY` | `0` | tidak | `1` = tampilkan QR, simpan sesi, keluar setelah terhubung. |

`loadConfig(env)` mengembalikan objek beku dan gagal cepat dengan pesan yang menyebut nama env bermasalah:

```js
loadConfig(env) -> {
  port, host: '127.0.0.1', token, inboundUrl, sessionDir, logLevel,
  outboundPerMinute, readReceipts, pairOnly, repoRoot
}
```

Validasi: `token` minimal 16 karakter; `inboundUrl` harus `http://127.0.0.1` atau `http://localhost`; `port` 1–65535; `sessionDir` absolut setelah `realpath` dan tidak berada di dalam `repoRoot`; `outboundPerMinute` 1–60.

### Penyimpanan sesi

`useMultiFileAuthState(sessionDir)` dari Baileys menulis `creds.json` dan file kunci. File-file itu adalah kredensial penuh perangkat tertaut: siapa pun yang memegangnya bisa mengirim pesan atas nama nomor Sanka.

- `process.umask(0o077)` dipasang sebagai statement pertama sebelum modul Baileys menyentuh disk, agar setiap file baru otomatis `0600` dan direktori `0700`.
- `prepareSessionDir(dir)` membuat direktori dengan mode `0700` bila belum ada.
- Bila direktori sudah ada dengan bit `0o077` menyala, mode diperbaiki ke `0700` dan dicatat `session_perms_fixed` (tanpa path lengkap di log, cukup hash).
- Setiap `*.json` di dalamnya diperiksa; yang lebih longgar dari `0600` di-`chmod` ke `0600`.
- `assertOutsideRepo(dir, repoRoot)`: `dir === repoRoot` atau berawalan `repoRoot + path.sep` menggagalkan start dengan pesan yang menyuruh memindahkan direktori. Ini yang mencegah `./sessions` atau `gateway/bridge/session` masuk repo.
- Isi file sesi tidak pernah dibaca untuk log, tidak pernah dikirim ke `/healthz`, dan tidak pernah masuk pesan error.
- Satu sesi hanya boleh dipakai satu proses bridge. Instance kedua pada direktori yang sama akan membuat WhatsApp mencabut sesi; dicatat di runbook operasional plan-8.
- Default `~/.local/share/sanka/whatsapp-session` adalah lokasi pengembangan. Plan-8 memakai `/var/lib/sanka/whatsapp` lewat unit systemd dengan `ReadWritePaths` hanya ke direktori itu; nilai itu diberikan sebagai `SANKA_BRIDGE_SESSION_DIR`, bukan diubah di kode.

### Normalisasi pesan masuk (`normalize.js`)

`normalizeMessage(msg, ctx)` adalah fungsi murni (tanpa socket, tanpa jaringan) sehingga bisa dites dengan objek pesan Baileys sebagai fixture.

```js
ctx = {
  botJids: Set<string>,   // JID ternormalisasi milik bot (PN dan LID)
  resolvePn: (jid) => string | null,  // LID -> PN, diinjeksi; null di tes
  nowMs: () => number,
}
normalizeMessage(msg, ctx) -> {
  message_id, chat_id, sender_id, text, is_group, mentions, reply_to_bot, timestamp
} | null
```

Aturan, berurutan:

1. **Buang yang bukan pesan pengguna**: `msg.key.remoteJid` berakhiran `@broadcast` (status), `@newsletter`, atau `@g.us` dengan `key.participant` kosong; `msg.message` null; `fromMe === true` (pesan yang dikirim perangkat Sanka sendiri, termasuk echo kiriman bridge, agar tidak jadi loop); `reactionMessage`, `protocolMessage` (termasuk edit pesan, yang bukan giliran baru), `pollUpdateMessage`, `senderKeyDistributionMessage`, dan `messageStubType`.
2. **Buka pembungkus**: `ephemeralMessage.message`, `viewOnceMessage.message`, `viewOnceMessageV2.message`, `viewOnceMessageV2Extension.message`, `documentWithCaptionMessage.message`, `deviceSentMessage.message`. Kedalaman maksimal 4; selebihnya dianggap tidak dikenal.
3. **Teks**: `conversation`, lalu `extendedTextMessage.text`, lalu caption dari `imageMessage` / `videoMessage` / `documentMessage` / `audioMessage`. Tidak ada teks dan tidak ada caption → `text: ""` (media-only diteruskan, Python yang memutuskan `blank_text`). Bridge tidak mengunduh media, tidak menulis file, dan tidak menambahkan penanda buatan seperti `[media]`.
4. **chat_id**: `jidNormalizedUser(msg.key.remoteJid)` — buang suffix device (`:12`) dan `@s.whatsapp.net` ganda. `is_group = chat_id.endsWith('@g.us')`.
5. **sender_id**: grup memakai `msg.key.participant ?? msg.key.remoteJid`; DM memakai `remoteJid`. Bila hasilnya `@lid`, coba `ctx.resolvePn(jid)`; berhasil → pakai JID PN hasil resolusi, gagal → pakai JID LID apa adanya (lihat Risiko).
6. **mentions**: `contextInfo.mentionedJid` dari message dalam dan `messageContextInfo`, dinormalkan, dibuang yang kosong, didedupe, dipotong ke 32 entri sesuai batas plan-3. Mention pada teks (`@sanka`) tidak diubah dan tidak dihapus dari `text`; Python yang mencocokkannya.
7. **reply_to_bot**: `contextInfo.participant` (JID penulis pesan yang dikutip) termasuk `ctx.botJids`, atau `contextInfo.remoteJid` adalah JID bot (kasus chat dengan diri sendiri). Perbandingan memakai bagian `user` saja setelah normalisasi, karena WhatsApp bisa mengirim PN dan LID untuk akun yang sama.
8. **timestamp**: `Number(msg.messageTimestamp)` bila masuk akal (dalam ±24 jam dari `ctx.nowMs()`); selain itu field dihilangkan agar Python memakai waktu terima.
9. **message_id**: `msg.key.id`; kosong atau lebih dari 200 karakter → `null`.

`normalizeMessage` tidak pernah melempar error untuk pesan aneh: input tak dikenal menghasilkan `null`, dan pemanggil menaikkan penghitung `inbound_skipped`.

Pemetaan id: `msg.key.id` masuk sebagai `message_id`; id yang sama dipakai Python sebagai kunci dedupe dan sebagai bagian `idempotency_key` (`<message_id>:<chunk_index>`). Untuk read receipt, `POST /v1/read` memakai `message_id` itu langsung sebagai `key.id` dengan `remoteJid = chat_id` dan `fromMe: false`. Bridge tidak menyimpan tabel pemetaan id lintas pesan; satu-satunya peta persisten-in-memory adalah store idempotensi kirim.

### Deduplikasi dan pengiriman ke Python (`deliver.js`, `ids.js`)

- `createBoundedMap({ttlMs, max})` menyediakan `get/set/has/size` dengan kedaluwarsa dan batas entri (buang yang tertua). Dipakai untuk dedupe inbound (TTL 10 menit, 2.000 id), idempotensi outbound (TTL 15 menit, 2.000 kunci), dan cache LID→PN.
- `createInboundDeliverer({post, clock, logger})` memakai satu worker FIFO agar urutan pesan yang diterima tetap terjaga:
  - `enqueue(event)` memasukkan ke antrean berbatas 500. Penuh → buang yang tertua, naikkan `inbound_dropped_overflow`.
  - Worker mengambil satu event, `post(url, body, {timeoutMs: 10000, headers})`, lalu memetakan hasil:
    - `202`/`200` → selesai; `200 ignored` mencatat `reason` sebagai field log (kode saja seperti `not_mentioned` atau `blank_text`, tanpa isi pesan).
    - `400`/`401`/`404`/`413`/`415` → buang event, catat kode. Tiga `401` berurutan → jeda pengiriman 60 detik dan `delivery_paused=true` di `/healthz` (indikasi token salah), bukan hot loop.
    - `429`, `5xx`, koneksi putus, timeout → ulangi maksimal 3 kali: 1s, 2s, 4s, atau `retry_after_ms`/`Retry-After` dari Python bila ada (maksimal 30 detik). Setelah percobaan habis, buang event dan catat `inbound_dropped_delivery`.
  - `flush(timeoutMs)` menunggu antrean kosong saat shutdown.
  - Isi pesan tidak pernah masuk log; yang dicatat hanya `msg_id`, `chat_kind` (dm/grup), `text_len`, `attempt`, dan `status`.

### Endpoint HTTP (`http.js`)

Server `node:http` yang **hanya** bind `127.0.0.1`:

- `Host` header wajib salah satu dari `127.0.0.1`, `localhost`, `[::1]` (dengan atau tanpa port); selain itu `400`. Ini pertahanan DNS rebinding, pola yang diadaptasi dari bridge Hermes (ADR-0013).
- `X-Sanka-Token` wajib pada semua endpoint `/v1/*`, dibandingkan constant-time: `timingSafeEqual(sha256(kirim), sha256(diharapkan))`. Gagal → `401 {"error":"unauthorized"}`. `GET /healthz` adalah satu-satunya pengecualian dan hanya mengembalikan metadata.
- Body: hanya `application/json`, maksimal 64 KiB (sama dengan batas Python). Melebihi → `413 {"error":"payload_too_large"}`; tipe lain → `415`.
- `server.requestTimeout = 15000`, `headersTimeout = 10000`, `keepAliveTimeout = 5000`.
- Respons error hanya berisi kode, tidak pernah menggemakan `text` atau `chat_id`.
- Tidak ada CORS, tidak ada static file, tidak ada route tambahan.

| Endpoint | Sukses | Error yang mungkin |
|---|---|---|
| `POST /v1/send` | `200 {message_id}` | `400 invalid_request` (chat_id/teks/kunci tidak valid, teks > 4096 karakter), `409 {error:"duplicate"}` (kunci sudah selesai), `429 {error:"rate_limited", retry_after_ms}` (pacing > 30s), `503 {error:"not_connected", retry_after_ms}` (socket sedang menyambung), `504 {error:"send_timeout"}` |
| `POST /v1/typing` | `204` (selalu; kegagalan presence hanya dicatat) | `400` state tidak dikenal, `401` |
| `POST /v1/read` | `204` (no-op bila read receipt dimatikan) | `400`, `401` |
| `GET /healthz` | `200 {status, connected, queue_depth, delivery_paused, uptime_s, bridge_version, baileys}` | — (tanpa auth) |

`/healthz` sengaja tanpa auth dan hanya berisi metadata, mengikuti pola `GET /healthz` di plan-3, supaya supervisor (plan-8) bisa memeriksa tanpa membagikan token. Isinya tidak memuat JID, nomor, path sesi, atau isi pesan. Health-check produksi, alerting, dan supervisor tetap plan-8.

### Outbound: idempotensi, pacing, urutan (`outbound.js`)

`createOutbound({transport, clock, logger, limits})` menerima transport yang bisa dipalsukan di tes:

```js
transport = {
  sendText(chatId, text) -> Promise<{ id }>   // Baileys: sock.sendMessage
  presence(chatId, state) -> Promise<void>    // sock.sendPresenceUpdate
  readMessages(keys) -> Promise<void>         // sock.readMessages
  isConnected() -> boolean
}
```

Alur `/v1/send`:

1. Validasi: `chat_id` berakhir `@s.whatsapp.net`/`@g.us`/`@lid`; `text` string tidak kosong setelah trim dan ≤ 4096 karakter; `idempotency_key` 8–300 karakter. Tidak valid → `400`. Bridge **tidak** memotong teks; pemotongan adalah keputusan formatter Python.
2. Idempotensi: kunci disimpan di `createBoundedMap`.
   - Status `done` → `409 {"error":"duplicate"}` (Python memperlakukannya sukses).
   - Status `inflight` → tunggu promise yang sama, kembalikan hasil `200` yang sama. Jadi retry tidak pernah mengirim dua kali, dan `409` hanya berarti "sudah benar-benar terkirim".
3. Pacing: satu antrean per `chat_id`, jarak minimum 1.200 ms antar kiriman ke chat yang sama (indikator "mengetik" tidak terlihat berkedip, dan pola kirim tidak menyerupai burst). Batas global jendela bergulir 60 detik = `outboundPerMinute`, sebagai pertahanan kedua setelah plan-3 menegakkan `limits.outbound_per_minute`.
4. Bila total tunggu melebihi 30 detik, kembalikan `429` dengan `retry_after_ms` alih-alih menahan request Python tanpa batas.
5. Socket belum terhubung → `503` dengan `retry_after_ms` 1.000–5.000.
6. Kirim `transport.sendText`, simpan `{state:'done', messageId}`. Gagal karena socket putus → kembalikan `503`; gagal karena alasan lain → `502 {"error":"send_failed"}` dan kunci **dihapus** agar retry Python boleh mencoba lagi.
7. Timeout 20 detik per kiriman. Bila timeout terjadi tetapi promise akhirnya selesai, id tetap tersimpan pada kunci; entri kedaluwarsa setelah 15 menit.
Tidak ada broadcast, tidak ada pengiriman ke lebih dari satu chat dalam satu panggilan, dan tidak ada retry otomatis di dalam bridge untuk `/v1/send` (retry adalah hak Python dengan kunci yang sama).

Store idempotensi in-memory: restart bridge menghapus semua kunci, sehingga retry Python yang datang setelah restart bisa mengirim ulang chunk yang sebelumnya sudah terkirim. Jendela retry Python pendek (3 percobaan, backoff 0,5/1/2 detik), jadi risikonya kecil tetapi nyata; dicatat di Risiko dan tidak ditutup dengan persistensi di plan-4.

`/v1/typing`: `state === 'composing'` → `presence(chat_id, 'composing')`, `paused` → `presence(chat_id, 'paused')`. State lain → `400`. Python mengirim ulang `composing` sendiri bila giliran lebih lama dari jendela typing WhatsApp; bridge tidak punya timer typing. Indikator hanya terlihat bila penerima sedang online—itu batas WhatsApp, bukan kegagalan bridge, jadi kegagalan presence tidak boleh menggagalkan giliran.

`/v1/read`: hanya memanggil `readMessages([{remoteJid: chat_id, id: message_id, fromMe: false}])` bila `SANKA_BRIDGE_READ_RECEIPTS=1`. Kegagalan dicatat, tetap `204`.

### Siklus koneksi, backoff, shutdown (`socket.js`, `backoff.js`)

- `nextDelay(attempt, {base: 1000, cap: 60000, jitter: 0.2, rand})` = `min(cap, base * 2^attempt)` × (0,8–1,2). Fungsi murni, dites tanpa timer.
- `classifyDisconnect(statusCode)`:
  - `401`/`403`/`loggedOut` → `re-pair` (jangan retry; keluar dengan kode 2 dan pesan agar owner memindai QR lagi).
  - `515` (restart required, umum setelah pairing) → `restart-now`, delay 1.000 ms.
  - lainnya/undefined → `retry`, attempt naik.
- Setelah koneksi `open` bertahan ≥ 60 detik, `attempt` direset ke 0.
- `fetchLatestBaileysVersion` dibatasi 10 detik dan menyimpan versi terakhir yang berhasil; gagal → pakai versi cache atau default library. Tanpa batas ini, fetch yang menggantung membuat reconnect tidak pernah jalan (pola dari Hermes).
- Hanya satu socket hidup pada satu waktu: `starting` flag mencegah dua `startSocket()` paralel, dan timer reconnect dibatalkan saat shutdown.
- `makeWASocket` diberi `getMessage: async () => ({ conversation: '' })`. Tanpa ini, Baileys 7.x membuang pesan yang butuh penetapan ulang sesi E2EE secara diam-diam.
- `syncFullHistory: false`, `markOnlineOnConnect: false`, `browser: ['Sanka', 'Chrome', '120.0']`, `printQRInTerminal: false`.
- QR ditampilkan lewat `qrcode-terminal` ke TTY saja. QR adalah materi pairing: tidak pernah masuk file log, `/healthz`, atau pesan error. Bila stdout bukan TTY, bridge mencetak satu baris bahwa QR butuh terminal dan keluar dengan kode 1 (pairing manual, bukan mode daemon).
- `messages.upsert` hanya tipe `notify`; tiap pesan dinormalkan dalam `try/catch` sendiri agar satu pesan rusak tidak menjatuhkan batch.
- `creds.update` → `saveCreds()`; kegagalan simpan dicatat `creds_save_failed` dan memicu `degraded` (sesi bisa hilang saat restart).
- Graceful shutdown pada `SIGINT`/`SIGTERM`:
  1. `shuttingDown = true`; endpoint kirim menjawab `503`.
  2. `server.close()` berhenti menerima koneksi baru.
  3. `deliverer.flush(5000)` menuntaskan antrean masuk.
  4. `sock.end(new Error('shutdown'))` dan lepas listener `connection.update` agar tidak menjadwalkan reconnect.
  5. `process.exit(0)`. Sinyal kedua atau lewat 10 detik → `exit(130)`.
- `uncaughtException`/`unhandledRejection` dicatat tanpa isi pesan lalu keluar dengan kode 1, supaya supervisor (plan-8) yang me-restart, bukan proses setengah hidup.

### Alur pesan lengkap

```text
WhatsApp -> messages.upsert(notify)
   -> normalizeMessage()            # fakta transport saja; null untuk yang dibuang
   -> dedupe message_id             # TTL 10 menit
   -> deliverer.enqueue()           # FIFO global, batas 500
   -> POST /v1/inbound              # X-Sanka-Token; retry 429/5xx/timeout
   -> Python: normalizer -> dedup -> router -> policy
              -> POST /v1/typing composing
              -> command/agent -> formatter
   -> POST /v1/send (chunk 0..n, idempotency_key "<message_id>:<n>")
   -> outbound: dedupe -> pacing per chat -> sendText -> {message_id}
   -> POST /v1/typing paused
```

### Kegagalan dan perilakunya

| Kejadian | Perilaku bridge |
|---|---|
| Python mati / belum siap | Antrean masuk menampung sampai 500 event; retry per event; overflow membuang yang tertua dan menaikkan penghitung. `/healthz` `degraded`. |
| Token salah | `400/401` dibuang, tiga `401` berurutan menjeda pengiriman 60 detik dan menyalakan `delivery_paused`; log menyebut `auth_failed`. |
| Socket putus biasa | Reconnect dengan backoff berjitter sampai 60 detik; pesan baru menunggu di antrean. |
| Logout / sesi dicabut | Keluar kode 2 tanpa retry; owner memindai QR lagi. |
| `creds.json` hilang | Baileys memancarkan QR baru; bridge menampilkan QR dan menunggu, tidak crash. |
| File sesi rusak/tidak terbaca | Dicatat `session_unreadable` lalu keluar kode 1; owner memindahkan direktori dan pairing ulang, bukan menghapus otomatis. |
| Body kirim > 64 KiB | `413` di HTTP; Python yang salah memotong teks akan terlihat jelas di log kode. |
| Kiriman timeout | `504`; kunci tetap `inflight` sampai selesai atau TTL 15 menit. |
| Dua instance bridge | Tidak dicegah kode (plan-8 supervisor); dicatat sebagai risiko operasional. |

## Dependency baru

Semua hanya di `gateway/bridge/package.json`, tidak menambah dependency Python.

| Paket | Versi | Lisensi | Alasan |
|---|---|---|---|
| `@whiskeysockets/baileys` | `7.0.0-rc13` (pin persis, tanpa `^`) | MIT | Satu-satunya implementasi WhatsApp Web multi-device yang layak untuk Node. Pin persis karena proyek ini masih `rc` dan bentuk event-nya berubah antar rilis. Versi yang benar-benar terpasang dicatat di Hasil. |
| `pino` | `^9.14.0` | MIT | Log JSON terstruktur dengan redaksi bawaan; Baileys juga memakai API logger ini, jadi tidak ada logger kedua. |
| `qrcode-terminal` | `0.12.0` (pin persis) | belum tercantum di lock contoh; dicek dengan `npm view qrcode-terminal license` pada langkah 2 dan dicatat di Hasil | Menampilkan QR pairing di terminal; berisi encoder QR sehingga tidak perlu menulis encoder sendiri. Tidak ada dependency transitif. |

Yang sengaja tidak dipakai: `express` (cukup `node:http`, empat route, body limit lebih mudah ditegakkan), `axios`/`undici` (Node 22 punya `fetch` + `AbortSignal`), `dotenv` (env diisi supervisor/`config/secrets.env`), dan framework tes apa pun (`node --test`).

`package-lock.json` di-commit; `node_modules/` sudah di-ignore `.gitignore`.

## Langkah

1. Minta persetujuan owner atas plan ini; pastikan ADR kontrak `/v1` dari plan-3 sudah `Accepted`, lalu ubah status plan menjadi `In Progress`. Baca `docs/plan/WAVE-1.md` untuk pembagian subagent dan prasyarat Node.
2. Buat `gateway/bridge/package.json` (`private: true`, `type: module`, `engines.node >=22.11 <25`), pasang tiga dependency dengan pin di atas, commit `package-lock.json`, dan catat versi Node (`node -v`) di Hasil.
3. Implementasikan `config.js` + `config.test.js`: semua env, default, dan penolakan nilai tidak valid.
4. Implementasikan `logger.js` + `logger.test.js`: level, redaksi `text`/`message`/`caption`/token, `maskJid()` (hash 6 heks + domain), dan bukti bahwa objek sesi/QR tidak bisa lolos ke output.
5. Implementasikan `session.js` + `session.test.js`: `umask(0o077)`, buat `0700`, perbaiki `0600`, tolak path di dalam repo.
6. Implementasikan `normalize.js` + `normalize.test.js` dengan fixture Baileys (DM teks, grup mention, reply ke bot, ephemeral, view-once, media caption, media tanpa caption, reaction, fromMe, status broadcast, JID `@lid`).
7. Implementasikan `backoff.js` dan `ids.js` beserta tes deterministik (clock dan `rand` disuntik).
8. Implementasikan `deliver.js` + `deliver.test.js` dengan `post` palsu: urutan, retry 429/500, berhenti pada 400, jeda setelah 401, overflow, flush.
9. Implementasikan `outbound.js` + `outbound.test.js`: validasi, `409` hanya untuk kunci selesai, retry dengan kunci sama tidak mengirim ulang, jarak 1.200 ms per chat, batas 20/menit, `503` saat tidak terhubung, `429` saat tunggu > 30 detik, tidak ada pemotongan teks.
10. Implementasikan `http.js` + `http.test.js`: bind loopback, Host header, token constant-time, batas 64 KiB, `415`, `404`, `405`, empat route, error tanpa echo.
11. Implementasikan `socket.js` dan `main.js`: Baileys, QR TTY, reconnect, `creds.update`, `messages.upsert`, `getMessage`, mode `SANKA_BRIDGE_PAIR_ONLY=1`, graceful shutdown.
12. Jalankan `npm ci`, `npm test` (node --test), dan `npm audit --omit=dev`; perbaiki temuan high/critical.
13. Pairing manual dengan nomor Sanka: `SANKA_BRIDGE_PAIR_ONLY=1 npm start`, pindai QR, pastikan direktori sesi `0700` dan tiap file `0600`, lalu jalankan mode normal.
14. Smoke nyata: DM `/ping` dari owner, satu obrolan agent di DM, satu mention grup di grup allowlist, satu grup tanpa mention (harus senyap), dua chunk balasan berurutan, indikator mengetik terlihat.
15. Restart bridge (`Ctrl-C` lalu start lagi) dan buktikan tidak ada QR: sesi tersambung ulang sendiri. Cek `GET /healthz` (tanpa token) melaporkan `connected:true`.
16. Periksa `git status` bersih dari materi sesi; buktikan `sessionDir` berada di luar repo dan `.gitignore` tidak perlu diubah.
17. Matikan WhatsApp pada ponsel Sanka (mode pesawat) lalu nyalakan: pastikan reconnect backoff berjalan dan tidak ada crash loop; catat percobaan yang dipakai.
18. Catat pin versi Baileys dan aturan satu-instance sebagai catatan di ADR-0013, perbarui `docs/LOG.md` dan tabel status di `ROADMAP.md`; isi bagian Hasil dan ubah status plan menjadi `Done`.

## Tes

Otomatis, `npm test` di `gateway/bridge`, tanpa jaringan dan tanpa WhatsApp:

| File tes | Skenario |
|---|---|
| `config.test.js` | Env lengkap menghasilkan config beku; token kosong/pendek, URL non-loopback, port tidak valid, dan `outboundPerMinute` di luar 1–60 ditolak dengan nama env |
| `logger.test.js` | `maskJid` deterministik dan tidak memuat digit nomor; field `text`/`token`/`authorization` teredaksi; payload QR tidak muncul di output |
| `session.test.js` | Direktori baru `0700`; direktori `0755` diperbaiki; file `0644` jadi `0600`; path di dalam repo (`./sessions`, `gateway/bridge/session`) ditolak |
| `normalize.test.js` | DM teks jadi event dengan `is_group:false`; grup memakai `participant` sebagai `sender_id`; `mentions` dedupe dan dipotong 32; `@sanka` tetap ada di `text` |
| `normalize.test.js` | Reply ke pesan bot → `reply_to_bot:true` (PN dan LID); reply ke pengguna lain → `false` |
| `normalize.test.js` | Ephemeral/view-once/document-with-caption terbuka; media tanpa caption → `text:""`; reaction, protocol, `fromMe`, dan `@broadcast` → `null` |
| `normalize.test.js` | `@lid` dengan `resolvePn` berhasil memakai JID PN; `resolvePn` gagal memakai JID LID tanpa melempar error; `timestamp` di luar ±24 jam dihilangkan |
| `backoff.test.js` | Delay 1s, 2s, 4s, … dibatasi 60s, dengan jitter dalam ±20%; `classifyDisconnect` memetakan 401/403/515/lainnya |
| `ids.test.js` | Entri kedaluwarsa TTL; entri tertua dibuang saat batas `max` tercapai |
| `deliver.test.js` | Urutan event terjaga; `202`/`200` selesai; `429`/`500` diulang 1s/2s/4s lalu dibuang; `400` langsung dibuang; overflow membuang yang tertua; `flush` menunggu antrean |
| `deliver.test.js` | Tiga `401` berurutan menjeda pengiriman dan menyalakan `delivery_paused`; isi pesan tidak muncul di log (logger palsu) |
| `outbound.test.js` | `409` untuk kunci selesai; retry kunci yang sama saat `inflight` menghasilkan satu panggilan `sendText` dan `200` yang sama |
| `outbound.test.js` | Jarak ≥ 1.200 ms antar kiriman chat sama, chat berbeda tidak saling menunggu; batas 20/menit; `429` saat tunggu > 30 detik |
| `outbound.test.js` | `503` saat transport tidak terhubung; `send_failed` menghapus kunci; teks > 4096 ditolak tanpa dipotong; `typing` `paused` memanggil presence `paused` |
| `http.test.js` | Token salah → `401`; Host non-loopback → `400`; body > 64 KiB → `413`; tipe non-JSON → `415`; route/metode salah → `404`/`405` |
| `http.test.js` | `/v1/send` meneruskan ke outbound dan mengembalikan `message_id`; `/v1/read` `204` saat receipt dimatikan; `/healthz` tidak memuat JID/path |

`socket.js` dan `main.js` hanya merangkai modul yang sudah dites. Bagian yang tidak bisa diuji tanpa WhatsApp (QR, event Baileys, reconnect nyata) dibuktikan lewat tabel manual di bawah. Pemisahan ini disengaja: semua logika yang bisa salah (backoff, normalisasi, pengiriman, HTTP) berada di modul yang tidak menyentuh socket.

Manual, butuh ponsel owner dan nomor Sanka (dicatat hasilnya di Hasil):

| Skenario | Bukti yang diharapkan |
|---|---|
| `SANKA_BRIDGE_PAIR_ONLY=1 npm start` | QR tampil di TTY saja; setelah pindai, sesi tersimpan `0700`/`0600`; proses keluar 0 |
| DM `/ping` dari owner | Balasan `pong`-style dari Python dalam beberapa detik |
| DM obrolan bebas | Balasan agent; `reasoning`/tool trace tidak muncul |
| Grup allowlist dengan `@sanka ...` | Balasan masuk ke grup; disebut sebagai balasan pesan |
| Grup allowlist tanpa mention | Tidak ada balasan; log hanya `inbound_skipped`/ignored, tanpa isi pesan |
| Balasan panjang > 1 chunk | Chunk terkirim berurutan naik, tanpa duplikat; indikator mengetik muncul |
| Restart bridge | Tersambung ulang tanpa QR; `/healthz` `connected:true` |
| Python dimatikan sementara | `/v1/inbound` gagal, bridge retry, tidak crash; setelah Python hidup lagi antrean terkirim |
| `git status --porcelain` | Tidak ada file sesi/kredensial |

## Kriteria selesai

- `npm ci` dan `npm test` lulus seluruh tes di tabel; `npm audit --omit=dev` tanpa temuan high/critical.
- Satu DM nyata dan satu mention grup nyata dari nomor owner menerima balasan dari agent Python; grup tanpa mention tidak dibalas.
- Restart bridge menyambung ulang tanpa memindai QR, dan `/healthz` melaporkan `connected:true` tanpa memuat nomor atau JID.
- Direktori sesi berada di luar repo, mode `0700` dengan file `0600`; `git status --porcelain` bersih setelah pemakaian nyata.
- Tidak ada logika bisnis di Node: tidak ada pembacaan config Python, tidak ada daftar nomor, tidak ada pemotongan teks, dan `text`/caption tidak pernah masuk log.
- Idempotensi terbukti: pengiriman ulang `idempotency_key` yang sama tidak menghasilkan dua pesan WhatsApp.
- Logout WhatsApp menghentikan proses dengan kode 2 tanpa loop reconnect; gangguan jaringan biasa tersambung kembali.
- ADR kontrak `/v1` dari plan-3 berstatus `Accepted`, catatan pin Baileys dan aturan satu-instance masuk ADR-0013, `docs/LOG.md` diperbarui, status plan menjadi `Done`.

## Yang perlu dipahami owner

- **Dua proses, satu arah keputusan.** Node hanya memindahkan pesan; kalau bridge mati, Sanka tidak bisa menerima atau mengirim WhatsApp, tetapi proses Python tetap hidup dan bisa dites. Jangan menaruh aturan izin di Node hanya karena "lebih cepat".
- **Direktori sesi setara kata sandi nomor.** Siapa pun yang menyalinnya bisa memakai nomor Sanka. Itu sebabnya direktori dijaga `0700`, file `0600`, dan dilarang berada di repo. Salinan cadangan, bila nanti dibuat plan-8, harus diperlakukan sebagai secret.
- **QR juga secret.** QR yang tampil di terminal bisa dipindai orang lain di ruangan yang sama. Pairing dilakukan di tempat privat, dan QR tidak pernah ditulis ke file.
- **`409` berarti "sudah terkirim", bukan "gagal".** Kunci idempotensi `<message_id>:<chunk_index>` inilah yang mencegah balasan ganda saat Python retry.
- **Bridge tidak memotong teks.** Kalau balasan terpotong aneh, perbaiki `Formatter` di Python, bukan bridge. Bridge hanya menolak teks > 4096 karakter sebagai batas aman transport.
- **Pacing itu perlindungan nomor, bukan fitur.** Jarak 1,2 detik per chat dan 20 pesan/menit global ada untuk mengurangi risiko diblokir, bukan untuk kerapian.
- **JID `@lid`.** Sebagian akun WhatsApp kini memakai LID, bukan nomor telepon. Bila resolusi LID→PN gagal, `sender_id` tetap JID LID dan `resolve_role` di Python akan menganggap pengirim sebagai peserta. Ini batas nyata plan-4 dan alasan `resolvePn` diuji terpisah.
- **Pesan bisa hilang saat bridge mati.** Antrean masuk hanya in-memory. Pesan yang belum diteruskan ke Python hilang bila proses mati. WhatsApp sendiri akan mengirim ulang pesan yang belum sampai ke perangkat tertaut, tetapi tidak ada jaminan urutan penuh setelah reconnect.

## Risiko

- **Jalur tidak resmi.** Baileys bukan API resmi; nomor bisa diblokir. Mitigasi plan-4: tanpa broadcast, pacing per chat dan global, tanpa retry storm, tanpa pesan borongan. Bila risiko tidak dapat diterima, pindah ke Cloud API lewat ADR baru (ADR-0013).
- **Baileys masih `rc`.** `7.0.0-rc13` bisa berubah perilaku di rilis berikutnya. Pin persis + `package-lock.json` adalah pertahanan utama; upgrade Baileys diperlakukan sebagai pekerjaan sendiri (tes penuh + pairing ulang di lingkungan uji), bukan `npm update` biasa.
- **Ketergantungan pada `getMessage` dan `fetchLatestBaileysVersion`.** Tanpa `getMessage`, pesan yang butuh penetapan ulang E2EE hilang diam-diam; tanpa batas waktu, fetch versi bisa menggantung dan mematikan reconnect. Keduanya dikunci di plan ini dan diberi tes unit pada bagian yang bisa dites.
- **LID vs PN.** Kegagalan resolusi membuat owner/panitia tidak dikenali. Mitigasi: cache LID→PN, fallback JID apa adanya, log `lid_unresolved` (masked) agar cepat terlihat; jangka panjang perlu keputusan Python untuk mencocokkan LID di config.
- **Kehilangan sesi.** File sesi rusak/hilang berarti pairing ulang; logout dari ponsel menghentikan bridge dengan kode 2. Tidak ada pairing otomatis, karena pairing otomatis tanpa owner justru berbahaya.
- **At-least-once, bukan exactly-once.** Bridge bisa mengirim event yang sama dua kali setelah retry jaringan; Python yang menduplikasi lewat `message_id` (`limits.inbound_dedup_ttl_minutes`, default 15 menit). Dedupe bridge 10 menit sengaja lebih pendek agar angkanya tidak lebih besar dari jendela dedupe Python. Batas jujur: dedupe Python hilang saat proses Python restart, jadi event yang sedang di-retry tepat saat restart bisa diproses dua kali. Itu risiko plan-3 (persistensi dedupe ditinjau di plan-8), bukan sesuatu yang bisa ditutup bridge.
- **Head-of-line blocking.** Satu worker FIFO menjaga urutan tetapi membuat satu event yang retry menahan event lain sampai 7 detik. Untuk satu nomor dan skala tim ini dapat diterima; bila antrean sering menumpuk, solusinya antrean per chat (bukan menambah worker paralel yang merusak urutan).
- **Dua instance bridge.** Dua proses pada direktori sesi yang sama membuat sesi dicabut. Plan-4 hanya mendokumentasikannya; pencegahan (lock file) dan supervisor ada di plan-8.
- **Idempotensi hilang saat bridge restart.** Kunci kirim hanya in-memory, jadi retry Python yang jatuh tepat setelah restart bridge bisa menghasilkan satu chunk ganda. Dampaknya pesan duplikat, bukan kerusakan data. Persistensi kunci ditinjau bersama dedupe plan-3 di plan-8.
- **Batasan fase.** Plan-4 sengaja tidak menangani media, backup sesi, dan health-check produksi. `GET /healthz` di sini minimal untuk smoke; jangan dianggap sebagai hardening plan-8.

## Catatan perubahan

- `test/helpers.js` ditambahkan sebagai double bersama (`createFakeClock`, `createFakeLogger`, `createFakeTransport`, `jsonResponse`, `startTestServer`) agar empat subagent tidak menulis fake yang berbeda. Tidak mengubah kontrak HTTP atau perilaku.
- Detail internal factory (`createInboundDeliverer`, `createOutbound`, `createServer`, `createSocketManager`) dipin di `WAVE-1.md` agar modul bisa ditulis paralel. Semua field HTTP dan env name tetap persis seperti rancangan di atas.

## Hasil

Belum dikerjakan.
