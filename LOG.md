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
