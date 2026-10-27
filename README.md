# SankaAgent

Bot WhatsApp pribadi: satu proses Python (Neonize) + model OpenAI-compatible + tool.
DM dan `@`-mention selalu dilayani; pesan grup lain diserahkan ke gate (LLM memilih
`reply`/`react`/`ignore`, nama yang disebut tanpa tag tetap boleh dinilai).
Setiap pesan masuk dicatat lengkap (nama, nomor, waktu, teks, lampiran, kutipan), disimpan ke
arsip SQLite lokal, dan 20 pesan terakhir per chat dikirim ke model sebagai satu payload. Pesan
yang lebih lama tetap ada di arsip dan bisa dicari agent lewat tool `chat_history`; konteks tidak
hilang saat restart. Lampiran diunduh ke `workspace/attachments/`; gambar dikirim ke model sebagai
data URL, teks di-inline, biner cukup diberi path untuk tool `sandbox`.
Pesan yang dilayani langsung ditandai "mengetik" sampai balasan terkirim (disentuh ulang tiap 8 detik).
Balasan normal berupa satu burst 1-3 bubble pendek dengan jeda mengetik, bukan satu blok panjang:
gate yang memutuskan berapa bubble, harness membatasi `humanize.max_bubbles`, dan tidak ada
double-text otomatis sebelum lawan bicara membalas. Sanka punya mood, drive, dan hubungan per orang
yang persisten (`affect.db`), bisa gabut/kesepian/ngambek/nangis, dan memutuskan sendiri mau
`reply`/`react`/`ignore` lewat gate LLM. Lihat [Emosi, gate balasan, dan otonomi](#emosi-gate-balasan-dan-otonomi).

## Pasang

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # isi OPENCODE_GO_API_KEY
cp config.example.yaml config.yaml
```

Prasyarat: Python 3.11+, ffmpeg (Neonize), `libmagic`. Tool `sandbox` memakai Podman di host
atau Docker CLI di container (lihat [Sandbox](#sandbox)).
Dependency tambahan untuk emosi: `affect-kernel` (Apache-2.0, zero-dependency), dipasang otomatis
oleh `pip install -e '.[dev]'`; import-nya hanya di `sanka/affect.py`.

## Konfigurasi

`config.yaml` (gitignored) memuat semua perilaku. Nomor owner ditulis internasional tanpa `+`.

| Bagian | Isi |
| --- | --- |
| `persona` | system prompt dasar: soul/karakter Sanka |
| `llm` | `base_url`, `model`, `api_key_env`, `max_turns`, `timeout_seconds`, `temperature` (balasan), `appraise_temperature` (pembacaan emosi) |
| `workspace` | direktori kerja tool `read`/`write`/`shell`/`sandbox`; `read`/`write` menolak path di luarnya |
| `whatsapp` | `session_db`, `history_db`, `require_mention`, `bot_name` |
| `roles` | `users` (nomor), `tools`, `commands`; pola glob (`*`, `mcp__*`) |
| `default_role` | role untuk nomor tak dikenal |
| `sandbox` | `runtime` (`podman`/`docker`/kosong = autodetect), `image`, `timeout_seconds`, `network`, `host_workspace`, `env` |
| `web_search` | `base_url` (instance SearXNG, kosong = hanya ddgs), `timeout_seconds` |
| `memory` | `path` DB memori (`0600`), `max_items` per scope, `max_chars` blok prompt |
| `skills` | `dir` berisi `SKILL.md`, `max_index_chars` untuk indeks di prompt |
| `affect` | emosi persisten: `enabled`, `path` (DB `0600`), `sulk_minutes`, `cry_minutes`, `silence_max_minutes`, `silence_max_ignores`, `allow_owner_sulk`, `decay_hours`, `max_appraisals_per_minute` |
| `gate` | keputusan balas: `enabled`, `reply_threshold`, `react_threshold`, `llm_decide`, `decide_temperature`, `max_decisions_per_minute`, `llm_review`, `review_low`, `review_high`, `max_reviews_per_minute`, `max_replies_per_10min`, `max_person_replies_per_10min`, `reply_cooldown_seconds` |
| `humanize` | bubble & jeda: `enabled`, `normal_chars`, `normal_parts`, `detail_chars`, `detail_parts`, `cpms`, `min_delay`, `max_delay`, `between_delay`, `max_bubbles`, `reaction_stickers` |
| `style` | profil gaya chat: `enabled`, `path` (JSON `0600`), `max_chars` |
| `ambient` | otonomi: `enabled`, `tick_seconds`, `digest_every`, `digest_cooldown`, `history_window`, `idle_minutes`, `proactive`, `chime_cooldown`, `chime_per_day`, `research_cooldown`, `research_per_day`, `research_results` |
| `mcp.servers` | per server: `command` + `args` + `env` (stdio) atau `url` (Streamable HTTP); tool diekspos sebagai `mcp__<server>__<tool>` |

Path relatif (`workspace`, `session_db`, `history_db`, `memory.path`, `skills.dir`, `affect.path`,
`style.path`) di-resolve dari folder `config.yaml`, bukan CWD. Default contoh memakai `.sanka/...`, jadi semua data runtime
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
Command: `/ping`, `/help [command]`, `/reset` (alias `/new`), `/whoami`, `/status`, `/tools`, `/cari`,
`/mood` (mood, energi, dan hubungan Sanka dengan pengirim),
plus `/backfill` (owner, DM; scan QR sekali), `/calmdown` (owner: menenangkan Sanka setelah
ngambek/nangis), `/memori`, `/skill`, `/kirim`, dan alias `/p`, `/h`, `/r`, `/ingat`, `/sop`, `/file`,
`/tenang`.
`/help` membaca metadata registry dan menampilkan kartu command; `/help <command>` menampilkan
usage, alias, dan izinnya untuk role pengirim.

Kalau WhatsApp mengalamatkan pengirim lewat LID (`...@lid`), cocokkan role dengan digit LID itu,
bukan nomor telepon: tambahkan keduanya di `roles.<nama>.users` bila perlu.

## Sandbox

`sandbox` menjalankan satu perintah shell di container terpisah dengan workspace di `/work`
(alias `container`, default hanya owner). Runtime dipilih dari `sandbox.runtime`:

- `podman` — host/laptop; mount memakai `:Z` (SELinux) dan `--userns=keep-id` agar file di
  workspace tetap milik uid pemanggil.
- `docker` — container/Dokploy; kedua flag Podman itu tidak dikirim.
- kosong — autodetect: `podman` bila ada di `PATH`, lalu `docker`. Bila tidak ada keduanya, tool
  mengembalikan error berisi cara memperbaikinya (bukan exception).

Isolasi default: `--rm`, `--network none` (isi `sandbox.network: bridge` bila butuh jaringan),
root filesystem read-only dengan tmpfs `/tmp`, dan output dipotong ke 16 KB terakhir dengan
penanda `(dipotong)`. `timeout` per panggilan (default `sandbox.timeout_seconds`) membunuh proses
runtime dan mengembalikan `error: timeout setelah N detik`.

Dengan runtime `docker`, container sandbox dijalankan sebagai uid/gid bot (`--user 1000:1000`)
supaya file hasil perintah di `/work` tidak menjadi milik root. Podman tidak memakai flag itu
karena sudah ditangani `--userns=keep-id`.

Container tidak mewarisi environment bot. Hanya `TZ` dan `LANG` yang diteruskan; tambahkan nama
lain lewat `sandbox.env: ["NO_PROXY"]` bila perlu. API key tidak pernah masuk argv/env sandbox.

### Podman vs Docker dan `host_workspace`

Bind mount diresolusi oleh daemon, bukan oleh proses bot. Kalau bot berjalan di container dan
runtime-nya adalah daemon Docker di host (Dokploy), `workspace` dari config
(`/app/.sanka/workspace`) tidak ada di host. Set `sandbox.host_workspace` ke path workspace versi
host, misalnya `/opt/sanka/.sanka/workspace`, supaya daemon me-mount folder yang benar. Bila
kosong, source mount memakai `workspace` dari config — benar untuk Podman yang berjalan di mesin
yang sama dengan bot.

Path `host_workspace` berada di namespace host sehingga tidak selalu terlihat dari container bot;
karena itu keberadaannya tidak dicek di sana dan path yang salah dilaporkan daemon. Nilai yang
terlihat tetapi berupa file tetap ditolak saat start.

Memberi container bot akses `/var/run/docker.sock` (atau socket Podman) setara memberi **root di
host**: siapa pun yang bisa memakai `sandbox` bisa me-mount seluruh filesystem host atau
menjalankan container privileged. Batasi `tools` role dan hanya lakukan bila memang dibutuhkan.

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

## Emosi, gate balasan, dan otonomi

Balasan normal bukan satu blok panjang: `humanize` memecahnya jadi 1-3 bubble pendek (maks
`max_bubbles` per turn), masing-masing dengan jeda mengetik dari panjang teks (`cpms`, dibatasi
`min_delay`/`max_delay`), dan chunk pertama tetap mengutip pesan asal. Jumlah bubble diputuskan
gate (`bubbles` di JSON keputusan, dibatasi `max_bubbles` dan kebutuhan panjang teks); satu pesan
masuk = satu burst, tidak ada bubble susulan otomatis sebelum lawan bicara membalas. Pesan yang
masuk sesaat sebelum burst terakhir terkirim dianggap sudah terwakili, jadi tag beruntun dijawab
sekali. Kalau `humanize.enabled: false`, semua balasan kembali memakai jalur lama (mengutip, tanpa
jeda).

**Dua engine: soul dan percakapan.** `sanka/affect.py` adalah soul engine: mood PAD + emosi OCC
per chat (`moods`), hubungan per orang (`people`), keanggotaan chat (`chat_people`), status diam
(`sulks`), drive (`drives`: energi/kebosanan/kesepian), ringkasan topik (`topics`), dan log singkat
(`events`) di `affect.path` (SQLite `0600`). Setiap pesan dibaca LLM lebih dulu (`APPRAISE_PROMPT`
+ `LLM.appraise`, `llm.appraise_temperature`) untuk menentukan intent (`APOLOGY`, `ABUSE`,
`AFFECTION`, `TEASE`, `VULNERABILITY`, `CURIOSITY`, `CHALLENGE`, `COMMAND`, `CASUAL`), arah pesan,
serta `hurt`/`warmth`; appraisal itulah yang menggerakkan mood dan sulk, bukan daftar kata. Regex di
`classify_intent_fallback` hanya cadangan saat LLM mati atau `affect.max_appraisals_per_minute`
habis, dan sengaja dipakai tanpa `hurt` sehingga tidak pernah memicu sulk dari tebakan. Appraisal
memakai paket `affect-kernel` (Apache-2.0) apa adanya; modul ini menambahkan delta trust/afeksi/
respek/kesal dan menurunkan label mood (`ngambek`, `nangis`, `kecewa`, `jahil`, `malu`, ...) yang
masuk prompt sebagai sikap. Hinaan yang diarahkan ke Sanka (`hurt` tinggi) membuatnya sulk
(`sulk_minutes`); kesedihan berlarut membuatnya nangis. Selama sulk Sanka tetap menjawab tag/DM,
tetapi menolak membantu: tanpa tool, tanpa memori, tanpa skill, tanpa rekap. Sulk berakhir karena
`/calmdown`, dua permintaan maaf/afeksi, atau waktu habis; `annoyance` tetap tersimpan sebagai
grudge, tetapi kesal ringan tidak lagi memicu diam. Kalau sulk membuatnya melewatkan pesan, Sanka
mengirim satu keluhan singkat (`hmm`) setelah `silence_max_ignores` pesan—bukan spam berulang.
`affect.allow_owner_sulk: false` membuat owner kebal sulk.

**Heartbeat 24/7.** `AffectStore.pulse` berjalan tiap `ambient.tick_seconds` untuk setiap chat yang
dikenal (diisi dari pesan live dan `history.recent_chats` saat start): mood meluruh ke baseline,
`annoyance`/`sadness` turun, drive berubah (energi turun, kebosanan dan kesepian naik), sulk
kedaluwarsa dihapus. Heartbeat tidak mengirim pesan; ia hanya menjaga state tetap hidup sehingga
Sanka bisa gabut atau kesepian tanpa pesan masuk. Pesan masuk memuaskan drive (kebosanan/kesepian
turun, energi naik).

**Pengenalan orang.** `chat_people` mencatat siapa yang benar-benar ikut bicara di masing-masing
chat; `known_people(chat_id)` dan blok prompt hanya menampilkan anggota chat itu. `sanka/style.py`
menyimpan agregat gaya per chat dan per orang (`person_profile`), jadi ritme balasan menyesuaikan
gaya orang yang sedang bicara. Keduanya diisi sekali dari arsip (`people_seed`, `style_sample`)
berisi agregat trait—sering menyuruh, suka ngejailin, sering memuji, kadang kasar—tanpa menyimpan
transkrip.

**Gate balasan.** `sanka/gate.py` memutuskan `reply`/`react`/`ignore`. Dengan `gate.llm_decide`
model menerima mood, drive, hubungan, 12 pesan terakhir (pesan Sanka dilabeli `kamu (Sanka)`),
status terakhirnya sendiri, dan pesan baru, lalu memilih sendiri (JSON
`{"act": ..., "bubbles": 1-3}`); skor deterministik (mention, nama disebut, pertanyaan, kata kedua,
topik, ledekan, kabar terakhir, dsb.) hanya prior/fallback saat LLM mati, `llm_decide` dimatikan,
atau sebagai nilai untuk chime otonom. Tag `@`/DM tidak bisa di-ignore: `ignore` dari model
ditimpa menjadi `reply` (sulk yang membuat balasannya menolak membantu), dan pesan seperti itu
tidak pernah diblokir cooldown maupun kuota. Cooldown dan rate limit (`max_replies_per_10min`,
`max_person_replies_per_10min`) hanya berlaku untuk pesan yang tidak ditujukan kepada Sanka, jadi
Sanka bisa ikut nimbrung tanpa mendominasi; `reply_cooldown_seconds: 0` mematikan jeda paksa dan
menyerahkan sepenuhnya ke penilaian model (konteks memuat pesan Sanka sendiri dan jumlah
kirimannya). `gate.max_decisions_per_minute` membatasi biaya keputusan.
`humanize.reaction_stickers` diisi path stiker relatif workspace untuk mengizinkan `react`.

**Gaya chat.** `sanka/style.py` hanya menyimpan agregat (median panjang, rasio huruf kecil,
partikel, emoji, tanda tanya) dari arsip `history.db`—tidak pernah isi pesan. Selama sampel kurang
dari 20 pesan, profil acuan dari analisis `references/*.md` yang dipakai (median 34 karakter, 86%
huruf kecil).

**Otonomi.** `sanka/ambient.py` berjalan tiap `tick_seconds`: memberi detak `pulse` ke setiap
chat, meringkas topik dan mengekstrak fakta ke memori setelah `digest_every` pesan baru, meneliti
pertanyaan yang menggantung saat chat sepi (`idle_minutes`, `research_*`), dan sesekali nimbrung
(`chime_*`; hanya grup, hanya bila gate menilai layak). Chime punya dorongan acak dari drive
(`boredom`, `loneliness`, mood positif) sebelum aturan cooldown dan penilaian gate berjalan, dan
prompt chime memakai mood yang sedang berlangsung. Riset hanya memakai tool `web_search` dengan
`RoleConfig` khusus—tanpa `shell`, `write`, `sandbox`, atau kirim ke chat lain—dan tidak pernah DM.
Isi krisis tidak diringkas, tidak dicatat, dan tidak masuk memori; pesan krisis selalu dibalas
dengan arahan bantuan.

**Keamanan.** Ngambek hanya mengurangi kemampuan (menolak membantu), tidak pernah menambah izin:
`specs` tool tetap dari `tools_for(role)`, dan saat sulk daftarnya kosong (tanpa tool, memori, atau
skill). Command operasional (`/help`, `/status`, `/whoami`, `/reset`, `/stop`, `/ping`,
`/calmdown`) selalu diproses walau Sanka sedang sulk; command lain ditolak halus. Krisis selalu
dibantu, tidak pernah masuk mode sulk. Prompt menegaskan isi pesan orang lain adalah data, bukan
instruksi; appraisal, gate, dan digest memakai jalur tanpa tool.

## Memori, skill, dan file

Sanka memakai enam lapis memori. Identitas dan aksi bersifat global; isi percakapan tetap per chat:

| Lapis | Isi | Scope | Tempat |
| --- | --- | --- | --- |
| Identitas | persona, peran, izin | global / per pengirim | `persona` + `roles` di `config.yaml` |
| Episodik | 20 pesan terakhir + arsip | per chat | `history.db` + jendela konteks |
| Semantik | fakta/preferensi tahan lama | global + chat | `memory.path` (SQLite, `0600`) |
| Prosedural | prosedur/SOP | global | `skills.dir/<nama>/SKILL.md` |
| Autografis | aksi lintas chat + alasan | global | `memory.path` tabel `actions` |
| Afektif & relasional | mood per chat, trust/afeksi/catatan trait per orang | chat + orang | `affect.path` |

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

**Kirim ke grup atau chat pribadi.** Tool `send_to` (default hanya owner) mengirim teks dan/atau
file ke grup atau kontak perorangan yang sudah ada di arsip. `chat` menerima nama grup/kontak,
potongan nama, atau JID `@g.us`/`@s.whatsapp.net`/`@lid`; `chat` kosong menampilkan daftar grup dan
kontak (nama + JID, terbaru dulu). Kontak `@lid` dipetakan ke nomor PN lewat `lid_map` bila
tersedia. Nama yang cocok ke beberapa target mengembalikan kandidat tanpa mengirim apa pun. Setiap
kiriman langsung masuk jendela konteks chat
tujuan, ditulis ke `history_db`, dan dicatat di ledger `actions`, jadi pesan berikutnya di chat itu
sudah melihat apa yang bot kirim tanpa menunggu event echo, dan chat asal tahu ke mana saja ia
mengirim. Prompt balasan mewajibkan tool call pada turn yang sama sebelum Sanka boleh mengaku
sudah mengirim (termasuk "cp kamu"). Pesan masuk dari grup mana pun tetap dicatat live sebelum filter
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

## Deploy (Docker/Dokploy)

Image produksi ada di `Dockerfile`; `.sanka/`, `config.yaml`, dan `.env` sengaja tidak masuk build
context. Container berjalan sebagai uid 1000, jadi bind mount data harus dimiliki uid itu.

```bash
podman build -t sanka-agent .
podman run -d --name sanka --restart unless-stopped \
  --userns=keep-id \
  --env-file .env \
  -v "$PWD/.sanka:/app/.sanka:Z" \
  -v "$PWD/config.yaml:/app/config.yaml:ro,Z" \
  sanka-agent
```

Di Docker rootful, buang `--userns=keep-id` dan sufiks `:Z` (SELinux) pada mount; uid container 1000
harus sama dengan pemilik folder host.

Dokploy (Application, bukan Compose):

1. Hubungkan repo `inimuqsith/SankaAgent`, branch `main`, build type **Dockerfile**.
2. Aktifkan **Auto Deploy** agar setiap push ke `main` memicu deploy ulang.
3. Isi environment `OPENCODE_GO_API_KEY`; jangan taruh di repo.
4. Bind mount `/opt/sanka/.sanka` -> `/app/.sanka` dan file `/opt/sanka/config.yaml` ->
   `/app/config.yaml`. Path relatif di config resolve dari folder config di container (`/app`), jadi
   `.sanka/...` menjadi `/app/.sanka` dan dipetakan ke `/opt/sanka/.sanka`.
5. Replicas **1**, update order **stop-first**, tanpa domain/port: bot hanya membuka koneksi keluar.
6. `config.yaml` dan `.env` tidak ikut image (lihat `.dockerignore`), jadi push/Deploy hanya
   mengganti kode. Setelah mengedit config di host, tekan **Reload** (restart container, tanpa
   rebuild); tombol **Deploy** baru perlu saat kode di repo berubah. DB baru (`affect.db`,
   `style.json`) dibuat di dalam `.sanka` yang dimount, jadi direktori itu harus writable oleh
   uid 1000.
7. Stiker reaksi dibaca dari `<workspace>/stickers/...` di dalam mount; salin file WebP-nya ke
   mis. `/opt/sanka/.sanka/workspace/stickers/`. Tanpa file itu, `react` otomatis menjadi
   `ignore` (log info), bukan error.

Cutover data:

```bash
# di mesin lama: hentikan bot dulu agar snapshot final dan tidak ada dua client
python scripts/backup.py            # arsip di ~/backups/*.tar.gz, DB via sqlite backup API
# kirim arsip ke server, lalu di server:
mkdir -p /opt/sanka && tar -xzf sanka-*.tar.gz -C /opt/sanka
chown -R 1000:1000 /opt/sanka && chmod 700 /opt/sanka/.sanka && chmod 600 /opt/sanka/.env /opt/sanka/.sanka/*.db
```

Satu session WhatsApp hanya boleh dipakai satu proses. Jangan jalankan bot server sebelum bot lokal
berhenti; kalau `whatsapp.db` tidak terbawa, server akan meminta pairing QR baru.

### Sandbox di Dokploy

Image sudah membawa Docker CLI, tetapi tool `sandbox` butuh daemon. Di Dokploy, jalankan bot
dengan daemon host hanya bila perlu:

- mount `/var/run/docker.sock:/var/run/docker.sock` dan pastikan uid 1000 bisa membukanya.
  Image membuat grup `docker` dengan gid yang sama dengan socket host dan memasukkan `sanka` ke
  grup itu. Default `DOCKER_GID=994`; samakan dengan host lewat build-arg saat build:

  ```bash
  # di host
  stat -c '%g %a' /var/run/docker.sock
  # build (ganti 994 bila hasil stat berbeda)
  docker build --build-arg DOCKER_GID=994 -t sanka-agent .
  ```

  Di Dokploy, isi build-arg `DOCKER_GID` pada pengaturan build. Jangan `chmod`/`chgrp` socket di
  host; kecocokan gid diselesaikan di image. Bila gid salah, `sandbox` mengembalikan pesan yang
  menyebut `DOCKER_GID` dan perintah `stat -c '%g %a' /var/run/docker.sock`.
- set `sandbox.runtime: docker`,
- set `sandbox.host_workspace` ke workspace versi host, bukan `/app/...`:

```yaml
sandbox:
  runtime: docker
  image: "docker.io/library/python:3.12-slim"
  network: none
  host_workspace: /opt/sanka/.sanka/workspace
```

Mount socket berarti container bot bisa menjadi root di host (lihat [Sandbox](#sandbox)). Tanpa
socket, `sandbox` mengembalikan error dari daemon; tool lain tetap berjalan dan bot tetap start.

`web_search.base_url` yang menunjuk `127.0.0.1:8080` tidak bisa dijangkau dari container; kosongkan
untuk fallback `ddgs`, atau arahkan ke SearXNG yang satu network. Docker mengirim `SIGINT` saat stop
(`STOPSIGNAL`) supaya SQLite ditutup rapi sebelum container diganti.
