# LOG

## 2026-10-27 (emosi, gaya pendek, gate, otonomi)

- `sanka/affect.py`: `AffectStore` (SQLite `affect.path`, 0600/WAL/busy_timeout) menyimpan mood PAD +
  emosi OCC per chat, hubungan per orang (trust/afeksi/respek/kesal/notes), `sulks`, `topics`, dan
  `events` 200 baris. Appraisal memakai `affect-kernel` 0.1.0 apa adanya; delta kustom hanya untuk
  trust/afeksi/respek/kesal. Satu hinaan -> level 1 (`sulk_minutes`), escalate ke level 2, nangis
  bila distress+sadness tinggi. Sulk tidak turun hanya karena annoyance turun; berakhir lewat
  `/calmdown`, dua permintaan maaf/afeksi (`appease>=2`), atau `until`. Isi krisis tidak pernah
  ditulis ke `events`/notes; krisis tetap dibalas.
- `sanka/gate.py`: `ReplyGate` -> `reply|react|ignore`. `LLM.decide` (mood + hubungan + 6 pesan
  terakhir) yang memutuskan; skor deterministik jadi prior/fallback, `LLM.judge` untuk borderline
  saat `llm_decide` dimatikan. Rate limit 10 menit per chat/orang + cooldown menang atas mention;
  `max_consecutive_ignores` memaksa balas agar Sanka tidak ghosting. Reaksi stiker dibatasi 1/5 menit.
- `sanka/style.py`: agregat gaya dari `history.db` (median, huruf kecil, partikel, emoji) tanpa
  menyimpan teks; seed acuan `references/*.md` (median 34) saat sampel < 20. `history.py` menambah
  `resolve_person`, `people_seed`, `style_sample` (tanpa ubah schema).
- `sanka/format.py`: `reply_budget`, `human_chunks` (fence utuh, potong per kalimat/kata, `…`),
  `humanize_reply`, `typing_delay`. `wa.py` mengirim bubble: chunk pertama kutip pesan, berikutnya
  `send_message`, jeda mengetik per bubble; command tetap satu jalur lama.
- Double-text: `LLM.followup` + `parse_followup` (`[DIAM]`/duplikat ditolak) menambah maks
  `humanize.max_followups` bubble sampai `max_bubbles`; dilewati untuk command, krisis, dan saat
  sulk. Tanpa tool, jadi izin tidak bertambah.
- `sanka/ambient.py`: digest topik+fakta ke memori/notes, riset `web_search` saat sepi, chime grup
  sesekali, dan `neglect` (sedih maks 1/jam). Tool context hanya `("web_search",)`, tidak pernah DM.
- `sanka/guard.py`: satu tempat regex krisis/injeksi/sensitif/tensi/fun + `EXEMPT_COMMANDS`
  (`help`, `status`, `whoami`, `reset`, `stop`, `ping`, `calmdown`) dan `command_name`.
- Config baru `affect`/`gate`/`humanize`/`style`/`ambient` (semua default mati, `_bool` agar
  `"false"` tidak truthy); `config.yaml` + contoh menyalakannya. Persona ditulis ulang: laki-laki
  dengan ekspresi emosional feminin, bukan asisten, tidak mengaku AI. Command baru `/calmdown`
  (owner, alias `/tenang`).
- Budget token latar: model reasoning menghabiskan jatah sebelum menulis, jadi `max_tokens`
  kecil menghasilkan teks kosong (finish=length). `decide` 400, `judge` 600, `followup` 400,
  `chime` 300, `digest` 500, `research_summary` 250; teks kosong dicatat warning dan otomatis
  jatuh ke fallback deterministik, bukan diam-diam gagal.
- Verifikasi: `pytest -q` 147 lulus (baru: affect/gate/style/ambient + integrasi balasan dan
  `system_prompt`); `python -m sanka selftest` -> `PASS affect`, `PASS style`, `SELFTEST OK`.
- Risiko/sisa: kualitas keputusan gate dan bahan double-text bergantung model; biaya LLM latar bisa
  ditekan lewat `gate.llm_review: false`, `gate.llm_decide: false`, `ambient.digest_every`, dan
  `ambient.research_per_day`. Smoke di grup tester (ledekan, hinaan, /calmdown, digest, riset)
  belum dijalankan karena butuh sesi WhatsApp nyata.


## 2026-10-22 (gid socket docker + ownership)

- `Dockerfile`: `ARG DOCKER_GID=994` setelah `RUN useradd ... sanka`; `groupadd -g "${DOCKER_GID}"
  docker || true` lalu `usermod -aG docker sanka`, sebelum `USER sanka`. Nilai bisa ditimpa lewat
  `--build-arg DOCKER_GID=<gid>`; acuan host `stat -c %g /var/run/docker.sock`.
- `sandbox`: runtime `docker` menambahkan `--user <uid>:<gid>` agar file di `/work` milik uid bot,
  bukan root daemon. Podman tidak memakai `--user` (tetap `--userns=keep-id`).
- Exit != 0 dengan `permission denied` + `docker.sock` -> pesan actionable yang menyebut
  `DOCKER_GID` dan `stat -c '%g %a' /var/run/docker.sock`; tidak memuat environment.
- Verifikasi: `pytest -q` 85 lulus; image dibangun dengan `--build-arg DOCKER_GID=994`, di dalamnya
  `id` = `uid=1000(sanka) gid=1000(sanka) groups=1000(sanka),994(docker)` dan Docker CLI 27.5.1.
  Daemon Docker tidak ada di mesin dev, jadi `docker ps` dan ownership di `/work` live diuji di server.

## 2026-10-22 (sandbox podman/docker)

- Tool `sandbox` mendukung dua runtime: `podman` (flag `--userns=keep-id`, mount `:Z`) dan
  `docker` (tanpa keduanya). `sandbox.runtime` kosong = autodetect podman lalu docker; bila tidak
  ada, tool mengembalikan error berisi cara memperbaiki, bukan exception.
