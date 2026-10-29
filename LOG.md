# LOG

## 2026-10-30 (fix: privacy assessment spent its budget on reasoning, sensitive turns stored at L2)

- Problem: `PrivacyService._assess` asked for a level with `max_tokens=160` and no
  effort override. `deepseek-v4.1-flash` is a reasoning model and reasoning tokens count
  against `max_tokens`, so the call ended `finish=length` with
  `LLM latar kosong (max_tokens=160 finish=length session=bg)` and `_assess` returned
  `PrivacyVerdict(level=default_level=2, ask=False)`. The turn was then captured at L2 with
  `confirmed=True`, and L0-L2 stay fluid everywhere, so sensitive content became freely
  shareable; nothing was skipped and no one was asked. Measured on the live endpoint: 5 of
  6 probe turns came back empty at 160 tokens, exactly the production symptom.
- Fix: `LLM.complete` gained `reasoning_effort`, sent only when non-empty, so the six
  existing callers keep today's behaviour. `sanka/engine/privacy.py` now computes the
  assessment budget from the turn length (`assess_budget`: 800 tokens plus one per 8
  characters, capped at 4000), asks for `medium` reasoning, retries once at double budget
  without the effort parameter so a provider that rejects it still gets a chance, and
  `_assess` returns `None` when both attempts give no JSON. `capture_turn` refuses to store
  that turn and logs `privacy assess gagal; capture dilewati`, instead of silently pinning
  it to `default_level`. The `enabled=False`/no-LLM branch still stores at `default_level`:
  that is explicit configuration, not a failed assessment. No config key, schema, or
  chat-visible string changed.
- Budget sizing came from the endpoint, not from guessing: 12/12 non-empty at 800 tokens
  for the two sensitive turns (worst reasoning 533, worst total 565); 608 was too tight
  (one level-4 turn needed 309 reasoning on one call and more than 608 on the next), which
  is why the minimum is 800 with 8 characters per extra token. Reasoning does not grow with
  input length: an 18k-character vent used 216 reasoning tokens inside a 3064 budget.
- Verification: `pytest -q` 278 passed with three new contracts in `tests/test_privacy.py`
  (budget growth and cap, retry with doubled budget and dropped effort, capture skipped on
  two empty replies); `python -m sanka selftest` SELFTEST OK. Live through
  `PrivacyService._assess` against OpenCode Go: six varied turns one call each, levels
  1/2/1/3/4/3, no empty completion, $0.00009-$0.00043 per turn; the legacy 160-token probe
  still reproduces the empty answer, so the check exercises the real failure. The deployed
  image needs a rebuild before the server sees this.

## 2026-10-30 (fix: ambient digest crashed with NameError MEMORY_KINDS)

- Problem: every group digest died with `NameError: name 'MEMORY_KINDS' is not
  defined` at `sanka/engine/llm.py:70` inside `parse_digest`, so `ambient.py` never
  stored the topic, keywords, or extracted facts — the loop logged
  `ambient digest gagal` and retried on every cycle. `parse_digest` compared each
  model-provided `kind` against a whitelist that was never declared; the crash fired
  only when the model returned at least one fact, which is why the suite stayed green
  (`tests/support.py` `FakeLLM.digest` returns a dict directly and bypasses parsing).
- Fix: `sanka/engine/llm.py` declares `MEMORY_KINDS = ("fact", "preference", "rule",
  "note")` next to `DIGEST_PROMPT`, matching the digest prompt's `fact|preference|rule`
  and the memory tool schema's `note` fallback; unknown kinds normalize to `note`.
- Verification: `pytest -q` 275 passed with the new `tests/test_llm.py`
  (`kind` normalization, secret/injection drop, garbage payload); `python -m sanka
  selftest` SELFTEST OK. Live digest smoked against OpenCode Go with a transcript
  containing a preference, a plan, and an API key: the call returned
  `fact`/`preference`/`rule` entries for Ana and Budi, dropped the key line, and no
  longer raised. An AST scan for other undefined globals then found only dead code:
  `sanka/engine/context.py` `_hydrate` called `download_media`, which never existed in
  core (the gateway owns it) and the function had no callers, so it was deleted and
  `Config` joined the module's `TYPE_CHECKING` block; the scan is clean now. Still
  pending on the server: the deployed image must be rebuilt — the running container
  still shows this traceback and, from the same log, the `missing x-opencode-session`
  mem0 400 that the mem0 patch below fixes.

## 2026-10-30 (fix: postgres parameter limit on import, mem0 capture without session header)

- Problem: the cutover from `2bc2834` (SQLite) to `15097fd` (Postgres) broke in two
  places. `sanka db import` passed every row of a table to `Database.upsert` in one
  statement: 5,571 messages × 12 columns ≈ 66k bind parameters, and PostgreSQL rejects
  anything above 65,535 (`psycopg.OperationalError: number of parameters must be between
  0 and 65535`). Mem0 auto-capture returned `HTTP 400 Request is missing
  x-opencode-session`, because the adapter lets Mem0 build its own OpenAI client while
  `engine/llm.py` is the only place that sends the header; replies and retrieval worked,
  new memories were never extracted.
- Fix: `sanka/engine/db.py` gained `UPSERT_BATCH_ROWS = 500` and `Database.upsert`
  iterates 500-row chunks — one conflict statement per chunk, `rowcount` summed. The
  `update` contract is unchanged: `None` still derives `excluded` columns per chunk, a
  callable is called once per chunk with that chunk's statement (`HistoryStore.add_many`),
  a mapping is reused because it holds literals/column expressions only. Widest table is
  `people` (19 columns) → 9,500 parameters per chunk. `sanka/admin/database.py` needs no
  edit; its single `db.upsert` per table now chunks upstream.
  `sanka/engine/memory/mem0.py` replaces the extraction client after
  `AsyncMemory.from_config` with `OpenAI(api_key, base_url=llm.base_url,
  default_headers={"x-opencode-session": "sanka-agent-mem0"})`; the embedder stays local
  HuggingFace, so no other client needs the header. If a future Mem0 drops `.llm.client`,
  startup logs a warning and retrieval keeps working (capture stays broken until the
  adapter is revised).
- Verification: `pytest -q` 272 passed, including
  `test_upsert_chunks_postgres_parameter_limit` (5,500 rows → 11 compiled statements of
  6,000 parameters; the single-statement form compiles 66,001) and
  `test_ready_sets_opencode_session_header`; `python -m sanka selftest` SELFTEST OK.
  Real `mem0ai 2.2.1` checked directly: `AsyncMemory.from_config(...).llm` is `OpenAILLM`
  with a synchronous `OpenAI` `.client` that keeps the header and `base_url`. Multi-chunk
  merge smoked on SQLite: 2,400 `add_many` tuples over 1,200 ids → 1,200 rows, with the
  richer `text`/`sender_name` preserved on both sides of the 500-row boundary. Pending
  server side: re-import the production legacy folder (expect `messages: 5571`, no
  parameter error), one fresh DM with no `missing x-opencode-session` in the log, and
  `sanka_memories` growing by ≥ 1.

## 2026-10-29 (feature: Sanka knows group names, contacts, and DM chats)

- Problem: the archive held only 15 groups and almost none had a name, so `send_to`
  could not find the group a user named. Cause: `chats.name` was only filled from
  `GroupInfoEv` (only when group metadata changed) and from `Conversation.name` during history
  sync; `client.get_joined_groups()` was never used. DM chat names were only filled
  when a message arrived, and never fell back to the `contacts` table that already held names
  from `whatsmeow_contacts`/inline contact.
- Fix: `history.sync_group_names` (called on the bot's `ConnectedEv`, the backfill CLI,
  and `/backfill`) stores the names of every joined group into `chats`; unnamed
  groups are fetched lazily via `wa.ensure_group_name` on their first message (cooldown
  `GROUP_NAME_RETRY = 300s` so an unreadable group is not contacted repeatedly);
  `Sessions.chat_name` for `@s.whatsapp.net`/`@lid` falls back to `contact_name` and then
  stores it; a new `Sessions.set_chat_name` handles server-side group renames. No schema
  change. The "versi WhatsApp Anda tidak mendukungnya" bug is untouched.
- Verification: `pytest -q` 239 passed (3 new tests: `sync_joined_group_names_fills_archive`,
  `dm_chat_name_falls_back_to_contact`, `group_name_is_fetched_when_archive_has_none`);
  `python -m sanka selftest` SELFTEST OK. After deploy, the log must contain
  `nama grup tersinkron: N` with `N > 0`.

## 2026-10-29 (fix: affect not passed to App, emotion frozen)

- Cause: in 3b83d41 the `affect=affect` line was overwritten by `recipes=recipes` when building
  `App(...)` (`sanka/__main__.py`). `App.__init__` defaults `affect=None`, so there was no
  error — every `if app.affect is not None` block in `wa.py` was skipped
  silently: `turn_start`/`turn_end` never ran, PAD stayed frozen at baseline,
  `trust/affection/annoyance`/`drives`/`topics` were never recorded, and `/mood` answered
  "state emosi belum aktif". The `emotions/sanka.json` file was still touched because
  `Ambient` still received `affect` (decay/pulse ran without any turn).
- Fix: one line — add `affect=affect` to `App(...)` in `__main__.py`, alongside
  `recipes`/`style`/`anima`. The only production `App(` construction.
- Second problem (separate, not fixed yet): since 2026-10-28 13:54 UTC the model writes
  `neutral` appraisals with a 0.0/0.0/0.0 delta (188 turns), so PAD only decays
  toward baseline even though turns are recorded. Not a code gate: `turn_end` passes the delta
  through as-is (weight min 0.2) and the prompt diff 349b241→3b83d41 does not touch appraisal.
  Needs post-deploy observation: if `/mood` moves again, bug #1 is done and only the
  prompt/model issue in #2 remains.
- Verification: `pytest -q` 236 passed; `python -m sanka selftest` SELFTEST OK (local
  venv). Push to origin/main triggers a rebuild.

## 2026-10-29 (fix: recipes module left behind, bot restart loop)

- Cause: commit 3b83d41 referenced `sanka/recipes.py` (`__main__`, `config`, `wa`,
  `test_reply`) but the file was not committed (untracked on the laptop, missed by
  `git add`). The image build still reported "done" because nothing failed, but the container
  died on start (`from .recipes import RecipeStore` → ModuleNotFoundError) and then
  restart-looped from ~03:36 UTC; container logs were empty because it kept restarting.
- Fix: commit `sanka/recipes.py` (`RecipeStore` + `run_recipe`),
  `sanka/tools/recipe.py` (recipe tool: list/view/save/remove, read family),
  `tests/test_recipes.py`, plus `ToolContext.recipes`, `/help` integration (the
  "Resep user" group + per-recipe detail, role filter), and the registry expectations.
- Verification: `pytest -q` 236 passed; `python -m sanka selftest` SELFTEST OK (local
  venv). Push to origin/main triggers a rebuild; the bot came back online once the deploy finished.

## 2026-10-28 (agent loop: run-till-answer, persistent context, whole output, send ledger)

- `sanka/llm.py::LLM.run`: the `for max_turns` loop was replaced by run-till-answer with a wall-clock
  budget `RUN_BUDGET_SECONDS = 240` (`time.monotonic()` checked each iteration); when it runs out, one
  final tool-free call + the `BUDGET_TEXT` reminder. `TURN_LIMIT_TEXT` was removed entirely. Loop
  guard: the same `(name, args-JSON)` three times in a row becomes an injection error without execution.
