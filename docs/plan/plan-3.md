# Plan 3 — Gateway Python dan normalizer

- Status: In Progress
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Membangun sisi Python dari gateway: server HTTP lokal yang menerima pesan dari bridge, normalizer payload menjadi `InboundMessage`, deduplikasi berbasis `message_id`, penyaringan grup/DM sesuai config, lalu pipeline yang menjalankan command atau agent dan mengirim balasan terformat kembali ke bridge.
Hasil akhir plan ini adalah gateway yang bisa diuji end-to-end dengan **bridge palsu** dan **runtime palsu**: pesan masuk, balasan keluar, duplikat diabaikan, grup tanpa mention diabaikan, dan tidak ada reasoning atau secret yang keluar.
Koneksi WhatsApp nyata tetap milik plan-4.

## Di luar cakupan

- Koneksi WhatsApp, Baileys, QR/pairing perangkat, sesi WhatsApp, reconnect, dan media (plan-4). Plan ini hanya menetapkan kontrak HTTP yang nanti dipenuhi bridge.
- Implementasi `AgentRuntime`, sesi, antrean agent, dan client OpenCode Go (plan-2). Plan ini memakainya lewat protokol yang sempit.
- Tool apa pun (plan-5/6), memori/skill (plan-7), `/verbose`, redaksi log menyeluruh, supervisor, dan deployment (plan-8).
- Persistensi dedup, persistensi antrean keluar, multi-proses, broker eksternal, TLS, dan autentikasi multi-token.
- Streaming token atau pengiriman parsial ke WhatsApp.
- Command baru. Plan ini hanya menyambungkan registry yang sudah ada (`/ping`, `/help`, `/whoami`, `/status`, `/new`, `/stop`).

## Prasyarat

- Plan-1 `Done`: `sanka.models.InboundMessage`, `sanka.router.route`, `sanka.policy` (`resolve_role`, `RateLimiter`, `Role`), `sanka.formatter.Formatter`, `sanka.commands.registry.CommandRegistry`.
- Plan-2 minimal mendekati selesai karena plan ini memakai tiga antarmuka darinya:
  - `AgentRuntime.handle(message, role) -> AgentReply(messages: tuple[str, ...])`
  - `SessionControl.reset(chat_id)` dan `stop(chat_id, requester_id, is_owner) -> StopOutcome` (dipakai `/new` dan `/stop`; didaftarkan lewat `build_registry(control)`)
  - `sanka.secrets.get_secret(env_name)` untuk token gateway.
  Plan-3 tetap bisa dikerjakan dengan `agent=None` (mode terdegradasi tanpa LLM) agar normalizer, gate, dan antrean keluar bisa dites lebih dulu, tetapi plan belum boleh `Done` sebelum integrasi runtime palsu dan runtime plan-2 sama-sama lulus.
- ADR-0013 (`Accepted`) sebagai dasar pola bridge.
- Owner menyetujui plan ini dan **ADR-0018 — Kontrak HTTP gateway Python ↔ bridge**; ADR sudah `Accepted`.
- `SANKA_GATEWAY_TOKEN` tersedia di environment atau `config/secrets.env` (0600) untuk menjalankan server sungguhan. Tes memakai token palsu yang diinjeksi.

## Rancangan

### Arah komunikasi

Dua arah, keduanya loopback:

```text
WhatsApp  <->  bridge Node/Baileys (plan-4)          Python gateway (plan-3)
                     |  POST /v1/inbound   --------------->  server HTTP
                     |                                        normalizer -> dedup -> gate
                     |                                        -> route -> command/agent
                     |  <--------- POST /v1/send, /v1/typing, /v1/read
                     |  <--------- (dipanggil oleh OutboundQueue)
```

- Bridge **mendorong** pesan masuk (push), bukan Python yang long-poll. Alasannya: bridge sudah punya koneksi persisten ke WhatsApp dan bisa retry sendiri; Python tidak perlu menyimpan kursor.
- Python **memanggil** bridge untuk semua keluaran memakai `gateway.bridge_url` yang sudah ada di config.
- ADR-0013 menyebut long-poll inbound sebagai perilaku yang boleh diadopsi dari Hermes. Plan ini memilih push + retry idempoten dan mencatat perbedaannya di Risiko; kontraknya tetap satu arah data yang sama sehingga pindah ke long-poll nanti tidak mengubah normalizer.

### Struktur file

```text
gateway/
  __init__.py
  normalizer.py     # InboundEvent, MalformedPayload, normalize_inbound()
  dedup.py          # TtlRegistry (dipakai untuk dedup pesan dan throttle notice)
  gate.py           # GateReason, check_gate(): allowlist grup + kebijakan DM + stale + teks kosong
  outbound.py       # BridgeClient protocol, HttpBridgeClient, OutboundKind/Item, OutboundQueue
  dispatcher.py     # Dispatcher, SubmitOutcome/SubmitResult, pipeline command/agent
  server.py         # GatewayServer (ThreadingHTTPServer), request handler, batas body, auth
  app.py            # GatewayApp, build_app(): merakit semua komponen + validasi loopback
  __main__.py       # python -m gateway
config/
  sanka.example.yaml  # tambah key gateway baru + access.bot_ids + limits.inbound_dedup_ttl_minutes
sanka/config.py      # parsing + validasi key baru
tests/
  gateway_fakes.py
  test_gateway_normalizer.py
  test_gateway_dedup.py
  test_gateway_gate.py
  test_gateway_outbound.py
  test_gateway_dispatcher.py
  test_gateway_server.py
  test_gateway_e2e.py
```

### Kontrak HTTP