- `sandbox.host_workspace` (opsional) jadi source bind mount saat daemon ada di host (Docker/
  Dokploy): path `/app/...` tidak ada di host. Path ini tidak divalidasi `is_dir()` dari container
  karena namespace berbeda; nilai yang terlihat sebagai file tetap ditolak saat start.
- Isolasi baru: `--rm`, `--network none` (override `sandbox.network`), rootfs read-only + tmpfs
  `/tmp`, output 16 KB terakhir ditandai `(dipotong)`, timeout per panggilan tetap membunuh proses.
  Env container hanya `TZ`/`LANG` (+ `sandbox.env`); env CLI minimal, API key tidak ikut.
- `Dockerfile`: Docker CLI statis 27.5.1 di `/usr/local/bin/docker` (curl dipasang lalu dibuang).
  Podman tidak masuk image.
- Verifikasi: `pytest -q` 84 lulus; `python -m sanka selftest` PASS termasuk sandbox Podman; smoke
  langsung Podman: `/work` ter-mount, uid 1000, tmpfs writable, tulis workspace ok; resep unduh
  Docker CLI diuji di `python:3.12-slim` (Docker version 27.5.1). Docker daemon tidak ada di mesin
  dev, jadi jalur docker diuji lewat argv/mock.

## 2026-10-22 (deploy + arsip)

- `Dockerfile` produksi: `python:3.12-slim`, ffmpeg + libmagic + tzdata, install package, user uid
  1000, `STOPSIGNAL SIGINT` agar Docker stop menutup SQLite rapi. `.dockerignore` membuang `.sanka/`,
  `config.yaml`, `.env`, venv, cache, dan tests dari build context.
- `scripts/backup.py`: arsip `.sanka` + `config.yaml` + `.env`; DB disalin lewat sqlite backup API
  (aman saat bot hidup), `integrity_check`, checkpoint WAL, tar.gz 0600 + sha256 + MANIFEST.
- README: jalur Dokploy Application (GitHub auto deploy, bind mount `/opt/sanka/.sanka` dan
  `config.yaml`, replica 1/stop-first), cutover data, dan batasan container (`sandbox` Podman,
  SearXNG loopback, uid bind mount).
- Verifikasi: `pytest -q` 76 lulus. Image `sanka-agent:test` (781 MB) dibangun dalam 287 dtk;
  build pertama mengunduh ffmpeg (207 paket, 477 MB), layer berikutnya cache. Di dalam image:
  import neonize/magic ok, ffmpeg/ffprobe 7.1.5, uid 1000; start container mencapai
  "Sanka siap. Scan QR..." dan hanya gagal karena `--network none`.
- Arsip data/session dibuat dari bot yang sedang hidup: integrity_check ok, session berisi 1 device
  + 494 kontak, history 386 pesan / 3 chat. Untuk cutover final, hentikan bot lalu jalankan
  `python scripts/backup.py` lagi supaya snapshot benar-benar final.

## 2026-10-21