- `sanka/llm.py::LLM.run` adds a send-confirmation pass: when `ctx.sent` is populated, one final
  tool-free call with the note `[Sudah terkirim via tool...]`; on failure it keeps the initial text (fail-open).
- `sanka/salience.py::select`: the tiebreak `min` changed from `-ORDER.index` to `ORDER.index`, so
  `memory`/`actions`/`skills` (last) survive longest on tied scores. `sanka/wa.py`:
  `bonuses["memory"] = 0.35` when the message's `keyword_set` ∩ memory is non-empty (applies even without affect).
- `sanka/memory.py::_select`: the LIKE clause between terms changed from AND to OR (high recall; the
  `_ranked` ranking still puts matches first). Two supersede tests were updated to the OR contract.
- `sanka/wa.py::handle_message`: `decision is None` + direct no longer goes `fallback_only`/`hmm`;
  it continues on the normal LLM path with a curt decision + `ground_tone`. The test was replaced with
  `test_decision_invalid_mention_gets_real_attempt`.
- Output budget: `format_reply` default 3500→10000; `MAX_SEND_TEXT` 60k→100k and `send_text`
  splits via `format_reply` (returns `(... N bagian)` when >1, a single string is unchanged);
  `MAX_OUTPUT` 20k→40k + a continuation hint in `TRUNCATED`; sandbox 16→64 KiB. The
  `test_format_reply`/`test_sandbox` fixtures were updated (sandbox symbolic to `MAX_OUTPUT_BYTES`).
- New `sanka/tools/__init__.py::ToolContext.sent: list[dict]`; successful `send`/`send_to` record
  `{tool, target, kind, summary}`, failures do not. `handle_message`: an empty closing with a populated
  `sent` becomes "Sudah kukirim." (not "(kosong)"). Permissions unchanged: six families, `send`
  OWNER_MEMBER, `send_to` OWNER_ONLY, the check stays in `execute`.
- Prompt: `DAILY_STYLE` (write recaps whole), `PRO_STYLE` (write script formatting whole, runtime splits
  ~10k, do not summarize), `system_prompt` (send note + write whole), `SOUL.md:15` (chat-only
  rule of <200 characters).
- New tests: `test_memory_survives_tiebreak_when_nothing_matches`,
  `test_search_matches_any_term_not_all`, `test_long_styled_rekap_arrives_whole_and_verbatim`,
  `test_tool_send_empty_closing_confirms_not_kosong`; `ctx.sent` is asserted in `test_send.py`.
- Verification: `pytest -q` 247 passed; `python -m sanka selftest` SELFTEST OK (sandbox SKIP,
  no docker); the `LLM.run` stub answered after 25 tool steps (not a limit), the repeated-call
  guard blocked the 3rd execution; `send_text` 25k → 3 sends + "(3 bagian)".

## 2026-10-28 (annoyance direction: /mood, cross-chat log, and groups)

- Bug: `AffectStore._payload` looked people up through `state.identity.relationship_id` (`sanka`)
  instead of the chat id, so `known_people("sanka")` was always empty and `/mood` never
  showed the "Ke <nama>" line. As a result a negative PAD showed up as "kesal" with no subject.
  `_payload` now takes a `chat_id` from `status()`; the payload adds `annoyance`.
- `sanka/commands/mood.py`: the "Ke <nama>" line includes the annoyance level toward that person
  (`kesal 0.2`) when above 0.05. "Perasaan: kesal" still comes from PAD (P < -0.2) and
  "Pulse" is still the last turn's appraisal; the two are different signals, not a second state.
- `sanka/affect.py::turn_end`: `emotion_log` entries from a turn get `chat_id` and `person_name`.
  `prompt(chat_id)` shows a trigger as `- hostility [Xavier]: ...`, and triggers from another chat
  as `[Xavier, chat lain]`; groups still get the member list with `kesal N`.
  `turn_start` marks old entries with `other_chat` for the decision block
  (`sanka/decision.py::_emotion_block`).
- `sanka/affect.py::_recent_log`: `pre_turn_decay` (internal bookkeeping) no longer shows up as
  "yang terakhir kamu rasakan", so the three most recent real triggers stay in the prompt.
- Tests: `test_turn_end_moves_pad_and_relationship` locks `person_name`/`relation`/`annoyance`
  from `status()`; `test_mood_names_who_it_is_annoyed_with` locks the annoyance-direction line;
  `test_prompt_log_names_person_and_skips_internal_decay` and
  `test_context_marks_who_and_other_chat_in_emotion_log` lock the log attribution.
- Verification: `pytest -q` 243 passed; `python -m sanka selftest` SELFTEST OK; replaying two chats
  produced `[Xavier, chat lain]` in a DM and `[Xavier]` in a group; replaying 12 hostility turns on a
  temporary store produced `person_name=Anda`, `relation=kenal`, `annoyance=0.2`.

## 2026-10-28 (prompt budget + ablation harness)

- `sanka/salience.py`: global-workspace-style block competition. `MANDATORY` (`base`, `safety`, `away`,
  `thread`, `emotion`, `vibe`) always gets in; `COMPETING` (`topic`, `style`, `person_style`,
  `memory`, `actions`, `skills`) is dropped one at a time, lowest score first
  (`0.7 * keyword_overlap/3 + bonus`, ties = last in `ORDER`), until the prompt fits.
- `sanka/config.py`: `PromptConfig` (`salience: true`, `max_chars: 9000`) + a `prompt:` loader.
  `config.yaml`/`config.example.yaml` document the new section.
- `sanka/wa.py::system_prompt`: prompt blocks are assembled as a dict and then chosen by `salience.select`;
  new `query`/`bonuses` parameters (old callers stay valid). `handle_message` fills bonuses from a
  pending topic (+0.3), ≥15 interactions (+0.25 person_style), familiarity ≥0.3 (+0.15 style), and
  professional mode (+0.2 skills); a failed affect read means empty bonuses, not an error.
- `sanka/chat.py` + `sanka/thread.py`: `STOPWORDS`, `FOLLOWUP_CUES`, `keyword_set`, and
  `followup_utterance` moved to chat; the situation rules (`open_thread`, `continues`) became pure
  functions taking `now`, used by `App.note_thread`/`App.continues_thread` and the harness.
- `sanka/ablate.py` + `scripts/ablate.py`: read-only harness. `classify` replays the gate from the
  archive (1-message reply window, situations use `message.when`), `replay_metrics` computes
  `called`/`thread_allowed`/`replied`/`false_positive`/`missed`/`unprompted_rate`, `prompt_budget`
  measures the prompt before/after competition, and `decision_shift` calls `decide` twice per sample
  (full vs `--ablate` blocks) without tools/sends/state. Without an API key, the decision part is skipped.
- Tests: `tests/test_salience.py`, `tests/test_thread.py`, `tests/test_ablate.py`, and
  `test_prompt_budget_drops_optional_blocks` in `tests/test_reply.py`.
- Verification: `pytest -q` 240 passed; `python -m sanka selftest` SELFTEST OK (sandbox SKIP);
  `scripts/ablate.py --last 100` on the real archive: 54 inbound messages, `unprompted_rate=0.259`,
  `before_mean=7744` so no block was dropped; `--llm-sample 3` produced
  `tone_changed=1` with before/after examples.

## 2026-10-28 (temporal gate, /stop, attention, AFT-lite, room vibe)

- `sanka/config.py`: `WhatsAppConfig.backlog_seconds` (600) and `quiet_seconds` (600) + loader;
  new `VibeConfig` and `AffectConfig.vibe` (default `enabled: false`), paths resolved like any other
  path and their parent created when config loads. `config.example.yaml` documents all of it.
- `sanka/wa.py::handle_message`: messages whose `when` is older than `backlog_seconds` only go to the
  archive (`App.backlog[chat]`, no LLM/`turn_end`); old commands are not executed either. The next
  turn receives `_away_block(missed)` once in `decide`+`system_prompt` (`Catatan kehadiran`).
  The `App.quiet` gate holds back non-command replies while crisis traffic keeps running.
- `sanka/commands/stop.py` (owner, alias `/diam`): `/stop [minutes]` (default `quiet_seconds/60`,
  max 1440) writes `App.quiet[chat]`, `/stop 0` releases it. `sanka/ambient.py::_chime` accepts a
  `quiet` predicate from `run_bot`, so ambient chimes fall silent too.
- `sanka/affect.py`: `PAD_WEIGHT` (me 1.0 / group 0.6 / other,unknown 0.2 / ignore max 0.2)
  weights dP/dA/dD; relationship deltas only for `addressed=me` and `act != ignore`, through
  `_inertia` (diminishing returns) and a `WINDOW_LIMIT` cap of 0.20 per direction per `WINDOW_SECONDS` 600
  (columns `window_start`/`window_trust`/`window_affection`/`window_respect`/`window_annoyance`,
  migrated by `_migrate`). `pad(chat_id)` reads PAD for memory tagging.
- `sanka/memory.py`: `valence`/`arousal` columns (migration that leaves rows untouched), `add(...)` normalizes
  and stores mood (`COALESCE` on upsert), `_ranked`/`context(mood=...)` use
  query-match score + recency + mood congruence (AFT-lite); without a mood the order stays recency.
  Reflection (`App.reflect`) and the `memory` tool fill valence/arousal from `affect.pad`
  (new `ToolContext.affect` at the end of the signature).
- New `sanka/vibe.py` (clean-room, no anima dependency): `VAD` from 8 EmoSense-ID labels,
  `advance` (current→baseline, baseline→neutral, hold), `VibeRunner` (heartbeat, lazy HF classifier
  on a thread, JSON state `0600` per chat, fail-open). `sanka/wa.py` calls `anima.observe` before the
  decision and injects the vibe block into the decision context + system prompt; `App.close` shuts it down.
  `pyproject.toml` extra `vibe` (torch, transformers<6), `Dockerfile` uses CPU torch + model
  cache at `/app/.sanka/hf`.
- Honest burst: `covered` is no longer "every message in the session" but the payload contents; messages arriving
  before the payload is assembled join the payload (and get answered), while messages arriving later
  get their own turn.
- Verification: `pytest -q` 189 passed; `python -m sanka selftest` SELFTEST OK (sandbox SKIP: no docker
  available). The fail-open path was tested in a Python 3.15 venv (no torch): log
  `vibe: model ... gagal dimuat; vibe nonaktif`, `observe` -> "".
- The real model was tested in a `python:3.12-slim` container (CPU torch + transformers 5.21, HF cache):
  loads in 4-5 seconds, "kesel/marah" -> `anger` 0.97 and "senang" -> `joy` 0.99. The probe found
  `_describe` read `baseline` so the block was always "netral"; the band is now read from
  `current`, probe results: the 2nd annoyed message -> "negatif", the 2nd happy message -> "positif"
  (`baseline` still moves slowly by design). One message does not shift the band because
  `attack` is 0.35 (the plan's "one message is immediately negative" expectation is inconsistent with
  `attack` 0.35 and the 0.35/0.6 bands).