Semua endpoint hanya menerima koneksi dari loopback. Server menolak start bila `gateway.listen_host` bukan `127.0.0.1`, `::1`, atau `localhost`.

Header wajib pada kedua arah: `X-Sanka-Token: <token>`; nilainya berasal dari environment variable yang namanya ada di `gateway.auth_token_env` (default `SANKA_GATEWAY_TOKEN`). Perbandingan memakai `hmac.compare_digest`. Server gagal start bila token tidak ada.

| Metode | Path | Auth | Fungsi |
|---|---|---|---|
| `GET` | `/healthz` | tidak | Status hidup gateway |
| `POST` | `/v1/inbound` | ya | Bridge mengirim satu pesan masuk |
| lainnya | — | — | `404` / `405` |

`GET /healthz` → `200 {"status":"ok","uptime_seconds":<int>,"outbound_pending":<int>}`. Tanpa auth agar supervisor (plan-8) bisa memeriksa tanpa membagikan token; isinya metadata saja, tanpa config atau isi pesan.

`POST /v1/inbound`:

| Field | Tipe | Wajib | Aturan |
|---|---|---|---|
| `message_id` | string | ya | tidak kosong, ≤ 200 karakter; id pesan WhatsApp dari bridge |
| `chat_id` | string | ya | bentuk `<local>@<domain>`, ≤ 200 karakter; `@g.us` berarti grup |
| `sender_id` | string | ya | bentuk sama; di grup berisi JID peserta |
| `text` | string | tidak (default `""`) | ≤ 32.768 karakter; media yang tidak didukung dikirim sebagai `""` |
| `is_group` | bool | tidak | bila ada, harus sama dengan `chat_id.endswith("@g.us")` |
| `mentions` | list[string] | tidak | ≤ 32 entri, tiap entri berbentuk JID; duplikat dibuang dengan urutan dipertahankan |
| `reply_to_bot` | bool | tidak (default `false`) | bridge sudah memutuskan pesan ini membalas pesan bot |
| `timestamp` | int/float | tidak | epoch **detik**; bila tidak ada dipakai waktu terima Python |

Field tak dikenal diabaikan (bridge boleh berkembang); namanya boleh dicatat di level debug, nilainya tidak.

Respons:

| Status | Body | Arti untuk bridge |
|---|---|---|
| `202` | `{"status":"accepted"}` | diterima dan akan diproses; jangan kirim ulang |
| `200` | `{"status":"duplicate"}` | `message_id` sudah pernah diproses; jangan kirim ulang |
| `200` | `{"status":"ignored","reason":"<kode>"}` | keputusan final; jangan kirim ulang |
| `400` | `{"error":"malformed_payload","reason":"<kode>"}` | payload cacat; jangan kirim ulang, cukup log |
| `401` | `{"error":"unauthorized"}` | token salah/hilang |
| `413` | `{"error":"payload_too_large"}` | body > 64 KiB |
| `415` | `{"error":"unsupported_media_type"}` | `Content-Type` bukan `application/json` |
| `429` | `{"error":"overloaded"}` | pipeline penuh; retry dengan backoff |
| `500` | `{"error":"internal"}` | galat tak terduga; retry dengan backoff |

Kode `reason` yang stabil: normalizer → `not_an_object`, `invalid_json`, `message_id_invalid`, `chat_id_invalid`, `sender_id_invalid`, `text_invalid`, `text_too_long`, `is_group_invalid`, `is_group_mismatch`, `mentions_invalid`, `reply_to_bot_invalid`, `timestamp_invalid`. Gate/router → `blank_text`, `stale`, `dm_not_allowed`, `group_not_allowed`, `not_mentioned`, `rate_limited`. Body tidak pernah menggemakan nilai dari payload.

Python → bridge (base `gateway.bridge_url`):

| Metode | Path | Body | Respons |
|---|---|---|---|
| `POST` | `/v1/send` | `{"chat_id","text","idempotency_key"}` | `200 {"message_id":"..."}`; `409` bila key sudah pernah dikirim (dianggap sukses) |
| `POST` | `/v1/typing` | `{"chat_id","state":"composing"\|"paused"}` | `204` |
| `POST` | `/v1/read` | `{"chat_id","message_id"}` | `204` |

`idempotency_key` berformat `"{inbound_message_id}:{chunk_index}"` (indeks mulai 0). Semua chunk satu balasan dikirim berurutan naik, satu request per potongan. Retry hanya untuk error koneksi, timeout, `429`, `503`, `504`, dan `5xx` (maksimal 3 kali, backoff 0,5/1/2 detik, menghormati `retry_after_ms` di body atau `Retry-After` sampai 30 detik); `4xx` lain dianggap gagal permanen dan dijatuhkan dengan log metadata. Bridge boleh ikut memperlambat pengiriman (pacing per chat, tolak dengan `429`/`503` saat antreannya sendiri penuh); plan-3 tetap menegakkan batas laju global sebelum memanggil `/v1/send`.

### Normalizer

`gateway/normalizer.py`:

```python
MAX_TEXT_CHARS = 32_768
MAX_MENTIONS = 32
MAX_ID_CHARS = 200
_JID_PATTERN = re.compile(r"^[0-9A-Za-z._:\-]+@[0-9A-Za-z.\-]+$")

@dataclass(frozen=True, slots=True)
class InboundEvent:
    message_id: str
    message: InboundMessage      # dari sanka.models, tidak diubah
    timestamp: float             # epoch detik

class MalformedPayload(ValueError):
    reason: str                  # salah satu kode di atas

def normalize_inbound(
    payload: object, *, now: Callable[[], float] = time.time
) -> InboundEvent: ...
```

