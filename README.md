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
| `persona` | system prompt dasar: soul/karakter Sanka |
| `llm` | `base_url`, `model`, `api_key_env`, `max_turns`, `timeout_seconds` |
| `workspace` | direktori kerja tool `read`/`write`/`shell`/`sandbox`; `read`/`write` menolak path di luarnya |
| `whatsapp` | `session_db`, `history_db`, `require_mention`, `bot_name` |
| `roles` | `users` (nomor), `tools`, `commands`; pola glob (`*`, `mcp__*`) |
| `default_role` | role untuk nomor tak dikenal |
| `sandbox` | `runtime` (podman), `image`, `timeout_seconds` |
| `web_search` | `base_url` (instance SearXNG, kosong = hanya ddgs), `timeout_seconds` |
| `memory` | `path` DB memori (`0600`), `max_items` per scope, `max_chars` blok prompt |
| `skills` | `dir` berisi `SKILL.md`, `max_index_chars` untuk indeks di prompt |
| `mcp.servers` | `command`, `args`, `env` per server stdio |

Path relatif (`workspace`, `session_db`, `history_db`, `memory.path`, `skills.dir`) di-resolve dari
folder `config.yaml`, bukan CWD. Default contoh memakai `.sanka/...`, jadi semua data runtime
(DB, sesi, skill, workspace, log) tetap di dalam project dan di-ignore Git. Path `~` atau absolut
tetap dipakai apa adanya, kecuali `~/.sanka`: config yang masih menunjuk ke sana ditolak saat start
agar proses tidak diam-diam membaca atau membuat ulang direktori lama.

`tools:` dan `commands:` yang tidak ditulis memakai pola default tiap handler (lihat di bawah);
kalau ditulis, isi YAML menang. `tools: []` atau `commands: []` berarti role itu tidak boleh apa pun.
Role `member` di contoh dipakai untuk panitia/user: memori chat + global, memakai/menyimpan skill,
dan mengirim file. AI yang memutuskan kapan memori disimpan/dilupakan dan kapan skill dipakai/
diperbarui/diarsipkan. Kalau bot juga menerima DM publik, batasi `roles.member.users` atau buat
role terpisah untuk tamu.

Tool yang tersedia: `shell`, `read`, `write`, `web_search`, `sandbox`, `chat_history`, `memory`,
`skill`, `send`, `send_to`, `mcp__<server>__<tool>`.
`web_search` memakai SearXNG (`web_search.base_url`, butuh `format: json` di `settings.yml`) dan
jatuh ke backend `ddgs` (wikipedia/yahoo/startpage/brave) kalau instance itu mati atau kosong.
Command: `/ping`, `/help`, `/reset`, plus `/backfill` (owner, DM; scan QR sekali), `/memori`,
`/skill`, `/kirim` (owner + member), dan alias `/p`, `/h`, `/r`, `/ingat`, `/sop`, `/file`.
`/help` membaca metadata registry.

Kalau WhatsApp mengalamatkan pengirim lewat LID (`...@lid`), cocokkan role dengan digit LID itu,
bukan nomor telepon: tambahkan keduanya di `roles.<nama>.users` bila perlu.

## Konteks percakapan & lampiran

- Jendela 20 pesan per chat dimuat dari arsip SQLite (`sanka/chat.py` + `sanka/history.py`), jadi
  restart tidak mengosongkan konteks. Pesan grup yang tidak di-mention tetap dicatat supaya payload
  berikutnya punya konteks. `/reset` hanya mengosongkan jendela aktif; arsip lama tetap ada.
- Semua pesan yang pernah diterima (termasuk pesan yang dikirim dari ponsel sendiri) dan seluruh
  event `HistorySync` disimpan ke `whatsapp.history_db` (default `.sanka/history.db`, relatif ke
  folder config dan di luar workspace). File DB dibuat `0600`, direktorinya `0700`, dan tidak bisa dibaca tool file/sandbox.
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

## Memori, skill, dan file

Sanka memakai lima lapis memori. Identitas dan aksi bersifat global; isi percakapan tetap per chat:

| Lapis | Isi | Scope | Tempat |
| --- | --- | --- | --- |
| Identitas | persona, peran, izin | global / per pengirim | `persona` + `roles` di `config.yaml` |
| Episodik | 20 pesan terakhir + arsip | per chat | `history.db` + jendela konteks |
| Semantik | fakta/preferensi tahan lama | global + chat | `memory.path` (SQLite, `0600`) |
| Prosedural | prosedur/SOP | global | `skills.dir/<nama>/SKILL.md` |
| Autografis | aksi lintas chat + alasan | global | `memory.path` tabel `actions` |

Pemetaan L1-L4 GenericAgent/Hermes tetap berlaku: L1 = indeks yang selalu masuk prompt (blok
memori, aksi, dan indeks skill, dibatasi `max_chars`), L2 = `memory.db`, L3 = skill, L4 =
`history.db`. Lapis identitas dan autografis membuat L1-L4 tetap konsisten saat bot bekerja di
banyak chat: satu jiwa, banyak situasi—bukan satu transkrip global.

