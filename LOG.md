# LOG

## 2026-10-28 (gate temporal, /stop, atensi, AFT-lite, vibe ruangan)

- `sanka/config.py`: `WhatsAppConfig.backlog_seconds` (600) dan `quiet_seconds` (600) + loader;
  `VibeConfig` baru dan `AffectConfig.vibe` (default `enabled: false`), path di-resolve seperti
  path lain dan parent-nya dibuat saat load config. `config.example.yaml` mendokumentasikan semuanya.
- `sanka/wa.py::handle_message`: pesan dengan `when` lebih tua dari `backlog_seconds` hanya masuk
  arsip (`App.backlog[chat]`, tanpa LLM/`turn_end`); command lama juga tidak dieksekusi. Turn
  berikutnya menerima `_away_block(missed)` sekali ke `decide`+`system_prompt` (`Catatan kehadiran`).
  Gate `App.quiet` menahan balasan non-command dan krisis tetap jalan.
- `sanka/commands/stop.py` (owner, alias `/diam`): `/stop [menit]` (default `quiet_seconds/60`,
  maks 1440) menulis `App.quiet[chat]`, `/stop 0` melepasnya. `sanka/ambient.py::_chime` menerima
  predikat `quiet` dari `run_bot`, jadi chime ambient ikut diam.
- `sanka/affect.py`: `PAD_WEIGHT` (me 1.0 / group 0.6 / other,unknown 0.2 / ignore max 0.2)
  menimbang dP/dA/dD; delta hubungan hanya untuk `addressed=me` dan `act != ignore`, lewat
  `_inertia` (diminishing returns) dan cap `WINDOW_LIMIT` 0.20 per arah per `WINDOW_SECONDS` 600
  (kolom `window_start`/`window_trust`/`window_affection`/`window_respect`/`window_annoyance`,
  dimigrasi `_migrate`). `pad(chat_id)` membaca PAD untuk penandaan memori.
- `sanka/memory.py`: kolom `valence`/`arousal` (migrasi tanpa menyentuh baris), `add(...)` menormalkan
  dan menyimpan mood (`COALESCE` saat upsert), `_ranked`/`context(mood=...)` memakai skor
  cocok-query + kebaruan + kongruensi mood (AFT-lite); tanpa mood urutan tetap kebaruan.
  Refleksi (`App.reflect`) dan tool `memory` mengisi valence/arousal dari `affect.pad`
  (`ToolContext.affect` baru di ujung signature).
- `sanka/vibe.py` baru (clean-room, tanpa dependency anima): `VAD` dari 8 label EmoSense-ID,
  `advance` (current→baseline, baseline→netral, hold), `VibeRunner` (heartbeat, classifier HF lazy
  di thread, state JSON `0600` per chat, fail-open). `sanka/wa.py` memanggil `anima.observe` sebelum
  keputusan dan menyisipkan blok vibe ke konteks keputusan + system prompt; `App.close` menutupnya.
  `pyproject.toml` extra `vibe` (torch, transformers<6), `Dockerfile` memakai torch CPU + cache
  model di `/app/.sanka/hf`.
- Burst yang jujur: `covered` tidak lagi "semua pesan di sesi" tapi isi payload; pesan yang menyusul
  sebelum payload dirakit ikut masuk payload (jadi ikut terjawab), pesan yang datang setelahnya
  dapat turn sendiri.
- Verifikasi: `pytest -q` 189 lulus; `python -m sanka selftest` SELFTEST OK (sandbox SKIP: docker
  tidak ada). Jalur fail-open diuji di venv Python 3.15 (tanpa torch): log
  `vibe: model ... gagal dimuat; vibe nonaktif`, `observe` -> "".
- Model asli diuji di container `python:3.12-slim` (torch CPU + transformers 5.21, cache HF):
  muat 4-5 detik, "kesel/marah" -> `anger` 0.97 dan "senang" -> `joy` 0.99. Probe menemukan
  `_describe` membaca `baseline` sehingga blok selalu "netral"; sekarang band dibaca dari
  `current`, hasil probe: pesan kesal ke-2 -> "negatif", pesan senang ke-2 -> "positif"
  (`baseline` tetap bergerak lambat sesuai desain). Satu pesan belum menggeser band karena
  `attack` 0.35 (ekspektasi "satu pesan langsung negatif" di plan tidak konsisten dengan
  `attack` 0.35 dan band 0.35/0.6).