- Annoyance fades on its own at the heartbeat: `AffectStore._soothe` (called by `pulse` on the existing
  5-minute gate) decays `people.annoyance` with `0.5 ** (hours / grudge_half_life_hours)`
  (default 1.5 hours -> a `musuh` label from 0.9 disappears in ~35 minutes, `0` = off), then zeroes
  values below `SOOTHE_FLOOR` 0.01. Only
  annoyance decays; trust/affection/respect are untouched. The `musuh` label (>= 0.75) can therefore
  drop on its own without `/calmdown`, and `/calmdown` remains the instant path.
- Ambient chime became an emotional trigger, not a timer: `Ambient._urge` computes the urge from
  loneliness/boredom/energy + arousal + sadness (negative pleasure) + pending, compared against
  `ambient.chime_urge` (default 0.45); `random.random()` and the dead 0.10 base are gone. The
  "chat was active <30 minutes ago" gate was replaced by "chat has been quiet >= `idle_minutes`"
  (max `CHIME_QUIET_WINDOW` 12 hours) because the old gate reset drives on every message, making the
  emotion component impossible to grow. Cooldown/daily ceiling stay as anti-spam guards.
- Chime is non-deterministic and rationed: probability = `(urge - chime_urge) / (1 - chime_urge)`, so
  the stronger the feeling the more likely it slips in, and the model can still decide `[DIAM]`.
  Added the global rails `chime_gap_seconds` (default 1800) and `chime_per_day_global` (default 4
  attempts / 24 hours for all chats) alongside the per-chat cooldown + ceiling.
- The chime prompt names the group, states the single identity (the same Sanka in ALD, the tester group, Sunan
  Dahlan, DMs — only the place differs), the urge value, and `memory.actions_context()` so it does not
  copy the same quip into many chats.
- One heart, not one per room: `_state_path` is always `state_dir/sanka.json` with
  `relationship_id=sanka`, the session stays single, `drives` are stored in the `chat_id=sanka` row, and
  `pulse`/`session` event ids use `sanka` so many chats do not speed up decay. The migration
  in `_migrate` adopts the newest per-chat state + newest drive when an old DB is opened. Vibe stays
  per chat because it is a reading of the room's atmosphere, not Sanka's feelings.
- `_soothe` is now global (no chat filter) and its half-life is multiplied by closeness
  `1 + 3*affection + 2*trust`: annoyance toward close people lasts longer, strangers fade quickly.
- Group reply gate: `whatsapp.group_reply` defaults to `addressed` — without a real mention, a reply to
  Sanka's message, or his name used as a word, a group message does not call `decide` at all
  (archive + `decision(act=ignore, reason="grup tanpa panggilan")`), so there is no reply and emotion
  does not move. `group_reply: model` restores the old behavior (trust the decision). Joining in
  without being called still goes through the rationed chime.
- Bubbles are decided by the model: `human_chunks`/`humanize_reply` use a `---` line as a hard boundary,
  and blank lines as boundaries only for chat (no list markers, no code blocks, each
  paragraph <= 300 characters). No forced splitting and no ellipsis; extra bubbles are merged
  into the last one. `---` inside a code block is ignored. `DAILY_STYLE` explains the convention (casual chat may use many bubbles,
  serious explanations/lists stay one message). `reply_budget` plus `normal_chars`/`normal_parts`/
  `detail_chars`/`detail_parts` were removed as dead; `max_bubbles` becomes the upper bound.
- `decision.ground_tone`: the `curt`/`hurt`/`angry` tone is lowered to `neutral` when PAD is neutral/positive
  and the person's annoyance is < 0.3, so a reply does not "follow the mood" while his heart is calm.
- Turn queue: per-chat `App.locks` were replaced by one global `App.turn`, so messages are processed in order
  across chats (heart/drive/vibe/memory state is not mixed up). Commands use `contextlib.nullcontext`
  so `/stop` can still cut into the queue. `App.reflect` takes the same lock, and
  `Ambient` receives `turn_busy=lambda: app.turn.locked()` so a chime does not slip in while a
  reply is in flight.
- Name calling tolerates stretching: `_name_regex` caches the pattern `s+a+n+k+a+`, so `SANKAAAAAAAA`,
  `sannnkaaaaaa`, `saaannkaaa` count as calls; `kesankan`/`sangka`/`s a n k a` do not.
- Conversation situations (`App.threads`): a reply opens a situation (interlocutor + keywords + TTL
  `whatsapp.thread_seconds` 900 sec). A follow-up from the same person with overlapping keywords
  or a short continuation utterance needs no mention; the situation closes when the sender differs, the
  context switches, or it expires. Situation hints enter the decision context (`build_context`) and the
  system prompt.
- Frozen emotions after the migration were fixed: state adopted by the migration still held an old active
  session (the chat id), so `record_turn(session_id="sanka")` was rejected with `no_active_session` and PAD
  never moved (symptom: `/mood` always at baseline 0.1/0.3/0.5). `_session` now uses the active
  session that exists on `active_session_conflict`, and a `record_turn` status other than
  `recorded`/`state_only`/`already_closed` is logged as a warning (`turn emosi tidak diterapkan`).
- Vibe is on by default: `VibeConfig.enabled` and the loader (`_bool(..., True)`) are now
  `true`, so a config without an `affect.vibe` block still runs vibe; on machines without torch/model
  the path is fail-open (one warning at start, `App.anima` stays `None`-effective).
## 2026-10-28 (emotion engine + autonomous decision v2)

- New `SOUL.md` at the root: the old persona moved over as-is + sections on autonomy, emotion, grounding, and
  WhatsApp formatting. `sanka/config.py::load_config` reads `SOUL.md` in the `config.yaml` folder and
  uses it as `Config.persona` (YAML `persona` is only a fallback); `Dockerfile` copies it.
  `config.yaml`/`config.example.yaml` no longer store the persona.
- `emotion-engine` (MIT) was vendored into `sanka/vendor/emotion_engine.py` + license/commit in
  `sanka/vendor/LICENSE.emotion-engine`; `affect-kernel` was removed from `pyproject.toml`.
- `sanka/affect.py` was rewritten as an engine adapter: one JSON state file per chat in
  `affect.state_dir` (bound `character_id=sanka`, baseline from `SOUL.md`, volatility
  `expressive`), new API `turn_start`/`turn_end`/`prompt`/`status`/`calm`/`pulse`/`stats`.
  SQLite keeps only `people`/`chat_people`/`topics`/`drives`/`meta`; the mood/sulk/event tables and all
  intent/sulk regexes are gone. Idempotency via `event_id` = message id.
- `sanka/gate.py` was removed; `sanka/decision.py` replaces it: `DECISION_PROMPT` (soul + direction/
  tone/mode/appraisal rules), a strict JSON parser with clamping, `build_context` (transport facts,
  emotion state, relations, 30 messages), and `decide` (4000 tokens, retry 8000). No regex/score;
  mention/DM/reply are overridden to `reply` by `ensure_reply`.
- `sanka/wa.py`: `should_process`/`mentioned` regex/`named`/sulk/`SIGH`/appraisal helpers were removed.
  `handle_message` is now: command (no decision/emotion) -> crisis (no decision) ->
  `turn_start` + `decide` + `turn_end` -> routing `ignore`/`react`/`reply`; typing only appears
  for replies. `system_prompt` receives `tone`/`decision_reason`/`emotion`, picks the style from the
  decision (not from tool facts), and forbids promising silence and inventing actions.
- `sanka/llm.py`: `APPRAISE_PROMPT`/`appraise`/`decide`/`judge`/`parse_verdict` were removed;
  `DIGEST_PROMPT` forbids facts from "Sanka" lines and the digest budget goes 1200->3000.
- `sanka/ambient.py`: `gate`/`neglect`/`silent` were removed; chime uses `affect.status` + drives,
  `pulse` takes one argument, and digest drops Sanka's own message lines. `AffectStore.pulse`
  skips load/save when its 5-minute bucket was already processed, so a 10-second tick does not rewrite the
  state file continuously.
- Commands: `/mood` uses `affect.status` (summary, PAD, pulse, trust), `/calmdown` = repair
  turn. `scripts/install_engines.py` installs the optional `senticore` skill into the runtime skill folder.
- Tests: `tests/test_gate.py` was removed; `tests/test_decision.py` is new; `test_affect`, `test_reply`,
  `test_smoke`, `test_ambient`, `test_commands` were rewritten for the new contract (ignore without
  typing, mention forced to reply with curt tone, crisis without a decision call, professional
  vs daily mode, digest without outgoing lines).
- Verification: `pytest -q` 166 passed; `python -m sanka selftest` SELFTEST OK; live
  deepseek-v4.1-flash decisions: neutral group chat -> `ignore`, mention -> `reply` with `curt` tone, insult ->
  `reply` with `hurt` tone and negative PAD.

## 2026-10-27 (DMs need no tag/name; chat kind in the context)

- `sanka/wa.py` `system_prompt(is_dm=...)`: in private chat the persona states that every message is
  for him — tag, mention, or name are unnecessary, and the direction rules for groups do not
  apply. `handle_message` passes `is_dm` through.
- `sanka/gate.py`: `decide` passes `is_dm` to `_ask`/`_review`/`_context`, and `_context`
  opens with `Chat: pesan pribadi (DM), semua pesan di sini untukmu.` or `Chat: grup.`
  so the model knows the chat type without guessing from the chat name. The same rule was added to
  `DECIDE_PROMPT`/`JUDGE_PROMPT`.
- `sanka/llm.py`: `APPRAISE_PROMPT` states that a DM is always `directed=true` (unless the message clearly
  talks about someone else); `_appraisal_context` writes `— grup` / `— pesan pribadi (DM)`.
  `_read_appraisal` already forces `directed=True` for DMs when the intent is valid.
- Tests: gate context DM/group, `system_prompt(is_dm=True)`, and one DM turn without tag/name that
  is still answered (`tests/test_gate.py`, `tests/test_smoke.py`, `tests/test_reply.py`).
  `pytest -q` 188 passed, `python -m sanka selftest` SELFTEST OK.

## 2026-10-28 (message direction, /mood, and sending to a contact)

- `sanka/chat.py`: `MEDIA_LABELS` + `prompt_entry(entry, bot_name, limit)` — one context line
  (name, text, media label, quote) for the gate and appraisal; it replaces the text-only renderer that
  dropped sticker/attachment messages without a caption. `_message_lines` is unchanged.
- `sanka/gate.py`: `decide(current=...)` passes the original message + direction facts (`men-tag kamu`,
  `membalas pesanmu`, `membalas pesan orang lain, bukan pesanmu`, name mention) to `_ask`/`_review`/
  `_context` (optional parameters, so old callers keep working). `_context` uses
  `prompt_entry` so `[stiker]` and quotes are visible. `DECIDE_PROMPT`/`JUDGE_PROMPT`
  now tell the model to determine the addressee first, recognize stretched/typo name variants, not
  redirect someone else's teasing target onto itself, not accuse sulking from a sticker, and
  not invent body conditions. `score()` and the tag/DM overrides are unchanged.
- `sanka/llm.py`, `sanka/wa.py`, `sanka/ambient.py`: the same direction/sticker/body rules in
  `APPRAISE_PROMPT`; `_appraisal_context` uses `prompt_entry` (stickers and quotes are no longer
  lost to `if not body: continue`); a "Cara membaca pesan" block in `system_prompt`; chime
  forbids inventing body conditions.
- `sanka/wa.py`: `_name_body` (repeated letters: `s+a+n+k+a+`) is used by `named`/`mentioned`, so
  `SANKAAAAAAAAA`/`SANNNKAAAAAA`/`sankaaaaaa` pass routing and `sankas` does not.