- Rewrite sederhana: satu proses Python (Neonize 0.5.2), tanpa gateway, tanpa bridge Node.
- Enam keluarga tool: `shell`, `read`, `write`, `web_search`, `sandbox` (Podman), `mcp`.
- Permission ala Discord: `roles` di YAML, pola glob, cek di satu choke point `tools.execute`.
- Riwayat percakapan in-memory (30 pesan/chat), `/reset` membersihkan.
- Tiga test smoke: role/glob, routing DM/grup, format balasan WhatsApp.
- Docs dipangkas jadi empat file: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`.
- Verifikasi: `pytest -q` 3 lulus; `python -m sanka selftest` PASS write/read, shell, sandbox
  (Podman 6.1.4, image python:3.12-slim), web_search, LLM (butuh header `x-opencode-session`
  dan User-Agent sendiri — tanpa itu OpenCode Go membalas 400 MissingSessionID).
- Tool calling nyata diuji ke model: prompt "baca selftest.txt" memicu tool `read` lalu jawaban
  berisi isi file. MCP diuji dengan server stdio lokal (`mcp__demo__tambah` -> `42`).
- Belum: pairing WhatsApp nyata (butuh scan QR dari ponsel owner).

## 2026-10-21 (registry handler)

- `sanka/registry.py`: satu bentuk `Handler` (name, aliases, description, usage, parameters,
  defaults per role, pattern) + `Registry` (nama/alias, deteksi duplikat, pola izin).
- Command pindah ke `sanka/commands/` dan di-import otomatis (`pkgutil`): `ping`, `help`, `reset`
  dengan alias `/p`, `/h` `/bantuan`, `/r` `/clear`. `/help` membaca metadata registry.
- Tool memakai `Handler` yang sama; `execute` tetap satu choke point (izin, alias, error, potong).
- Role tanpa `tools:`/`commands:` di YAML memakai default handler; daftar eksplisit tetap menang.
- Verifikasi: `pytest -q` 3 lulus, `selftest` PASS semua, `/p`, `/h`, `/r`, command tak dikenal,
  role tanpa daftar (default), dan modul command drop-in terdeteksi tanpa mengubah dispatcher.

## 2026-10-21 (balasan & pairing)

- Bug live: `client.reply_message(chunk, ev.Message)` melempar `AttributeError: Info` karena Neonize
  butuh wrapper event (`Info` + `Message`) untuk menentukan chat tujuan dan quote. Fix: oper `ev`
  utuh di `sanka/wa.py`.
- Test regresi `tests/test_reply.py` (3): DM memakai wrapper + quote ID/participant/teks, grup
  membalas ke JID grup bukan sender, pengirim LID memakai `SenderAlt`. Test memakai
  `NewAClient.build_reply_message` asli; hanya `send_message` diganti perekam — revert fix =
  3 test gagal dengan traceback yang sama.
- Aturan test di `AGENTS.md` dilonggarkan: suite tetap kecil, fokus kontrak repo, bukan menguji
  ulang perilaku library.
- Verifikasi: `pytest -q` 6 lulus; `python -m sanka selftest` PASS; owner mengonfirmasi balasan
  berhasil di DM maupun grup setelah pairing nyata.

## 2026-10-21 (tool auto-discovery)

- `sanka/tools.py` jadi package `sanka/tools/`: satu modul per tool (`read`, `write`, `shell`,
  `web_search`, `sandbox`, `mcp`) dengan `TOOL = Handler(...)` plus `family`; `pkgutil` meng-import
  otomatis seperti command. `ToolContext`, `execute`, dan `FAMILIES` tinggal di `__init__.py`.
- `sanka/registry.py`: `validate()` menegakkan kontrak handler (nama = nama file, pola nama/alias,
  deskripsi, `fn`/`pattern`, `family`, skema `parameters`, `defaults`). `discover()` mengumpulkan
  semua error plugin lalu raise `RegistryError` sekali; duplikat nama/alias juga diagregasi.
  `Registry.patterns` menerima `None` (default handler) beda dari `()` (tolak semua).
- `python -m sanka` membungkus import first-party: plugin invalid keluar dengan satu baris
  `sanka: handler tidak valid: ...` dan exit 1, bukan traceback. `run_bot` mencatat daftar
  command/tool hasil discovery dan role yang memakai wildcard.
- `RoleConfig.tools`/`.commands` menjadi `tuple | None`: field tidak ditulis = default handler,
  `[]` = tolak semua. `sanka/mcp.py` memberi `family="mcp"` pada handler dinamis.
- Test baru `tests/test_registry.py` (4): handler bawaan, tool drop-in, agregasi error, izin `[]`.
- Verifikasi: `pytest -q` 10 lulus; `python -c` menampilkan daftar command/tool hasil discovery;
  tool drop-in `echo_tmp` terdeteksi tanpa edit core; plugin invalid (`COMMAND = 1`, `TOOL = 1`)
  exit 1 dengan satu pesan; `python -m sanka selftest` PASS write/read, shell, sandbox,
  web_search, LLM, `SELFTEST OK`.

## 2026-10-21 (status mengetik)

- `sanka/wa.py`: pesan yang lolos `should_process` langsung mengirim presence `COMPOSING`
  (media `TEXT`) ke JID chat asal, disegarkan tiap `TYPING_REFRESH` (8 detik) lewat task kecil
  selama LLM/tool berjalan, lalu `PAUSED` di blok `finally` — termasuk saat handler gagal.
- Presence bersifat kosmetik: exception-nya ditelan dan dicatat `log.debug`, jadi balasan tetap
  terkirim walau server menolak update presence.
- Enum dari `neonize.utils.enum` (`CHAT_PRESENCE_COMPOSING`/`_PAUSED`, `..._MEDIA_TEXT`), bukan
  enum proto: kode Go neonize memakai `state.value` sebagai indeks slice, dan enum proto (1/2)
  akan out-of-range.
- Test `tests/test_reply.py` (2 baru): urutan `COMPOSING` -> balasan -> `PAUSED` ke JID grup, dan
  grup tanpa mention tidak menghasilkan presence sama sekali.
- Verifikasi: `pytest -q` 12 lulus; smoke refresh (LLM 90 ms, interval 20 ms) menghasilkan 5
  `COMPOSING` tanpa task tertinggal; presence yang selalu raise tidak menggagalkan balasan;
  `python -m sanka selftest` PASS.

## 2026-10-21 (konteks & lampiran)

- `sanka/chat.py` (baru): log 20 pesan per chat (`ChatMessage`/`Quote`/`Media`, `Sessions`),
  deskriptor media dari field proto (unwrap `viewOnce`/`ephemeral`/`documentWithCaption`), unduhan
  `download_any` ke `workspace/attachments/<chat>/` (file lama dipakai ulang), dan
  `build_user_content` yang merakit payload.
- Payload memuat nama, nomor, waktu lokal, teks, kutipan (`↳ membalas`), dan baris lampiran per
  pesan. Gambar dipilih berurutan (pesan terbaru -> kutipannya -> sisanya, maks 6 / 5 MB / 10 MB)
  lalu diinjeksi sebagai data URL; dokumen ber-MIME `image/*` ikut diinjeksi karena deteksi memakai
  MIME/ekstensi, bukan nama field. Teks panjang dipotong dan disimpan utuh di
  `workspace/whatsapp/<chat>/<id>.txt` dengan penanda `read path="..."`.
- `sanka/wa.py`: pencatatan terjadi sebelum routing (grup tanpa mention tetap masuk log, tanpa
  presence), kutipan diresolusi lewat `stanzaID`, `should_process(has_media=True)` menerima gambar
  tanpa caption, balasan bot dicatat sebagai entri `outgoing`, dan system prompt menjelaskan
  payload/gambar/izin sandbox. Riwayat gaya chat-completion dihapus; `HISTORY_LIMIT` 30 -> 20.
- `sanka/llm.py`: `run(system, user_content, ...)` menerima `str | list[dict]`; APIError 4xx pada
  payload bergambar diulang sekali tanpa gambar plus catatan path (bukan langsung `API_ERROR_TEXT`).
- Bug temuan test: `Info.VerifiedName` adalah message proto (bukan string), jadi pemakaian
  `Pushname or VerifiedName or ...` menghasilkan nama pengirim kosong. Nama sekarang dari `Pushname`
  dengan fallback digit nomor, dites lewat kutipan yang menampilkan `628222222222`.
- Test: `tests/test_context.py` (6) untuk log/payload/pemotongan/gambar/PDF/kutipan,
  `tests/test_reply.py` (2 baru) untuk riwayat grup dan kutipan dari log, `tests/test_smoke.py`
  (1 baru) untuk routing media tanpa caption. Total 22 lulus.
- Verifikasi: `pytest -q` 22 lulus; `python -m sanka selftest` -> `SELFTEST OK`.
- Smoke nyata dengan `deepseek-v4.1-flash`: PNG merah 64x64 dikirim sebagai `documentMessage`
  (`image/png`) -> balasan "Merah"; PDF => model memanggil `sandbox` 2x dan menjawab
  `KODE-RAHASIA-123`; teks 40 baris => model memanggil `read` pada path penanda dan menjawab
  "baris 40"; payload bergambar yang ditolak HTTP 400 diulang tanpa gambar. Role `member` tanpa
  `sandbox` ditolak (`error: tool 'sandbox' tidak diizinkan untuk role 'member'`).

## 2026-10-21 (izin role runtime)

- `config.yaml` (gitignored): `roles.owner.users` diisi nomor asli + LID dari session DB
  (`6285111242960`, `130799689031758`); `roles.member.tools` ditambah `sandbox` atas permintaan
  owner, jadi member = `read`, `web_search`, `sandbox`. `shell`/`write` tetap owner-only.
- Handler `sandbox` tetap `defaults=OWNER_ONLY`; role lain hanya dapat lewat daftar `tools:`
  eksplisit. README diperbaiki (bukan "owner-only" mutlak).
- Bukti: pengirim `628999999999` (member) dengan config asli -> `role=member`, model memanggil
  `sandbox` 2x dan menjawab isi PDF `MEMBER-OK-777`. Bot di-restart 23:54:37, tersambung kembali.

## 2026-10-21 (identitas bot di prompt)

- `sanka/wa.py`: `system_prompt(config, role_name, bots)` menambah baris identitas
  (`Identitasmu: <bot_name>, nomor <nomor HP + LID>. Mention @<nomor itu> ... adalah untukmu`).
  `handle_message` meneruskan `app.bot_digits`. Tanpa ini model membaca mention
  `@73041086251252` (LID bot) sebagai nomor orang lain, persis seperti balasan live 23.54
  ("mention-nya ke @+7... bukan ke aku").
- Test `tests/test_smoke.py`: prompt memuat nomor PN + LID bot (23 test lulus).
- Bukti live: group event dengan `mentionedJID` = LID bot -> "Itu akun aku sendiri, Sanka."
  Tanpa baris identitas, model justru mengarang pemetaan LID -> nomor ("terhubung ke 628567770837")
  padahal nomor itu tidak ada di payload.
- Bot di-restart 23:58:46 (PID 38536, tersambung) supaya prompt baru terpakai.

## 2026-10-22 (web_search SearXNG)

- `sanka/config.py`: `WebSearchConfig(base_url, timeout_seconds)`; key YAML `web_search`; kosong =
  hanya ddgs. `config.example.yaml` diberi contoh, `config.yaml` diarahkan ke
  `http://127.0.0.1:8080` (container `sanka-searxng` yang sudah ada).
- `sanka/tools/web_search.py`: `_searx` memanggil `GET /search?q=...&format=json` (urllib di
  `to_thread`, UA `sanka-agent/0.1`, timeout dari config), `searx_hits` memetakan JSON -> hit
  seragam dan membuang entri tanpa URL. Fallback ke loop `ddgs` (`WEB_BACKENDS`) bila instance mati,
  JSON ditolak, atau hasil kosong; log satu baris, tanpa traceback.
- Pengukuran (kueri "searxng vs duckduckgo"): SearXNG 41 hasil/0,70 s vs ddgs hanya 1 backend jalan
  (yahoo) 5 hasil/1,33 s; `backend="auto"` gagal total karena `html.duckduckgo.com` unreachable.
- Test: `tests/test_smoke.py` (+2) untuk `searx_hits` dan parsing YAML `web_search` (25 test lulus).
- Bukti live: tool dengan config asli -> 5 URL dari SearXNG (1,66 s); `base_url` diarahkan ke port
  mati -> log `searxng ... gagal (URLError...); fallback ke ddgs` lalu hasil ddgs tetap keluar.

## 2026-10-22 (mention asli & teks bersih)

- `sanka/chat.py`: `clean()` membuang karakter isolasi bidi (`U+2068`/`U+2069`, `U+200E/F`,
  `U+202A-E`) dan NBSP; dipakai `message_text()` dan `_flatten()` sehingga payload tidak lagi berisi
  token mention "kotor". `ChatMessage.sender_jid` menyimpan JID asli (`…@lid` / `…@s.whatsapp.net`)
  untuk mention balasan.
- `sanka/wa.py`: `_resolve_mentions()` mengubah `@<nomor>` di balasan jadi `@<digit>` kanonik dan
  mengumpulkan JID peserta chat; balasan dikirim sebagai `ExtendedTextMessage` dengan
  `contextInfo.mentionedJID` eksplisit (quote tetap lewat `reply_message`). `format_reply` hasilnya
  lewat `clean()` dulu. Prompt menambah baris: tulis `@<nomor>` persis, tanpa `+`/spasi.
- Tanpa flag `mentions_are_lids` global: JID disebut eksplisit, jadi pengirim LID dan nomor HP bisa
  jadi mention dalam satu balasan.
- Test: `tests/test_reply.py` (+2) untuk mention LID (bentuk `@+1 3079 96890 31758`) dan nomor HP
  plus token tak dikenal; `tests/test_context.py` (+1) untuk pembuangan isolasi; total 28 lulus.
- Bukti live: pesan grup dengan mention `@⁨Sanka⁩`, model menjawab `Halo Muqsith! @130799689031758`
  -> proto keluar `mentionedJID=['130799689031758@lid']`, `stanzaID` quote utuh, tanpa `U+2068`.
- "Le Watasi" di balasan 00.07 adalah artefak model (tidak deterministik, bukan dari payload);
  mitigasi: payload dan balasan sekarang bebas karakter isolasi + token mention kanonik.

## 2026-10-22 (read/write terkunci ke workspace)

- `sanka/tools/__init__.py:_resolve()` sekarang me-resolve path (termasuk symlink) lalu menolak
  apa pun di luar `workspace` dengan `PermissionError`: `/etc/passwd`, `../config.yaml`, dan
  `~/.sanka/whatsapp.db` yang dikirim member lewat `read` berubah dari bocor jadi
  `error: path di luar workspace: ...`. Sebelumnya path absolut/`~/...` dipakai apa adanya.
- `execute()` menangkap `PermissionError` tanpa `log.exception` supaya penolakan path yang wajar
  tidak membanjiri log dengan traceback; pesan tetap dipotong `MAX_ERROR`.
- Deskripsi parameter `read`/`write` dan tabel `workspace` di README menyebut batas ini.
- Test `tests/test_context.py:test_paths_confined_to_workspace`: path relatif dan absolut di dalam
  workspace tetap terbaca; `/etc/passwd`, `../catatan.txt`, dan symlink `escape -> /etc/passwd`
  ditolak; `write` juga tidak bisa keluar workspace.
- Verifikasi: `pytest -q` 29 lulus; `python -m sanka selftest` SELFTEST OK (write/read, shell,
  sandbox, web_search, LLM); smoke langsung dengan config asli: role member hanya punya
  `read, web_search, sandbox, mcp__*`, tiga path luar ditolak, `selftest.txt` tetap terbaca.

## 2026-10-22 (arsip pesan & backfill)

- `sanka/history.py` (baru): SQLite `history_db` per akun, tabel `messages` + FTS5, kunci
  `(chat_id, msg_id)`, WAL, `secure_delete`, file `0600` dan direktori `0700`. Semua pesan live
  (termasuk `IsFromMe` dari ponsel sendiri) dan seluruh `HistorySync` disimpan; proto media/quote
  ikut disimpan agar lampiran lama bisa diunduh ulang.
- `Sessions` menjadi write-through: 20 pesan terakhir dimuat dari DB setelah restart, `/reset`
  memakai cursor `reset_ts` sehingga hanya jendela aktif yang kosong, dan quote lama dicari lewat DB.
- `HistorySyncEv` didaftarkan di `run_bot`; ingest per batch 500 baris di thread terpisah. DeviceProps
  meminta 730 hari/5 GB, `requireFullSync` selama `full_sync_done` belum ada, dan field
  `supportGroupHistory` (15) disisipkan manual karena proto Python Neonize 0.5.2 belum punya field
  itu sementara binding Go-nya punya.
- Tool `chat_history` (`family=read`) mencari teks/nama di arsip **hanya untuk chat aktif**, dengan
  `query`, `before`, `after`, `limit`; prompt sistem memberi tahu model bahwa pesan lama ada di arsip.
- Backfill untuk sesi yang sudah terlanjur pairing: `/backfill` (owner, DM) mengirim QR sebagai
  gambar, memakai `backfill.db` terpisah, menulis ke `history.db`, lalu logout + hapus sesi. CLI
  `python -m sanka backfill` menyediakan jalur terminal.
- `config.py`: `whatsapp.history_db` (default `~/.sanka/history.db`) dan `secure_db()` untuk
  `session_db`/`history_db`; session DB lama yang tadinya `0644` diamankan ke `0600`.
- Verifikasi: `pytest -q` 35 lulus; `python -m sanka selftest` SELFTEST OK; restart live menulis
  11 pesan uji ke `history.db` dan tool `chat_history` mengembalikan arsip chat tersebut; smoke
  `python -m sanka backfill` menampilkan QR tanpa mengganggu sesi utama.
- Batas nyata: WhatsApp hanya mengirim history saat perangkat di-link; sesi yang sudah ada tidak
  bisa memutar ulang sync awal, dan Neonize 0.5.2 belum mengekspos request history on-demand
  (`SendPeerMessage` tidak ada di binding), jadi backfill memerlukan scan QR sekali.

## 2026-10-22 (nama chat)

- `chats` sekarang punya kolom `name`; DB lama dimigrasi otomatis dengan `ALTER TABLE` saat
  `HistoryStore` dibuka. `reset` mempertahankan nama, hanya menggeser `reset_ts`.
- Nama diisi dari `Conversation.name`/`displayName` saat ingest `HistorySync`, dari
  `GroupInfoEvent.Name` saat grup berubah, dan dari push name untuk DM yang belum punya nama.
  Nama yang sudah ada tidak ditimpa oleh pesan DM biasa.
- Header payload menjadi `Konteks WhatsApp "<nama>" (...)`, tool `chat_history` menampilkan
  `Arsip "<nama>"`, sehingga model dan laporan tidak lagi menebak dari isi pesan.
- Nama tiga chat yang sudah ada diisi dari koreksi owner: Tester Sanka & MapHb,
  ALD XXVII JHEPRET CLUB FOTOGRAFI, dan DM Muqsith.
- Verifikasi: `pytest -q` 37 lulus; migrasi DB lama diuji; tool langsung menampilkan
  `Arsip "Tester Sanka & MapHb"`; bot live sudah restart dengan skema baru.

## 2026-10-22 (nama pengirim dari kontak)

- `history.db`: tabel `contacts(jid, name, push_name)` dan `lid_map(lid, pn)`; LID/PN dinormalisasi
  ke JID penuh. `import_contacts()` membaca `whatsmeow_contacts` + `whatsmeow_lid_map` dari session
  DB secara read-only saat start/connect; `HistorySync` menambah `inlineContacts`/`pushnames`.
- Resolusi nama: kontak `full_name`/`first_name` → nama kontak via mapping LID→PN → push name → ID.
  `_refresh_sender_names()` memperbarui pesan lama yang masih berisi LID/angka atau push name,
  dengan index `messages(sender_jid)`; nama bot/outgoing tidak diubah.
- Live message memakai nama kontak bila tersedia; DM name hanya diisi jika belum ada.
- Bukti pada arsip: `20289710768283@lid` → “Kak Fahri JC”, `130799689031758@lid` →
  “Muqsith Utama”, grup ALD tetap 110 pesan.
- Verifikasi: `pytest -q` 39 lulus; tes inline contact + refresh kontak lama; bot live sudah
  restart dengan resolver kontak.

## 2026-10-22 (memori, skill, kirim file)

- `sanka/memory.py` (baru): SQLite `memory.db` (0600) untuk fakta/preferensi tahan lama.
  Satu tabel dengan `scope` `chat`/`global`; dedupe `(scope, kind, text)`, pencarian LIKE, hapus
  per id/query, dan blok L1 terbatas (`max_items`, `max_chars`) untuk system prompt. Role dengan
  tool `memory` menerima memori chat + global.
- Tool `memory` (owner + member) dengan `scope=chat|global` untuk `add/search/list/forget`. Command
  `/memori [global]` untuk tambah/cari/daftar/hapus. AI memutuskan kapan menyimpan/melupakan; prompt
  menegaskan hanya fakta eksplisit/tahan lama yang boleh disimpan.
- `LLM.reflect` + `App.reflect`: setelah turn (kecuali command), model mengekstrak 0-3 fakta/
  preferensi/prosedur tahan lama secara otomatis (`memory.auto_extract`, default aktif), lalu
  menyimpannya dengan scope chat/global. Berjalan sebagai task latar agar tidak menahan balasan;
  filter regex untuk kredensial/rahasia dan dedupe SQLite. Prompt sistem juga menyuruh model
  memutuskan sendiri kapan memuat/menyimpan skill dan memori, bukan menunggu command user.
- `sanka/skills.py` (baru): memindai `<skills.dir>/**/SKILL.md`, frontmatter `name`/`description`,
  indeks nama+deskripsi yang dibatasi (`max_index_chars`) untuk progressive disclosure; tool `skill`
  menangani `list/view/read/save/archive` untuk owner + member. Archive memindahkan folder ke
  `.archive/`, bukan menghapus; file pendukung dibatasi ke folder skill; skill invalid/duplikat
  dilewati. Command `/skill` untuk daftar/muat. Ini adaptasi L1/L2/L3/L4 GenericAgent + format
  SKILL.md Hermes.
- `sanka/tools/send.py` + `sanka/commands/kirim.py` + `App.send_file`: kirim dokumen dari workspace
  ke chat aktif (maks 100 MB), sukses dicatat sebagai `(file terkirim: ...)` di jendela konteks.
  `resolve_path()` diekstrak dari tool context agar command memakai batas workspace yang sama.
  `send`/`kirim` tersedia untuk owner dan member.
- `sanka/wa.py`: system prompt menerima blok memori + indeks skill; `ToolContext` membawa store
  memori/skill dan callback `send_file`. Memori (chat/global), lifecycle skill, dan kirim file
  tersedia untuk owner + member; AI yang memutuskan.
- `config.py` / `config.example.yaml`: bagian `memory` dan `skills`. `TASK.md` mencatat integrasi
  penuh (URL/PDF/audio/calendar/email) dan state tugas/crash recovery sebagai pekerjaan nanti.
- Verifikasi: `pytest -q` 56 lulus; `python -m sanka selftest` SELFTEST OK (termasuk memory dan
  skill); smoke store/tool/command/file dengan fake client lulus.

## 2026-10-22 (umpan balik memori & skill)

- `sanka/wa.py`: refleksi kini bergerbang—hanya role yang punya tool `memory`, pesan pengguna ≥8
  karakter, dan bukan command. Transkrip 8 pesan sebelumnya (dipotong 3000 karakter) ikut dikirim ke
  `LLM.reflect`, jadi fakta dari percakapan berjalan tertangkap. Konteks diambil sebelum balasan
  dikirim supaya jawaban bot tidak terhitung dua kali.
- `sanka/memory.py`: `context()` menerima `query`; entri yang cocok dengan pesan terakhir didahulukan,
  sisanya diisi yang terbaru, dan tiap baris memuat tanggal `updated_at`. Batas `max_items`/`max_chars`
  tidak berubah.
- `sanka/skills.py`: sidecar `.usage.json` (tulis atomik) mencatat `uses` + `last` tiap `view`; indeks
  menampilkan `(dipakai N×, terakhir YYYY-MM-DD)` sehingga `archive` punya bukti. Format `SKILL.md`
  tidak berubah.
- `sanka/llm.py`: prompt refleksi menegaskan `kind=rule` hanya untuk aturan singkat; prosedur bertahap
  adalah bahan skill, bukan memori.
- Uji baru: gate role, transkrip yang dikirim, skip pesan pendek, ranking query + tanggal, telemetri
  `.usage.json`. Verifikasi: `pytest -q` 59 lulus; `python -m sanka selftest` SELFTEST OK.

## 2026-10-22 (supersede memori)

- `sanka/memory.py`: kolom `superseded_by` + migrasi otomatis untuk DB lama (termasuk fallback bila
  proses lain menambah kolom lebih dulu). `add(..., supersedes=<id>)` memvalidasi id aktif, satu
  scope, satu kind, dan bukan dirinya sendiri; baris lama ditandai, tidak dihapus. Menambah ulang
  teks yang pernah digantikan mengaktifkannya kembali (`superseded_by=NULL`). `_select` hanya
  mengembalikan entri aktif, jadi prompt/pencarian otomatis bersih.
- `sanka/tools/memory.py`: parameter `supersedes` + pesan `#lama digantikan`; deskripsi tool
  menjelaskan kontraknya.