- `is_group` dihitung dari `chat_id`, bukan dipercaya dari bridge. Bila bridge mengirim `is_group` yang bertentangan → `MalformedPayload("is_group_mismatch")`.
- JID hanya divalidasi bentuknya (ada `@`, karakter aman, panjang). Normalisasi nomor, `@lid`, dan `@s.whatsapp.net` adalah tugas bridge (plan-4).
- `text` hanya di-`strip()` untuk pengecekan kosong di gate; isi asli diteruskan ke agent apa adanya.
- `timestamp` negatif/nol atau bukan angka → `timestamp_invalid`. Tidak ada pembulatan zona waktu; semua epoch UTC.
- `InboundMessage` tidak diubah (kontrak plan-1/plan-2), sehingga `timestamp` hidup di `InboundEvent`.

### Deduplikasi dan TTL

`gateway/dedup.py` menyediakan `TtlRegistry`:

```python
class TtlRegistry:
    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic,
                 max_entries: int = 10_000) -> None: ...
    def register(self, key: str) -> bool:   # True = baru, False = sudah ada
    def __len__(self) -> int: ...
```

- Satu instance untuk dedup pesan (`ttl = limits.inbound_dedup_ttl_minutes`, default 15 menit) dan satu instance untuk throttle notice (TTL 24 jam untuk notice pairing, 10 menit untuk notice rate limit), dengan kunci berbeda (`"msg:<id>"`, `"pair:<sender>"`, `"rate:<sender>"`).
- Pembersihan lazy: sapu entri kedaluwarsa setiap 256 `register()` atau saat `len > 90% max_entries`; bila masih penuh, buang entri tertua (urutan insersi `dict`).
- `message_id` didaftarkan **sebelum** gate dan sebelum pemrosesan. Artinya keputusan final apa pun (duplikat, diabaikan, ditolak rate limit) membuat retry berikutnya mendapat `duplicate`, bukan diproses ulang.

**In-memory, bukan persisten — alasan untuk plan ini:**

1. Duplikat nyata datang dalam hitungan detik (bridge retry), jauh di dalam satu masa hidup proses. Restart menghapus dedup **dan** pekerjaan yang sedang berjalan sekaligus, jadi tidak ada pesan yang setengah diproses lalu dikirim dua kali.
2. Repo belum punya lapisan persistensi (plan-1 menundanya), dan menambah SQLite sekarang berarti skema, migrasi, serta penulisan tahan-crash hanya untuk id pesan berumur menit.
3. Pertahanan duplikat **kirim** yang sesungguhnya ada di sisi bridge lewat `idempotency_key` (plan-4). Dedup Python melindungi dari kerja LLM ganda; idempotensi bridge melindungi dari pesan WhatsApp ganda.
4. Batasnya jujur: bila proses restart di tengah jendela retry bridge, satu pesan bisa diproses dua kali. Persistensi dedup masuk plan-8 bila terbukti perlu.

### Gate: allowlist grup dan kebijakan DM

`gateway/gate.py`:

```python
class GateReason(str, Enum):
    ALLOWED = "allowed"
    BLANK_TEXT = "blank_text"
    STALE = "stale"
    DM_NOT_ALLOWED = "dm_not_allowed"
    GROUP_NOT_ALLOWED = "group_not_allowed"

def check_gate(event: InboundEvent, config: SankaConfig, role: Role,
               *, now: Callable[[], float] = time.time) -> GateReason: ...
```

Urutan pemeriksaan:

1. Teks kosong setelah `strip()` → `BLANK_TEXT`. Pesan media yang belum didukung berakhir di sini.
2. `abs(now() - event.timestamp) > gateway.max_inbound_age_seconds` → `STALE`. Ini mencegah bridge yang baru reconnect membanjiri Sanka dengan obrolan lama. `0` menonaktifkan pemeriksaan.
3. Grup: `group_policy == "allowlist"` → `chat_id` harus ada di `access.allowed_group_ids` (cocok persis, termasuk `@g.us`); `open` → semua grup. Grup yang tidak lolos **diabaikan diam-diam** (tanpa balasan) agar Sanka tidak berisik di grup asing.
4. DM: `dm_policy == "open"` → semua pengirim. `dm_policy == "pairing"` → hanya pengirim yang perannya `OWNER` atau `COMMITTEE` (yaitu nomor yang sudah terdaftar di config).
   Pengirim lain ditolak oleh gate; `Dispatcher` yang mengirim satu notice singkat (maksimal sekali per 24 jam per pengirim) lewat `notices` + antrean keluar, tanpa LLM: "Sanka belum terhubung dengan nomor ini. Minta owner menambahkan nomormu."
   Notice pairing tidak dikirim bila rate limit pengirim sudah habis.

Catatan penting: `pairing` versi plan-3 adalah **allowlist + notice**, bukan alur tukar kode. Alur pairing interaktif (kode sekali pakai, `/pair <kode>`, daftar nomor terpasang yang bertahan setelah restart) butuh penyimpanan persisten dan command baru; itu tidak masuk plan ini (lihat Risiko).

### Dispatcher

`gateway/dispatcher.py`:

```python
class SubmitOutcome(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    OVERLOADED = "overloaded"

@dataclass(frozen=True, slots=True)
class SubmitResult:
    outcome: SubmitOutcome
    reason: str = ""

class AgentHandler(Protocol):
    def handle(self, message: InboundMessage, role: Role) -> AgentReply: ...

class Dispatcher:
    def __init__(self, *, config: SankaConfig, registry: CommandRegistry,
                 formatter: Formatter, limiter: RateLimiter,
                 outbound: OutboundQueue, agent: AgentHandler | None,
                 dedup: TtlRegistry, notices: TtlRegistry,
                 workers: int = 8,
                 wall_clock: Callable[[], float] = time.time) -> None: ...
    def submit(self, event: InboundEvent) -> SubmitResult: ...
    def shutdown(self, wait: bool = True) -> None: ...
```

Alur `submit()` (sinkron, cepat, tanpa LLM; dipanggil dari thread HTTP):

```text
1. dedup.register("msg:" + message_id)?  tidak -> DUPLICATE
2. role = resolve_role(sender_id, config.access)
3. check_gate(...) != ALLOWED -> (notice bila perlu) IGNORED(reason)
4. route(message, bot_ids=config.access.bot_ids,
         require_mention=config.gateway.require_mention)
   IGNORE -> IGNORED("not_mentioned")
5. route == COMMAND: limiter.allow(sender_id)? tidak -> (notice rate limit sekali) IGNORED("rate_limited")
   route == AGENT: rate limit diperiksa oleh AgentRuntime (jangan periksa lagi di sini)
6. semaphore.acquire(timeout=2s)? gagal -> OVERLOADED
7. executor.submit(_process, event, role, route)
   -> ACCEPTED
```

Router sengaja dicek **sebelum** rate limit: pesan grup yang tidak menyebut Sanka adalah mayoritas lalu lintas grup, dan tidak boleh menghabiskan kuota pengirim yang nanti benar-benar memanggil Sanka.

`_process()` berjalan di `ThreadPoolExecutor(max_workers=8, thread_name_prefix="sanka-gw")`:

- `route == COMMAND`:
  `reply = registry.dispatch(text, CommandContext(role, config, sender_id, chat_id, is_group))`
  lalu `outbound.enqueue_reply(chat_id, [reply], base_key=message_id)`. Tidak ada typing untuk command (balasannya instan; indikator mengetik hanya untuk jalur agent).
- `route == AGENT`:
  `outbound.enqueue_typing(chat_id, "composing")` → `agent.handle(message, role)` → `outbound.enqueue_typing(chat_id, "paused")` → `outbound.enqueue_reply(chat_id, reply.messages, base_key=message_id)`.
  Bila `agent is None` (mode terdegradasi / sebelum plan-2 mendarat): balasan tetap `"Fitur AI belum aktif."`.
  `AgentClientError` sudah diubah runtime menjadi balasan ramah (plan-2); dispatcher tidak menambah teks sendiri untuk kasus itu.
  Exception tak terduga: log tipe + traceback (tanpa isi pesan), lalu kirim satu balasan generik `"Maaf, ada gangguan di sisiku. Coba lagi sebentar."` lewat antrean keluar. Worker tidak boleh mati karena satu exception.
- Tidak ada pekerjaan LLM di thread HTTP. `submit()` tidak pernah menunggu model.

Urutan antar-pesan:

- Pesan `AGENT` dari chat yang sama sudah diserialkan oleh `ChatQueue` plan-2; plan-3 tidak membuat antrean kedua.
- Command dieksekusi cepat dan deterministik. Interleaving command dengan pekerjaan agent yang sedang berjalan di chat yang sama didefinisikan oleh plan-2: `/new` menghentikan pekerjaan dan membuang antrean, `/stop` menghentikan pekerjaan pengirim.
- Urutan balasan keluar per chat dijamin `OutboundQueue` (FIFO per `chat_id`), bukan oleh urutan selesainya model.

Rate limiter:

- Satu instance `RateLimiter` dimiliki wiring aplikasi dan dioper ke `Dispatcher` **dan** `AgentRuntime`. Pembagiannya: `Dispatcher.submit()` memeriksa rate limit **hanya untuk jalur `COMMAND`**; jalur `AGENT` diserahkan ke `AgentRuntime.handle()` yang sudah memeriksa di plan-2. Dengan begitu kuota terpakai tepat satu kali per pesan. Dispatcher tidak boleh memeriksa ulang di langkah 5 untuk pesan `AGENT`.

### Antrean keluar

`gateway/outbound.py`:

```python
class OutboundKind(str, Enum):
    SEND = "send"
    TYPING = "typing"
    READ = "read"

@dataclass(frozen=True, slots=True)
class OutboundItem:
    kind: OutboundKind
    chat_id: str
    text: str = ""
    idempotency_key: str = ""
    message_id: str = ""       # untuk READ
    state: str = "composing"   # untuk TYPING

class BridgeClient(Protocol):
    def send(self, chat_id: str, text: str, idempotency_key: str) -> str | None: ...
    def typing(self, chat_id: str, state: str) -> None: ...
    def read(self, chat_id: str, message_id: str) -> None: ...

class BridgeError(Exception):
    retryable: bool
    status: int | None
    retry_after: float | None

class HttpBridgeClient:      # httpx.Client(timeout=gateway.request_timeout_seconds)
    def __init__(self, base_url: str, token: str, timeout: float,
                 client: httpx.Client | None = None) -> None: ...

class OutboundQueue:
    def __init__(self, bridge: BridgeClient, formatter: Formatter, *,
                 per_minute: int, clock: Callable[[], float] = time.monotonic,
                 max_per_chat: int = 32, max_attempts: int = 3) -> None: ...
    def start(self) -> None: ...
    def stop(self, timeout: float = 5.0) -> None: ...
    def enqueue_reply(self, chat_id: str, messages: Sequence[str], *, base_key: str) -> None: ...
    def enqueue_typing(self, chat_id: str, state: str = "composing") -> None: ...
    def enqueue_read(self, chat_id: str, message_id: str) -> None: ...
    def wait_idle(self, timeout: float = 5.0) -> bool: ...
    @property
    def pending(self) -> int: ...
```

