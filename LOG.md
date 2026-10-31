# LOG

## 2026-11-01 — pattern character sheet, clean tree, Sonar-ready

Goal: stop carrying a dirty training corpus, make the character data conditional ("if this, then
that"), and leave the repo small enough to audit.

**Character data**
- `sanka/data/patterns.json` v2: 43 patterns, 36 required seeds + 7 from owner-corrected lines
  (dead_air_exit, sticker_saved, honest_unknown, callback_detail, meme_unknown, group_protective,
  flirt_receptive); every row is reachable from `room_register`.
- Only six invariants are `always` (language_lock, length, question_limit, grounding, floor,
  curse_trigger); they need no keyword hit and sit at priority 2-3 so they never displace a register
  move in the sort.
- `say` lines are verbatim owner-approved corrections; `avoid` lists the rejected phrases ("bukan
  drama", "bidadari dari surga"). `sanka/data/pattern_vectors.json`: 43 × 384 dims.

**Selector**
- `sanka/engine/patterns.py` replaces `examples.py`: gate filter, keyword hits over the last 8
  messages, cosine against the shipped vectors, sort by `(-score, -priority, id)`, render ≤180 chars.
  `encoder=None` forces the keyword path; the default lazily loads MiniLM on CPU; vectors with a
  different model/dim or <80% coverage are ignored; keys of ≤3 characters match whole words only.
  Measured: 9.1 s first call (model load), 12.9 ms warm CPU per turn. Cutover:
  `config.character.patterns` (0..6, default 0; host config 4), prompt header `## Room patterns
  (apply the move, never copy the wording)`, `agent.py` passes the lines to `character_block`;
  `examples` is gone from config, template, and example YAML.

**Tooling**
- `scripts/patterns.py`: `--check` (schema, gates, duplicates, register reachability), `--embed`
  (atomic, model missing = exit 2), `--stats`, `--build-xlsx`, `--apply-xlsx` (konten/contoh/hindari/
  keys/komentar 1:1 by id, unknown ids reported).
- `scripts/replay.py`: replays `tests/data/scenarios.json` through the live prompt with
  `character_block(patterns=...)` + `regulate`, deterministic hard-fail metrics, and a rubric judge
  whose verdict comes from its scores and the rubric thresholds. Writes `workspace/replay-<id>.json`.

**Deleted**
- Corpus pipeline: `fetch_conversations`, `browser`, `firefox_cookies`, `fetch_datasets`,
  `ingest_conversations`, `excel_review`, `chat_review`, `train_conversation`, plus their two tests
  (the XLSX contract moved to `tests/test_patterns_cli.py`).
- Data: `conversation_datasheet.json`, `long_dialogues.json`, `sanka/data/examples.json`,
  `tests/data/fixtures/`, `references/inbox/`; scenarios/rubric/bad examples survive in
  `tests/data/scenarios.json`. Owner chat exports in `references/` untouched.
- `workspace/`: 36 artifacts (~3.5 MB incl. media, raw captures, workbooks) → only
  `review-patterns.xlsx` (43 patterns × 12 columns).

**Docs**
- `SOUL.md`: 11 468 → 2 948 chars, 71 lines, character-card sections. Removed the trainer/coach
  framing, the "don't admit you're an AI" line, and the duplicated level table; the nuanced moves now
  live in the catalogue.
- `README.md`: 1 128 → 413 lines (EN 207 / ID 205) with the pattern lifecycle, SOUL boundary, replay,
  and a plain statement that the corpus pipeline is gone. `TASK.md`: deliverables + data contract.
  `AGENTS.md`: one pattern rule. `pyproject.toml`: ruff/pytest-cov, ruff + branch coverage config.
  New `sonar-project.properties`.

**Verification**
- `pytest -q`: 391 passed (414 − 41 pipeline/examples tests + 18 pattern tests); `--cov=sanka` 57%
  total (vendor engine 26% drags it down), `selftest OK`.
- `scripts/patterns.py --check` → `patterns=43 ok`; `--stats` → banter 25, neutral 30, serious 21;
  `always=6`, male-only 3, tox≥3 3, adult≥1 2.
- Selector: comfort first for "capek banget, gak kuat" at (1,0); count 0 → `[]`; banter male (3,2) →
  roast_return first; unknown target yields no roast or adult pattern.
- Workbook roundtrip: build → edit one `konten` → apply → check clean, other rows untouched.
- `config import` → `engine.character.patterns = 4` in both `config.yaml` and the DB document.
- Replay `roast_group --limit 4` with a real key: `hard_fail=0`, judge averages 4.7-5.0, no curse for
  the female turn (`limits [1, 0]`). An `Agent.handle` turn with `patterns=4` carries the selected
  lines into the system prompt (`## Room patterns`), now covered by `test_agent.py`.
- Schema unchanged (no migration): `engine_lease`, privacy, document, and gateway tests stay green.

**Static analysis (Sonar not run)**
- `sonar-scanner` and Docker are not installed here, so the SonarCloud gate is **unverified**; the
  properties file and `coverage.xml` are ready for CI.
- `ruff check .` (line 100, complexity 12, vendor excluded): 346 pre-existing findings — 229 E501,
  36 I001, 21 C901, 15 PLR0912, 13 E402, 12 PLR0915, plus small F/SIM/UP. New and touched files
  (`engine/patterns.py`, `scripts/patterns.py`, `scripts/replay.py`, pattern tests) are clean.
- Fixed one real portability bug found this way: `reply._heading` used a backslash inside an f-string
  expression, which is a syntax error on Python 3.11 while `requires-python >= 3.11`; now 3.11-safe.
- Known hot spots left alone on purpose: `Agent.handle` (complexity 56, 218 statements),
  `config.load_config` (51 statements), `prompts.character_block` (long prose lines).

**Open** — real WhatsApp end-to-end needs production credentials/QR; SonarCloud gate needs a run.

## 2026-10-31 — riset korpus 1000 item: 15 pola baru + lapisan fakta

**Riset** (`scripts/research.py`, 24 area, 3 run)
- 1000 item unik dari Muse (`muse-spark-1.3-contributor`): 854 sintetis, 146 kutipan asli; 138 klaim
  muncul di ≥2 domain, 59 di antaranya berjenis fakta.
- Artefak: `workspace/research/<stamp>/` (balasan mentah Muse, `filtered.json`, `sources.json`,
  workbook 8 sheet), `study.json`/`study.xlsx` (27 konstruksi berulang, leksikon, konteks 24 area),
  `corpus_vectors.json` (1000 × 384), `fakta.json` (klaim + domain + URL). Salinan luar repo:
  `~/sanka-research/20261031-korpus/` dengan `manifest.json` berisi sha256.

**Pola** (`sanka/data/patterns.json`, 43 → 58)
- 15 baris `riset_*`: curhat, kasar-akrab, Jawa Timuran, Sunda, konflik, menolak, kerja, bola, meme,
  sindiran, singkatan chat, kampus UIN, grup campuran, ajak kumpul, sapa harian. 24 `say` dari
  korpus; `source: derived`; `komentar` menunjuk run + topik.
- Gate sindiran politik `min_toxicity` 1 → 0: sindiran bukan soal kekasaran ruangan. Diuji muncul di
  ruangan toxicity 0 dan tetap muncul di 1-2.
- `--check` → `patterns=58 ok`; `--embed` → `vectors=58 dim=384`.

**Fakta** (`sanka/data/fakta.json`, lapisan baru)
- 59 fakta publik, 129 domain sumber, tiap baris wajib ≥2 domain; `--check` ok, `--embed` 59 × 384.
- `sanka/engine/knowledge.py`: `load_facts` (cache, rusak = kosong) + `select_facts` (kata kunci khas
  atau cosine ≥0.55, sort deterministik skor lalu id, render ≤220). `agent.py` memilih
  `character.facts` per turn dan `character_block` merender `## Known facts`.
- `CharacterConfig.facts` 0..6; host `config.yaml` 3, `config.example.yaml` dan template admin 0.
- Smoke: "uin malang maba mahad" → fakta mahad peringkat 1; "sekaten apa?" → 2 fakta Sekaten;
  "orang bandung ke orang tua pakai bahasa apa?" → fakta lemes/loma peringkat 1; "wkwk" → kosong.

**Verifikasi** — `pytest -q` 403 passed; `sanka selftest` OK; `scripts/patterns.py --check` dan
`scripts/fakta.py --check` bersih.

**Koreksi pemilik (ejaan + gaya)** — `nguyu` → `ngguyu` di `riset_konflik_netral` (korpus: 5×
"ngguyu", 1× "nguyu"); 4 `say` yang dipakai di dua pola diganti baris korpus lain; semua 55 `say` dan
59 `fakta.text` kini diawali huruf besar. Validator menolak `say`/fakta berhuruf kecil dan `say` ganda
lintas baris (2 tes baru). Vektor pola tidak berubah (embed = `content + keys`); vektor fakta
di-embed ulang. `pytest -q` 406 passed.

## 2026-11-01 — tumbuh dari korpus: 306 fakta, 72 pola

Goal: stop losing true material from the 1000-item corpus; every uncovered fact lands in
`fakta.json` and every uncovered style cluster becomes a pattern.

**Aturan bukti** — `scripts/fakta.py` `MIN_DOMAINS` 2 → 1: satu URL sudah cukup, `confidence` 3 hanya
saat ≥2 domain itu juga `size >= 2`. `AGENTS.md`/`TASK.md` mengikuti (≥1 URL sumber, dua domain
menaikkan keyakinan). Cap pola 30-60 dihapus (`>= 30` di dua tes, TASK/README).

**Pola kurasi (7)** — `riset_oprec`, `riset_organisasi_pilih`, `riset_panitia_divisi`,
`riset_mubes_demisioner`, `riset_ospek`, `riset_proker_dana`, `riset_ukt_jalur`: kosakata organisasi
kampus (UKM/BEM/HIMA, open recruitment, mubes, demisioner, panitia, ospek, proker, danus, UKT,
SPAN-PTKIN, KIP Kuliah) yang belum punya baris. 60 → 67 pola.

**`scripts/grow.py`** (baru) — korpus JSONL → fakta/pola: embed MiniLM, gugus greedy (0.82 cosine + ≥2
kata isi), lewati gugus yang sudah tertutup fakta (0.60) atau pola (0.80) sehingga rerun idempoten.
`context`/`description`/`idiom` masuk apa adanya; `line`/`comment`/`meme` ditulis ulang lewat
`research.client` (Muse) — Muse `muse-spark-1.3-contributor` selalu balas kosong untuk prompt JSON ini
(reasoning menghabiskan budget, `finish=length`), jadi setelah balasan kosong pertama panggilan pindah
ke model default repo pada budget 1500 token; panggilan diparalelkan 4 dan hasilnya dirakit berurutan
supaya id/urutan tetap deterministik. Kunci: kata ≥4 huruf + akronim (UKM/BEM/UKT) dengan prioritas
tertinggi, karena `[a-z]{4,}` lama membuang kosakata kampus yang justru jadi tujuan tugas ini.

**Hasil** — `--facts --dry-run` `clusters=976 added=252 skipped=724` (tanpa tulis); run nyata
`added=247` → `fakta=306`, `vectors=306 dim=384`, `--check` bersih. `--patterns` `added=5` →
`patterns=72`, `vectors=72 dim=384`, `--check` bersih. Satu baris dibuang validator: `t3-tombok` (teks
<10 karakter).

**Verifikasi** — `pytest -q` 406 passed; `sanka selftest` OK. Smoke seleksi: query kampus natural
("rek aku arep melu ukm teater komedi opo mapala nang uin malang") → fakta UKM peringkat 1; "bem hima
ukm bedane opo rek nang kampus" → fakta BEM/HIMA peringkat 1; "mubes kemarin gimana hasil lpj
pengurus lama demisioner" → `riset_mubes_demisioner` peringkat 1 (neutral dan serious); "oprec bem
kapan dibuka" (soal) → `riset_oprec` peringkat 3.

**Batas yang tersisa** — dua smoke 4-kata dari rencana tidak lolos ambang: "ukm uin malang apa aja
rek" dan "mubes kemarin gimana" hanya menyumbang 1-2 keyword hit (bobot 0,3 di `knowledge._score`,
0,1 di `patterns._score`), sedangkan cosine MiniLM untuk query pendek naik-turun — baris UKM jatuh ke
peringkat 9, `riset_mubes_demisioner` ke peringkat 9 di register neutral. Perbaikannya ada di bobot
selector (`KEY_WEIGHT`/`HIT_WEIGHT`), bukan di data, dan sengaja tidak diubah di sini.
