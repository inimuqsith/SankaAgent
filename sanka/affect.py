"""Emosi persisten: state emotion-engine per chat + hubungan per orang, topik, dan drives.

Matematika emosi memakai `emotion-engine` (MIT) yang di-vendor apa adanya di
`sanka/vendor/emotion_engine.py`; modul ini hanya menyimpan satu file state per chat,
memetakan keputusan LLM ke PAD/relasi, dan menyajikan blok prompt. Semua kegagalan DB/file
ditangkap pemanggil di `wa.py`; store tidak mematikan bot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path

from . import guard
from .config import AffectConfig
from .vendor import emotion_engine as engine

log = logging.getLogger(__name__)

CHARACTER = "sanka"
NOTES_KEEP = 4
NOTES_TEXT = 120
PROMPT_CHARS = 1200
TOPIC_CHARS = 400
LOG_LINES = 3
PULSE_MINUTES = 5.0  # pre_turn_decay lebih rapat dari ini tidak mengubah apa pun
PULSE_MAX_HOURS = 24.0  # batas pertumbuhan drive setelah lama mati/clock skew
PAD_WEIGHT = {"me": 1.0, "group": 0.6, "other": 0.2, "unknown": 0.2}
WINDOW_SECONDS = 600.0  # jendela pembatas gerak hubungan per orang
WINDOW_LIMIT = 0.20  # delta maksimum per arah per jendela

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
  person_id TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  aliases TEXT NOT NULL DEFAULT '[]',
  role TEXT NOT NULL DEFAULT 'member',
  stage TEXT NOT NULL DEFAULT 'stranger',
  trust REAL NOT NULL DEFAULT 0.1,
  affection REAL NOT NULL DEFAULT 0,
  respect REAL NOT NULL DEFAULT 0.5,
  annoyance REAL NOT NULL DEFAULT 0,
  familiarity REAL NOT NULL DEFAULT 0,
  interactions INTEGER NOT NULL DEFAULT 0,
  appease INTEGER NOT NULL DEFAULT 0,
  last_seen REAL NOT NULL DEFAULT 0,
  window_start REAL NOT NULL DEFAULT 0,
  window_trust REAL NOT NULL DEFAULT 0,
  window_affection REAL NOT NULL DEFAULT 0,
  window_respect REAL NOT NULL DEFAULT 0,
  window_annoyance REAL NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS topics (
  chat_id TEXT PRIMARY KEY,
  summary TEXT NOT NULL DEFAULT '',
  keywords TEXT NOT NULL DEFAULT '[]',
  unresolved TEXT NOT NULL DEFAULT '[]',
  pending INTEGER NOT NULL DEFAULT 0,
  last_message REAL NOT NULL DEFAULT 0,
  last_digest REAL NOT NULL DEFAULT 0,
  last_research REAL NOT NULL DEFAULT 0,
  last_chime REAL NOT NULL DEFAULT 0,
  research_day TEXT NOT NULL DEFAULT '',
  research_count INTEGER NOT NULL DEFAULT 0,
  chime_day TEXT NOT NULL DEFAULT '',
  chime_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drives (
  chat_id TEXT PRIMARY KEY,
  energy REAL NOT NULL DEFAULT 0.6,
  boredom REAL NOT NULL DEFAULT 0.2,
  loneliness REAL NOT NULL DEFAULT 0.0,
  last_pulse REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_people (
  chat_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  last_seen REAL NOT NULL DEFAULT 0,
  interactions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, person_id)
);
"""