Keputusan:

1. **Satu worker thread** dengan `dict[chat_id, deque[OutboundItem]]` dan penjadwalan round-robin antar-chat. FIFO per chat terjaga, chat lain tidak perlu thread sendiri, dan batas laju global bisa dihitung di satu tempat. Kirim bersifat sinkron ke loopback (timeout 10 detik); bridge lambat memperlambat antrean, bukan menggandakan worker.
2. **Formatter adalah satu-satunya pintu keluar.** `enqueue_reply()` menjalankan `formatter.format(text, ResponseKind.TEXT)` untuk setiap teks, membuang hasil kosong, lalu memasukkan satu `SEND` per potongan dengan `idempotency_key = f"{base_key}:{index}"`. Command dan agent sama-sama melewati jalur ini, sehingga blok reasoning/tool tetap tersaring walaupun runtime lupa. Formatting ganda (runtime plan-2 + gateway) bersifat idempoten: strip tag tidak berubah pada teks bersih, dan potongan yang sudah ≤ `max_chars` tidak dipotong lagi.
3. **Batas laju keluar** memakai jendela bergulir 60 detik berisi maksimal `limits.outbound_per_minute` timestamp. `SEND` menunggu slot (tidur yang bisa dibangunkan `stop()`), `TYPING`/`READ` hanya dikirim bila slot tersedia saat itu dan **dijatuhkan** bila tidak — indikator kosmetik tidak boleh menahan balasan.
4. **Batas antrean** `max_per_chat = 32`. Melebihi batas: item dijatuhkan dan dicatat (`outbound_overflow`); untuk `SEND` ini dianggap bug dan dicatat pada level error. Tidak ada balasan "antrean penuh" karena mengirim balasan justru menambah antrean.
5. **Retry** hanya untuk `send` dan hanya bila `BridgeError.retryable`, yaitu error koneksi, timeout, `429`, `503`, `504`, dan `5xx` lain; maksimal 3 percobaan dengan backoff 0,5/1/2 detik yang bisa diinterupsi. Bila bridge mengirim `retry_after_ms` di body (`429 rate_limited`, `503 not_connected`) atau header `Retry-After`, nilainya dipakai sebagai jeda, dibatasi maksimal 30 detik. `409` pada `/v1/send` berarti chunk itu sudah pernah selesai dikirim → dianggap sukses. `200` untuk key yang masih in-flight juga dianggap sukses. Setelah gagal total: item dibuang, log metadata (`chat` digest, kode error, percobaan), tidak ada teks pesan di log.
6. `TYPING("paused")` tetap dikirim setelah chunk terakhir walaupun pengiriman gagal, agar indikator tidak menggantung.
7. `send_read_receipts: true` → `enqueue_read(chat_id, message_id)` untuk pesan yang lolos gate, sebelum routing. Default `false` karena menandai dibaca pun mengirim sinyal ke WhatsApp.

### Server HTTP

`gateway/server.py`:

```python
MAX_BODY_BYTES = 64 * 1024

class GatewayServer:
    def __init__(self, dispatcher: Dispatcher, *, token: str, host: str, port: int) -> None: ...
    @property
    def bound_port(self) -> int: ...     # berguna saat tes memakai port 0
    def serve_forever(self) -> None: ...
    def shutdown(self) -> None: ...
```

- `ThreadingHTTPServer` + `BaseHTTPRequestHandler`, `daemon_threads = True`, `allow_reuse_address = True`, `protocol_version = "HTTP/1.1"`.
- `server_version = "sanka-gateway"`, `sys_version = ""` agar versi Python tidak diumumkan; `log_message()` diarahkan ke `logging` dan hanya memuat method, path, status, serta durasi. Query string tidak pernah dipakai.
- Handler memeriksa `Content-Length` (wajib; `Transfer-Encoding: chunked` ditolak `411`), menolak body > `MAX_BODY_BYTES` dengan `413` sebelum membaca seluruh body, dan memakai `json.loads` pada byte yang sudah dibatasi.
- Token diperiksa **sebelum** body dibaca, memakai `hmac.compare_digest` pada `X-Sanka-Token`.
- Binding: `GatewayApp` memvalidasi host loopback, dan `GatewayServer` memeriksa ulang (`ValueError`) agar tidak ada jalur kode yang bisa membuka `0.0.0.0`.
- Thread HTTP hanya melakukan parsing, normalisasi, lalu `dispatcher.submit()`. Tidak ada pekerjaan model dan tidak ada panggilan jaringan keluar dari thread HTTP; pengiriman dilakukan worker `OutboundQueue`.

Pemetaan hasil `submit()` ke respons:

| `SubmitResult` | Status HTTP | Body |
|---|---|---|
| `ACCEPTED` | `202` | `{"status":"accepted"}` |
| `DUPLICATE` | `200` | `{"status":"duplicate"}` |
| `IGNORED` | `200` | `{"status":"ignored","reason":<reason>}` |
| `OVERLOADED` | `429` | `{"error":"overloaded"}` |

`gateway/app.py` merakit semuanya:

```python
@dataclass(slots=True)
class GatewayApp:
    dedup: TtlRegistry
    notices: TtlRegistry
    outbound: OutboundQueue
    dispatcher: Dispatcher
    server: GatewayServer
    def start(self) -> None: ...      # start outbound worker
    def stop(self) -> None: ...       # shutdown server, executor, outbound
    def serve_forever(self) -> None: ...

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

def build_app(config: SankaConfig, *, registry: CommandRegistry,
              agent: AgentHandler | None, limiter: RateLimiter,
              token: str) -> GatewayApp: ...
```

`gateway/__main__.py` membaca `SANKA_CONFIG` (default `config/sanka.yaml`), memuat config, mengambil token lewat `sanka.secrets.get_secret(config.gateway.auth_token_env)`, membangun registry + runtime plan-2, lalu `serve_forever()`; `SIGINT`/`SIGTERM` memicu `stop()` yang rapi.

### Perubahan config

Key baru (semua wajib ada di `config/sanka.yaml`; validasi gagal cepat dengan nama key):

| Key | Default contoh | Validasi |
|---|---|---|
| `gateway.listen_host` | `127.0.0.1` | harus loopback |
| `gateway.listen_port` | `8099` | 1–65535 |
| `gateway.auth_token_env` | `SANKA_GATEWAY_TOKEN` | nama env `^[A-Z_][A-Z0-9_]*$` |
| `gateway.request_timeout_seconds` | `10` | 1–60 |
| `gateway.max_inbound_age_seconds` | `300` | 0–3600 (`0` = nonaktif) |
| `access.bot_ids` | `[]` | tuple string; JID/nomor bot untuk deteksi mention |
| `limits.inbound_dedup_ttl_minutes` | `15` | 1–1440 |

`access.bot_ids` diperlukan karena `router.route()` mencocokkan JID mention dari WhatsApp; tanpa daftar ini deteksi mention hanya mengandalkan teks `@sanka` dan `reply_to_bot`. Bridge plan-4 tetap mengirim `mentions` apa adanya.

### Mode kegagalan

| Kejadian | Perilaku |
|---|---|
| Body bukan JSON / field cacat | `400` + kode reason; tidak ada nilai payload yang digemakan |
| Token salah/hilang | `401`; body tidak dibaca |
| `message_id` sama dikirim ulang | `200 duplicate`; tidak ada kerja kedua |
| Pesan lebih tua dari `max_inbound_age_seconds` | `200 ignored` reason `stale`; tidak masuk LLM |
| Grup tidak ada di allowlist | `200 ignored` reason `group_not_allowed`; tanpa balasan |
| Grup allowlist tanpa mention | `200 ignored` reason `not_mentioned` |
| DM dengan `dm_policy: pairing` dari nomor tak dikenal | `200 ignored` reason `dm_not_allowed`; satu notice per 24 jam |
| Rate limit pengirim habis | `200 ignored` reason `rate_limited`; satu notice per 10 menit |
| Pipeline penuh | `429 overloaded`; bridge retry |
| Bridge mati saat kirim | retry 3×, lalu buang item + log metadata; balasan lain tetap jalan |
| Bridge membalas `429`/`503`/`504` atau `5xx` | retry dengan `retry_after_ms`/`Retry-After` (maks 30 detik) memakai key yang sama |
| Bridge membalas `409` | dianggap sukses |
| Runtime melempar exception tak terduga | balasan generik + log tipe/traceback tanpa isi pesan |
| `agent is None` | balasan `"Fitur AI belum aktif."` |
| Server dimatikan saat antrean berisi | `stop()` memberi waktu `stop(timeout=5s)`; item tersisa dibuang dan dicatat jumlahnya |

### Keamanan

- **Batas kepercayaan**: hanya loopback + token bersama. Bridge dianggap proses tepercaya di host yang sama, bukan pengguna. Tidak ada endpoint yang menerima perintah administratif.
- **Tidak ada kebocoran isi**: log hanya `message_id`, digest 8 karakter pertama SHA-256 dari `chat_id`/`sender_id`, peran, route, hasil, durasi. Teks pesan, nama pengguna, dan token tidak pernah dilog. `/healthz` tidak memuat config.
- **Batas input**: body 64 KiB, teks 32.768 karakter, mention ≤ 32, id ≤ 200 karakter. Semua batas mengembalikan `400`/`413` tanpa memproses.
- **Hanya teks final**: semua keluaran melewati `Formatter`, dan `AgentReply` plan-2 hanya berisi `content`. `reasoning_content` tidak pernah masuk gateway karena tidak pernah masuk `AgentReply`.
- **Rate limit keluar** melindungi nomor WhatsApp dari pola spam (SECURITY.md). Tidak ada broadcast, tidak ada pengiriman ke chat di luar yang memicu pesan.
- **Secret**: token hanya dari env/`config/secrets.env` (0600); `config/sanka.yaml` tetap non-secret.

## Dependency baru

Tidak ada dependency runtime baru.

- `httpx` (sudah ditambahkan plan-2) dipakai untuk `HttpBridgeClient`; tes memakai `httpx.MockTransport`, tanpa jaringan.
- Server memakai stdlib: `http.server.ThreadingHTTPServer`, `json`, `threading`, `concurrent.futures`, `hmac`, `hashlib`, `logging`. Cukup karena hanya satu klien tepercaya di loopback, laju < 10 pesan/detik, tanpa TLS, dan tanpa routing kompleks. FastAPI/uvicorn akan menambah dependency dan siklus hidup ASGI tanpa manfaat yang sepadan untuk plan ini; bila kelak butuh webhook publik, penggantinya dibahas di ADR baru.
- Tes memakai `pytest` yang sudah ada.

## Langkah