- `sanka/llm.py` + `sanka/wa.py`: refleksi menerima blok memori aktif ber-ID dan boleh mengisi
  `supersedes`; id divalidasi store. Jika supersede ditolak, fakta baru tetap disimpan tanpa
  supersede. Prompt sistem menyuruh model mengisi `supersedes` hanya saat mengganti entri lama.
- Uji: supersede menyembunyikan baris lama tapi menyisakan row, tolak beda scope/kind/id mati/self,
  migrasi DB lama, tool, parse id (`"7"` diterima, `true` ditolak), refleksi + fallback. Verifikasi:
  `pytest -q` 61 lulus; `python -m sanka selftest` SELFTEST OK; DB asli `~/.sanka/memory.db` sudah
  punya kolom `superseded_by`.

## 2026-10-22 (jalur kirim stiker/foto/dokumen)

- `sanka/wa.py`: `send_kind()` membaca header file, bukan nama. WebP dengan dimensi ≤512 px dan
  penanda alpha/animasi dikirim lewat `send_sticker`; JPEG/PNG dan WebP lain lewat `send_image`;
  sisanya tetap `send_document`. Parser menangani VP8X, VP8 lossy, dan VP8L tanpa dependensi baru.
- Caption stiker tidak didukung WhatsApp, jadi dikirim sebagai pesan teks setelah stiker; entri
  konteks tetap satu `(stiker terkirim: ...)`. Hasil tool kini menyebut jenisnya (`stiker`/`foto`/
  `file terkirim`) supaya model tahu apa yang benar-benar terkirim.