PERSON_DEFAULTS: dict = {
    "name": "",
    "aliases": "[]",
    "role": "member",
    "stage": "stranger",
    "trust": 0.1,
    "affection": 0.0,
    "respect": 0.5,
    "annoyance": 0.0,
    "familiarity": 0.0,
    "interactions": 0,
    "appease": 0,
    "last_seen": 0.0,
    "window_start": 0.0,
    "window_trust": 0.0,
    "window_affection": 0.0,
    "window_respect": 0.0,
    "window_annoyance": 0.0,
    "notes": "[]",
}
DRIVE_DEFAULTS: dict = {
    "energy": 0.6,
    "boredom": 0.2,
    "loneliness": 0.0,
    "last_pulse": 0.0,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _inertia(delta: float, current: float) -> float:
    """Hasil makin kecil saat nilai sudah ekstrem: orang yang sudah kesal tidak makin kesal linear."""
    return delta * (1.0 - current) if delta >= 0 else delta * current


def _ago(stamp: float, moment: float) -> str:
    """Jarak waktu kasar untuk prompt; angka mentah tidak pernah ditampilkan."""
    age = float(stamp or 0.0)
    if age <= 0:
        return "belum diketahui"
    delta = max(0.0, moment - age)
    if delta < 3600:
        return "baru saja"
    if delta < 86400:
        return f"{int(delta // 3600)} jam lalu"
    return f"{int(delta // 86400)} hari lalu"


def _load(raw: str, default: object) -> object:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def stage_for(interactions: int, trust: float, affection: float) -> str:
    if interactions >= 80 and trust >= 0.5 and affection >= 0.7:
        return "intimate"
    if interactions >= 40:
        return "close"
    if interactions >= 15:
        return "friend"
    if interactions >= 5:
        return "acquaintance"
    return "stranger"


def _relation(affection: float, annoyance: float, trust: float, stage: str = "") -> str:
    """Label hubungan untuk prompt; stage arsip tetap dihormati agar orang lama bukan "baru"."""
    if annoyance >= 0.75:
        return "musuh"
    if affection >= 0.85 and trust >= 0.50:
        return "cinta"
    if affection >= 0.70 and trust >= 0.40:
        return "naksir"
    if affection >= 0.55 or stage == "intimate":
        return "dekat"
    if affection >= 0.30 or trust >= 0.40 or stage in {"friend", "close"}:
        return "teman"
    if trust >= 0.15 or stage == "acquaintance":
        return "kenal"
    return "baru"


class AffectStore:
    """State emosi per chat (emotion-engine) + hubungan per orang, topik, dan drives."""

    def __init__(self, path: Path | str, config: AffectConfig | None = None, soul: str = ""):
        self.config = config or AffectConfig()
        self.soul = str(soul or "").strip()
        self.path = Path(path).expanduser()
        created = not self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if created:
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        raw_dir = self.config.state_dir
        self.state_dir = (
            Path(raw_dir).expanduser() if raw_dir else self.path.with_name("emotions")
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _migrate(self) -> None:
        """DB lama belum punya kolom jendela hubungan; tambahkan tanpa menyentuh baris."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(people)")}
        for name in (
            "window_start",
            "window_trust",
            "window_affection",
            "window_respect",
            "window_annoyance",
        ):
            if name not in columns:
                self._conn.execute(
                    f"ALTER TABLE people ADD COLUMN {name} REAL NOT NULL DEFAULT 0"
                )

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise sqlite3.ProgrammingError("affect store sudah ditutup")
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------ state engine

    def _state_path(self, chat_id: str) -> Path:
        digest = hashlib.sha1(str(chat_id).encode("utf-8")).hexdigest()[:16]
        return self.state_dir / f"{digest}.json"

    def _state(self, chat_id: str) -> tuple[Path, dict]:
        """State v3 terikat untuk satu chat; dibuat sekali dengan baseline dari SOUL.md."""
        path = self._state_path(chat_id)
        state = engine.load_state(path)
        if state.get("identity", {}).get("status") != "bound":
            state = engine.default_state(character_id=CHARACTER, relationship_id=chat_id)
            if self.soul:
                state = engine.apply_configuration(state, self.soul, source="soul")
            state["volatility_profile"] = self.config.volatility or "expressive"
            engine.save_state(path, state)
        return path, state

    def _session(self, state: dict, chat_id: str) -> tuple[dict, str]:
        """Satu sesi per chat; konflik sesi aktif memakai sesi yang sudah berjalan."""
        payload = {
            "session_id": chat_id,
            "event_id": f"session:{chat_id}",
            "occurred_at": engine.now_iso(),
            "character_id": CHARACTER,
            "relationship_id": chat_id,
        }
        state, result = engine.session_start(state, **payload)
        if result["status"] != "active_session_conflict":
            return state, chat_id
        active = str(state["session"].get("active_session_id") or chat_id)
        state, _ = engine.session_start(state, **{**payload, "session_id": active})
        return state, active

    def turn_start(self, chat_id: str, message_id: str) -> dict:
        """Buka sesi + satu langkah decay sebelum keputusan LLM dibuat."""
        with self._lock:
            path, state = self._state(chat_id)
            state, session_id = self._session(state, chat_id)
            state, _ = engine.pre_turn_decay(
                state,
                session_id=session_id,
                event_id=f"decay:{message_id}",
                character_id=CHARACTER,
                relationship_id=chat_id,
            )
            engine.save_state(path, state)
            emotion = state["emotion"]
            log_lines = state.get("emotion_log", [])[-LOG_LINES:]
            return {
                "summary": engine.emotion_summary(state),
                "pulse": engine.pulse_summary(state),
                "pleasure": float(emotion["pleasure"]),
                "arousal": float(emotion["arousal"]),
                "dominance": float(emotion["dominance"]),
                "trust_tier": engine.public_status(state)["trust_tier"],
                "log": [dict(item) for item in log_lines if isinstance(item, dict)],
            }

    def turn_end(
        self,
        chat_id: str,
        person_id: str,
        name: str,
        text: str,
        role: str,
        sender_jid: str,
        decision,
        message_id: str,
    ) -> None:
        """Satu pesan selesai dinilai: PAD bergerak, hubungan dan topik ikut tercatat."""
        moment = time.time()
        with self._lock:
            path, state = self._state(chat_id)
            if message_id and engine.event_already_processed(state, message_id):
                return  # event yang sama tidak boleh menggerakkan emosi dua kali
            state, session_id = self._session(state, chat_id)
            addressed = ""
            act = ""
            if decision is not None:
                addressed = str(getattr(decision, "addressed", "") or "")
                act = str(getattr(decision, "act", "") or "")
                weight = PAD_WEIGHT.get(addressed, 0.2)
                if act == "ignore":
                    weight = min(weight, 0.2)
                appraisal = decision.appraisal
                emotion = state["emotion"]
                dP = float(appraisal.dP) * weight
                dA = float(appraisal.dA) * weight
                dD = float(appraisal.dD) * weight
                pleasure = engine.clamp_dimension(
                    "pleasure", float(emotion["pleasure"]) + dP
                )
                arousal = engine.clamp_dimension(
                    "arousal", float(emotion["arousal"]) + dA
                )
                dominance = engine.clamp_dimension(
                    "dominance", float(emotion["dominance"]) + dD
                )
                meaning = str(self._meaning(decision)).strip()[:240]
                state, _ = engine.record_turn(
                    state,
                    pleasure,
                    arousal,
                    dominance,
                    appraisal=str(getattr(appraisal, "label", "") or "neutral"),
                    situation=" ".join(str(text or "").split())[:240]
                    or "(tanpa teks)",
                    relational_meaning=meaning or None,
                    session_id=session_id,
                    event_id=message_id,
                    host_approved=True,
                    character_id=CHARACTER,
                    relationship_id=chat_id,
                )
                engine.save_state(path, state)
            conn = self._require()
            person = self._touch_person(conn, person_id, name, role, moment)
            self._touch_chat_person(conn, chat_id, person_id, moment)
            self._touch_drives(conn, chat_id, moment, activity=True)
            self._bump_topic(conn, chat_id, moment)
            if decision is not None and addressed == "me" and act != "ignore":
                self._apply_relationship(conn, person_id, person, decision.relationship, moment)
            conn.commit()

    @staticmethod
    def _meaning(decision) -> str:
        note = str(getattr(decision.appraisal, "note", "") or "").strip()
        reason = str(getattr(decision, "reason", "") or "").strip()
        return note or reason

    def _apply_relationship(
        self, conn: sqlite3.Connection, person_id: str, person: dict, delta, moment: float
    ) -> None:
        """Delta hubungan dengan diminishing returns dan cap per jendela 10 menit."""
        start = float(person.get("window_start") or 0.0)
        if moment - start >= WINDOW_SECONDS:
            start = moment
            for name in ("trust", "affection", "respect", "annoyance"):
                person[f"window_{name}"] = 0.0
        values: dict[str, float] = {}
        for name in ("trust", "affection", "respect", "annoyance"):
            raw = float(getattr(delta, name, 0.0))
            current = float(person[name])
            applied = _inertia(raw, current) if raw else 0.0
            used = float(person.get(f"window_{name}") or 0.0)
            remaining = (
                WINDOW_LIMIT - max(0.0, used)
                if applied >= 0
                else WINDOW_LIMIT + min(0.0, used)
            )
            if remaining <= 0:
                applied = 0.0
            elif abs(applied) > remaining:
                applied = math.copysign(remaining, applied)
            new = _clamp(current + applied)
            applied = new - current
            values[name] = new
            person[f"window_{name}"] = used + applied
        stage = stage_for(int(person["interactions"]), values["trust"], values["affection"])
        conn.execute(
            "UPDATE people SET trust=?, affection=?, respect=?, annoyance=?, stage=?, "
            "window_start=?, window_trust=?, window_affection=?, window_respect=?, window_annoyance=? "
            "WHERE person_id=?",
            (
                values["trust"],
                values["affection"],
                values["respect"],
                values["annoyance"],
                stage,
                start,
                person["window_trust"],
                person["window_affection"],
                person["window_respect"],
                person["window_annoyance"],
                person_id,
            ),
        )

    def _person(self, conn: sqlite3.Connection, person_id: str) -> dict:
        row = conn.execute("SELECT * FROM people WHERE person_id=?", (person_id,)).fetchone()
        return dict(row) if row is not None else {"person_id": person_id, **PERSON_DEFAULTS}

    def _touch_person(
        self, conn: sqlite3.Connection, person_id: str, name: str, role: str, moment: float
    ) -> dict:
        person = self._person(conn, person_id)
        clean_name = " ".join(str(name or "").split())[:80]
        aliases = [str(item) for item in _load(person["aliases"], [])]
        if clean_name and person["name"] and clean_name != person["name"] and clean_name not in aliases:
            aliases = [*aliases, clean_name][-8:]
        chosen = clean_name or person["name"]
        interactions = int(person["interactions"]) + 1
        familiarity = min(1.0, interactions / 200.0)
        conn.execute(
            "INSERT INTO people(person_id, name, aliases, role, interactions, familiarity, last_seen) "
            "VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(person_id) DO UPDATE SET "
            "name=excluded.name, aliases=excluded.aliases, role=excluded.role, "
            "interactions=excluded.interactions, familiarity=excluded.familiarity, "
            "last_seen=excluded.last_seen",
            (person_id, chosen, json.dumps(aliases[-8:]), role, interactions, familiarity, moment),
        )
        person.update(
            name=chosen, aliases=json.dumps(aliases[-8:]), role=role,
            interactions=interactions, familiarity=familiarity, last_seen=moment,
        )
        return person

    def _touch_chat_person(
        self, conn: sqlite3.Connection, chat_id: str, person_id: str, moment: float
    ) -> None:
        """Keanggotaan chat: siapa yang benar-benar ikut bicara di sini, bukan global."""
        conn.execute(
            "INSERT INTO chat_people(chat_id, person_id, last_seen, interactions) "
            "VALUES(?, ?, ?, 1) ON CONFLICT(chat_id, person_id) DO UPDATE SET "
            "last_seen=excluded.last_seen, interactions=chat_people.interactions+1",
            (chat_id, person_id, moment),
        )

    def _bump_topic(self, conn: sqlite3.Connection, chat_id: str, moment: float) -> None:
        conn.execute(
            "INSERT INTO topics(chat_id, pending, last_message) VALUES(?, 1, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET pending=topics.pending+1, "
            "last_message=excluded.last_message",
            (chat_id, moment),
        )

    def _drives(self, conn: sqlite3.Connection, chat_id: str) -> dict:
        row = conn.execute("SELECT * FROM drives WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(row) if row is not None else {"chat_id": chat_id, **DRIVE_DEFAULTS}

    def _write_drives(self, conn: sqlite3.Connection, chat_id: str, data: dict) -> None:
        conn.execute(
            "INSERT INTO drives(chat_id, energy, boredom, loneliness, last_pulse) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
            "energy=excluded.energy, boredom=excluded.boredom, "
            "loneliness=excluded.loneliness, last_pulse=excluded.last_pulse",
            (
                chat_id,
                _clamp(float(data["energy"])),
                _clamp(float(data["boredom"])),
                _clamp(float(data["loneliness"])),
                float(data["last_pulse"]),
            ),
        )

    def _touch_drives(
        self, conn: sqlite3.Connection, chat_id: str, moment: float, *, activity: bool
    ) -> None:
        """Pesan masuk memuaskan drive; jam yang lewat dilakukan `pulse`."""
        data = self._drives(conn, chat_id)
        if activity:
            data["boredom"] = _clamp(float(data["boredom"]) - 0.15)
            data["loneliness"] = _clamp(float(data["loneliness"]) - 0.20)
            data["energy"] = _clamp(float(data["energy"]) + 0.03)
        data["last_pulse"] = moment
        self._write_drives(conn, chat_id, data)

    # ------------------------------------------------------------------ heartbeat

    def drives(self, chat_id: str) -> dict:
        """Drive publik (energi, kebosanan, kesepian) untuk prompt dan chime."""
        with self._lock:
            data = self._drives(self._require(), chat_id)
        return {key: float(data[key]) for key in ("energy", "boredom", "loneliness")}

    def _latest_affection(self, conn: sqlite3.Connection, chat_id: str) -> float:
        row = conn.execute(
            "SELECT p.affection FROM chat_people cp JOIN people p ON p.person_id=cp.person_id "
            "WHERE cp.chat_id=? ORDER BY cp.last_seen DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return _clamp(row["affection"]) if row is not None else 0.2

    def pulse(self, chat_id: str) -> dict:
        """Detak 24/7: state meluruh (maks satu langkah per 5 menit) dan drive tumbuh."""
        moment = time.time()
        event_id = f"pulse:{chat_id}:{int(moment // 300)}"
        with self._lock:
            path, state = self._state(chat_id)
            if engine.event_already_processed(state, event_id):
                # tick di dalam bucket yang sama tidak menulis apa pun: file state tetap.
                return self._payload(state, self._drives(self._require(), chat_id))
            state, session_id = self._session(state, chat_id)
            state, _ = engine.pre_turn_decay(
                state,
                session_id=session_id,
                event_id=event_id,
                character_id=CHARACTER,
                relationship_id=chat_id,
            )
            engine.save_state(path, state)
            conn = self._require()
            data = self._drives(conn, chat_id)
            last = float(data["last_pulse"] or 0.0)
            if last <= 0:  # chat lama/DB baru: detak pertama hanya menambatkan waktu
                data["last_pulse"] = moment
                self._write_drives(conn, chat_id, data)
                conn.commit()
                return self._payload(state, data)
            elapsed = min(max(0.0, moment - last), PULSE_MAX_HOURS * 3600.0)
            if elapsed >= PULSE_MINUTES * 60:
                hours = elapsed / 3600.0
                data["energy"] = _clamp(float(data["energy"]) - 0.06 * hours)
                data["boredom"] = _clamp(float(data["boredom"]) + 0.08 * hours)
                attachment = self._latest_affection(conn, chat_id)
                data["loneliness"] = _clamp(
                    float(data["loneliness"]) + (0.03 + 0.05 * attachment) * hours
                )
                data["last_pulse"] = moment
                self._write_drives(conn, chat_id, data)
                conn.commit()
        return self._payload(state, data)

    # ------------------------------------------------------------------ tenang

    def calm(self, chat_id: str, person_id: str = "") -> bool:
        """Repair turn: PAD kembali ke baseline dan kekesalan orang target turun setengah."""
        target = " ".join(str(person_id or "").split())
        with self._lock:
            path, state = self._state(chat_id)
            conn = self._require()
            if target:
                pid = self.person_by_name(chat_id, target) or ""
                if not pid:
                    digits = "".join(ch for ch in target if ch.isdigit())
                    row = (
                        conn.execute(
                            "SELECT person_id FROM people WHERE person_id=?", (digits,)
                        ).fetchone()
                        if digits
                        else None
                    )
                    pid = str(row["person_id"]) if row is not None else ""
                rows = (
                    conn.execute(
                        "SELECT person_id, annoyance FROM people WHERE person_id=?", (pid,)
                    ).fetchall()
                    if pid
                    else []
                )
            else:
                rows = conn.execute(
                    "SELECT p.person_id, p.annoyance FROM chat_people cp "
                    "JOIN people p ON p.person_id=cp.person_id WHERE cp.chat_id=?",
                    (chat_id,),
                ).fetchall()
            emotion = state["emotion"]
            annoyance = max(
                [float(row["annoyance"]) for row in rows],
                default=max(0.0, -float(emotion["pleasure"])),
            )
            upset = float(emotion["pleasure"]) < 0 or annoyance > 0.1
            for row in rows:
                conn.execute(
                    "UPDATE people SET annoyance=? WHERE person_id=?",
                    (_clamp(float(row["annoyance"]) * 0.5), str(row["person_id"])),
                )
            conn.commit()
            state, session_id = self._session(state, chat_id)
            baseline = state["personality_baseline"]
            state, _ = engine.record_turn(
                state,
                baseline["pleasure"],
                baseline["arousal"],
                baseline["dominance"],
                appraisal="repair",
                situation="owner menenangkan",
                session_id=session_id,
                event_id=f"calm:{time.time_ns()}",
                host_approved=True,
                character_id=CHARACTER,
                relationship_id=chat_id,
            )
            engine.save_state(path, state)
        return bool(upset)

    # ------------------------------------------------------------------ topik & orang

    def topic(self, chat_id: str) -> dict:
        with self._lock:
            row = self._require().execute(
                "SELECT * FROM topics WHERE chat_id=?", (chat_id,)
            ).fetchone()
        if row is None:
            return {
                "chat_id": chat_id,
                "summary": "",
                "keywords": [],
                "unresolved": [],
                "pending": 0,
                "last_message": 0.0,
                "last_digest": 0.0,
                "last_research": 0.0,
                "last_chime": 0.0,
                "research_day": "",
                "research_count": 0,
                "chime_day": "",
                "chime_count": 0,
            }
        data = dict(row)
        data["keywords"] = _load(data["keywords"], [])
        data["unresolved"] = _load(data["unresolved"], [])
        return data

    def topic_block(self, chat_id: str) -> str:
        data = self.topic(chat_id)
        summary = str(data["summary"]).strip()
        if not summary:
            return ""
        lines = [f"Topik yang sedang hangat di chat ini: {summary}"]
        pending = [str(item) for item in data["unresolved"] if str(item).strip()][:2]
        if pending:
            lines.append(f"Pertanyaan yang belum terjawab: {'; '.join(pending)}")
        text = "\n".join(lines)
        return text if len(text) <= TOPIC_CHARS else text[: TOPIC_CHARS - 1] + "…"

    def set_topic(
        self,
        chat_id: str,
        summary: str,
        keywords: list[str],
        unresolved: list[str],
        now: float | None = None,
    ) -> None:
        moment = time.time() if now is None else float(now)
        clean_keywords = [str(item).strip() for item in keywords if str(item).strip()][:10]
        clean_pending = [str(item).strip() for item in unresolved if str(item).strip()][:5]
        with self._lock:
            conn = self._require()
            conn.execute(
                "INSERT INTO topics(chat_id, summary, keywords, unresolved, last_message) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
                "summary=excluded.summary, keywords=excluded.keywords, "
                "unresolved=excluded.unresolved, last_message=excluded.last_message",
                (
                    chat_id,
                    " ".join(str(summary or "").split())[:240],
                    json.dumps(clean_keywords),
                    json.dumps(clean_pending),
                    moment,
                ),
            )
            conn.commit()

    def pending(self, chat_id: str) -> int:
        return int(self.topic(chat_id)["pending"])

    def mark_digest(self, chat_id: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        with self._lock:
            conn = self._require()
            conn.execute(
                "INSERT INTO topics(chat_id, pending, last_digest) VALUES(?, 0, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET pending=0, last_digest=excluded.last_digest",
                (chat_id, moment),
            )
            conn.commit()

    def mark_research(self, chat_id: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        day = time.strftime("%Y-%m-%d", time.localtime(moment))
        with self._lock:
            conn = self._require()
            data = self.topic(chat_id)
            count = int(data["research_count"]) + 1 if data["research_day"] == day else 1
            conn.execute(
                "INSERT INTO topics(chat_id, last_research, research_day, research_count) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
                "last_research=excluded.last_research, research_day=excluded.research_day, "
                "research_count=excluded.research_count",
                (chat_id, moment, day, count),
            )
            conn.commit()

    def mark_chime(self, chat_id: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        day = time.strftime("%Y-%m-%d", time.localtime(moment))
        with self._lock:
            conn = self._require()
            data = self.topic(chat_id)
            count = int(data["chime_count"]) + 1 if data["chime_day"] == day else 1
            conn.execute(
                "INSERT INTO topics(chat_id, last_chime, chime_day, chime_count) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
                "last_chime=excluded.last_chime, chime_day=excluded.chime_day, "
                "chime_count=excluded.chime_count",
                (chat_id, moment, day, count),
            )
            conn.commit()

    def person_by_name(self, chat_id: str, name: str) -> str | None:
        target = " ".join(str(name or "").split()).casefold()
        if not target:
            return None
        with self._lock:
            rows = self._require().execute(
                "SELECT person_id, name, aliases FROM people ORDER BY last_seen DESC LIMIT 500"
            ).fetchall()
        for row in rows:
            if str(row["name"]).casefold() == target:
                return str(row["person_id"])
        for row in rows:
            aliases = [str(item).casefold() for item in _load(row["aliases"], [])]
            if target in aliases:
                return str(row["person_id"])
        for row in rows:
            if target and target in str(row["name"]).casefold():
                return str(row["person_id"])
        return None

    def known_people(self, chat_id: str, limit: int = 8) -> list[dict]:
        """Orang yang benar-benar ikut chat ini, terbaru dulu; chat lain tidak bocor ke sini."""
        with self._lock:
            rows = self._require().execute(
                "SELECT p.*, cp.interactions AS chat_interactions, "
                "cp.last_seen AS chat_last_seen FROM chat_people cp "
                "JOIN people p ON p.person_id=cp.person_id WHERE cp.chat_id=? "
                "ORDER BY cp.last_seen DESC LIMIT ?",
                (chat_id, max(1, int(limit))),
            ).fetchall()
        people: list[dict] = []
        for row in rows:
            item = dict(row)
            item["aliases"] = _load(item["aliases"], [])
            item["notes"] = _load(item["notes"], [])
            people.append(item)
        return people

    def note_person(self, person_id: str, note: str) -> None:
        text = guard.safe_text(note, NOTES_TEXT)
        if not person_id or not text:
            return
        with self._lock:
            conn = self._require()
            person = self._person(conn, person_id)
            notes = [str(item) for item in _load(person["notes"], [])]
            if text in notes:
                return
            notes = [*notes, text][-NOTES_KEEP:]
            conn.execute("UPDATE people SET notes=? WHERE person_id=?", (json.dumps(notes), person_id))
            conn.commit()

    # ------------------------------------------------------------------ prompt

    def _payload(self, state: dict, drives: dict, person_id: str = "") -> dict:
        emotion = state["emotion"]
        trust_tier = engine.public_status(state)["trust_tier"]
        person = None
        if person_id:
            with self._lock:
                person = next(
                    (
                        item
                        for item in self.known_people(
                            str(state["identity"].get("relationship_id") or ""), 50
                        )
                        if item["person_id"] == person_id
                    ),
                    None,
                )
        return {
            "summary": engine.emotion_summary(state),
            "pulse": engine.pulse_summary(state),
            "pleasure": float(emotion["pleasure"]),
            "arousal": float(emotion["arousal"]),
            "dominance": float(emotion["dominance"]),
            "trust_tier": trust_tier,
            "trust": float(state.get("trust", 0.0)),
            "energy": float(drives["energy"]),
            "boredom": float(drives["boredom"]),
            "loneliness": float(drives["loneliness"]),
            "turns": int(state.get("total_turns", 0)),
            "relation": (
                _relation(
                    float(person["affection"]),
                    float(person["annoyance"]),
                    float(person["trust"]),
                    str(person["stage"]),
                )
                if person is not None
                else ""
            ),
            "person_name": str(person["name"]) if person is not None else "",
        }

    def status(self, chat_id: str, person_id: str = "") -> dict:
        """Data untuk /mood: ringkasan state, PAD, trust, dan hubungan dengan pengirim."""
        with self._lock:
            _, state = self._state(chat_id)
            drives = self._drives(self._require(), chat_id)
        return self._payload(state, drives, person_id)

    def pad(self, chat_id: str) -> dict:
        """PAD saat ini: dipakai untuk menandai valence/arousal memori yang ditulis."""
        with self._lock:
            _, state = self._state(chat_id)
        emotion = state["emotion"]
        return {
            "pleasure": float(emotion["pleasure"]),
            "arousal": float(emotion["arousal"]),
            "dominance": float(emotion["dominance"]),
        }

    def prompt(self, chat_id: str, person_id: str = "") -> str:
        """Blok prompt: ringkasan state, pulse, PAD, log terakhir, dan sikap ke orang di chat."""
        with self._lock:
            _, state = self._state(chat_id)
            drives = self._drives(self._require(), chat_id)
            people = self.known_people(chat_id, 6)
        emotion = state["emotion"]
        trust_tier = engine.public_status(state)["trust_tier"]
        parts = [
            (
                f"Keadaan hatimu: {engine.emotion_summary(state)}. "
                f"P {float(emotion['pleasure']):.1f}, A {float(emotion['arousal']):.1f}, "
                f"D {float(emotion['dominance']):.1f}, trust {trust_tier}. "
                f"Pulse: {engine.pulse_summary(state)}."
            )
        ]
        log_lines: list[str] = []
        for entry in state.get("emotion_log", [])[-LOG_LINES:]:
            if not isinstance(entry, dict):
                continue
            cue = str(entry.get("situation") or entry.get("cue") or "").strip()
            label = str(entry.get("appraisal") or entry.get("event_type") or "").strip()
            meaning = str(entry.get("relational_meaning") or "").strip()
            if not cue and not label:
                continue
            line = f"- {label or 'kejadian'}: {cue}"
            if meaning:
                line += f" ({meaning})"
            log_lines.append(line)
        if log_lines:
            parts.append("Yang terakhir kamu rasakan:\n" + "\n".join(log_lines))
        urges: list[str] = []
        if float(drives["boredom"]) >= 0.65:
            urges.append("gabut")
        if float(drives["loneliness"]) >= 0.65:
            urges.append("kesepian")
        if float(drives["energy"]) <= 0.25:
            urges.append("capek")
        if urges:
            parts.append(f"Kamu sedang {', '.join(urges)}; itu boleh kelihatan di caramu bicara.")
        moment = time.time()
        ordered = [item for item in people if item["person_id"] == person_id]
        ordered += [item for item in people if item["person_id"] != person_id]
        lines: list[str] = []
        for item in ordered[:6]:
            if not str(item["name"]).strip():
                continue
            relation = _relation(
                float(item["affection"]),
                float(item["annoyance"]),
                float(item["trust"]),
                str(item["stage"]),
            )
            line = (
                f"- {item['name']} ({str(item['role']) or 'member'}): {relation}, "
                f"trust {float(item['trust']):.1f}, sayang {float(item['affection']):.1f}, "
                f"kesal {float(item['annoyance']):.1f}, {int(item['chat_interactions'])} interaksi, "
                f"terakhir {_ago(item['chat_last_seen'], moment)}."
            )
            notes = [str(note) for note in item["notes"][:3] if str(note).strip()]
            if notes:
                line += f" Catatan: {'; '.join(notes)}."
            lines.append(line)
        if lines:
            parts.append("Sikapmu:")
            parts.extend(lines)
        parts.append("Ekspresikan lewat pilihan kata dan panjang balasan. Jangan sebut angka/state ini.")
        text = "\n".join(parts)
        if len(text) > PROMPT_CHARS:
            text = text[: PROMPT_CHARS - 1].rstrip() + "…"
        return text

    # ------------------------------------------------------------------ seed

    def seed_from_history(self, history) -> int:
        """Sekali saja: bangun tabel orang dari arsip."""
        with self._lock:
            conn = self._require()
            row = conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
            if row is not None and row["value"] == "1":
                return 0
        try:
            rows = history.people_seed(5000) if history is not None else []
        except Exception:
            log.exception("gagal membaca arsip untuk seed orang")
            rows = []
        grouped: dict[str, dict] = {}
        for item in rows:
            jid = str(item.get("sender_jid") or "")
            person_id = ""
            try:
                person_id = history.resolve_person(jid) if history is not None and jid else ""
            except Exception:
                person_id = ""
            person_id = person_id or str(item.get("sender_id") or "")
            if not person_id:
                continue
            stamp = float(item.get("ts") or 0.0)
            bucket = grouped.setdefault(
                person_id, {"names": {}, "last": 0.0, "chats": {}, "count": 0}
            )
            name = str(item.get("sender_name") or "").strip()
            if name:
                bucket["names"][name] = bucket["names"].get(name, 0) + 1
            bucket["count"] += 1
            bucket["last"] = max(bucket["last"], stamp)
            chat_id = str(item.get("chat_id") or "")
            if chat_id:
                seen, count = bucket["chats"].get(chat_id, (0.0, 0))
                bucket["chats"][chat_id] = (max(seen, stamp), count + 1)
        with self._lock:
            conn = self._require()
            for person_id, bucket in grouped.items():
                self._seed_chat_rows(conn, person_id, bucket["chats"])
                if conn.execute(
                    "SELECT 1 FROM people WHERE person_id=?", (person_id,)
                ).fetchone() is not None:
                    continue
                count = int(bucket["count"])
                name = max(bucket["names"], key=bucket["names"].get) if bucket["names"] else ""
                conn.execute(
                    "INSERT INTO people(person_id, name, role, stage, trust, affection, respect, "
                    "familiarity, interactions, last_seen, notes) VALUES(?, ?, 'member', ?, 0.1, 0, "
                    "0.5, ?, ?, ?, '[]')",
                    (
                        person_id,
                        name,
                        stage_for(count, 0.1, 0.0),
                        min(1.0, count / 200.0),
                        count,
                        bucket["last"],
                    ),
                )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('seeded', '1') ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value"
            )
            conn.commit()
        return len(grouped)

    @staticmethod
    def _seed_chat_rows(conn: sqlite3.Connection, person_id: str, chats: dict) -> None:
        for chat_id, (seen, count) in chats.items():
            conn.execute(
                "INSERT INTO chat_people(chat_id, person_id, last_seen, interactions) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(chat_id, person_id) DO UPDATE SET "
                "last_seen=max(chat_people.last_seen, excluded.last_seen), "
                "interactions=max(chat_people.interactions, excluded.interactions)",
                (chat_id, person_id, float(seen), int(count)),
            )

    def seed_chat_people(self, history) -> int:
        """Sekali saja untuk DB emosi lama: lengkapi anggota tiap chat dari arsip."""
        with self._lock:
            conn = self._require()
            row = conn.execute(
                "SELECT value FROM meta WHERE key='chat_people_seeded'"
            ).fetchone()
            if row is not None and row["value"] == "1":
                return 0
        try:
            rows = history.people_seed(5000) if history is not None else []
        except Exception:
            log.exception("gagal membaca arsip untuk seed keanggotaan chat")
            rows = []
        grouped: dict[str, dict[str, tuple[float, int]]] = {}
        for item in rows:
            chat_id = str(item.get("chat_id") or "")
            jid = str(item.get("sender_jid") or "")
            person_id = ""
            try:
                person_id = history.resolve_person(jid) if history is not None and jid else ""
            except Exception:
                person_id = ""
            person_id = person_id or str(item.get("sender_id") or "")
            if not chat_id or not person_id:
                continue
            stamp = float(item.get("ts") or 0.0)
            chats = grouped.setdefault(person_id, {})
            seen, count = chats.get(chat_id, (0.0, 0))
            chats[chat_id] = (max(seen, stamp), count + 1)
        with self._lock:
            conn = self._require()
            for person_id, chats in grouped.items():
                self._seed_chat_rows(conn, person_id, chats)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('chat_people_seeded', '1') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            conn.commit()
        return sum(len(chats) for chats in grouped.values())

    def stats(self) -> dict:
        with self._lock:
            conn = self._require()

            def _count(table: str) -> int:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                return int(row[0])

            return {
                "people": _count("people"),
                "topics": _count("topics"),
                "drives": _count("drives"),
                "chat_people": _count("chat_people"),
                "states": len(list(self.state_dir.glob("*.json"))),
            }