- Kesal reda sendiri di detak jantung: `AffectStore._soothe` (dipanggil `pulse` di gate 5 menit
  yang sudah ada) meluruhkan `people.annoyance` dengan `0.5 ** (jam / grudge_half_life_hours)`
  (default 1,5 jam -> label musuh dari 0.9 hilang dalam ~35 menit, `0` = mati), lalu melunaskan
  nilai di bawah `SOOTHE_FLOOR` 0.01. Hanya
  annoyance yang meluruh; trust/afeksi/respek tidak disentuh. Label `musuh` (>= 0.75) karena itu
  bisa turun sendiri tanpa `/calmdown`, dan `/calmdown` tetap jalur instan.
- Chime ambient jadi pemicu emosional, bukan timer: `Ambient._urge` menghitung dorongan dari
  loneliness/boredom/energy + arousal + sedih (pleasure negatif) + pending, dibandingkan
  `ambient.chime_urge` (default 0.45); `random.random()` dan basis mati 0.10 dibuang. Gerbang
  "chat baru aktif <30 menit" diganti "chat sepi >= `idle_minutes`" (maks `CHIME_QUIET_WINDOW`
  12 jam) karena gate lama membuat drive selalu di-reset pesan sehingga komponen emosi mustahil
  tumbuh. Cooldown/plafon harian tetap sebagai pengaman anti-spam.
- Chime tidak deterministik dan berjatah: peluang = `(urge - chime_urge) / (1 - chime_urge)`, jadi
  makin kuat perasaannya makin mungkin menyelituk, dan model tetap bisa memutuskan `[DIAM]`.
  Ditambah rail global `chime_gap_seconds` (default 1800) dan `chime_per_day_global` (default 4
  percobaan / 24 jam untuk semua chat) di samping cooldown + plafon per chat.
- Prompt chime menyebut nama grup, identitas tunggal (Sanka yang sama di ALD, grup tester, Sunan
  Dahlan, DM — hanya tempatnya beda), nilai dorongan, dan `memory.actions_context()` supaya tidak
  menyalin celetukan yang sama ke banyak chat.
- Satu hati, bukan satu per ruangan: `_state_path` selalu `state_dir/sanka.json` dengan
  `relationship_id=sanka`, sesi tetap satu, `drives` disimpan di baris `chat_id=sanka`, dan id
  event `pulse`/`session` memakai `sanka` supaya banyak chat tidak mempercepat peluruhan. Migrasi
  di `_migrate` mengadopsi state per chat terbaru + drive terbaru saat DB lama dibuka. Vibe tetap
  per chat karena itu pembacaan suasana ruangan, bukan perasaan Sanka.
- `_soothe` sekarang global (tanpa filter chat) dan paruh waktunya dikali kedekatan
  `1 + 3*afeksi + 2*trust`: kesal pada orang dekat bertahan lebih lama, orang asing reda cepat.
- Gate balasan grup: `whatsapp.group_reply` default `addressed` — tanpa mention asli, balasan ke
  pesan Sanka, atau namanya disebut sebagai kata, pesan grup tidak memanggil `decide` sama sekali
  (arsip + `decision(act=ignore, reason="grup tanpa panggilan")`), jadi tidak ada balasan dan emosi
  tidak bergerak. `group_reply: model` mengembalikan perilaku lama (percaya keputusan). Nimbrung
  tanpa panggilan tetap lewat chime yang berjatah.
- Bubble ditentukan model: `human_chunks`/`humanize_reply` memakai baris `---` sebagai batas pasti,
  dan baris kosong sebagai batas hanya untuk obrolan (tanpa penanda daftar, tanpa blok kode, tiap
  paragraf <= 300 karakter). Tanpa pemotongan paksa dan tanpa elipsis; kelebihan bubble digabung
  ke yang terakhir. `---` di dalam blok kode diabaikan. `DAILY_STYLE` menjelaskan konvensinya (obrolan santai boleh banyak bubble,
  penjelasan serius/daftar tetap satu pesan). `reply_budget` beserta `normal_chars`/`normal_parts`/
  `detail_chars`/`detail_parts` dihapus karena sudah mati; `max_bubbles` jadi batas atas.