- Deskripsi tool `send`, `/kirim`, README, dan TASK diperbarui. Uji kontrak baru memakai header
  WebP sintetis: stiker 342 px, WebP 800×600 sebagai foto, JPEG sebagai foto, `.bin` sebagai dokumen.
- Verifikasi: `pytest -q` 63 lulus; `python -m sanka selftest` SELFTEST OK. Smoke file asli:
  `selfie-topi-hitam.webp` (342×342, animasi, 242 KB) terdeteksi stiker dan konversi Neonize
  menghasilkan WebP 512×512 animasi 411 KB (<500 KB); stiker statis 512×512 135 KB juga terdeteksi
  stiker.

## 2026-10-22 (kirim ke grup lain)

- `sanka/tools/send_to.py` (baru, `family=write`, default owner): target hanya grup yang sudah ada
  di arsip. `chat` menerima JID, nama persis, atau potongan nama; kandidat ganda/unknown
  dikembalikan sebagai daftar (nama + JID) tanpa mengirim. `text` dan/atau `path`+`caption`; path
  tetap dibatasi workspace lewat `_resolve`. `send` lama tetap untuk chat aktif.
- `sanka/history.py`: `groups(query, limit)` menyatukan grup dari `messages` dan `chats` (nama ikut,
  terbaru dulu) supaya target bisa dicari tanpa membuka isi grup lain.