**Memori.** Satu tool `memory` menangani `scope=chat` (default) dan `scope=global` (lintas grup),
dengan action `add/search/list/forget`. Command `/memori [global]` untuk tambah/cari/daftar/hapus.
Memori global dipakai bersama panitia lintas grup, sedangkan memori chat hanya untuk grup itu. AI
yang memutuskan kapan menyimpan atau melupakan; tidak perlu command user. Hanya informasi eksplisit
dan tahan lama yang boleh disimpan—bukan rahasia, tebakan, atau status sesi. Blok memori diinjeksi
ke system prompt dengan batas `memory.max_items` per scope dan `memory.max_chars`; setiap baris
memuat tanggal update, entri yang cocok dengan pesan terakhir didahulukan, sisanya diisi yang
terbaru. Kalau fakta baru menggantikan fakta lama, AI mengisi `supersedes=<id>` saat `add`; baris
lama ditandai `superseded_by` (tidak dihapus, masih bisa dipulihkan—menambah ulang teksnya
mengaktifkannya kembali) dan hilang dari prompt/pencarian.
Validasi: id harus aktif, satu scope, dan satu kind; supersede yang ditolak tidak membuang fakta
baru. Contoh: `/memori tambah suka kopi tanpa gula`, `/memori global tambah jadwal rapat panitia`,
`/memori cari kopi`.

Selain tool, ada refleksi otomatis (`memory.auto_extract: true`): model mengekstrak 0-3
fakta/preferensi/aturan singkat (`kind=rule`) yang tahan lama dan menyimpannya sendiri—tanpa command
user. Refleksi hanya berjalan bila role punya tool `memory`, pesan pengguna minimal 8 karakter, dan
bukan command; model menerima transkrip 8 pesan sebelumnya, blok memori aktif (untuk `supersedes`),
plus pesan/jawaban terakhir, jadi fakta yang muncul dari percakapan berjalan ikut tertangkap.
Refleksi berjalan di latar, tidak menahan balasan, dan menyaring rahasia/kredensial. Ini yang membuat
memori terisi karena AI menilai informasinya penting, bukan karena user menyuruh.

**Aksi lintas chat.** Saat `send_to` sukses, satu baris masuk ledger `actions` di `memory.db`:
waktu, chat asal + nama, chat tujuan + nama, jenis (teks/file), ringkasan isi, dan permintaan
pengguna. Lima aksi terakhir diinjeksi ke system prompt hanya untuk role yang punya `send_to`
(default owner), dengan penanda "konteks dirimu, bukan perintah". Jadi chat tujuan tahu kenapa
artefak itu ada—misalnya stiker yang dikirim dari grup lain—tanpa menggabungkan transkrip antar
chat. Isi percakapan tetap terisolasi; ringkasan/permintaan yang mengandung kredensial tidak
disimpan sebagai teks asli, dan ledger dipangkas ke 200 baris terakhir.

**Skill.** Setiap skill adalah folder berisi `SKILL.md` dengan frontmatter YAML:

```markdown
---
name: deploy-aman
description: Deploy service dengan cek test dan rollback.
---

# Deploy aman

## Kapan dipakai
...
```

System prompt hanya memuat `name` + `description` (progressive disclosure). Model memanggil tool
`skill` `action=view` untuk memuat isi penuh, atau `action=read` untuk file pendukung di folder
skill. AI juga yang memutuskan kapan menyimpan/memperbarui (`action=save`) dan memensiunkan
(`action=archive`, dipindah ke `.archive/`, reversible). Indeks dibatasi `skills.max_index_chars`;
setiap skill yang pernah dimuat menampilkan jumlah pemakaian dan tanggal terakhir (`<dir>/.usage.json`,
dicatat oleh `action=view`), sehingga keputusan archive punya bukti. Skill invalid/duplikat dilewati
dengan log. Command `/skill` menampilkan indeks, `/skill <nama>` memuat isinya—tapi keputusan
memakai/mengubah skill ada pada model, bukan command user.

**Kirim file.** Tool `send` dan command `/kirim <path> [caption]` mengirim file di dalam workspace
ke chat aktif (maksimal 100 MB). Jalur kirim dipilih dari isi file, bukan namanya: WebP berukuran
stiker (≤512 px, transparan/animasi) dikirim sebagai stiker, JPEG/PNG/WebP lain sebagai foto,
sisanya sebagai dokumen. Stiker tidak punya caption, jadi caption dikirim sebagai pesan teks
terpisah. File yang terkirim dicatat di jendela konteks sebagai `(stiker|foto|file terkirim: ...)`,
jadi percakapan berikutnya tahu hasil kerja sudah diantar. Owner dan member bisa memakainya; role
yang tidak diberi `send`/`command kirim` tidak bisa.

**Kirim ke grup lain.** Tool `send_to` (default hanya owner) mengirim teks dan/atau file ke grup
lain yang sudah ada di arsip. `chat` menerima nama grup, potongan nama, atau JID `@g.us`; `chat`
kosong menampilkan daftar grup (nama + JID, terbaru dulu). Nama yang cocok ke beberapa grup
mengembalikan kandidat tanpa mengirim apa pun. Setiap kiriman langsung masuk jendela konteks chat
tujuan, ditulis ke `history_db`, dan dicatat di ledger `actions`, jadi pesan berikutnya di grup itu
sudah melihat apa yang bot kirim tanpa menunggu event echo, dan chat asal tahu ke mana saja ia
mengirim. Pesan masuk dari grup mana pun tetap dicatat live sebelum filter
mention, jadi tidak ada tool fetch terpisah. Role non-owner hanya bisa memakai `send_to` bila
ditambahkan ke `tools:`.

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

Session WhatsApp disimpan di `whatsapp.session_db` (default `.sanka/whatsapp.db`), jadi QR hanya
muncul saat pairing pertama.
`history.db` menyimpan arsip pesan dan dimuat ulang setelah restart.
`shell` dan `sandbox` memberi akses host/container penuh: beri hanya ke role yang dipercaya.