- `decision.ground_tone`: nada `curt`/`hurt`/`angry` diturunkan ke `neutral` bila PAD netral/positif
  dan annoyance orangnya < 0.3, supaya balasan tidak "ikut suasana" saat hatinya tenang.
- Antrean turn: `App.locks` per chat diganti satu `App.turn` global, jadi pesan diproses berurutan
  lintas chat (state hati/drive/vibe/memori tidak tertukar). Command memakai `contextlib.nullcontext`
  supaya `/stop` tetap bisa memotong antrean. `App.reflect` ikut mengambil lock yang sama, dan
  `Ambient` menerima `turn_busy=lambda: app.turn.locked()` supaya chime tidak menyelituk saat ada
  balasan berjalan.
- Vibe dinyalakan secara default: `VibeConfig.enabled` dan loader (`_bool(..., True)`) sekarang
  `true`, jadi config tanpa blok `affect.vibe` tetap menjalankan vibe; di mesin tanpa torch/model
  jalurnya fail-open (warning sekali saat start, `App.anima` tetap `None`-efektif).
## 2026-10-28 (engine emosi + keputusan otonom v2)

- `SOUL.md` baru di root: persona lama dipindah apa adanya + bagian otonomi, emosi, grounding, dan
  format WhatsApp. `sanka/config.py::load_config` membaca `SOUL.md` di folder `config.yaml` dan
  memakainya sebagai `Config.persona` (YAML `persona` hanya fallback); `Dockerfile` menyalinnya.
  `config.yaml`/`config.example.yaml` tidak lagi menyimpan persona.
- `emotion-engine` (MIT) di-vendor ke `sanka/vendor/emotion_engine.py` + license/commit di
  `sanka/vendor/LICENSE.emotion-engine`; `affect-kernel` dihapus dari `pyproject.toml`.
- `sanka/affect.py` ditulis ulang sebagai adapter engine: satu file state JSON per chat di
  `affect.state_dir` (bound `character_id=sanka`, baseline dari `SOUL.md`, volatility
  `expressive`), API baru `turn_start`/`turn_end`/`prompt`/`status`/`calm`/`pulse`/`stats`.
  SQLite tinggal `people`/`chat_people`/`topics`/`drives`/`meta`; tabel mood/sulk/event dan semua
  regex intent/sulk dibuang. Idempotensi lewat `event_id` = id pesan.
- `sanka/gate.py` dihapus; `sanka/decision.py` menggantikannya: `DECISION_PROMPT` (soul + aturan
  arah/nada/mode/appraisal), parser JSON ketat dengan clamp, `build_context` (fakta transport,
  state emosi, relasi, 30 pesan), dan `decide` (4000 token, retry 8000). Tidak ada regex/score;
  mention/DM/reply ditimpa jadi `reply` oleh `ensure_reply`.
- `sanka/wa.py`: `should_process`/`mentioned` regex/`named`/sulk/`SIGH`/appraisal helpers dibuang.
  `handle_message` sekarang: command (tanpa keputusan/emosi) -> krisis (tanpa keputusan) ->
  `turn_start` + `decide` + `turn_end` -> routing `ignore`/`react`/`reply`; typing hanya muncul
  untuk reply. `system_prompt` menerima `tone`/`decision_reason`/`emotion`, memilih gaya dari
  keputusan (bukan fakta tool), dan melarang janji diam serta mengarang tindakan.
- `sanka/llm.py`: `APPRAISE_PROMPT`/`appraise`/`decide`/`judge`/`parse_verdict` dibuang;
  `DIGEST_PROMPT` melarang fakta dari baris "Sanka" dan budget digest 1200->3000.
- `sanka/ambient.py`: `gate`/`neglect`/`silent` dibuang; chime memakai `affect.status` + drive,
  `pulse` satu argumen, dan digest membuang baris pesan Sanka sendiri. `AffectStore.pulse`
  melewati load/save bila bucket 5 menitnya sudah diproses, jadi tick 10 detik tidak menulis file
  state terus-menerus.
- Command: `/mood` memakai `affect.status` (ringkasan, PAD, pulse, trust), `/calmdown` = repair
  turn. `scripts/install_engines.py` memasang skill opsional `senticore` di folder skill runtime.
- Uji: `tests/test_gate.py` dihapus; `tests/test_decision.py` baru; `test_affect`, `test_reply`,
  `test_smoke`, `test_ambient`, `test_commands` ditulis ulang untuk kontrak baru (ignore tanpa
  typing, mention dipaksa reply dengan tone curt, krisis tanpa panggilan keputusan, mode
  professional vs daily, digest tanpa baris keluar).