1. Owner sudah menyetujui plan ini dan ADR-0018; status plan adalah `In Progress`. Baca `docs/plan/WAVE-0.md` untuk pembagian subagent.
2. Tambah key config baru di `sanka/config.py` + validasi (termasuk loopback dan nama env), perbarui `config/sanka.example.yaml`, tambahkan `gateway*` ke `[tool.setuptools.packages.find] include` di `pyproject.toml`, dan tambahkan tes di `tests/test_config.py`.
3. Implementasikan `gateway/normalizer.py` (`InboundEvent`, `MalformedPayload`, `normalize_inbound`) beserta tes validasi dan default.
4. Implementasikan `gateway/dedup.py` (`TtlRegistry`) beserta tes TTL, pembersihan, dan kapasitas.
5. Implementasikan `gateway/gate.py` beserta tes allowlist grup, kebijakan DM, stale, dan teks kosong.
6. Implementasikan `gateway/outbound.py`: `BridgeClient` protocol, `HttpBridgeClient` (`httpx`), `OutboundQueue` (FIFO per chat, round-robin, batas laju, retry, chunking via `Formatter`), lalu tesnya dengan bridge palsu dan jam palsu.
7. Implementasikan `gateway/dispatcher.py`: `Dispatcher`, `SubmitResult`, jalur command dan agent, notice pairing/rate limit, penanganan exception. Tes memakai `FakeRuntime` yang mengembalikan `AgentReply`.
8. Implementasikan `gateway/server.py`, `app.py`, dan `__main__.py`: handler HTTP, batas body, auth token, validasi loopback, perakitan komponen, dan graceful shutdown.
9. Tulis tes server pada loopback nyata (port `0`) dan tes end-to-end dengan bridge palsu: duplikat, grup tanpa mention, command, agent, formatter, outbound, tanpa kebocoran.
10. Jalankan `pytest -q` dan `python -m compileall sanka core gateway`. Lakukan smoke manual singkat: jalankan `python -m gateway` dengan config contoh + token uji, `curl 127.0.0.1:8099/healthz`, satu `POST /v1/inbound`, cek satu `send` tercatat pada bridge palsu, lalu hentikan. Tidak ada koneksi WhatsApp.
11. Perbarui `docs/ARCHITECTURE.md` (bagian gateway + kontrak), `docs/SECURITY.md` (token loopback, batas body), ADR-0018 bila kontrak berubah, `docs/LOG.md`; isi bagian Hasil dan ubah status plan menjadi `Done`.

## Tes

| File/tes | Skenario |
|---|---|
| `test_gateway_normalizer.py` | Payload lengkap → `InboundMessage` benar; `is_group` diturunkan dari `chat_id`; `timestamp` default waktu terima |
| `test_gateway_normalizer.py` | `mentions` dirapikan (duplikat dibuang, urutan tetap); field opsional absen memakai default |
| `test_gateway_normalizer.py` | Ditolak dengan kode tepat: `message_id_invalid`, `chat_id_invalid`, `sender_id_invalid`, `text_too_long`, `is_group_mismatch`, `mentions_invalid`, `timestamp_invalid`, `not_an_object` |
| `test_gateway_dedup.py` | `register()` pertama `True`, kedua `False`; setelah TTL lewat boleh lagi; kapasitas menyapu entri kedaluwarsa |
| `test_gateway_gate.py` | Grup di luar allowlist ditolak; grup di allowlist lolos; `group_policy: open` meloloskan semua |
| `test_gateway_gate.py` | `dm_policy: open` meloloskan peserta; `pairing` meloloskan owner/panitia dan menolak peserta; notice pairing sekali per 24 jam |
| `test_gateway_gate.py` | Teks kosong dan timestamp basi ditolak dengan reason yang benar |
| `test_gateway_outbound.py` | Satu balasan panjang dipecah formatter menjadi chunk berurutan dengan `idempotency_key` `"<id>:0"`, `"<id>:1"`, … |
| `test_gateway_outbound.py` | Dua balasan untuk chat yang sama terkirim berurutan; chat berbeda tidak saling memblokir urutan |
| `test_gateway_outbound.py` | Batas `outbound_per_minute` dihormati (jam palsu); `typing` dijatuhkan saat jendela penuh; `send` menunggu |
| `test_gateway_outbound.py` | `5xx`, `429`, `503`, `504` di-retry lalu sukses (jeda dari `retry_after_ms`); `409` dianggap sukses; `4xx` lain dibuang; tag reasoning hilang sebelum kirim |
| `test_gateway_dispatcher.py` | `message_id` duplikat tidak memproses ulang dan tidak menambah outbound |
| `test_gateway_dispatcher.py` | Grup tanpa mention → `ignored:not_mentioned`, nol panggilan runtime, nol outbound |
| `test_gateway_dispatcher.py` | `/ping` → `registry.dispatch` dipanggil dengan peran benar, balasan terformat terkirim |
| `test_gateway_dispatcher.py` | Pesan DM → `FakeRuntime.handle` dipanggil sekali dengan `Role` benar, balasan terkirim, typing composing/paused |
| `test_gateway_dispatcher.py` | Rate limit → satu notice, lalu `ignored:rate_limited`; runtime tidak dipanggil |
| `test_gateway_dispatcher.py` | Runtime melempar exception → balasan generik, worker tetap hidup; `agent=None` → "Fitur AI belum aktif." |
| `test_gateway_dispatcher.py` | `caplog` tidak memuat teks pesan, JID utuh, atau token |
| `test_gateway_server.py` | Tanpa token `401`; token salah `401`; `Content-Type` salah `415`; body > 64 KiB `413`; path lain `404`; `GET` ke `/v1/inbound` `405` |
| `test_gateway_server.py` | `/healthz` `200` tanpa auth; `POST /v1/inbound` valid → `202 accepted`; kiriman kedua id sama → `200 duplicate` (server loopback nyata, port 0) |
| `test_gateway_e2e.py` | Fake bridge + fake runtime + server nyata: DM `/ping` → satu `send` terformat; DM biasa → satu `send` dari agent; grup dengan mention → agent; grup tanpa mention → nol outbound; tidak ada teks reasoning/secret di respons maupun log |
| `test_gateway_e2e.py` | `listen_host: 0.0.0.0` ditolak saat `build_app`; `agent=None` tetap melayani command |