- `sanka/affect.py`: `modes()` adds `relation` (`_relation` for that person) and `person_name`.
- `sanka/commands/mood.py` (new): `/mood` shows the mood label, feeling (valence), energy,
  boredom/loneliness, sulk/cry status, and the relationship with the sender; fallback
  `state emosi belum aktif.` without AffectStore. `guard.EXEMPT_COMMANDS` + `roles.member.commands`
  (config + example) gained `mood`, so `/mood` still answers while Sanka is sulking.
- `sanka/history.py`: `contacts(query, limit)` — individual contacts in the archive, `@lid` mapped to
  PN via `lid_map`, deduped per send JID.
- `sanka/tools/send_to.py`: `_resolve_target` matches JID/exact name/partial against the combined
  group + contact set (ambiguous -> `beberapa target cocok`), an empty list shows `Grup di arsip` and
  `Kontak di arsip`, and an `@lid` JID mapped to a single PN is still sendable. The `cross_send` prompt
  requires a successful `send_to` call before claiming a send ("aku cp kamu aja").
- Tests: `tests/test_gate.py` (sticker context + quote + direction), `tests/test_context.py`
  (`prompt_entry`), `tests/test_commands.py` (`/mood` + without affect), `tests/test_smoke.py`
  (stretched name + routing), `tests/test_send.py` (send to contact + ledger + contact list),
  `tests/test_registry.py` (mood in the registry and the member pattern). `pytest -q` 179 passed,
  `python -m sanka selftest` SELFTEST OK (member now has `mood`).

## 2026-10-27 (dual soul: LLM appraisal, heartbeat, sulk that still answers)

- `sanka/llm.py`: `APPRAISE_PROMPT` + `LLM.appraise` (tool-free, `llm.appraise_temperature`) and
  `parse_appraisal` (intent of 9 values, `directed` bool/"true", `hurt`/`warmth` 0..1, `sarcasm`, `why`
  <=80; invalid required field -> `{}`). `parse_decision` was replaced by `parse_verdict` -> `(act, bubbles)`.
  `LLM.run` uses `llm.temperature`; `decide` uses `gate.decide_temperature`; chime 0.9.
  `LLM.followup`/`parse_followup`/`FOLLOWUP_WORDS` were removed. Appraisal `max_tokens` 300 -> 700
  and digest 500 -> 1200: reasoning models burn the allowance first (`finish=length`), so
  appraisal fell back without `hurt` and digest wrote no topics.
- `sanka/affect.py`: `classify_intent` -> `classify_intent_fallback` (backup, `hurt=0`, never
  sulks). `observe(appraisal=...)` uses the LLM result: intent/direction/hurt/warmth, delta
  scaled by intensity, `_record_event` uses the same intent, `_atmosphere` shifts with
  hurt/warmth. Sulk only from `ABUSE` with `hurt>=0.35` (level 2 when `hurt>=0.70`/high annoyance/anger)
  or real sadness; the `annoyance>=0.40` trigger was removed so playful teasing no longer
  causes silence. `drives` table + `pulse` (mood decay, drive growth, sulk expiry), `chat_people` table
  (chat membership) + `known_people(chat_id)` limited to that chat's members,
  `seed_chat_people` for old DBs, prompt carries drives and interaction/last seen.
- `sanka/wa.py`: appraisal is collected before `observe` (`_appraisal_context`: chat, sender,
  role, affect prompt, last 8 messages, last message) with the
  `affect.max_appraisals_per_minute` guard; failure/limit -> fallback. `sulk_mode`: non-exempt commands
  are rejected with `SULK_REPLIES`, LLM replies use `SULK_PROMPT` without `specs`/memory/skills,
  one bubble; crisis never sulks; an `ignore` gate during sulk records `note_ignored` and
  sends one `hmm` at the threshold. `is_named` (name mentioned without a tag) is passed to the gate.
  Replies: one burst, `planned = min(max_bubbles, max(gate.bubbles, text need))`;
  `_followups`/`FOLLOWUP_RULES` were removed. `_stale` absorbs messages already waiting in the window
  when the last burst is assembled (per-chat id snapshot), so consecutive tags are answered once without
  swallowing messages that arrive afterwards. `system_prompt` receives `person_style` + `sender`.
- `sanka/gate.py`: `Decision.bubbles`, `score(named=...)` (+0.45, cancelling the
  `human_flow` penalty), `_over_limit(attention, direct)`: `direct` messages (tag/DM) pass unconditionally,
  cooldown only for non-attention, chat/person caps only for non-direct. This came from a production
  finding: `@Sanka halooo ?` (score 0.65) was ignored with `reason=rate_limit` after 8 replies/10
  minutes, so tag/DM must not be subject to quotas. `reply_cooldown_seconds` is set to 0 in the example
  (forced pauses off; the model decides to stay silent), LLM decision for every non-command message
  with the `gate.max_decisions_per_minute` guard, `_context` carries own send status, sender
  name, and the note "menyebut namamu tanpa men-tag". `_ignores`/`max_consecutive_ignores`
  were removed; tag/DM `ignore` is overridden to `reply`.
- `sanka/ambient.py`: `prime()` fills `known_chats` from `history.recent_chats(50)`;
  the first heartbeat for a chat without a `drives` row only anchors `last_pulse` (it does not
  jump to energy 0/boredom 1 as it would if `elapsed` were counted from the epoch), and drive growth
  is capped at 24 hours so a long downtime/clock skew cannot max out every drive. `last_decay`
  only advances by the number of `decay_hours` steps actually applied (the remainder is kept), so a
  2-minute tick still decays PAD per hour; the continuous part (annoyance/sadness/active emotion) uses
  `elapsed` since `last_pulse`, not since `last_decay`;
  `_tick` calls `pulse` for every chat before digest; chime is gated by urge from drives + mood,
  the chime prompt uses `affect.prompt`. `sanka/history.py`: `_human_rows` carries `chat_id`,
  `style_sample` returns a dict, `recent_chats`. `sanka/style.py`: `person_profile`
  (`PERSON_MIN=8`), seed per chat/person. `sanka/format.py`: `reply_budget(humanize=...)` reads
  `normal_*`/`detail_*`.
- Config: `llm.temperature`/`appraise_temperature`, `affect.max_appraisals_per_minute`,
  `gate.decide_temperature`/`max_decisions_per_minute`; `humanize.normal_parts=2`,
  `detail_parts=3`, `max_bubbles=3`; `followups`/`max_followups`/`followup_delay`/
  `max_consecutive_ignores` removed (YAML, dataclass, loader). The example config uses
  `require_mention: false` so the gate does the filtering, not mentions.
- Safe upgrade without a reset: the `drives`/`chat_people` tables are created with `IF NOT EXISTS`; the old emotion
  DB stays in use and chat membership is filled once via `seed_chat_people` (flag
  `chat_people_seeded`). Only `.sanka/style.json` needs refreshing (a derived aggregate, safe
  to delete) so the per-person/chat profile is filled straight from the archive instead of waiting for live samples.
- Verification: `pytest -q` 170 passed (new: appraisal->sulk, sulk rejecting tool/command, crisis
  bypassing sulk, tag burst, gate bubbles, chat_people, pulse/drives + first-heartbeat anchor, chime
  urge, person style, group commands following the mention gate);
  `python -m sanka selftest` -> `PASS affect`, `PASS style`, `SELFTEST OK`.
- Risks/remaining: appraisal and gate decision quality depend on the model; LLM cost goes up because
  every non-command message is evaluated twice (appraisal + gate) — reduced via
  `affect.max_appraisals_per_minute`, `gate.max_decisions_per_minute`, or `gate.llm_decide:
  false`. Tests use a fake LLM; smoke in a real group (subtle insults, tagging while sulking, heartbeat
  after 20 minutes of silence) still needs to run on the server.


## 2026-10-27 (emotion, short style, gate, autonomy)

- `sanka/affect.py`: `AffectStore` (SQLite `affect.path`, 0600/WAL/busy_timeout) stores PAD mood +
  OCC emotions per chat, per-person relationships (trust/affection/respect/annoyance/notes), `sulks`, `topics`, and
  `events` over 200 rows. Appraisal uses `affect-kernel` 0.1.0 as-is; custom deltas only for
  trust/affection/respect/annoyance. One insult -> level 1 (`sulk_minutes`), escalating to level 2, crying
  when distress+sadness are high. A sulk does not lift just because annoyance drops; it ends via
  `/calmdown`, two apologies/affection (`appease>=2`), or `until`. Crisis content is never
  written to `events`/notes; crisis messages are still answered.
- `sanka/gate.py`: `ReplyGate` -> `reply|react|ignore`. `LLM.decide` (mood + relationships + last 6
  messages) decides; the deterministic score becomes prior/fallback, `LLM.judge` handles borderline cases
  when `llm_decide` is off. A 10-minute rate limit per chat/person + cooldown beat mentions;
  `max_consecutive_ignores` forces a reply so Sanka does not ghost. Sticker reactions are capped at 1/5 minutes.
- `sanka/style.py`: style aggregate from `history.db` (median, lowercase, particles, emoji) without
  storing text; reference seeds `references/*.md` (median 34) when samples < 20. `history.py` adds
  `resolve_person`, `people_seed`, `style_sample` (no schema change).
- `sanka/format.py`: `reply_budget`, `human_chunks` (whole fences, split per sentence/word, `…`),
  `humanize_reply`, `typing_delay`. `wa.py` sends bubbles: the first chunk quotes the message, the following ones
  use `send_message`, with a typing pause per bubble; commands keep the old single path.
- Double-text: `LLM.followup` + `parse_followup` (`[DIAM]`/duplicates rejected) add at most
  `humanize.max_followups` bubbles up to `max_bubbles`; skipped for commands, crisis, and while
  sulking. Tool-free, so permissions do not grow.
- `sanka/ambient.py`: digest topics+facts into memory/notes, `web_search` research when quiet, an occasional group
  chime, and `neglect` (sadness max 1/hour). Tool context is only `("web_search",)`, never DMs.
- `sanka/guard.py`: one place for crisis/injection/sensitive/tension/fun regexes + `EXEMPT_COMMANDS`
  (`help`, `status`, `whoami`, `reset`, `stop`, `ping`, `calmdown`) and `command_name`.
- New config `affect`/`gate`/`humanize`/`style`/`ambient` (all off by default, `_bool` so
  `"false"` is not truthy); `config.yaml` + the example turn them on. Persona rewritten: a man
  with feminine emotional expression, not an assistant, never admits being an AI. New command `/calmdown`
  (owner, alias `/tenang`).
- Background token budget: reasoning models spend the allowance before writing, so a small
  `max_tokens` yields empty text (finish=length). `decide` 400, `judge` 600, `followup` 400,
  `chime` 300, `digest` 500, `research_summary` 250; empty text is logged as a warning and automatically
  falls back to the deterministic path instead of failing silently.
- Verification: `pytest -q` 147 passed (new: affect/gate/style/ambient + reply integration and
  `system_prompt`); `python -m sanka selftest` -> `PASS affect`, `PASS style`, `SELFTEST OK`.
- Risks/remaining: gate decision quality and double-text material depend on the model; background LLM cost can
  be reduced via `gate.llm_review: false`, `gate.llm_decide: false`, `ambient.digest_every`, and
  `ambient.research_per_day`. Smoke in the tester group (teasing, insults, /calmdown, digest, research)
  has not run because it needs a real WhatsApp session.