- Verifikasi: `pytest -q` 166 lulus; `python -m sanka selftest` SELFTEST OK; keputusan live
  deepseek-v4.1-flash: obrolan grup netral -> `ignore`, mention -> `reply` tone `curt`, hinaan ->
  `reply` tone `hurt` dengan PAD negatif.

## 2026-10-27 (DM tidak butuh tag/nama; chat kind di konteks)

- `sanka/wa.py` `system_prompt(is_dm=...)`: di chat pribadi persona menyatakan semua pesan
  untuknya — tag, mention, atau namanya tidak diperlukan, dan aturan arah untuk grup tidak
  berlaku. `handle_message` meneruskan `is_dm`.
- `sanka/gate.py`: `decide` meneruskan `is_dm` ke `_ask`/`_review`/`_context`, dan `_context`
  membuka dengan `Chat: pesan pribadi (DM), semua pesan di sini untukmu.` atau `Chat: grup.`
  supaya model tahu jenis chat tanpa menebak dari nama chat. Aturan yang sama ditambahkan ke
  `DECIDE_PROMPT`/`JUDGE_PROMPT`.
- `sanka/llm.py`: `APPRAISE_PROMPT` menyatakan DM selalu `directed=true` (kecuali pesan jelas
  membicarakan orang lain); `_appraisal_context` menulis `— grup` / `— pesan pribadi (DM)`.
  `_read_appraisal` sudah memaksa `directed=True` untuk DM bila intent valid.
- Uji: gate context DM/grup, `system_prompt(is_dm=True)`, dan satu turn DM tanpa tag/nama yang
  tetap dijawab (`tests/test_gate.py`, `tests/test_smoke.py`, `tests/test_reply.py`).
  `pytest -q` 188 lulus, `python -m sanka selftest` SELFTEST OK.

## 2026-10-28 (arah pesan, /mood, dan kirim ke CP)

- `sanka/chat.py`: `MEDIA_LABELS` + `prompt_entry(entry, bot_name, limit)` — satu baris konteks
  (nama, teks, label media, kutipan) untuk gate dan appraisal; pengganti renderer teks-saja yang
  membuang pesan stiker/lampiran tanpa caption. `_message_lines` tidak berubah.
- `sanka/gate.py`: `decide(current=...)` meneruskan pesan asli + fakta arah (`men-tag kamu`,
  `membalas pesanmu`, `membalas pesan orang lain, bukan pesanmu`, sebut nama) ke `_ask`/`_review`/
  `_context` (parameter opsional, jadi pemanggil lama tetap jalan). `_context` memakai
  `prompt_entry` sehingga `[stiker]` dan kutipan ikut terlihat. `DECIDE_PROMPT`/`JUDGE_PROMPT`
  kini menyuruh model menentukan objek bicara dulu, mengenali variasi nama memanjang/typo, tidak
  memindahkan objek sindiran orang lain ke diri sendiri, tidak menuduh ngambek dari stiker, dan
  tidak mengarang kondisi tubuh. `score()` dan override tag/DM tidak berubah.
- `sanka/llm.py`, `sanka/wa.py`, `sanka/ambient.py`: aturan arah/stiker/tubuh yang sama di
  `APPRAISE_PROMPT`; `_appraisal_context` memakai `prompt_entry` (stiker dan kutipan tidak lagi
  hilang karena `if not body: continue`); blok "Cara membaca pesan" di `system_prompt`; chime
  melarang mengarang kondisi tubuh.
- `sanka/wa.py`: `_name_body` (huruf berulang: `s+a+n+k+a+`) dipakai `named`/`mentioned`, jadi
  `SANKAAAAAAAAA`/`SANNNKAAAAAA`/`sankaaaaaa` lolos routing dan `sankas` tidak.
- `sanka/affect.py`: `modes()` menambah `relation` (`_relation` untuk orang itu) dan `person_name`.
- `sanka/commands/mood.py` (baru): `/mood` menampilkan label mood, perasaan (valence), energi,
  gabut/kesepian, status ngambek/nangis, dan hubungan dengan pengirim; fallback
  `state emosi belum aktif.` tanpa AffectStore. `guard.EXEMPT_COMMANDS` + `roles.member.commands`
  (config + example) ditambah `mood`, jadi `/mood` tetap menjawab saat Sanka ngambek.