## Kriteria selesai

- `pytest -q` lulus semua (tes plan-1/plan-2 tetap hijau) dan `python -m compileall sanka core` tanpa error.
- Server hanya bisa start pada host loopback; `0.0.0.0` ditolak oleh validasi config dan oleh `GatewayServer`.
- `POST /v1/inbound` tanpa `X-Sanka-Token` yang benar selalu `401`, dan token tidak pernah muncul di log, respons, atau pesan.
- Tes end-to-end dengan bridge palsu membuktikan: duplikat diabaikan, grup tanpa mention diabaikan, command dibalas, agent dibalas, `Formatter` diterapkan, endpoint `send` dipanggil, dan tidak ada reasoning/secret yang keluar.
- Dedup memakai `message_id` dengan TTL dari config, dan keputusan final (termasuk ignored) tidak diproses ulang saat bridge retry.
- Pengiriman keluar berurutan per chat, memakai `idempotency_key` stabil, dan menghormati `limits.outbound_per_minute`.
- `config/sanka.example.yaml`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, ADR-0018, dan `docs/LOG.md` diperbarui; status plan menjadi `Done`.

## Yang perlu dipahami owner

- **Kenapa dua arah HTTP, bukan satu.** Bridge memegang koneksi WhatsApp dan mendorong pesan masuk; Python memanggil bridge untuk mengirim. Keduanya di loopback dengan token bersama, jadi tidak ada port yang terbuka ke jaringan.
- **Dedup in-memory adalah pilihan sadar.** Ia melindungi dari kerja LLM ganda dalam satu masa hidup proses. Yang melindungi dari pesan WhatsApp ganda adalah `idempotency_key` yang dihormati bridge (plan-4). Restart menghapus dedup; itu tercatat sebagai risiko, bukan bug.
- **Formatter tetap satu-satunya pintu keluar.** `enqueue_reply()` memformat semua teks, termasuk balasan command dan pesan yang sudah diformat runtime. Karena idempoten, tidak ada teks yang berubah dua kali.
- **Satu worker keluar.** Itu yang membuat urutan per chat dan batas laju mudah dibuktikan. Konsekuensinya satu kiriman lambat memperlambat chat lain; timeout 10 detik membatasi dampaknya.
- **Rate limit dicek sekali per pesan.** Command dicek di dispatcher, agent dicek di runtime. Kalau dicek di dua tempat, pengguna kehilangan separuh kuotanya tanpa sebab yang terlihat.
- **`pairing` di plan-3 = allowlist + notice.** Alur tukar kode butuh penyimpanan persisten dan command baru; itu belum ada di sini.
- **Thread HTTP tidak pernah menunggu LLM.** `submit()` hanya melakukan pengecekan murah lalu menyerahkan pekerjaan ke executor; bridge mendapat `202` segera.

## Risiko

- **Dedup hilang saat restart.** Bridge yang retry tepat setelah restart bisa membuat satu pesan diproses dua kali. Mitigasi: idempotensi kirim di bridge (plan-4) dan TTL dedup 15 menit. Persistensi ditinjau di plan-8.
- **Ketergantungan pada plan-2.** Bila `AgentRuntime.handle`, `AgentReply`, `SessionControl`, atau `secrets.get_secret` berubah saat plan-2 dikerjakan, plan-3 harus menyesuaikan protokol `AgentHandler`. Integrasi nyata baru boleh dianggap selesai setelah plan-2 `Done`.
- **Perbedaan dengan roadmap/ADR-0013 soal long-poll.** Plan ini memilih push + retry. Bila owner ingin long-poll seperti Hermes, yang berubah hanya sisi bridge dan satu kelas pengirim di Python; normalizer, dedup, dan gate tidak terpengaruh.
- **`pairing` belum lengkap.** Roadmap tidak menugaskan alur pairing interaktif ke plan mana pun. Plan-3 hanya menyediakan gerbang allowlist + notice; alur kode sekali pakai perlu plan/ADR tambahan (kemungkinan plan-8). Ini disebut agar tidak dianggap "sudah selesai".
- **Satu worker keluar bisa menjadi bottleneck** saat rekap panjang dipecah menjadi banyak chunk untuk banyak chat. Diukur setelah plan-4/5; bila perlu, worker per chat ditambahkan tanpa mengubah kontrak.
- **Clock skew Node vs Python** bisa menandai pesan sebagai `stale` bila `max_inbound_age_seconds` terlalu kecil. Default 300 detik memberi ruang; bridge dan Python berjalan di host yang sama.
- **`ThreadingHTTPServer` bukan server yang di-hardening untuk publik.** Pengamanannya adalah bind loopback + token. Jangan pernah mengekspos port ini, termasuk lewat reverse proxy.
- **Token bersama di environment** bisa dibaca proses lain milik user yang sama. Dapat diterima untuk host satu pengguna; manajemen secret menyeluruh ada di plan-8.

## Catatan perubahan

Belum ada.

## Hasil

Belum dikerjakan.