## 2026-10-22 (docker socket gid + ownership)

- `Dockerfile`: `ARG DOCKER_GID=994` after `RUN useradd ... sanka`; `groupadd -g "${DOCKER_GID}"
  docker || true` then `usermod -aG docker sanka`, before `USER sanka`. The value can be overridden via
  `--build-arg DOCKER_GID=<gid>`; the host reference is `stat -c %g /var/run/docker.sock`.
- `sandbox`: the `docker` runtime adds `--user <uid>:<gid>` so files in `/work` belong to the bot uid,
  not the daemon's root. Podman does not use `--user` (it keeps `--userns=keep-id`).
- Exit != 0 with `permission denied` + `docker.sock` -> an actionable message naming
  `DOCKER_GID` and `stat -c '%g %a' /var/run/docker.sock`; it does not include the environment.
- Verification: `pytest -q` 85 passed; the image was built with `--build-arg DOCKER_GID=994`, inside it
  `id` = `uid=1000(sanka) gid=1000(sanka) groups=1000(sanka),994(docker)` and Docker CLI 27.5.1.
  No Docker daemon on the dev machine, so `docker ps` and `/work` ownership were tested live on the server.

## 2026-10-22 (sandbox podman/docker)

- The `sandbox` tool supports two runtimes: `podman` (flags `--userns=keep-id`, mount `:Z`) and
  `docker` (neither). An empty `sandbox.runtime` = autodetect podman then docker; if neither is
  available, the tool returns an error explaining how to fix it, not an exception.
- `sandbox.host_workspace` (optional) becomes the bind mount source when the daemon runs on the host (Docker/
  Dokploy): the `/app/...` path does not exist on the host. This path is not validated with `is_dir()` from inside the
  container because the namespaces differ; a value that looks like a file is still rejected at start.
- New isolation: `--rm`, `--network none` (overrides `sandbox.network`), read-only rootfs + tmpfs
  `/tmp`, the last 16 KB of output marked `(dipotong)`, and the per-call timeout still kills the process.
  Container env is only `TZ`/`LANG` (+ `sandbox.env`); the CLI env is minimal, API keys are not included.
- `Dockerfile`: static Docker CLI 27.5.1 at `/usr/local/bin/docker` (curl installed then removed).
  Podman is not in the image.
- Verification: `pytest -q` 84 passed; `python -m sanka selftest` PASS including the Podman sandbox; direct
  Podman smoke: `/work` mounted, uid 1000, tmpfs writable, workspace write ok; the Docker CLI download
  recipe was tested in `python:3.12-slim` (Docker version 27.5.1). No Docker daemon on the dev
  machine, so the docker path was tested via argv/mock.

## 2026-10-22 (deploy + archive)

- Production `Dockerfile`: `python:3.12-slim`, ffmpeg + libmagic + tzdata, package install, uid
  1000 user, `STOPSIGNAL SIGINT` so Docker stop closes SQLite cleanly. `.dockerignore` drops `.sanka/`,
  `config.yaml`, `.env`, venv, caches, and tests from the build context.
- `scripts/backup.py`: archives `.sanka` + `config.yaml` + `.env`; the DB is copied via the sqlite backup API
  (safe while the bot is running), `integrity_check`, WAL checkpoint, tar.gz 0600 + sha256 + MANIFEST.
- README: the Dokploy Application path (GitHub auto deploy, bind mount `/opt/sanka/.sanka` and
  `config.yaml`, replica 1/stop-first), data cutover, and container limitations (`sandbox` Podman,
  SearXNG loopback, uid bind mount).
- Verification: `pytest -q` 76 passed. Image `sanka-agent:test` (781 MB) was built in 287 sec;
  the first build downloaded ffmpeg (207 packages, 477 MB), later layers were cached. Inside the image:
  neonize/magic import ok, ffmpeg/ffprobe 7.1.5, uid 1000; container start reached
  "Sanka siap. Scan QR..." and only failed because of `--network none`.
- The data/session archive was made from the running bot: integrity_check ok, the session holds 1 device
  + 494 contacts, history 386 messages / 3 chats. For the final cutover, stop the bot and run
  `python scripts/backup.py` again so the snapshot is truly final.

## 2026-10-21

- Simple rewrite: one Python process (Neonize 0.5.2), no gateway, no Node bridge.
- Six tool families: `shell`, `read`, `write`, `web_search`, `sandbox` (Podman), `mcp`.
- Discord-style permissions: `roles` in YAML, glob patterns, checked at a single choke point `tools.execute`.
- In-memory conversation history (30 messages/chat), `/reset` clears it.
- Three smoke tests: role/glob, DM/group routing, WhatsApp reply formatting.
- Docs trimmed to four files: `TASK.md`, `README.md`, `LOG.md`, `AGENTS.md`.
- Verification: `pytest -q` 3 passed; `python -m sanka selftest` PASS write/read, shell, sandbox
  (Podman 6.1.4, image python:3.12-slim), web_search, LLM (it needs an `x-opencode-session` header
  and its own User-Agent — without that OpenCode Go replies 400 MissingSessionID).
- Real tool calling was tested against the model: the prompt "baca selftest.txt" triggered the `read` tool and the answer
  contained the file contents. MCP was tested with a local stdio server (`mcp__demo__tambah` -> `42`).