- `sanka/history.py`: `contacts(query, limit)` — kontak perorangan di arsip, `@lid` dipetakan ke
  PN lewat `lid_map`, dedupe per JID kirim.
- `sanka/tools/send_to.py`: `_resolve_target` mencocokkan JID/nama persis/potongan atas gabungan
  grup + kontak (ambigu -> `beberapa target cocok`), daftar kosong menampilkan `Grup di arsip` dan
  `Kontak di arsip`, JID `@lid` yang dipetakan ke satu PN tetap bisa dikirim. Prompt `cross_send`
  mewajibkan panggilan `send_to` yang sukses sebelum mengaku mengirim ("aku cp kamu aja").
- Uji: `tests/test_gate.py` (konteks stiker + kutipan + arah), `tests/test_context.py`
  (`prompt_entry`), `tests/test_commands.py` (`/mood` + tanpa affect), `tests/test_smoke.py`
  (nama memanjang + routing), `tests/test_send.py` (kirim ke kontak + ledger + daftar kontak),
  `tests/test_registry.py` (mood di registry dan pola member). `pytest -q` 179 lulus,
  `python -m sanka selftest` SELFTEST OK (member kini punya `mood`).

## 2026-10-27 (jiwa ganda: appraisal LLM, heartbeat, sulk yang tetap menjawab)

- `sanka/llm.py`: `APPRAISE_PROMPT` + `LLM.appraise` (tanpa tool, `llm.appraise_temperature`) dan
  `parse_appraisal` (intent 9 nilai, `directed` bool/"true", `hurt`/`warmth` 0..1, `sarcasm`, `why`
  <=80; field wajib invalid -> `{}`). `parse_decision` diganti `parse_verdict` -> `(act, bubbles)`.
  `LLM.run` memakai `llm.temperature`; `decide` memakai `gate.decide_temperature`; chime 0.9.
  `LLM.followup`/`parse_followup`/`FOLLOWUP_WORDS` dihapus. `max_tokens` appraisal 300 -> 700
  dan digest 500 -> 1200: model reasoning menghabiskan jatah dulu (`finish=length`), sehingga
  appraisal jatuh ke fallback tanpa `hurt` dan digest tidak menulis topik.
- `sanka/affect.py`: `classify_intent` -> `classify_intent_fallback` (cadangan, `hurt=0`, tidak
  pernah sulk). `observe(appraisal=...)` memakai hasil LLM: intent/arah/hurt/warmth, delta
  diskalakan intensitas, `_record_event` memakai intent yang sama, `_atmosphere` bergeser oleh
  hurt/warmth. Sulk hanya dari `ABUSE` dengan `hurt>=0.35` (level 2 bila `hurt>=0.70`/kesal/anger
  tinggi) atau kesedihan nyata; pemicu `annoyance>=0.40` dihapus sehingga ledekan main-main tidak
  lagi membuat diam. Tabel `drives` + `pulse` (decay mood, drive tumbuh, sulk kedaluwarsa), tabel
  `chat_people` (keanggotaan chat) + `known_people(chat_id)` yang hanya anggota chat itu,
  `seed_chat_people` untuk DB lama, prompt memuat drive dan interaksi/terakhir terlihat.
- `sanka/wa.py`: appraisal dikumpulkan sebelum `observe` (`_appraisal_context`: chat, pengirim,
  role, prompt affect, 8 pesan terakhir, pesan terakhir) dengan pagar
  `affect.max_appraisals_per_minute`; gagal/limit -> fallback. `sulk_mode`: command non-exempt
  ditolak dengan `SULK_REPLIES`, balasan LLM memakai `SULK_PROMPT` tanpa `specs`/memori/skill,
  satu bubble; krisis tidak pernah sulk; gate `ignore` saat sulk mencatat `note_ignored` dan
  mengirim satu `hmm` pada ambang. `is_named` (nama disebut tanpa tag) diteruskan ke gate.
  Balasan: satu burst, `planned = min(max_bubbles, max(gate.bubbles, kebutuhan teks))`;
  `_followups`/`FOLLOWUP_RULES` dihapus. `_stale` menyerap pesan yang sudah menunggu di jendela
  saat burst terakhir disusun (snapshot id per chat), jadi tag beruntun dijawab sekali tanpa
  menelan pesan yang datang setelahnya. `system_prompt` menerima `person_style` + `sender`.