- `sanka/wa.py`: `App.send_text(chat_id, text)` mengirim teks ke chat mana pun, memakai mention asli
  dari log chat tujuan, dan langsung `Sessions.append(... outgoing=True)` di chat tujuan — jadi
  jendela konteks + `history_db` grup itu realtime, bukan menunggu event echo. `ToolContext`
  membawa `send_file` (kini `chat_id` pertama) dan `send_message`; prompt hanya menyebut `send_to`
  bila tool itu ada di role.
- Pesan masuk dari grup lain memang sudah dicatat di `handle_message` sebelum gate mention, jadi
  tidak ada tool `fetch`; test `test_group_history_keeps_unaddressed_messages` tetap menjaga jalur
  itu. Kiriman keluar lintas grup ditutup test baru: resolusi potongan nama, penulisan DB chat
  tujuan, chat asal tidak tersentuh, target ambigu, unknown, daftar grup, dan penolakan role member.
- Verifikasi: `pytest -q` 65 lulus; `python -m sanka selftest` SELFTEST OK (owner punya `send_to`,
  member tidak). Smoke pada salinan `~/.sanka/history.db` asli: `chat="jhepret"` -> JID
  `120363422939208659@g.us`, transport palsu menerima teks, DB mencatat `outgoing=1` dengan nama
  `sanka`). Bot live di-restart 03:05:05 (PID 103256, log `~/.sanka/sanka.log`); startup menampilkan
  tool `send_to` dan tersambung ke WhatsApp.

