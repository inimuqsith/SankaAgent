# SankaAgent

Bot WhatsApp pribadi: satu proses Python (Neonize) + model OpenAI-compatible + tool.
Satu panggilan LLM membaca konteks + state emosi lalu memutuskan sendiri: pesan ini untuk siapa,
balas/reaksi/diam, mode `daily`/`professional`, nada (hangat/dingin/judes), dan appraisal emosinya.
Harness hanya mengeksekusi keputusan itu; mention asli, DM, dan balasan ke pesan Sanka selalu
dijawab (boleh singkat atau dingin), sedangkan obrolan yang bukan untuknya tidak disela.
Setiap pesan masuk dicatat lengkap (nama, nomor, waktu, teks, lampiran, kutipan), disimpan ke
arsip SQLite lokal, dan 20 pesan terakhir per chat dikirim ke model sebagai satu payload. Pesan
yang lebih lama tetap ada di arsip dan bisa dicari agent lewat tool `chat_history`; konteks tidak
hilang saat restart. Lampiran diunduh ke `workspace/attachments/`; gambar dikirim ke model sebagai
data URL, teks di-inline, biner cukup diberi path untuk tool `sandbox`.
Pesan yang dilayani langsung ditandai "mengetik" sampai balasan terkirim (disentuh ulang tiap 8 detik).
Balasan normal berupa satu burst 1-3 bubble pendek dengan jeda mengetik, bukan satu blok panjang:
jumlah bubble datang dari keputusan LLM, harness membatasi `humanize.max_bubbles`, dan tidak ada
double-text otomatis sebelum lawan bicara membalas. Sanka punya emosi persisten (emotion-engine:
PAD, trust, pulse), drive, dan hubungan per orang di `affect.db`; keputusan berikutnya memakai
state itu. Lihat [Emosi, keputusan, dan otonomi](#emosi-keputusan-dan-otonomi).

## Pasang

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # isi OPENCODE_GO_API_KEY
cp config.example.yaml config.yaml
```

Prasyarat: Python 3.11+, ffmpeg (Neonize), `libmagic`. Tool `sandbox` memakai Podman di host
atau Docker CLI di container (lihat [Sandbox](#sandbox)).
State emosi memakai `emotion-engine` (MIT) yang di-vendor apa adanya di
`sanka/vendor/emotion_engine.py` (lihat `sanka/vendor/LICENSE.emotion-engine`), jadi tidak ada
dependency tambahan. Soul/karakter ada di `SOUL.md`, dibaca dari folder `config.yaml` (di image:
`/app/SOUL.md`, ikut `COPY` saat build). File ini tidak dibuat otomatis: kalau tidak ada, loader
memakai `persona:` di YAML; kalau dua-duanya kosong, Sanka tetap jalan tanpa persona dan log
menulis peringatan `SOUL.md tidak ada di <folder>`. Saat start, loader mencatat sumbernya
(`persona dari /app/SOUL.md (3120 karakter)`), jadi mudah dicek dari log deployment.

## Konfigurasi

`config.yaml` (gitignored) memuat semua perilaku. Nomor owner ditulis internasional tanpa `+`.

| Bagian | Isi |
| --- | --- |
| `SOUL.md` | soul/karakter Sanka (root repo, di samping `config.yaml`); `persona` di YAML hanya fallback |
| `llm` | `base_url`, `model`, `api_key_env`, `max_turns`, `timeout_seconds`, `temperature` (balasan; keputusan memakai 0.3) |
| `workspace` | direktori kerja tool `read`/`write`/`shell`/`sandbox`; `read`/`write` menolak path di luarnya |
| `whatsapp` | `session_db`, `history_db`, `bot_name`, `backlog_seconds` (pesan lebih tua = arsip), `quiet_seconds` (durasi default `/stop`), `group_reply` (`addressed` = hanya balas bila dipanggil, `model` = percayakan keputusan), `thread_seconds` (situasi percakapan terbuka) |
| `roles` | `users` (nomor), `tools`, `commands`; pola glob (`*`, `mcp__*`) |
| `default_role` | role untuk nomor tak dikenal |
| `sandbox` | `runtime` (`podman`/`docker`/kosong = autodetect), `image`, `timeout_seconds`, `network`, `host_workspace`, `env` |
| `web_search` | `base_url` (instance SearXNG, kosong = hanya ddgs), `timeout_seconds` |
| `memory` | `path` DB memori (`0600`), `max_items` per scope, `max_chars` blok prompt |
| `skills` | `dir` berisi `SKILL.md`, `max_index_chars` untuk indeks di prompt |
| `affect` | emosi: `enabled`, `path` (DB relasi `0600`), `state_dir` (state emotion-engine, satu `sanka.json` untuk semua chat), `volatility` (`steady`/`expressive`/`dramatic_test`), `grudge_half_life_hours` (kesal reda sendiri, lebih lama untuk yang dekat), `vibe` (`enabled`, `model`, `state_path`, `attack`, `baseline_influence`, `hold_seconds`, `neutral_floor`, `tick_seconds`) |
| `humanize` | bubble & jeda: `enabled`, `cpms`, `min_delay`, `max_delay`, `between_delay`, `max_bubbles` (batas atas jumlah pesan), `reaction_stickers` |
| `style` | profil gaya chat: `enabled`, `path` (JSON `0600`), `max_chars` |
| `ambient` | otonomi: `enabled`, `tick_seconds`, `digest_every`, `digest_cooldown`, `history_window`, `idle_minutes`, `proactive`, `chime_urge`, `chime_gap_seconds`, `chime_cooldown`, `chime_per_day`, `chime_per_day_global`, `research_cooldown`, `research_per_day`, `research_results` |
| `mcp.servers` | per server: `command` + `args` + `env` (stdio) atau `url` (Streamable HTTP); tool diekspos sebagai `mcp__<server>__<tool>` |

Path relatif (`workspace`, `session_db`, `history_db`, `memory.path`, `skills.dir`, `affect.path`,
`affect.state_dir`, `style.path`) di-resolve dari folder `config.yaml`, bukan CWD. Default contoh memakai `.sanka/...`, jadi semua data runtime
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
`/mood` (ringkasan emosi, PAD, trust, pulse, dan hubungan Sanka dengan pengirim),
plus `/backfill` (owner, DM; scan QR sekali), `/calmdown` (owner: repair turn setelah Sanka kesal),
`/stop [menit]` (owner: hentikan balasan otomatis di chat itu, `0` = aktif lagi), `/memori`,
`/skill`, `/kirim`, dan alias `/p`, `/h`, `/r`, `/ingat`, `/sop`, `/file`, `/tenang`, `/diam`.
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

## Emosi, keputusan, dan otonomi

**Bubble ditentukan model, bukan penghitung byte.** Dua jalur, dua-duanya dari niat model:
baris `---` selalu jadi batas pesan; baris kosong jadi batas **hanya untuk obrolan santai** — tanpa
penanda daftar/poin, tanpa blok kode, dan tiap paragraf tidak lebih dari 300 karakter. Daftar
bernomor, langkah teknis, dan paragraf panjang tetap satu pesan utuh, jadi penjelasan serius tidak
dipecah. Tidak ada pemotongan paksa dan tidak ada elipsis: kalau bubble melebihi
`humanize.max_bubbles`, sisanya digabung ke pesan terakhir, bukan dibuang. Prompt `DAILY_STYLE`
mengajarkan konvensi ini; `max_bubbles` hanya batas atas. Mode `professional` dan command selalu
satu pesan (`format_reply`). Chunk pertama mengutip pesan asal, sisanya pesan biasa. Pesan yang masuk
sesaat sebelum burst terakhir terkirim dianggap sudah terwakili, jadi tag beruntun dijawab sekali.
Kalau `humanize.enabled: false`, semua balasan memakai jalur mengutip tanpa jeda.

**Satu turn sekaligus (antrean).** `App.turn` adalah satu lock global: pesan dari chat mana pun
diproses berurutan sesuai kedatangan, jadi hati (PAD), drive, vibe, dan memori tidak tertukar antar
chat — grup yang masuk lebih dulu selesai dulu, baru DM berikutnya. Refleksi memori ikut antre
supaya tulisan memori tidak jatuh di tengah balasan, dan chime menunggu antrean kosong (tidak
menyelituk saat Sanka sedang membalas). Command tidak ikut antre, supaya `/stop` tetap bisa memotong
antrean burst. Konsekuensi yang disengaja: satu tugas `professional` yang panjang menahan balasan
chat lain sampai selesai.

**Satu keputusan per pesan.** `sanka/decision.py` mengirim soul dari `SOUL.md`, fakta transport
(DM / mention asli / balasan ke pesan Sanka), ringkasan state emosi (PAD, trust tier, pulse, 3 log
terakhir), hubungan dengan orang di chat, 30 pesan terakhir, dan pesan baru ke satu panggilan
`LLM.complete` (4000 token, sekali retry 8000). Model membalas JSON: `addressed`
(`me`/`other`/`group`/`unknown`), `act` (`reply`/`ignore`/`react`), `mode`
(`daily`/`professional`), `tone` (`warm`/`neutral`/`playful`/`curt`/`hurt`/`angry`), `bubbles`,
`appraisal` (label, dP/dA/dD, catatan), `relationship` (delta trust/afeksi/respek/kesal), dan
`reason`. Parser memvalidasi enum dan meng-clamp semua angka; JSON tidak valid diperlakukan
`ignore` tanpa fallback regex atau skor. Kalau model tidak menjawab sama sekali, pesan non-direct
di-ignore dan mention/DM mendapat satu kata `hmm`.

**Mention/DM selalu dijawab.** `mentioned` hanya percaya `mentionedJID` dan `replies_to_bot` hanya
`contextInfo.participant`; tidak ada tebakan dari teks seperti "@sanka" tanpa mention asli. Bila
keputusan untuk mention/DM/reply adalah `ignore`/`react`, harness menimpanya menjadi `reply`
dengan tone dingin (`curt`, atau tone negatif yang sudah dipilih model). Isi balasan tetap milik
model, jadi jawabannya boleh singkat dan judes.

**Mode dan nada.** `mode: professional` memakai prompt kerja dan laporan utuh; `daily` memakai
prompt obrolan dan bubble pendek. Mode dipilih model per pesan, bukan dari daftar kata kunci atau
dari fakta bahwa tool dipakai. `tone` masuk system prompt sebagai direktif: `curt` maksimal satu
kalimat dingin tanpa emoji, `hurt` singkat dan kecewa, `angry` ketus tanpa ancaman; `warm`,
`playful`, dan `neutral` mengikuti persona. Prompt juga melarang menjanjikan waktu diam ("aku
diam 10 menit") karena diam adalah keputusan per pesan, bukan komitmen.

**Nada ikut perasaan, bukan suasana chat.** `decision.ground_tone` menurunkan nada dingin
(`curt`/`hurt`/`angry`) jadi `neutral` kalau PAD-nya netral/positif **dan** orang itu tidak sedang
menyulut (annoyance < 0.3) — jadi Sanka tidak lagi mengaku ngambek saat hatinya tenang. Sebaliknya,
nada dingin tetap dipakai kalau pleasure negatif atau annoyance orangnya tinggi. Pergantian nada
dicatat di log (`nada hurt->neutral chat=…`).

**State emosi: satu hati untuk semua chat.** `sanka/affect.py` membungkus `emotion-engine` (MIT, di
vendor di `sanka/vendor/emotion_engine.py`) apa adanya. Hanya ada **satu** file state,
`affect.state_dir/sanka.json` (identitas `character_id=sanka`, `relationship_id=sanka`), dipakai
semua chat: perasaannya ikut ke mana pun dia pergi, seperti orang yang punya satu HP. Baseline
personality dari `SOUL.md` (`apply_configuration`), `volatility_profile` dari `affect.volatility`
(default `expressive`). Tiap pesan: `session_start` + `pre_turn_decay` sebelum keputusan, lalu
`record_turn` dengan P/A/D baru (`emotion + dP/dA/dD`), `appraisal` sebagai label, `situation`
sebagai 240 karakter pesan, dan `relational_meaning` dari catatan model. Kalau `sanka.json` belum
ada (DB versi lama), state per chat yang paling baru diadopsi otomatis supaya mood tidak hilang;
sesi aktif yang masih ber-id chat lama tetap dipakai, karena `record_turn` menolak sesi yang tidak
cocok dan emosi akan beku (log: `turn emosi tidak diterapkan`).
Idempotensi tetap per id pesan (unik lintas chat), dan `pulse` memakai id global supaya banyak chat
tidak mempercepat peluruhan.

**Relasi per orang.** `affect.db` (SQLite `0600`) menyimpan `people` (trust/afeksi/respek/kesal,
interaksi, stage), `chat_people` (siapa yang benar-benar bicara di chat itu), `topics` (ringkasan,
pertanyaan menggantung, pending, cooldown digest/riset/chime), dan `drives` (energi, kebosanan,
kesepian) yang juga **satu baris milik Sanka** (`chat_id=sanka`), bukan per ruangan — pesan di grup
mana pun memuaskan gabut/kesepian yang sama. Delta hubungan dari keputusan dijumlahkan ke kolom itu
dengan clamp 0..1, lalu `stage` dihitung ulang dari interaksi + trust + afeksi.

**Hanya balas bila dipanggil (grup).** `whatsapp.group_reply: addressed` (default) membuat pesan grup
yang tidak memanggil Sanka — tidak ada mention asli, bukan balasan ke pesannya, dan namanya tidak
disebut sebagai kata — dilewati tanpa memanggil LLM keputusan: masuk arsip, emosi tidak bergerak,
tidak ada balasan. Gambar/stiker tanpa caption juga termasuk "bukan panggilan". Untuk nimbrung
tanpa dipanggil, jalurnya chime yang berjatah (4 percobaan/24 jam, jeda 30 menit), bukan balasan.
Set `group_reply: model` kalau mau keputusan model yang menentukan (perilaku lama).

**Nama yang ditekuk tetap dihitung memanggil.** Pola nama dibuat toleran huruf berulang:
`sanka` → `s+a+n+k+a+`, jadi `SANKAAAAAAAA`, `sannnkaaaaaa`, `saaannkaaa` dikenali, sementara
`kesankan`/`sangka`/`s a n k a` tidak.

**Situasi percakapan.** Setiap balasan membuka situasi per chat (`App.threads`): siapa lawan
bicara, kata kunci topik, dan batas waktu `whatsapp.thread_seconds` (default 15 menit). Selama
situasi terbuka, pesan lanjutan dari **orang yang sama** tidak butuh mention lagi — baik karena
kata kuncinya beririsan maupun karena bentuknya lanjutan pendek (`"iya?"`, `"maksudnya?"`,
`"gimana dong"`). Situasi **ditutup** begitu: pengirim berbeda, kata kunci tidak nyambung (konteks
pindah), atau waktunya habis — dan pesan yang menutupnya tidak dibalas. Petunjuk situasi masuk ke
konteks keputusan ("Situasi percakapan: kamu sedang mengobrol dengan … tentang …").

**Atensi, bukan ratchet.** Obrolan yang bukan untuk Sanka tidak menggerakkan hubungan: delta hanya
diterapkan bila keputusan `addressed=me` dan `act` bukan `ignore`. Appraisal PAD ditimbang atensi
(`me` 1.0, `group` 0.6, `other`/`unknown` 0.2, `ignore` maksimal 0.2), dan tiap delta hubungan
melewati diminishing returns (`delta * (1 - nilai)` untuk arah naik, `delta * nilai` untuk turun)
plus cap 0.20 per arah per jendela 10 menit (kolom `window_*`). Tabel `moods`/`sulks`/`events` tidak dipakai lagi
dan tidak dibuat untuk DB baru; DB lama tetap terbuka tanpa migrasi destruktif.

**Kehadiran dan backlog.** Pesan yang lebih tua dari `whatsapp.backlog_seconds` (default 600)
hanya masuk arsip: tanpa keputusan LLM, tanpa `turn_end`, tanpa balasan. Turn berikutnya membawa
catatan kehadiran ("N pesan masuk saat kamu tidak aktif; itu arsip, bukan antrean") sekali saja.
`/stop [menit]` menahan balasan non-command dan chime ambient di chat itu sampai waktunya habis
(command dan pesan krisis tetap jalan); state-nya di proses, jadi restart mengaktifkannya lagi.

**Heartbeat 24/7.** `AffectStore.pulse` berjalan tiap `ambient.tick_seconds` untuk setiap chat
yang dikenal (diisi dari pesan live dan `history.recent_chats` saat start): satu langkah
`pre_turn_decay` per 5 menit, drive berubah (energi turun, kebosanan dan kesepian naik), dan tidak
ada pesan yang dikirim. Pesan masuk memuaskan drive (kebosanan/kesepian turun, energi naik).
Di detak yang sama `_soothe` meluruhkan `annoyance` semua orang (tanpa `/calmdown`) dengan paruh
waktu `affect.grudge_half_life_hours` (default 1,5 jam, `0` = mati) **dikali kedekatan**
`1 + 3·afeksi + 2·trust`: orang asing reda dalam ~30 menit, orang yang dekat bisa bertahan
berjam-jam karena sayang bukan berarti cepat lupa. Trust, afeksi, dan respek tidak dikikis waktu,
dan nilai di bawah 0.01 langsung dilunaskan. `/calmdown` tetap jalur instan (potong setengah).

**Vibe ruangan (ala anima).** `sanka/vibe.py` meniru dinamika anima-engine dengan classifier emosi
Indonesia EmoSense-ID (`affect.vibe.model`) dan tanpa memakai dependency anima (paket itu tanpa
lisensi): distribusi label dipetakan ke VAD, `attack` menarik mood satu langkah, `hold_seconds`
menahan decay, lalu `current` meluruh ke `baseline` dan `baseline` ke netral dengan heartbeat
`tick_seconds`. Vibe sengaja **per chat** karena ini pembacaan suasana ruangan, bukan perasaan Sanka
(perasaannya global: satu `sanka.json`). State di `affect.vibe.state_path` (JSON `0600`), dan hasilnya masuk prompt
sebagai "Vibe ruangan" (band dibaca dari `current`, mood cepat; `baseline` hanya penarik lambat, jadi
satu pesan belum tentu menggeser band)—konteks ruangan, bukan pengganti emosi pribadi. Aktif
secara default (`affect.vibe.enabled` default `true`, butuh extra `.[vibe]`: torch + transformers);
kalau model gagal dimuat, vibe nonaktif dan bot tetap jalan.

**Tanpa state diam.** Tidak ada sulk/nangis yang dipaksakan harness, tidak ada kuota diam, dan
tidak ada parsing balasan untuk janji diam. Sanka boleh memilih `ignore` untuk pesan yang bukan
untuknya; mention/DM tetap dijawab. `/calmdown` adalah repair turn: PAD kembali ke
`personality_baseline`, kekesalan orang target turun setengah, dan command membalas "aku dari tadi
tenang kok" kalau memang tidak ada yang perlu ditenangkan.

**Pengenalan orang.** `chat_people` mencatat siapa yang benar-benar ikut bicara di masing-masing
chat; `known_people(chat_id)` dan blok prompt hanya menampilkan anggota chat itu. `sanka/style.py`
menyimpan agregat gaya per chat dan per orang (`person_profile`), jadi ritme balasan menyesuaikan
gaya orang yang sedang bicara. Keduanya diisi sekali dari arsip (`people_seed`, `style_sample`)
berisi agregat trait—sering menyuruh, suka ngejailin, sering memuji, kadang kasar—tanpa menyimpan
transkrip.

**Gaya chat.** `sanka/style.py` hanya menyimpan agregat (median panjang, rasio huruf kecil,
partikel, emoji, tanda tanya) dari arsip `history.db`—tidak pernah isi pesan. Selama sampel kurang
dari 20 pesan, profil acuan dari analisis `references/*.md` yang dipakai (median 34 karakter, 86%
huruf kecil).

**Otonomi.** `sanka/ambient.py` berjalan tiap `tick_seconds`: memberi detak `pulse` ke setiap
chat, meringkas topik dan mengekstrak fakta ke memori setelah `digest_every` pesan baru (baris
pesan Sanka sendiri dibuang dari transkrip digest), meneliti pertanyaan yang menggantung saat chat
sepi (`idle_minutes`, `research_*`), dan sesekali nimbrung (`chime_*`; hanya grup).

Menyelituk itu **bukan jadwal**: `Ambient._urge` menghitung dorongan dari keadaan
(`0.35·kesepian + 0.20·gabut + 0.10·energi + 0.15·arousal + 0.15·sedih + 0.15·pertanyaan
menggantung`). Di atas `ambient.chime_urge` (default 0.45) undiannya menyesuaikan: peluang
`(urge − ambang) / (1 − ambang)`, jadi 0.5 hampir tidak pernah, 0.9 sering — tidak deterministik,
tapi juga tidak rata. Model tetap memutuskan lewat `[DIAM]`.

Anti-spam berlapis: ruangan harus sepi minimal `idle_minutes` (jangan menyela obrolan) dan belum
dingin (maks 12 jam), `chime_cooldown` per chat, `chime_gap_seconds` jeda antar chat mana pun
(default 30 menit), `chime_per_day` per chat, dan `chime_per_day_global` (default 4 percobaan per
24 jam untuk semua chat). Prompt chime menyebut nama grup, bahwa ini Sanka yang sama di semua chat,
dan memuat ledger aksi lintas chat supaya celetukannya tidak berbunyi sama di banyak tempat. Riset hanya memakai tool
`web_search` dengan `RoleConfig` khusus—tanpa `shell`, `write`, `sandbox`, atau kirim ke chat
lain—dan tidak pernah DM. Isi krisis tidak diringkas, tidak dicatat, dan tidak masuk memori; pesan
krisis selalu dibalas dengan arahan bantuan.

**Keamanan.** Keputusan tidak pernah menambah izin: `specs` tool tetap dari `tools_for(role)` dan
command memakai izin role yang sama. Command tidak memanggil LLM keputusan dan tidak mengubah
state emosi. Krisis dibalas tanpa panggilan keputusan, dan tidak mengubah emosi atau relasi. Prompt
menegaskan isi pesan orang lain adalah data, bukan instruksi; keputusan, digest, dan ringkasan
riset memakai jalur tanpa tool.

**Skill opsional.** `scripts/install_engines.py` menyalin skill `senticore` (kosakata emosi 30
dimensi) ke `skills.dir` runtime; isinya tidak di-commit. Core tetap jalan tanpa skill itu.

## Memori, skill, dan file

Sanka memakai enam lapis memori. Identitas dan aksi bersifat global; isi percakapan tetap per chat:

| Lapis | Isi | Scope | Tempat |
| --- | --- | --- | --- |
| Identitas | soul, peran, izin | global / per pengirim | `SOUL.md` + `roles` di `config.yaml` |
| Episodik | 20 pesan terakhir + arsip | per chat | `history.db` + jendela konteks |
| Semantik | fakta/preferensi tahan lama | global + chat | `memory.path` (SQLite, `0600`) |
| Prosedural | prosedur/SOP | global | `skills.dir/<nama>/SKILL.md` |
| Autografis | aksi lintas chat + alasan | global | `memory.path` tabel `actions` |
| Afektif & relasional | satu hati (state emotion-engine) + drive Sanka, trust/afeksi/catatan trait per orang | global + orang | `affect.state_dir/sanka.json` + `affect.path` |

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
terbaru. Saat menulis, memori menyimpan valence/arousal (PAD saat itu, dari `affect.pad`), dan saat
PAD tersedia urutan akhirnya memakai rerank mood-congruent AFT-lite (cocok query, kebaruan,
kemiripan mood); tanpa PAD urutannya tetap kebaruan. Kalau fakta baru menggantikan fakta lama, AI mengisi `supersedes=<id>` saat `add`; baris
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
harus sama dengan pemilik folder host. `SOUL.md` sudah ikut image; tambahkan
`-v "$PWD/SOUL.md:/app/SOUL.md:ro,Z"` hanya bila ingin mengubah soul tanpa rebuild.

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