- `sanka/gate.py`: `Decision.bubbles`, `score(named=...)` (+0.45, meniadakan penalti
  `human_flow`), `_over_limit(attention, direct)`: pesan `direct` (tag/DM) lolos tanpa syarat,
  cooldown hanya untuk non-attention, cap chat/orang hanya untuk non-direct. Ini hasil temuan
  produksi: `@Sanka halooo ?` (score 0.65) di-ignore `reason=rate_limit` setelah 8 balasan/10
  menit, jadi tag/DM tidak boleh terkena kuota. `reply_cooldown_seconds` diset 0 di contoh
  (jeda paksa dimatikan; model yang memutuskan diam), keputusan LLM untuk setiap pesan non-command
  dengan pagar `gate.max_decisions_per_minute`, `_context` memuat status kiriman sendiri, nama
  pengirim, dan catatan "menyebut namamu tanpa men-tag". `_ignores`/`max_consecutive_ignores`
  dihapus; tag/DM `ignore` ditimpa `reply`.
- `sanka/ambient.py`: `prime()` mengisi `known_chats` dari `history.recent_chats(50)`;
  detak pertama untuk chat yang belum punya baris `drives` hanya menambatkan `last_pulse` (tidak
  melompat ke energi 0/gabut 1 seperti kalau `elapsed` dihitung dari epoch), dan pertumbuhan drive
  dibatasi 24 jam agar downtime panjang/clock skew tidak memaksimalkan semua drive. `last_decay`
  hanya maju sejumlah langkah `decay_hours` yang benar-benar diterapkan (sisanya disimpan), jadi
  tick 2 menit tetap meluruhkan PAD per jam; bagian kontinu (kesal/sedih/emosi aktif) memakai
  `elapsed` sejak `last_pulse`, bukan sejak `last_decay`;
  `_tick` memanggil `pulse` tiap chat sebelum digest; chime bergerbang urge dari drive + mood,
  prompt chime memakai `affect.prompt`. `sanka/history.py`: `_human_rows` membawa `chat_id`,
  `style_sample` mengembalikan dict, `recent_chats`. `sanka/style.py`: `person_profile`
  (`PERSON_MIN=8`), seed per chat/orang. `sanka/format.py`: `reply_budget(humanize=...)` membaca
  `normal_*`/`detail_*`.
- Config: `llm.temperature`/`appraise_temperature`, `affect.max_appraisals_per_minute`,
  `gate.decide_temperature`/`max_decisions_per_minute`; `humanize.normal_parts=2`,
  `detail_parts=3`, `max_bubbles=3`; `followups`/`max_followups`/`followup_delay`/
  `max_consecutive_ignores` dihapus (YAML, dataclass, loader). Contoh config memakai
  `require_mention: false` supaya gate yang menyaring, bukan mention.
- Upgrade aman tanpa reset: tabel `drives`/`chat_people` dibuat `IF NOT EXISTS`; DB emosi lama
  tetap dipakai dan keanggotaan chat diisi sekali lewat `seed_chat_people` (flag
  `chat_people_seeded`). Yang perlu disegarkan hanya `.sanka/style.json` (agregat turunan, aman
  dihapus) supaya profil per orang/chat langsung terisi dari arsip, bukan menunggu sampel live.
- Verifikasi: `pytest -q` 170 lulus (baru: appraisal->sulk, sulk menolak tool/command, krisis
  bypass sulk, burst tag, gate bubbles, chat_people, pulse/drives + anchor detak pertama, chime
  urge, person style, command grup mengikuti gate mention);
  `python -m sanka selftest` -> `PASS affect`, `PASS style`, `SELFTEST OK`.
- Risiko/sisa: kualitas appraisal dan keputusan gate bergantung model; biaya LLM naik karena
  setiap pesan non-command dinilai dua kali (appraisal + gate) — ditekan lewat
  `affect.max_appraisals_per_minute`, `gate.max_decisions_per_minute`, atau `gate.llm_decide:
  false`. Test memakai LLM tiruan; smoke di grup nyata (hinaan halus, tag saat sulk, heartbeat
  setelah 20 menit diam) masih perlu dijalankan di server.


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