## 2026-10-22 (ledger aksi lintas chat)

- `sanka/memory.py`: tabel `actions` (`ts`, `source`, `source_name`, `target`, `target_name`,
  `kind`, `summary`, `request`) di `memory.db`, index recency, `log_action` (pangkas ke 200 baris,
  buang isi/permintaan berkredensial), `recent_actions`, dan `actions_context` (5 aksi, ≤800 char).
  Filter kredensial menjadi `memory.SENSITIVE` dan dipakai bersama oleh refleksi.
- `sanka/tools/send_to.py`: setiap komponen kiriman yang sukses menulis satu baris ledger; kegagalan
  transport tidak menulis. Nama chat asal/tujuan diambil saat kirim supaya prompt tetap terbaca bila
  grup berganti nama. Pencatatan bersifat best-effort: error ledger tidak membatalkan kiriman.
- `sanka/tools/__init__.py` + `sanka/wa.py`: `ToolContext.request` membawa pesan pemicu; system
  prompt menerima blok `actions` setelah memori dan sebelum skill, hanya untuk role yang punya
  `send_to` (default owner). Chat tujuan kini tahu asal artefak tanpa menggabungkan transkrip.
- `README.md`: model lima lapis (identitas, episodik, semantik, prosedural, autografis) + penjelasan
  ledger; pemetaan L1-L4 tetap. `TASK.md`: item memori autografis.
- Uji: ledger cap/urutan/format/filter kredensial; `send_to` sukses mencatat source/target/nama/
  request dan gagal tidak mencatat; urutan blok prompt memori → aksi → skill.
- Verifikasi: `pytest -q` 68 lulus; `python -m sanka selftest` SELFTEST OK. DB asli sudah punya
  tabel `actions`. Bot live di-restart 03:22:41 (PID 108021) dan tersambung ke WhatsApp; daftar tool
  memuat `send_to`. Kirim lintas chat live tidak dipaksakan agar tidak mengirim ke grup nyata.

## 2026-10-22 (data runtime project-local)

- `sanka/config.py`: `_expand(value, base)` me-resolve path relatif dari folder `config.yaml`, bukan
  CWD; `~`/absolut tetap apa adanya. Default `memory.path` dan `skills.dir` menjadi `.sanka/...`,
  begitu juga fallback `load_config` untuk workspace, session DB, history DB, memori, dan skill.