- Not yet: real WhatsApp pairing (needs a QR scan from the owner's phone).

## 2026-10-21 (registry handler)

- `sanka/registry.py`: a single `Handler` shape (name, aliases, description, usage, parameters,
  per-role defaults, pattern) + `Registry` (name/alias, duplicate detection, permission patterns).
- Commands moved to `sanka/commands/` and are auto-imported (`pkgutil`): `ping`, `help`, `reset`
  with aliases `/p`, `/h` `/bantuan`, `/r` `/clear`. `/help` reads the registry metadata.
- Tools use the same `Handler`; `execute` remains the single choke point (permissions, aliases, errors, truncation).
- A role without `tools:`/`commands:` in YAML uses the handler defaults; an explicit list still wins.
- Verification: `pytest -q` 3 passed, `selftest` PASS on everything, `/p`, `/h`, `/r`, an unknown command,
  a role without a list (default), and a drop-in command module detected without changing the dispatcher.

## 2026-10-21 (replies & pairing)

- Live bug: `client.reply_message(chunk, ev.Message)` threw `AttributeError: Info` because Neonize
  needs the event wrapper (`Info` + `Message`) to determine the target chat and the quote. Fix: pass the whole `ev`
  in `sanka/wa.py`.
- Regression tests `tests/test_reply.py` (3): a DM uses the wrapper + quote ID/participant/text, a group
  replies to the group JID instead of the sender, an LID sender uses `SenderAlt`. The tests use the
  real `NewAClient.build_reply_message`; only `send_message` is replaced by a recorder — reverting the fix =
  3 tests fail with the same traceback.
- The test rule in `AGENTS.md` was loosened: the suite stays small, focused on the repo contract rather than re-testing
  library behavior.
- Verification: `pytest -q` 6 passed; `python -m sanka selftest` PASS; the owner confirmed replies
  work in both DMs and groups after real pairing.

## 2026-10-21 (tool auto-discovery)

- `sanka/tools.py` became the `sanka/tools/` package: one module per tool (`read`, `write`, `shell`,
  `web_search`, `sandbox`, `mcp`) with `TOOL = Handler(...)` plus `family`; `pkgutil` auto-imports
  them like commands. `ToolContext`, `execute`, and `FAMILIES` stay in `__init__.py`.
- `sanka/registry.py`: `validate()` enforces the handler contract (name = file name, name/alias pattern,
  description, `fn`/`pattern`, `family`, `parameters` schema, `defaults`). `discover()` collects
  all plugin errors and then raises `RegistryError` once; duplicate names/aliases are aggregated too.
  `Registry.patterns` accepts `None` (handler default), unlike `()` (reject all).
- `python -m sanka` wraps first-party imports: an invalid plugin exits with a single line
  `sanka: handler tidak valid: ...` and exit 1, not a traceback. `run_bot` logs the discovered
  command/tool list and the roles using wildcards.
- `RoleConfig.tools`/`.commands` become `tuple | None`: an unwritten field = handler default,
  `[]` = reject all. `sanka/mcp.py` gives dynamic handlers `family="mcp"`.
- New test `tests/test_registry.py` (4): built-in handlers, drop-in tool, error aggregation, `[]` permission.
- Verification: `pytest -q` 10 passed; `python -c` lists the discovered commands/tools;
  a drop-in tool `echo_tmp` is detected without editing core; invalid plugins (`COMMAND = 1`, `TOOL = 1`)
  exit 1 with one message; `python -m sanka selftest` PASS write/read, shell, sandbox,
  web_search, LLM, `SELFTEST OK`.

## 2026-10-21 (typing status)

- `sanka/wa.py`: a message that passes `should_process` immediately sends `COMPOSING` presence
  (media `TEXT`) to the originating chat JID, refreshed every `TYPING_REFRESH` (8 seconds) by a small task
  while the LLM/tools run, then `PAUSED` in the `finally` block — including when the handler fails.
- Presence is cosmetic: its exceptions are swallowed and logged at `log.debug`, so the reply is still
  sent even if the server rejects the presence update.
- Enums come from `neonize.utils.enum` (`CHAT_PRESENCE_COMPOSING`/`_PAUSED`, `..._MEDIA_TEXT`), not the
  proto enum: the neonize Go code uses `state.value` as a slice index, and the proto enum (1/2)
  would be out of range.
- Test `tests/test_reply.py` (2 new): the order `COMPOSING` -> reply -> `PAUSED` to the group JID, and
  a group without a mention produces no presence at all.
- Verification: `pytest -q` 12 passed; a refresh smoke run (LLM 90 ms, 20 ms interval) produced 5
  `COMPOSING` with no leftover task; a presence call that always raises does not fail the reply;
  `python -m sanka selftest` PASS.

## 2026-10-21 (context & attachments)

- `sanka/chat.py` (new): a 20-message log per chat (`ChatMessage`/`Quote`/`Media`, `Sessions`),
  media descriptors from proto fields (unwrapping `viewOnce`/`ephemeral`/`documentWithCaption`), downloads via
  `download_any` into `workspace/attachments/<chat>/` (old files are reused), and
  `build_user_content`, which assembles the payload.
- The payload carries name, number, local time, text, quote (`↳ membalas`), and an attachment line per
  message. Images are picked in order (newest message -> its quote -> the rest, max 6 / 5 MB / 10 MB)
  and then injected as data URLs; documents with an `image/*` MIME type are injected too because detection uses
  MIME/extension, not the field name. Long text is truncated and stored whole in
  `workspace/whatsapp/<chat>/<id>.txt` with a `read path="..."` marker.
- `sanka/wa.py`: recording happens before routing (a group without a mention still enters the log, without
  presence), quotes are resolved via `stanzaID`, `should_process(has_media=True)` accepts images
  without a caption, bot replies are recorded as `outgoing` entries, and the system prompt explains the
  payload/images/sandbox permissions. The chat-completion style history was removed; `HISTORY_LIMIT` 30 -> 20.
- `sanka/llm.py`: `run(system, user_content, ...)` accepts `str | list[dict]`; an APIError 4xx on an
  image payload is retried once without the images plus a path note (instead of going straight to `API_ERROR_TEXT`).
- Bug found by the tests: `Info.VerifiedName` is a proto message (not a string), so using
  `Pushname or VerifiedName or ...` produced an empty sender name. The name now comes from `Pushname`
  with the number digits as fallback, tested through a quote showing `628222222222`.
- Tests: `tests/test_context.py` (6) for log/payload/truncation/images/PDF/quotes,
  `tests/test_reply.py` (2 new) for group history and quotes from the log, `tests/test_smoke.py`
  (1 new) for media routing without a caption. 22 passed in total.
- Verification: `pytest -q` 22 passed; `python -m sanka selftest` -> `SELFTEST OK`.
- Real smoke run with `deepseek-v4.1-flash`: a 64x64 red PNG sent as `documentMessage`
  (`image/png`) -> reply "Merah"; a PDF => the model called `sandbox` twice and answered
  `KODE-RAHASIA-123`; 40 lines of text => the model called `read` on the marker path and answered
  "baris 40"; an image payload rejected with HTTP 400 was retried without the images. The `member` role without
  `sandbox` was rejected (`error: tool 'sandbox' tidak diizinkan untuk role 'member'`).

## 2026-10-21 (runtime role permissions)

- `config.yaml` (gitignored): `roles.owner.users` filled with the real number + LID from the session DB
  (`6285111242960`, `130799689031758`); `roles.member.tools` gained `sandbox` at the owner's request,
  so member = `read`, `web_search`, `sandbox`. `shell`/`write` stay owner-only.
- The `sandbox` handler keeps `defaults=OWNER_ONLY`; other roles can only get it through an explicit
  `tools:` list. README fixed (it is not absolutely "owner-only").
- Evidence: sender `628999999999` (member) with the real config -> `role=member`, the model called
  `sandbox` twice and answered the PDF contents `MEMBER-OK-777`. The bot was restarted at 23:54:37 and reconnected.

## 2026-10-21 (bot identity in the prompt)

- `sanka/wa.py`: `system_prompt(config, role_name, bots)` adds an identity line
  (`Identitasmu: <bot_name>, nomor <nomor HP + LID>. Mention @<nomor itu> ... adalah untukmu`).
  `handle_message` passes `app.bot_digits`. Without this the model read the mention
  `@73041086251252` (the bot's LID) as someone else's number, exactly like the live reply at 23.54
  ("mention-nya ke @+7... bukan ke aku").
- Test `tests/test_smoke.py`: the prompt contains the PN number + the bot LID (23 tests passed).
- Live evidence: a group event with `mentionedJID` = the bot LID -> "Itu akun aku sendiri, Sanka."
  Without the identity line the model instead invented an LID -> number mapping ("terhubung ke 628567770837")
  even though that number was not in the payload.
- The bot was restarted at 23:58:46 (PID 38536, connected) so the new prompt took effect.

## 2026-10-22 (web_search SearXNG)

- `sanka/config.py`: `WebSearchConfig(base_url, timeout_seconds)`; YAML key `web_search`; empty =
  ddgs only. `config.example.yaml` got an example, and `config.yaml` was pointed at
  `http://127.0.0.1:8080` (the existing `sanka-searxng` container).
- `sanka/tools/web_search.py`: `_searx` calls `GET /search?q=...&format=json` (urllib inside
  `to_thread`, UA `sanka-agent/0.1`, timeout from config), `searx_hits` maps the JSON -> uniform hits
  and drops entries without a URL. It falls back to the `ddgs` loop (`WEB_BACKENDS`) when the instance is down,
  the JSON is rejected, or results are empty; one log line, no traceback.
- Measurement (query "searxng vs duckduckgo"): SearXNG 41 results/0.70 s vs ddgs with only 1 working backend
  (yahoo) 5 results/1.33 s; `backend="auto"` failed entirely because `html.duckduckgo.com` was unreachable.
- Tests: `tests/test_smoke.py` (+2) for `searx_hits` and `web_search` YAML parsing (25 tests passed).
- Live evidence: the tool with the real config -> 5 URLs from SearXNG (1.66 s); `base_url` pointed at a dead
  port -> log `searxng ... gagal (URLError...); fallback ke ddgs` and the ddgs results still came out.

## 2026-10-22 (real mentions & clean text)

- `sanka/chat.py`: `clean()` strips bidi isolation characters (`U+2068`/`U+2069`, `U+200E/F`,
  `U+202A-E`) and NBSP; it is used by `message_text()` and `_flatten()` so the payload no longer carries
  "dirty" mention tokens. `ChatMessage.sender_jid` keeps the original JID (`…@lid` / `…@s.whatsapp.net`)
  for reply mentions.
- `sanka/wa.py`: `_resolve_mentions()` turns `@<number>` in a reply into the canonical `@<digits>` and
  collects the chat participants' JIDs; replies are sent as `ExtendedTextMessage` with
  `contextInfo.mentionedJID` set explicitly (the quote still goes through `reply_message`). `format_reply` output
  passes through `clean()` first. The prompt adds a line: write `@<number>` exactly, without `+`/spaces.
- No global `mentions_are_lids` flag: JIDs are stated explicitly, so an LID sender and a phone number can
  both be mentions in one reply.
- Tests: `tests/test_reply.py` (+2) for an LID mention (shape `@+1 3079 96890 31758`) and a phone number
  plus an unknown token; `tests/test_context.py` (+1) for stripping isolation characters; 28 passed in total.
- Live evidence: a group message with the mention `@⁨Sanka⁩`, the model answered `Halo Muqsith! @130799689031758`
  -> outgoing proto `mentionedJID=['130799689031758@lid']`, the `stanzaID` quote intact, no `U+2068`.
- "Le Watasi" in the 00.07 reply is a model artifact (non-deterministic, not from the payload);
  mitigation: the payload and replies are now free of isolation characters + mention tokens are canonical.

## 2026-10-22 (read/write locked to the workspace)

- `sanka/tools/__init__.py:_resolve()` now resolves the path (including symlinks) and then rejects
  anything outside `workspace` with `PermissionError`: `/etc/passwd`, `../config.yaml`, and
  `~/.sanka/whatsapp.db` sent by a member through `read` went from leaking to
  `error: path di luar workspace: ...`. Previously absolute/`~/...` paths were used as-is.
- `execute()` catches `PermissionError` without `log.exception` so ordinary path rejections
  do not flood the log with tracebacks; the message is still truncated by `MAX_ERROR`.
- The `read`/`write` parameter descriptions and the `workspace` table in the README state this boundary.
- Test `tests/test_context.py:test_paths_confined_to_workspace`: relative and absolute paths inside the
  workspace are still readable; `/etc/passwd`, `../catatan.txt`, and the symlink `escape -> /etc/passwd`
  are rejected; `write` cannot escape the workspace either.
- Verification: `pytest -q` 29 passed; `python -m sanka selftest` SELFTEST OK (write/read, shell,
  sandbox, web_search, LLM); a direct smoke run with the real config: the member role only has
  `read, web_search, sandbox, mcp__*`, the three outside paths were rejected, and `selftest.txt` stayed readable.

## 2026-10-22 (message archive & backfill)

- `sanka/history.py` (new): a per-account SQLite `history_db`, a `messages` table + FTS5, keyed on
  `(chat_id, msg_id)`, WAL, `secure_delete`, files `0600` and directories `0700`. All live messages
  (including `IsFromMe` from the owner's own phone) and the whole `HistorySync` are stored; media/quote
  protos are stored too so old attachments can be downloaded again.
- `Sessions` becomes write-through: the last 20 messages are loaded from the DB after a restart, `/reset`
  uses a `reset_ts` cursor so only the active window is emptied, and old quotes are looked up through the DB.
- `HistorySyncEv` is registered in `run_bot`; ingest runs in batches of 500 rows on a separate thread. DeviceProps
  asks for 730 days/5 GB, `requireFullSync` while `full_sync_done` is missing, and the
  `supportGroupHistory` field (15) is inserted manually because the Neonize 0.5.2 Python proto lacks that
  field while its Go binding has it.
- The `chat_history` tool (`family=read`) searches text/names in the archive **only for the active chat**, with
  `query`, `before`, `after`, `limit`; the system prompt tells the model that old messages live in the archive.
- Backfill for sessions that were already paired: `/backfill` (owner, DM) sends the QR as an
  image, uses a separate `backfill.db`, writes into `history.db`, then logs out + deletes the session. The CLI
  `python -m sanka backfill` provides a terminal path.
- `config.py`: `whatsapp.history_db` (default `~/.sanka/history.db`) and `secure_db()` for
  `session_db`/`history_db`; an old session DB that was `0644` is secured to `0600`.
- Verification: `pytest -q` 35 passed; `python -m sanka selftest` SELFTEST OK; a live restart wrote
  11 test messages into `history.db` and the `chat_history` tool returned that chat's archive; the
  `python -m sanka backfill` smoke run showed a QR without disturbing the main session.
- Real limitation: WhatsApp only sends history when a device is linked; an existing session cannot
  replay the initial sync, and Neonize 0.5.2 does not expose on-demand history requests
  (`SendPeerMessage` is absent from the binding), so backfill requires one QR scan.

## 2026-10-22 (chat names)

- `chats` now has a `name` column; old DBs are migrated automatically with `ALTER TABLE` when
  `HistoryStore` is opened. `reset` keeps the name and only advances `reset_ts`.
- Names are filled from `Conversation.name`/`displayName` during `HistorySync` ingest, from
  `GroupInfoEvent.Name` when a group changes, and from the push name for DMs that have no name yet.
  An existing name is not overwritten by ordinary DM messages.
- The payload header becomes `Konteks WhatsApp "<nama>" (...)`, the `chat_history` tool shows
  `Arsip "<nama>"`, so the model and reports no longer guess from the message contents.
- The names of the three existing chats were filled from the owner's corrections: Tester Sanka & MapHb,
  ALD XXVII JHEPRET CLUB FOTOGRAFI, and the Muqsith DM.
- Verification: `pytest -q` 37 passed; the old DB migration was tested; the tool directly showed
  `Arsip "Tester Sanka & MapHb"`; the live bot was restarted with the new schema.

## 2026-10-22 (sender names from contacts)

- `history.db`: the `contacts(jid, name, push_name)` and `lid_map(lid, pn)` tables; LID/PN are normalized
  to full JIDs. `import_contacts()` reads `whatsmeow_contacts` + `whatsmeow_lid_map` from the session
  DB read-only on start/connect; `HistorySync` adds `inlineContacts`/`pushnames`.
- Name resolution: contact `full_name`/`first_name` → contact name via the LID→PN mapping → push name → ID.
  `_refresh_sender_names()` updates old messages that still contain an LID/number or a push name,
  using the `messages(sender_jid)` index; bot/outgoing names are left alone.
- Live messages use the contact name when available; a DM name is only filled if it is still missing.
- Evidence in the archive: `20289710768283@lid` → “Kak Fahri JC”, `130799689031758@lid` →
  “Muqsith Utama”, the ALD group stays at 110 messages.
- Verification: `pytest -q` 39 passed; tests for inline contacts + refreshing old contacts; the live bot was
  restarted with the contact resolver.

## 2026-10-22 (memory, skills, file sending)

- `sanka/memory.py` (new): SQLite `memory.db` (0600) for durable facts/preferences.
  One table with `scope` `chat`/`global`; dedupe on `(scope, kind, text)`, LIKE search, deletion
  by id/query, and a bounded L1 block (`max_items`, `max_chars`) for the system prompt. A role with the
  `memory` tool gets chat + global memory.
- The `memory` tool (owner + member) with `scope=chat|global` for `add/search/list/forget`. The command
  `/memori [global]` to add/search/list/delete. The AI decides when to store/forget; the prompt
  insists that only explicit/durable facts may be stored.
- `LLM.reflect` + `App.reflect`: after a turn (except commands) the model extracts 0-3 durable facts/
  preferences/procedures automatically (`memory.auto_extract`, on by default), then
  stores them with chat/global scope. It runs as a background task so it does not hold up replies;
  a regex filter for credentials/secrets and SQLite dedupe. The system prompt also tells the model
  to decide on its own when to load/store skills and memory instead of waiting for a user command.
- `sanka/skills.py` (new): scans `<skills.dir>/**/SKILL.md`, `name`/`description` frontmatter,
  a bounded name+description index (`max_index_chars`) for progressive disclosure; the `skill` tool
  handles `list/view/read/save/archive` for owner + member. Archive moves the folder to
  `.archive/` instead of deleting it; supporting files are confined to the skill folder; invalid/duplicate skills
  are skipped. The `/skill` command lists/loads. This adapts GenericAgent's L1/L2/L3/L4 + the Hermes
  SKILL.md format.
- `sanka/tools/send.py` + `sanka/commands/kirim.py` + `App.send_file`: send a document from the workspace
  to the active chat (max 100 MB); success is recorded as `(file terkirim: ...)` in the context window.
  `resolve_path()` was extracted from the tool context so the command uses the same workspace boundary.
  `send`/`kirim` are available to owner and member.
- `sanka/wa.py`: the system prompt receives the memory block + skill index; `ToolContext` carries the memory/
  skill stores and the `send_file` callback. Memory (chat/global), skill lifecycle, and file sending
  are available to owner + member; the AI decides.
- `config.py` / `config.example.yaml`: the `memory` and `skills` sections. `TASK.md` notes full
  integration (URL/PDF/audio/calendar/email) and task state/crash recovery as later work.
- Verification: `pytest -q` 56 passed; `python -m sanka selftest` SELFTEST OK (including memory and
  skills); store/tool/command/file smoke runs with a fake client passed.

## 2026-10-22 (memory & skill feedback)

- `sanka/wa.py`: reflection is now gated—only roles that have the `memory` tool, user messages ≥8
  characters, and non-commands. The previous 8-message transcript (truncated to 3000 characters) is sent to
  `LLM.reflect` too, so facts from the running conversation get captured. The context is taken before the reply
  is sent so the bot's answer is not counted twice.
- `sanka/memory.py`: `context()` takes a `query`; entries matching the last message come first,
  the rest is filled with the newest, and each line carries the `updated_at` date. The `max_items`/`max_chars`
  limits are unchanged.
- `sanka/skills.py`: a `.usage.json` sidecar (atomic write) records `uses` + `last` on each `view`; the index
  shows `(dipakai N×, terakhir YYYY-MM-DD)` so `archive` has evidence. The `SKILL.md` format
  is unchanged.
- `sanka/llm.py`: the reflection prompt insists that `kind=rule` is only for short rules; step-by-step procedures
  are skill material, not memory.
- New tests: role gate, the transcript that is sent, skipping short messages, query + date ranking, `.usage.json`
  telemetry. Verification: `pytest -q` 59 passed; `python -m sanka selftest` SELFTEST OK.

## 2026-10-22 (memory supersede)

- `sanka/memory.py`: a `superseded_by` column + automatic migration for old DBs (including a fallback when
  another process added the column first). `add(..., supersedes=<id>)` validates an active id, one
  scope, one kind, and not itself; the old row is marked, not deleted. Adding back
  text that was once superseded reactivates it (`superseded_by=NULL`). `_select` only
  returns active entries, so the prompt/search is clean automatically.
- `sanka/tools/memory.py`: a `supersedes` parameter + the `#lama digantikan` message; the tool
  description explains the contract.
- `sanka/llm.py` + `sanka/wa.py`: reflection receives the active memory block with ids and may fill in
  `supersedes`; ids are validated by the store. If a supersede is rejected, the new fact is still stored without
  it. The system prompt tells the model to fill `supersedes` only when replacing an old entry.
- Tests: supersede hides the old row but keeps the row, rejecting different scope/kind/dead id/self,
  old DB migration, the tool, id parsing (`"7"` accepted, `true` rejected), reflection + fallback. Verification:
  `pytest -q` 61 passed; `python -m sanka selftest` SELFTEST OK; the real DB `~/.sanka/memory.db` already
  has the `superseded_by` column.

## 2026-10-22 (sticker/photo/document send paths)

- `sanka/wa.py`: `send_kind()` reads the file header, not the name. WebP with dimensions ≤512 px and
  alpha/animation markers is sent via `send_sticker`; JPEG/PNG and other WebP via `send_image`;
  everything else stays `send_document`. The parser handles VP8X, lossy VP8, and VP8L with no new dependencies.
- WhatsApp does not support sticker captions, so they are sent as a text message after the sticker; the
  context entry stays a single `(stiker terkirim: ...)`. The tool result now names the kind (`stiker`/`foto`/
  `file terkirim`) so the model knows what actually went out.
- The `send` tool description, `/kirim`, README, and TASK were updated. The new contract tests use synthetic
  WebP headers: a 342 px sticker, an 800×600 WebP as a photo, a JPEG as a photo, a `.bin` as a document.
- Verification: `pytest -q` 63 passed; `python -m sanka selftest` SELFTEST OK. Real-file smoke:
  `selfie-topi-hitam.webp` (342×342, animated, 242 KB) was detected as a sticker and the Neonize conversion
  produced a 512×512 animated WebP of 411 KB (<500 KB); a static 512×512 sticker of 135 KB was also detected
  as a sticker.

## 2026-10-22 (sending to other groups)

- `sanka/tools/send_to.py` (new, `family=write`, owner default): targets are only groups that already exist
  in the archive. `chat` accepts a JID, an exact name, or a name fragment; multiple/unknown
  candidates are returned as a list (name + JID) without sending. `text` and/or `path`+`caption`; the path
  is still confined to the workspace via `_resolve`. The old `send` remains for the active chat.
- `sanka/history.py`: `groups(query, limit)` unions groups from `messages` and `chats` (names included,
  newest first) so a target can be found without opening another group's contents.
- `sanka/wa.py`: `App.send_text(chat_id, text)` sends text to any chat, uses the real mention
  from the target chat's log, and immediately does `Sessions.append(... outgoing=True)` in the target chat — so
  that group's context window + `history_db` are realtime instead of waiting for an echo event. `ToolContext`
  carries `send_file` (now `chat_id` first) and `send_message`; the prompt only mentions `send_to`
  when that tool is in the role.
- Inbound messages from other groups were already recorded in `handle_message` before the mention gate, so
  there is no `fetch` tool; the `test_group_history_keeps_unaddressed_messages` test keeps that path
  alive. Cross-group outgoing sends are covered by new tests: name-fragment resolution, writing to the target
  chat DB, the origin chat untouched, ambiguous targets, unknown targets, the group list, and rejection for the member role.
- Verification: `pytest -q` 65 passed; `python -m sanka selftest` SELFTEST OK (owner has `send_to`,
  member does not). Smoke on a copy of the real `~/.sanka/history.db`: `chat="jhepret"` -> JID
  `120363422939208659@g.us`, the fake transport received the text, the DB recorded `outgoing=1` with the name
  `sanka`). The live bot was restarted at 03:05:05 (PID 103256, log `~/.sanka/sanka.log`); startup showed the
  `send_to` tool and it connected to WhatsApp.

## 2026-10-22 (cross-chat action ledger)

- `sanka/memory.py`: an `actions` table (`ts`, `source`, `source_name`, `target`, `target_name`,
  `kind`, `summary`, `request`) in `memory.db`, a recency index, `log_action` (trimmed to 200 rows,
  dropping credential-bearing content/requests), `recent_actions`, and `actions_context` (5 actions, ≤800 chars).
  The credential filter became `memory.SENSITIVE` and is shared with reflection.
- `sanka/tools/send_to.py`: every successful send component writes one ledger row; transport failures
  write nothing. Origin/target chat names are taken at send time so the prompt stays readable if a
  group is renamed. Logging is best-effort: a ledger error does not cancel the send.
- `sanka/tools/__init__.py` + `sanka/wa.py`: `ToolContext.request` carries the triggering message; the system
  prompt receives the `actions` block after memory and before skills, only for roles that have
  `send_to` (owner default). The target chat now knows where an artifact came from without merging transcripts.
- `README.md`: the five-layer model (identity, episodic, semantic, procedural, autographic) + an explanation of the
  ledger; the L1-L4 mapping is unchanged. `TASK.md`: the autographic memory item.
- Tests: ledger cap/order/format/credential filter; a successful `send_to` records source/target/name/
  request and a failure records nothing; the prompt block order memory → actions → skills.
- Verification: `pytest -q` 68 passed; `python -m sanka selftest` SELFTEST OK. The real DB already has the
  `actions` table. The live bot was restarted at 03:22:41 (PID 108021) and connected to WhatsApp; the tool list
  includes `send_to`. Live cross-chat sending was not forced so nothing went to a real group.

## 2026-10-22 (project-local runtime data)

- `sanka/config.py`: `_expand(value, base)` resolves relative paths from the `config.yaml` folder, not the
  CWD; `~`/absolute paths stay as-is. The defaults for `memory.path` and `skills.dir` become `.sanka/...`,
  as do the `load_config` fallbacks for workspace, session DB, history DB, memory, and skills.
- `config.example.yaml` + the local `config.yaml`: every data path moved to `.sanka/...` with a resolve
  note; `.gitignore` adds `.sanka/`. `README.md` explains runtime data living inside the project.
- Migration: the bot was stopped gracefully, the contents of `~/.sanka` (whatsapp.db, history.db, memory.db, skills,
  workspace, sanka.log) were moved to `.sanka/`, then `~/.sanka` was deleted. Row counts were preserved:
  386 archive messages and 3 memories.
- Verification: `pytest -q` 69 passed (including the relative-path resolve test); `python -m sanka selftest`
  SELFTEST OK. Every path from `load_config` is under the project root. The live bot was restarted at
  03:29:20 (PID 109867) and connected to WhatsApp.

## 2026-10-22 (post-migration audit)

- `sanka/config.py`: `_reject_legacy` rejects `workspace`, `whatsapp.session_db`, `history_db`,
  `memory.path`, and `skills.dir` that still live under `~/.sanka`, with a message to move them
  to `.sanka/` relative to the config folder. The old `load_config` test ensures startup fails instead of
  silently recreating the old directories.
- `sanka/tools/__init__.py`: `resolve_path` rejects paths under `~/.sanka` with a message that the data
  has moved to the project's `.sanka/`; the path confinement test ensures that message appears, so an old
  archive does not lure reads into a directory that has been deleted.
- Runtime audit: `~/.sanka` does not exist, no process/fd opens the old paths, and bot PID
  111844 only opens `.sanka/{whatsapp,history,memory}.db*` + `.sanka/sanka.log`. The active 20-message
  window contains no old paths.
- The `sanka-service-lifecycle` skill was updated: data in the project's `.sanka/`, the detached start
  recipe, FD verification via `/proc/<pid>`, and a ban on compatibility symlinks. The old service harness
  (`sanka`, `sanka-gateway`, `sanka-bridge`, `fake-bridge`) was removed.
- The remaining `~/.sanka` text is only guards/tests/docs and history in `LOG.md`/`TASK.md`; old
  conversation archives in `workspace/whatsapp/*.txt` may contain old paths as message content, not as
  configuration or active reads.
- Verification: `pytest -q` 70 passed; selftest OK; the live bot was restarted at 03:35:02 (PID 111844) and
  connected to WhatsApp.

## 2026-10-22 (soul + command card)

- `config.yaml` + `config.example.yaml`: `persona` refilled with Sanka's soul from SankaAgentOld
  (Si Kecil Penjelajah Sanankerto): identity, speaking style, WhatsApp formatting, and answer rules.
  Still a single source in config, with no new `SOUL.md` (AGENTS: only four docs).
- `sanka/commands/help.py`: `/help` is a WhatsApp card again, `🧭 *Command Sanka*`, listing the
  commands allowed for the role, aliases, usage, and the closing "ngobrol biasa tanpa slash"; the role/tool
  lines were removed. Command descriptions use emoji.
- `sanka/commands/{ping,reset,kirim,memori,skill,backfill}.py`: command replies use the old style
  (`🏓 Pong! Sanka hidup dan siap.`, `🧹 Siap! ...`, `🧠`/`🔎`/`✅`/`📚`/`📤`/`🗂️`); serious errors
  stay emoji-free. The memory list uses `•`.
- `sanka/wa.py`: an unknown command points to `/help`; a forbidden command uses `🔒`.
- Tests: `test_help_renders_whatsapp_command_card` locks the header/footer and the absence of tool lines;
  the unknown-command reply expectation was updated.
- Verification: `pytest -q` 71 passed; `python -m sanka selftest` SELFTEST OK; the real 2354-char persona
  entered the system prompt; the live bot was restarted (PID 113710) and connected to WhatsApp.

## 2026-10-22 (command observability)

- `sanka/commands/whoami.py`: sender JID, role, chat type/name/JID, and bot number; the sender now
  travels in `CommandContext` (field `sender`, filled from the Neonize event).
- `sanka/commands/status.py`: model, role, chat, archive count + active window, chat/global memory,
  skills, and the tool/MCP counts. Every read is best-effort; a broken store does not block the reply.
- `sanka/commands/tools.py`: the list of tools actually allowed for the role, descriptions truncated to 110 chars.
- `sanka/commands/cari.py`: active-chat archive search without an LLM, 10 newest results first, time +
  sender + 180-char text.
- `sanka/commands/help.py`: `/help <command>` shows the function, usage, aliases, and rejects commands
  not available to the role. Registry lookup is lazy so discovery does not become circular.
- `/reset` gained the alias `/new`. `MemoryStore.count(scopes)` counts active entries without reading
  the whole content.
- `config.example.yaml` + `config.yaml`: the member commands gained `whoami`, `status`, `tools`, `cari`.
- Tests: `tests/test_commands.py` locks whoami/status/tools/cari/help detail output; the registry
  set was updated; `MemoryStore.count` is tested.
- Verification: `pytest -q` 76 passed; a real handler smoke run with a temporary store produced every
  expected command card; `python -m sanka selftest` SELFTEST OK; the live bot was restarted
  (PID 115972) and connected to WhatsApp.

## 2026-10-29 — Soul engine + gateway refactor

- The core moved to `sanka/engine/` (agent, config, DB, prompt, store, tools, commands). `sanka/wa.py`
  was removed; WhatsApp and the CLI are now gateways under `sanka/gateways/`.
- One database (SQLAlchemy Core; Postgres at runtime, SQLite in tests). Transcript, affect, style, vibe,
  skills, recipes, config/soul/persona/role documents, approvals, audit, and the lease live in the DB.
  `engine.yaml`/`soul.yaml`/`SOUL.md`/`roles/` are only seeds + mirrors.
- Semantic memory moved to Mem0 OSS with `person`/`chat`/`global` scopes; identity is cross-chat,
  the transcript stays per chat, and another person's private memory is never queried. `memory_legacy` +
  `sanka memory migrate` for old data.
- `engine_lease` + supervisor: one primary, the other instances standby/freeze; `/freeze` and
  `sanka console --takeover` move the lease.
- Deterministic approval: `shell`, `sandbox`, and `settings` wait for `/approve` (default `yolo`).
  The `settings` tool validates through the loader, writes the DB document + mirror, backs up, audits, then reloads.
- English prompt scaffolding; reply language from `soul.language`; the `Self`/`Roles`/`Memory`
  blocks are mandatory in the prompt.
- Migration commands: `sanka config migrate` → `sanka db upgrade` → `sanka db import --sqlite .sanka`
  → `sanka memory migrate`; copy `whatsapp.db`, workspace, `.env`.
- Verification: `pytest -q` 207 passed; `sanka selftest` OK; `sanka console --takeover` answered with the
  real LLM, `/status` was complete, `/quit` released the lease. The two-process lease was tested: A primary, B takeover,
  A freeze, B quit, A promoted back.
- Postgres 16 + pgvector was tested for real: `db upgrade`, importing 386 old SQLite messages, and fixing the
  `messages_id_seq` sequence after importing explicit ids. Real Mem0 was tested with pgvector:
  `sanka memory migrate` moved 3 rows and is idempotent, plus the add/search/context/forget adapter.
  The HuggingFace embedder is not installed on the dev machine (Python 3.15 has no torch/onnxruntime wheel yet);
  verification used a deterministic OpenAI-compatible embedding server, production uses HF in Docker.

## 2026-10-30 — Config consolidation, privacy levels, YAML language selector

- Config folded into one host-local `config.yaml` (host keys + `engine` + `gateways` + `soul`) and
  one `roles.yaml`; `SOUL.md` stays the persona. `engine.yaml`, `gateway.yaml`, `soul.yaml`, and
  `roles/` removed; the loader rejects the split layout with a `sanka config migrate` hint.
- `sanka init` writes exactly `config.yaml`, `roles.yaml`, `SOUL.md`, `.env` (`roles.yaml`/`.env`
  are `0600`); `sanka config migrate` folds legacy or split layouts, backs up into
  `config.bak-<timestamp>/`, and removes old files only with `--force`; document mirrors merge
  engine/soul into `config.yaml` and aggregate every role into `roles.yaml`.
- Language selection lives only in YAML (`soul.language`, `en`/`id`); `/language en|id`
  (owner-only) writes the soul document, mirrors `config.yaml`, and reloads. There is no Python
  translation catalogue.
- Memory entries now carry privacy level 0-4 (level 5/credentials are never stored) plus
  `owner_id`/`subject_id`/`confirmed`/`vetoed`. A deterministic `PrivacyPolicy` decides
  show/disclose from group vs DM ceilings, owner identity, confirmation, and veto; L0-L2 stay
  fluid, group L3/L4 ask the owner, vetoed entries never surface.
- `PrivacyService` handles pending confirmations, capture assessment (asks at `ask_level`), and
  pre-delivery review; the LLM only labels a provisional level, phrases the question in the soul
  language, and lists mentioned entries. The `memory` tool exposes level/subject/confirmed/veto;
  `/memori` shows levels, hides vetoed entries, and limits global add/delete to `memory_admin`.
- Verification: `pytest -q` 239 passed; the scaffold produces exactly four files; `db upgrade`
  seeded engine/soul/persona/role documents; `config import`/`export` round-trip kept host +
  engine + gateways + soul; a split config migrated with backup and loaded with `memory_admin`;
  `sanka console --takeover` smoke ran `/language en`, `/status` (`Language: en` plus privacy
  flags), `/language id`, `/quit`; `sanka selftest` reported SELFTEST OK with privacy PASS.

## 2026-10-30 (approval: shell command patterns instead of gating every shell)

- Problem: `approval.sensitive_tools: [shell, sandbox]` made every shell call and every sandbox
  run wait for `/approve`, so ordinary work (`ls`, `git status`, sandbox probes) stalled while the
  really dangerous operations were not distinguished. Fix: `shell` and `sandbox` are no longer
  sensitive tools by default; `ApprovalPolicy.check` now gates (1) internal changes through
  `settings`, (2) destructive shell commands matched against `approval.sensitive_commands`, and
  (3) destructive internal actions `memory forget` and `skill archive`. `recipe`, `skill save`,
  `memory add`/`veto`, and `sandbox` run free.
- Classifier (`sanka/engine/safety.py`, deterministic, no LLM): `shlex` with punctuation chars
  splits segments on `; && || | & ( )`; leading `VAR=…` assignments and wrappers
  (`sudo`, `env`, `nohup`, `nice`, `ionice`, `time`, `command`, `builtin`, `exec`, `stdbuf`,
  `xargs`, with their value-taking options) are stripped before `fnmatch` against the patterns;
  `bash|sh|zsh|dash|ksh -c …` is re-scanned recursively; `$(…)`/backticks are re-scanned up to
  depth 3; unparseable input or deeper nesting counts as sensitive. `SHELL_WRAPPERS`,
  `WRAPPER_VALUES`, `SHELL_OPERATORS`, `DESTRUCTIVE_ACTIONS`, `MAX_DEPTH` sit next to it.
- Config: `DEFAULT_SENSITIVE_COMMANDS` in `sanka/engine/config.py` (29 patterns: `rm *`, `dd *`,
  `wipefs *`, `mkfs*`, `systemctl *`, `kill*`, `chmod *`, `mount *`, `git reset --hard*`,
  `git clean -*f*`, `git push *--force*`, `find * -delete*`, …). Missing key → defaults; `[]` →
  command gate off; `sensitive_tools` missing/`[]` → no fully gated tool, and `mode: off` still
  bypasses the whole `SafetyPolicy` (the `settings` tool keeps its own `approval.settings` gate).
  `sanka init` seed (`sanka/admin/templates.py`) and `config.example.yaml` write the new block.
- Migration: local `config.yaml` `engine.approval` rewritten, then `sanka config import` →
  `sanka config export`; the `engine` document in `.sanka/sanka.db` now holds
  `sensitive_tools: []` + the 29 patterns, so the DB no longer overrides the file back to the old
  behaviour. README (both languages) documents the new gate list and the coarse-denylist limits.
- Verification: `pytest -q` 270 passed (new `tests/test_safety.py`: 23 cases over benign commands,
  `rm`/`sudo`/`bash -c`/`$(…)`/`dd`/`git force-push`/`find -delete`/unterminated quote, sensitive
  tool gate, strict mode, `mode: off`, destructive actions, empty pattern list; plus
  `test_sensitive_shell_command_waits_then_approve_executes` and two `sensitive_commands` config
  tests). `python -m sanka selftest` SELFTEST OK (memory/sandbox skipped: no Postgres/docker).
  Loaded the live `config.yaml` through `build_safety`: `ls -la`/`printf`/`read`/`sandbox`/
  `memory add`/`skill save`/`recipe remove` → allow; `rm -rf …`/`git push --force`/`memory forget`/
  `skill archive` → pending. Wrapper and segment evasions (`FOO=1 rm`, `nohup rm`, `env -i rm`,
  `xargs rm`, `sh -c`, `echo a; rm`, `(rm …)`, `git clean -xfd`) all stayed pending.
