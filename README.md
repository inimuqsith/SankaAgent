# SankaAgent

Bot WhatsApp pribadi: satu proses Python (Neonize) + model OpenAI-compatible + tool.
DM selalu dilayani; grup hanya saat bot di-mention atau dibalas.
Setiap pesan masuk dicatat lengkap (nama, nomor, waktu, teks, lampiran, kutipan), disimpan ke
arsip SQLite lokal, dan 20 pesan terakhir per chat dikirim ke model sebagai satu payload. Pesan
yang lebih lama tetap ada di arsip dan bisa dicari agent lewat tool `chat_history`; konteks tidak
hilang saat restart. Lampiran diunduh ke `workspace/attachments/`; gambar dikirim ke model sebagai
data URL, teks di-inline, biner cukup diberi path untuk tool `sandbox`.
Pesan yang dilayani langsung ditandai "mengetik" sampai balasan terkirim (disentuh ulang tiap 8 detik).

## Pasang

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # isi OPENCODE_GO_API_KEY
cp config.example.yaml config.yaml
```

Prasyarat: Python 3.11+, ffmpeg (Neonize), `libmagic`. Podman dipakai tool `sandbox`.

## Konfigurasi

`config.yaml` (gitignored) memuat semua perilaku. Nomor owner ditulis internasional tanpa `+`.

| Bagian | Isi |
| --- | --- |
| `persona` | system prompt dasar |
| `llm` | `base_url`, `model`, `api_key_env`, `max_turns`, `timeout_seconds` |
| `workspace` | direktori kerja tool `read`/`write`/`shell`/`sandbox`; `read`/`write` menolak path di luarnya |
| `whatsapp` | `session_db`, `history_db`, `require_mention`, `bot_name` |
| `roles` | `users` (nomor), `tools`, `commands`; pola glob (`*`, `mcp__*`) |
| `default_role` | role untuk nomor tak dikenal |
| `sandbox` | `runtime` (podman), `image`, `timeout_seconds` |
| `web_search` | `base_url` (instance SearXNG, kosong = hanya ddgs), `timeout_seconds` |
| `mcp.servers` | `command`, `args`, `env` per server stdio |

`tools:` dan `commands:` yang tidak ditulis memakai pola default tiap handler (lihat di bawah);
kalau ditulis, isi YAML menang. `tools: []` atau `commands: []` berarti role itu tidak boleh apa pun.

Tool yang tersedia: `shell`, `read`, `write`, `web_search`, `sandbox`, `chat_history`,
`mcp__<server>__<tool>`.
`web_search` memakai SearXNG (`web_search.base_url`, butuh `format: json` di `settings.yml`) dan
jatuh ke backend `ddgs` (wikipedia/yahoo/startpage/brave) kalau instance itu mati atau kosong.
Command: `/ping`, `/help`, `/reset`, `/backfill` (owner, DM; scan QR sekali), plus alias `/p`,
`/h`, `/r`. `/help` membaca metadata registry.

Kalau WhatsApp mengalamatkan pengirim lewat LID (`...@lid`), cocokkan role dengan digit LID itu,
bukan nomor telepon: tambahkan keduanya di `roles.<nama>.users` bila perlu.

## Konteks percakapan & lampiran

- Jendela 20 pesan per chat dimuat dari arsip SQLite (`sanka/chat.py` + `sanka/history.py`), jadi
  restart tidak mengosongkan konteks. Pesan grup yang tidak di-mention tetap dicatat supaya payload
  berikutnya punya konteks. `/reset` hanya mengosongkan jendela aktif; arsip lama tetap ada.
- Semua pesan yang pernah diterima (termasuk pesan yang dikirim dari ponsel sendiri) dan seluruh
  event `HistorySync` disimpan ke `whatsapp.history_db` (default `~/.sanka/history.db`, di luar
  workspace). File DB dibuat `0600`, direktorinya `0700`, dan tidak bisa dibaca tool file/sandbox.
  Deduplikasi memakai `(chat_id, message_id)`.
- Nama chat disimpan di `chats.name` dari `HistorySync` (`Conversation.name`/`displayName`) dan
  `GroupInfoEvent`; DM memakai push name saat nama belum ada. Header payload, tool `chat_history`,
  dan ringkasan memakai nama asli, bukan menebak dari isi pesan.
- Nama pengirim diutamakan dari kontak WhatsApp (`full_name`/`first_name`) lewat
  `whatsmeow_contacts` + `lid_map`, lalu push name, lalu ID. Kontak diimpor read-only dari session
  DB saat start/connect; `HistorySync` juga membawa `inlineContacts`/`pushnames`. Jadi LID
  `20289710768283` tampil sebagai “Kak Fahri JC”, bukan angka.
- Tool `chat_history` hanya mencari chat yang sedang dipakai (`query`, `before`, `after`, `limit`),
  jadi isi grup lain tidak bocor ke jawaban. Pesan yang lebih tua dari 20 entri payload tetap bisa
  dicari; gambar/teks lampiran lama diunduh ulang saat masuk jendela payload.
- Saat pairing baru, Sanka meminta history sync 730 hari / 5 GB dan mengaktifkan group history.
  WhatsApp tetap yang memutuskan kiriman; pesan yang sudah tidak ada di ponsel tidak bisa diambil.
  Neonize 0.5.2 belum mengekspos request history on-demand, jadi sesi yang sudah terlanjur pairing
  sebelum fitur ini hanya mendapat pesan baru.
- Untuk backfill sesi lama tanpa mengganggu bot utama, jalankan perangkat sementara lalu scan QR.
  Dari WhatsApp, owner bisa mengirim `/backfill` di DM: bot mengirim gambar QR, menyimpan hasil sync,
  lalu logout dan menghapus sesi sementara. Alternatif terminal:

  ```bash
  .venv/bin/python -m sanka backfill
  ```

  Keduanya memakai `backfill.db` terpisah dan menulis ke `history.db`. Bot utama boleh tetap
  berjalan. WhatsApp membatasi jumlah perangkat tertaut; kalau penuh, hapus salah satu perangkat
  lama dulu di pengaturan WhatsApp.
- Tiap entri payload berbentuk `[YYYY-MM-DD HH:MM] nama (nomor):` diikuti teks, kutipan
  (`↳ membalas ...`), dan baris lampiran. Entri terakhir adalah pesan yang harus dijawab.
- Lampiran diunduh sebelum panggilan LLM ke `workspace/attachments/<chat>/<id>-<nama>`; file yang
  sudah ada dipakai ulang. Gambar (field `imageMessage` atau dokumen ber-MIME `image/*`) dikirim
  inline sebagai data URL, maksimal 6 gambar, 5 MB per file, 10 MB total; sisanya hanya diberi path.
- Lampiran teks (`text/*`, JSON/YAML/XML, atau ekstensi teks) di-inline untuk pesan terbaru; pesan
  lama cukup metadata + path. Teks atau lampiran yang dipotong disimpan utuh di
  `workspace/whatsapp/<chat>/<id>.txt` dengan penanda `read path="..."` (tool `read` menerima path
  itu langsung, `sandbox` memakai `/work/<path>`).
- PDF/audio/video tidak diparse Sanka: model diminta memeriksanya lewat `sandbox` (default hanya
  `owner`; role lain harus mencantumkan `sandbox` di `tools:`) atau melaporkan bahwa role pengirim
  butuh izin sandbox.
- Balasan yang memuat `@<nomor>` dikirim sebagai mention asli: JID diambil dari log chat, bentuk
  `@+62 ...`/`@62 ...` dikanonikkan jadi `@62...`, dan alamat yang tidak dikenal cuma jadi teks.
  Karakter isolasi bidi (`U+2068`/`U+2069`) pembungkus mention WhatsApp dibuang dari teks masuk
  maupun balasan.

## Menambah command atau tool

Command baru = satu modul di `sanka/commands/`, registry meng-import otomatis:

```python
from ..registry import Handler
from . import CommandContext

async def echo(ctx: CommandContext) -> str:
    return ctx.args

COMMAND = Handler(
    name="echo", description="Ulangi argumen.", fn=echo,
    aliases=("e",), usage="<teks>", defaults={"owner": True, "member": True},
)
```

Tool baru = satu modul di `sanka/tools/`, registry meng-import otomatis dengan kontrak yang sama;
`family` wajib salah satu dari `FAMILIES` dan nama file harus sama dengan `name`:

```python
from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _resolve

async def _stat(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    return f"{path}: {path.stat().st_size} byte" if path.is_file() else "error: file tidak ditemukan"

TOOL = Handler(
    name="stat", family="read", description="Ukuran file di workspace.", fn=_stat,
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    defaults=OWNER_MEMBER,
)
```

Handler tidak valid (nama, alias, `family`, skema `parameters`, atau `defaults` salah) menggagalkan
start dengan satu pesan berisi semua modul bermasalah. Modul helper diberi awalan `_`.
`defaults` memetakan nama role ke bool; role tanpa daftar `tools:`/`commands:` memakai default itu,
sedangkan `tools: []` / `commands: []` berarti tolak semua.

## Jalankan

```bash
python -m sanka               # pairing: scan QR di terminal
python -m sanka selftest      # cek tool + LLM tanpa WhatsApp
python -m sanka backfill      # perangkat sementara: tarik history lama, lalu logout
```

Session WhatsApp disimpan di `whatsapp.session_db`, jadi QR hanya muncul saat pairing pertama.
`history.db` menyimpan arsip pesan dan dimuat ulang setelah restart.
`shell` dan `sandbox` memberi akses host/container penuh: beri hanya ke role yang dipercaya.