- `config.example.yaml` + `config.yaml` lokal: semua path data pindah ke `.sanka/...` dengan catatan
  resolve; `.gitignore` menambahkan `.sanka/`. `README.md` menjelaskan data runtime di dalam project.
- Migrasi: bot dihentikan graceful, isi `~/.sanka` (whatsapp.db, history.db, memory.db, skills,
  workspace, sanka.log) dipindah ke `.sanka/`, lalu `~/.sanka` dihapus. Jumlah baris dipertahankan:
  386 pesan arsip dan 3 memori.
- Verifikasi: `pytest -q` 69 lulus (termasuk test resolve path relatif); `python -m sanka selftest`
  SELFTEST OK. Semua path hasil `load_config` berada di bawah root project. Bot live di-restart
  03:29:20 (PID 109867) dan tersambung ke WhatsApp.

## 2026-10-22 (audit pasca-migrasi)

- `sanka/config.py`: `_reject_legacy` menolak `workspace`, `whatsapp.session_db`, `history_db`,
  `memory.path`, dan `skills.dir` yang masih berada di bawah `~/.sanka`, dengan pesan agar pindah
  ke `.sanka/` relatif folder config. Test `load_config` lama memastikan start gagal, bukan
  diam-diam membuat ulang direktori lama.
- `sanka/tools/__init__.py`: `resolve_path` menolak path di bawah `~/.sanka` dengan pesan bahwa data
  sudah pindah ke `.sanka/` project; test path confinement memastikan pesan itu muncul, jadi arsip
  lama tidak memancing pembacaan ke direktori yang sudah dihapus.
- Audit runtime: `~/.sanka` tidak ada, tidak ada proses/fd yang membuka path lama, dan bot PID
  111844 hanya membuka `.sanka/{whatsapp,history,memory}.db*` + `.sanka/sanka.log`. Jendela 20 pesan
  aktif tidak memuat path lama.
- Skill `sanka-service-lifecycle` diperbarui: data di `.sanka/` project, resep start detached,
  verifikasi FD via `/proc/<pid>`, dan larangan membuat symlink kompatibilitas. Service harness lama
  (`sanka`, `sanka-gateway`, `sanka-bridge`, `fake-bridge`) sudah dihapus.
- Sisa teks `~/.sanka` hanya berupa guard/test/dokumentasi dan riwayat di `LOG.md`/`TASK.md`; arsip
  percakapan lama di `workspace/whatsapp/*.txt` bisa memuat path lama sebagai isi pesan, bukan
  konfigurasi atau pembacaan aktif.
- Verifikasi: `pytest -q` 70 lulus; selftest OK; bot live di-restart 03:35:02 (PID 111844) dan
  tersambung ke WhatsApp.

## 2026-10-22 (soul + kartu command)

- `config.yaml` + `config.example.yaml`: `persona` diisi ulang dengan soul Sanka dari SankaAgentOld
  (Si Kecil Penjelajah Sanankerto): identitas, gaya bicara, format WhatsApp, dan aturan jawaban.
  Tetap satu sumber di config, tanpa `SOUL.md` baru (AGENTS: hanya empat docs).
- `sanka/commands/help.py`: `/help` kembali menjadi kartu WhatsApp `🧭 *Command Sanka*`, berisi
  command sesuai izin role, alias, usage, dan penutup "ngobrol biasa tanpa slash"; baris role/tool
  dihapus. Deskripsi command memakai emoji.
- `sanka/commands/{ping,reset,kirim,memori,skill,backfill}.py`: balasan command memakai gaya lama
  (`🏓 Pong! Sanka hidup dan siap.`, `🧹 Siap! ...`, `🧠`/`🔎`/`✅`/`📚`/`📤`/`🗂️`); error serius
  tetap tanpa emoji. Daftar memori memakai `•`.
- `sanka/wa.py`: command tak dikenal memberi petunjuk `/help`; command terlarang memakai `🔒`.
- Uji: `test_help_renders_whatsapp_command_card` mengunci header/footer dan tidak adanya baris tool;
  ekspektasi balasan command tak dikenal diperbarui.
- Verifikasi: `pytest -q` 71 lulus; `python -m sanka selftest` SELFTEST OK; persona nyata 2354 char
  masuk system prompt; bot live di-restart (PID 113710) dan tersambung ke WhatsApp.

## 2026-10-22 (command observability)

- `sanka/commands/whoami.py`: sender JID, role, jenis/nama/JID chat, dan nomor bot; sender kini
  ikut di `CommandContext` (field `sender`, diisi dari event Neonize).
- `sanka/commands/status.py`: model, role, chat, jumlah arsip + jendela aktif, memori chat/global,
  skill, dan jumlah tool/MCP. Semua pembacaan best-effort; store rusak tidak memblokir balasan.
- `sanka/commands/tools.py`: daftar tool yang benar-benar diizinkan role, deskripsi dipotong 110 char.
- `sanka/commands/cari.py`: pencarian arsip chat aktif tanpa LLM, 10 hasil terbaru dulu, waktu +
  pengirim + teks 180 char.
- `sanka/commands/help.py`: `/help <command>` menampilkan fungsi, usage, aliases, dan menolak command
  yang tidak tersedia untuk role. Lookup registry dilakukan lazy agar discovery tidak sirkular.
- `/reset` mendapat alias `/new`. `MemoryStore.count(scopes)` menghitung entri aktif tanpa membaca
  seluruh isi.
- `config.example.yaml` + `config.yaml`: command member ditambah `whoami`, `status`, `tools`, `cari`.
- Uji: `tests/test_commands.py` mengunci output whoami/status/tools/cari/help detail; set registry
  diperbarui; `MemoryStore.count` diuji.
- Verifikasi: `pytest -q` 76 lulus; smoke handler nyata dengan store sementara menghasilkan semua
  kartu command yang diharapkan; `python -m sanka selftest` SELFTEST OK; bot live di-restart
  (PID 115972) dan tersambung ke WhatsApp.
