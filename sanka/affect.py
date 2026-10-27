"""Emosi persisten: mood PAD/OCC, hubungan per orang, sulk/nangis, topik, dan catatan.

Matematika appraisal memakai `affect_kernel` (Apache-2.0) apa adanya; modul ini hanya
menyimpan state, memetakan pesan WhatsApp ke intent, dan menurunkan mode (ngambek, jahil,
naksir). Semua kegagalan DB ditangkap pemanggil di `wa.py`; store tidak mematikan bot.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

from affect_kernel import (
    AffectState,
    AppraisalGoals,
    AttachmentState,
    PADMood,
    Personality,
    RelationshipState,
    appraise_turn,
    decay_mood,
    mood_octant,
)

from . import guard
from .config import AffectConfig

log = logging.getLogger(__name__)

PERSONALITY = Personality(O=0.85, C=0.55, E=0.65, A=0.75, N=0.55)
GOALS = AppraisalGoals(rapport=0.85, intellectual=0.75, autonomy=0.60, respect=0.70, honesty=0.90)
EVENTS_KEEP = 200
NOTES_KEEP = 4
NOTES_TEXT = 120
PROMPT_CHARS = 900
TOPIC_CHARS = 400

APOLOGY = re.compile(
    r"\b(maaf|sorry|sori|salahku|aku salah|aku ngerti|aku paham|jangan marah|baikan|damai)\b",
    re.IGNORECASE,
)
ABUSE = re.compile(
    r"\b(goblok|bodoh|tolol|bangsat|jancok|sampah|mampus|jahanam|setan|iblis|bacot|gak guna|"
    r"nyepele|jahat|nakal)\b",
    re.IGNORECASE,
)
AFFECTION = re.compile(
    r"\b(sayang|cinta|kangen|rindu|peluk|cium|manis|baper|naksir|istri|pacar|jodoh|nikah)\b",
    re.IGNORECASE,
)
TEASE = re.compile(r"\b(wkwk|awok|hehe|🗿|jengek|nyepele|boong|coli|gooning|jahil)\b", re.IGNORECASE)
VULNERABILITY = re.compile(
    r"\b(sedih|nangis|nelangsa|stres|stress|capek|lelah|sakit|takut|cemas|overthinking|pusing|"
    r"gagal|putus|kehilangan|curhat)\b",
    re.IGNORECASE,
)
QUESTION = re.compile(r"\b(gimana|kenapa|kok|apa|siapa|kapan|di mana|berapa)\b", re.IGNORECASE)
CHALLENGE = re.compile(r"\b(salah|bukan gitu|keliru|koreksi|harusnya|menurutku)\b", re.IGNORECASE)
COMMAND = re.compile(
    r"\b(tolong|coba|bantu|buat|buatkan|kirim|rekap|cek|carikan|cari|jelaskan|ubah|hapus|pindah|"
    r"simpan|tag|ingatkan|lanjut|kerjakan|susun|hitung)\b",
    re.IGNORECASE,
)
POLITE = re.compile(r"(tolong|please|mohon|bantuin)", re.IGNORECASE)
PRESSURE = re.compile(
    r"\b(udah|sudah|maafin|baikan|jangan gitu|kasihan|diemin aja)\b", re.IGNORECASE
)
PRAISE = re.compile(r"\b(pinter|hebat|keren|bagus|mantap|good job)\b", re.IGNORECASE)

INTENTS = frozenset(
    {"APOLOGY", "ABUSE", "AFFECTION", "TEASE", "VULNERABILITY", "CURIOSITY", "CHALLENGE", "COMMAND", "CASUAL"}
)
KERNEL_INTENT = {
    "APOLOGY": "APOLOGY",
    "ABUSE": "ABUSE",
    "VULNERABILITY": "VULNERABILITY",
    "AFFECTION": "VULNERABILITY",
    "CURIOSITY": "CURIOSITY",
    "CHALLENGE": "CHALLENGE",
    "TEASE": "CASUAL",
    "COMMAND": "CASUAL",
    "CASUAL": "CASUAL",
}
# (trust, affection, respect, annoyance, sadness) per intent; mode khusus di komentar.
DELTAS: dict[str, tuple[float, float, float, float, float]] = {
    "ABUSE": (-0.08, -0.05, -0.10, 0.45, 0.20),
    "APOLOGY": (0.03, 0.01, 0.02, -0.25, -0.10),
    "AFFECTION": (0.02, 0.06, 0.0, -0.05, -0.05),
    "VULNERABILITY": (0.02, 0.03, 0.0, -0.05, 0.05),
    "CURIOSITY": (0.01, 0.0, 0.01, 0.0, 0.0),
    "CHALLENGE": (0.0, 0.0, 0.0, 0.02, 0.0),
    "TEASE": (0.0, 0.01, 0.0, 0.02, 0.0),
    "COMMAND": (0.0, 0.0, 0.0, 0.04, 0.0),
    "CASUAL": (0.005, 0.0, 0.0, 0.0, 0.0),
}
OCTANT_LABEL = {
    "exuberant": "semangat",
    "dependent": "manja",
    "relaxed": "tenang",
    "docile": "malu",
    "hostile": "kesal",
    "anxious": "cemas",
    "disdainful": "jutek",
    "bored": "bosan",
    "neutral": "biasa",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS moods (
  chat_id TEXT PRIMARY KEY,
  valence REAL NOT NULL DEFAULT 0,
  arousal REAL NOT NULL DEFAULT 0,
  dominance REAL NOT NULL DEFAULT 0,
  baseline REAL NOT NULL DEFAULT 0,
  occ TEXT NOT NULL DEFAULT '{}',
  active TEXT NOT NULL DEFAULT '{}',
  annoyance REAL NOT NULL DEFAULT 0,
  sadness REAL NOT NULL DEFAULT 0,
  last_ts REAL NOT NULL DEFAULT 0,
  last_decay REAL NOT NULL DEFAULT 0
);
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
  notes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS sulks (
  chat_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  level INTEGER NOT NULL DEFAULT 0,
  crying INTEGER NOT NULL DEFAULT 0,
  since REAL NOT NULL DEFAULT 0,
  until REAL NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  ignored INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, person_id)
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
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  chat_id TEXT NOT NULL,
  person_id TEXT NOT NULL DEFAULT '',
  intent TEXT NOT NULL,
  valence REAL NOT NULL,
  arousal REAL NOT NULL,
  reproach REAL NOT NULL,
  distress REAL NOT NULL,
  summary TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

MOOD_DEFAULTS: dict = {
    "valence": 0.0,
    "arousal": 0.0,
    "dominance": 0.0,
    "baseline": 0.0,
    "occ": "{}",
    "active": "{}",
    "annoyance": 0.0,
    "sadness": 0.0,
    "last_ts": 0.0,
    "last_decay": 0.0,
}
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
    "notes": "[]",
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _load(raw: str, default: object) -> object:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def classify_intent(text: str, *, directed: bool, is_dm: bool) -> str:
    """Pesan -> intent; urutan deterministik supaya perilaku bisa dites tanpa DB/LLM."""
    body = str(text or "")
    if APOLOGY.search(body):
        return "APOLOGY"
    if directed and ABUSE.search(body):
        return "ABUSE"
    if AFFECTION.search(body):
        return "AFFECTION"
    if TEASE.search(body):
        return "TEASE"
    if VULNERABILITY.search(body):
        return "VULNERABILITY"
    if "?" in body or QUESTION.search(body):
        return "CURIOSITY"
    if CHALLENGE.search(body):
        return "CHALLENGE"
    if COMMAND.search(body) and not POLITE.search(body):
        return "COMMAND"
    return "CASUAL"


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


def mine_notes(texts: list[str]) -> list[str]:
    """Trait ringkas dari arsip satu orang; hanya agregat, tanpa menyimpan teks mentah."""
    rows = [str(text or "") for text in texts if str(text or "").strip()]
    total = len(rows)
    if not total:
        return []
    orders = sum(1 for text in rows if COMMAND.search(text))
    teases = sum(1 for text in rows if TEASE.search(text))
    praises = sum(1 for text in rows if AFFECTION.search(text) or PRAISE.search(text))
    insults = sum(1 for text in rows if ABUSE.search(text))
    questions = sum(1 for text in rows if text.strip().endswith("?"))
    notes: list[str] = []
    if orders / total >= 0.12:
        notes.append("sering menyuruh/mengatur")
    if teases / total >= 0.08:
        notes.append("suka ngejailin")
    if praises >= 3:
        notes.append("sering memuji")
    if questions / total >= 0.15:
        notes.append("sering bertanya")
    if insults / total >= 0.05:
        notes.append("kadang kasar; jangan balas kasar")
    return notes[:NOTES_KEEP]


class AffectStore:
    """SQLite kecil untuk mood per chat, hubungan per orang, sulk, dan topik."""

    def __init__(self, path: Path | str, config: AffectConfig | None = None):
        self.config = config or AffectConfig()
        self.path = Path(path).expanduser()
        created = not self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if created:
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise sqlite3.ProgrammingError("affect store sudah ditutup")
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------ baca state

    def _mood(self, conn: sqlite3.Connection, chat_id: str) -> dict:
        row = conn.execute("SELECT * FROM moods WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(row) if row is not None else {"chat_id": chat_id, **MOOD_DEFAULTS}

    def _person(self, conn: sqlite3.Connection, person_id: str) -> dict:
        row = conn.execute("SELECT * FROM people WHERE person_id=?", (person_id,)).fetchone()
        return dict(row) if row is not None else {"person_id": person_id, **PERSON_DEFAULTS}

    def _sulk(self, conn: sqlite3.Connection, chat_id: str, person_id: str) -> dict | None:
        row = conn.execute(
            "SELECT * FROM sulks WHERE chat_id=? AND person_id=?", (chat_id, person_id)
        ).fetchone()
        return dict(row) if row is not None else None

    def _active(self, mood: dict) -> dict[str, float]:
        raw = _load(mood.get("active", "{}"), {})
        return {str(key): float(value) for key, value in raw.items()}

    def _intent(self, conn: sqlite3.Connection, chat_id: str, person_id: str) -> str:
        row = conn.execute(
            "SELECT intent FROM events WHERE chat_id=? AND person_id=? ORDER BY id DESC LIMIT 1",
            (chat_id, person_id),
        ).fetchone()
        return str(row["intent"]) if row is not None else ""

    def _label(self, mood: dict, active: dict, intent: str, *, level: int, crying: bool, playful: bool) -> str:
        valence = float(mood["valence"])
        trust = float(mood.get("trust", 0.0))
        label = OCTANT_LABEL.get(
            mood_octant(valence, float(mood["arousal"]), float(mood["dominance"])), "biasa"
        )
        if active.get("disappointment", 0.0) >= 0.30 or (
            active.get("distress", 0.0) >= 0.30 and valence < 0
        ):
            label = "kecewa"
        if active.get("admiration", 0.0) >= 0.35 or (intent == "CURIOSITY" and valence > 0.1):
            label = "penasaran"
        if intent == "AFFECTION" and valence > 0.20:
            label = "malu"
        if active.get("joy", 0.0) >= 0.40 and trust >= 0.40:
            label = "bangga"
        if crying:
            label = "nangis"
        if level >= 1:
            label = "ngambek"
        if playful:
            label = "jahil"
        return label

    def modes(self, chat_id: str, person_id: str = "") -> dict:
        """Mode turunan untuk gate/prompt: playful, crush, label mood, dan sulk."""
        with self._lock:
            conn = self._require()
            mood = self._mood(conn, chat_id)
            person = self._person(conn, person_id)
            intent = self._intent(conn, chat_id, person_id)
            sulk = self._sulk(conn, chat_id, person_id) if person_id else None
        mood["trust"] = float(person["trust"])
        active = self._active(mood)
        valence = float(mood["valence"])
        playful = (active.get("joy", 0.0) >= 0.30 and valence >= 0.25) or intent == "TEASE"
        crush = float(person["affection"]) >= 0.70 and float(person["trust"]) >= 0.40
        level = int(sulk["level"]) if sulk else 0
        crying = bool(int(sulk["crying"])) if sulk else False
        return {
            "label": self._label(
                mood, active, intent, level=level, crying=crying, playful=playful
            ),
            "valence": valence,
            "arousal": float(mood["arousal"]),
            "playful": playful,
            "crush": crush,
            "level": level,
            "crying": crying,
        }

    # ------------------------------------------------------------------ tulis state

    def observe(
        self,
        chat_id: str,
        person_id: str,
        name: str,
        text: str,
        *,
        directed: bool,
        is_dm: bool,
        role: str = "member",
        sender_jid: str = "",
        now: float | None = None,
    ) -> None:
        """Satu pesan masuk: upsert orang, appraisal bila ditujukan, atmosfer grup, topik."""
        moment = time.time() if now is None else float(now)
        body = str(text or "")
        crisis = guard.is_crisis(body)
        with self._lock:
            conn = self._require()
            person = self._touch_person(conn, person_id, name, role, moment)
            mood = self._mood(conn, chat_id)
            if not crisis and directed:
                person, mood = self._appraise(conn, chat_id, person_id, person, mood, body, moment)
                self._record_event(conn, chat_id, person_id, person, mood, body, moment)
            elif not crisis:
                self._atmosphere(conn, chat_id, mood, body)
            topic = conn.execute("SELECT * FROM topics WHERE chat_id=?", (chat_id,)).fetchone()
            pending = (int(topic["pending"]) if topic is not None else 0) + 1
            conn.execute(
                "INSERT INTO topics(chat_id, pending, last_message) VALUES(?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET pending=excluded.pending, "
                "last_message=excluded.last_message",
                (chat_id, pending, moment),
            )
            conn.commit()

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

    def _appraise(
        self,
        conn: sqlite3.Connection,
        chat_id: str,
        person_id: str,
        person: dict,
        mood: dict,
        body: str,
        moment: float,
    ) -> tuple[dict, dict]:
        intent = classify_intent(body, directed=True, is_dm=not chat_id.endswith("@g.us"))
        # Time decay: mood ditarik ke baseline tiap jam penuh (maks 24) sebelum impulse baru.
        last = float(mood["last_decay"] or 0.0)
        step = max(1.0, float(self.config.decay_hours) * 3600.0)
        pad = PADMood(
            valence=float(mood["valence"]),
            arousal=float(mood["arousal"]),
            dominance=float(mood["dominance"]),
        )
        if last and moment - last >= step:
            for _ in range(min(24, int((moment - last) // step))):
                pad = decay_mood(pad, PERSONALITY, person["stage"], float(mood["baseline"]))
            mood["last_decay"] = moment
        state = AffectState(
            mood=pad,
            personality=PERSONALITY,
            goals=GOALS,
            relationship=RelationshipState(stage=person["stage"], trust=float(person["trust"])),
            attachment=AttachmentState(
                weight=float(person["affection"]),
                comfort=float(person["affection"]) * 0.8,
                longing=_clamp((1.0 - float(person["familiarity"])) * float(person["affection"])),
            ),
            baseline_valence=float(mood["baseline"]),
            occ_carry={
                name: float(value)
                for name, value in _load(mood["occ"], {}).items()
                if 0.0 <= float(value) <= 1.0
            },
        )
        result = appraise_turn(state, KERNEL_INTENT[intent], message=body)
        active = {name: float(value) for name, value in result.active_emotions.items()}
        trust, affection, respect, annoyance, sadness = DELTAS[intent]
        minutes = max(0.0, (moment - float(mood["last_ts"] or moment)) / 60.0)
        decay = 0.01 * minutes
        person["trust"] = _clamp(float(person["trust"]) + trust)
        person["affection"] = _clamp(float(person["affection"]) + affection)
        person["respect"] = _clamp(float(person["respect"]) + respect)
        person["annoyance"] = _clamp(float(person["annoyance"]) + annoyance)
        if intent == "APOLOGY":
            person["appease"] = int(person["appease"]) + 1
        person["stage"] = stage_for(
            int(person["interactions"]), float(person["trust"]), float(person["affection"])
        )
        mood["annoyance"] = _clamp(float(mood["annoyance"]) - decay + annoyance)
        mood["sadness"] = _clamp(float(mood["sadness"]) - decay + sadness)
        mood["valence"] = float(result.state.mood.valence)
        mood["arousal"] = float(result.state.mood.arousal)
        mood["dominance"] = float(result.state.mood.dominance)
        mood["baseline"] = float(result.state.baseline_valence)
        mood["occ"] = json.dumps(dict(result.state.occ_carry))
        mood["active"] = json.dumps(active)
        mood["last_ts"] = moment
        mood["last_decay"] = mood["last_decay"] or moment
        self._write_person(conn, person_id, person)
        self._write_mood(conn, chat_id, mood)
        self._update_sulk(conn, chat_id, person_id, person, mood, active, intent, moment)
        return person, mood

    def _write_person(self, conn: sqlite3.Connection, person_id: str, person: dict) -> None:
        conn.execute(
            "UPDATE people SET trust=?, affection=?, respect=?, annoyance=?, stage=?, appease=? "
            "WHERE person_id=?",
            (
                float(person["trust"]),
                float(person["affection"]),
                float(person["respect"]),
                float(person["annoyance"]),
                person["stage"],
                int(person["appease"]),
                person_id,
            ),
        )

    def _write_mood(self, conn: sqlite3.Connection, chat_id: str, mood: dict) -> None:
        conn.execute(
            "INSERT INTO moods(chat_id, valence, arousal, dominance, baseline, occ, active, "
            "annoyance, sadness, last_ts, last_decay) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET valence=excluded.valence, arousal=excluded.arousal, "
            "dominance=excluded.dominance, baseline=excluded.baseline, occ=excluded.occ, "
            "active=excluded.active, annoyance=excluded.annoyance, sadness=excluded.sadness, "
            "last_ts=excluded.last_ts, last_decay=excluded.last_decay",
            (
                chat_id,
                float(mood["valence"]),
                float(mood["arousal"]),
                float(mood["dominance"]),
                float(mood["baseline"]),
                str(mood["occ"]),
                str(mood["active"]),
                float(mood["annoyance"]),
                float(mood["sadness"]),
                float(mood["last_ts"]),
                float(mood["last_decay"]),
            ),
        )

    def _update_sulk(
        self,
        conn: sqlite3.Connection,
        chat_id: str,
        person_id: str,
        person: dict,
        mood: dict,
        active: dict[str, float],
        intent: str,
        moment: float,
    ) -> None:
        valence = float(mood["valence"])
        anger = 0.6 * active.get("reproach", 0.0) + 0.4 * max(0.0, -valence)
        sadness_score = 0.6 * active.get("distress", 0.0) + 0.4 * float(mood["sadness"])
        annoyance = float(person["annoyance"])
        level = 0
        if annoyance >= 0.40 or anger >= 0.65:
            level = 1
        if annoyance >= 0.75 or anger >= 0.85:
            level = 2
        cry = sadness_score >= 0.70 and valence <= -0.30
        existing = self._sulk(conn, chat_id, person_id)
        active_sulk = bool(existing) and moment < float(existing["until"]) and (
            int(existing["level"]) or int(existing["crying"])
        )
        # Permintaan maaf berulang mengakhiri sulk; satu kali belum cukup.
        if active_sulk and int(person["appease"]) >= 2:
            conn.execute(
                "DELETE FROM sulks WHERE chat_id=? AND person_id=?", (chat_id, person_id)
            )
            return
        if active_sulk:
            level = max(level, int(existing["level"]))
            cry = cry or bool(int(existing["crying"]))
        if not level and not cry:
            if existing is not None:
                conn.execute(
                    "DELETE FROM sulks WHERE chat_id=? AND person_id=?", (chat_id, person_id)
                )
            return
        window = (self.config.cry_minutes if cry else self.config.sulk_minutes) * 60.0
        until = moment + window if (level or cry) else 0.0
        if active_sulk:
            until = max(until, float(existing["until"]))
        if intent != "ABUSE" and active_sulk:
            until = float(existing["until"])  # hanya hinaan baru yang memperpanjang
        since = float(existing["since"]) if active_sulk else moment
        ignored = int(existing["ignored"]) if existing is not None else 0
        reason = "dihina" if intent == "ABUSE" else ("sedih" if cry else "kesal")
        conn.execute(
            "INSERT INTO sulks(chat_id, person_id, level, crying, since, until, reason, ignored) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, person_id) DO UPDATE SET "
            "level=excluded.level, crying=excluded.crying, since=excluded.since, "
            "until=excluded.until, reason=excluded.reason",
            (chat_id, person_id, level, int(cry), since, until, reason, ignored),
        )

    def _atmosphere(self, conn: sqlite3.Connection, chat_id: str, mood: dict, body: str) -> None:
        valence = float(mood["valence"])
        arousal = float(mood["arousal"])
        if guard.TENSION.search(body):
            valence -= 0.02
            arousal += 0.02
        if guard.FUN.search(body):
            valence += 0.02
            arousal += 0.01
        mood["valence"] = _clamp(valence, -1.0, 1.0)
        mood["arousal"] = _clamp(arousal, -1.0, 1.0)
        mood["last_ts"] = mood["last_ts"] or time.time()
        self._write_mood(conn, chat_id, mood)

    def _record_event(
        self,
        conn: sqlite3.Connection,
        chat_id: str,
        person_id: str,
        person: dict,
        mood: dict,
        body: str,
        moment: float,
    ) -> None:
        active = self._active(mood)
        summary = guard.safe_text(body, 160)
        conn.execute(
            "INSERT INTO events(ts, chat_id, person_id, intent, valence, arousal, reproach, "
            "distress, summary) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                moment,
                chat_id,
                person_id,
                classify_intent(body, directed=True, is_dm=not chat_id.endswith("@g.us")),
                float(mood["valence"]),
                float(mood["arousal"]),
                active.get("reproach", 0.0),
                active.get("distress", 0.0),
                summary,
            ),
        )
        conn.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (EVENTS_KEEP,),
        )

    # ------------------------------------------------------------------ sulk / calm

    def silent(self, chat_id: str, person_id: str = "") -> bool:
        """True bila Sanka masih ngambek/nangis ke orang ini (atau siapa pun di chat ini)."""
        moment = time.time()
        with self._lock:
            conn = self._require()
            if person_id:
                rows = conn.execute(
                    "SELECT * FROM sulks WHERE chat_id=? AND person_id=?", (chat_id, person_id)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM sulks WHERE chat_id=?", (chat_id,)).fetchall()
            alive = False
            for row in rows:
                if not (int(row["level"]) or int(row["crying"])):
                    continue
                if moment < float(row["until"]):
                    alive = True
                    continue
                conn.execute(
                    "DELETE FROM sulks WHERE chat_id=? AND person_id=?",
                    (row["chat_id"], row["person_id"]),
                )
            if rows:
                conn.commit()
        return alive

    def sulk_until(self, chat_id: str, person_id: str = "") -> float:
        with self._lock:
            row = self._sulk(self._require(), chat_id, person_id) if person_id else None
        return float(row["until"]) if row is not None else 0.0

    def note_ignored(self, chat_id: str, person_id: str) -> int:
        """Catat bahwa Sanka memilih diam; hitungan ini memicu satu keluhan singkat."""
        with self._lock:
            conn = self._require()
            conn.execute(
                "UPDATE sulks SET ignored=ignored+1 WHERE chat_id=? AND person_id=?",
                (chat_id, person_id),
            )
            row = self._sulk(conn, chat_id, person_id)
            conn.commit()
        return int(row["ignored"]) if row is not None else 0

    def appeasing(self, text: str) -> bool:
        body = str(text or "")
        return bool(APOLOGY.search(body) or AFFECTION.search(body) or PRESSURE.search(body))

    def calm(self, chat_id: str, person_id: str = "") -> bool:
        """Hentikan sulk/nangis: target bisa nama, nomor, atau kosong (seluruh chat)."""
        target = " ".join(str(person_id or "").split())
        with self._lock:
            conn = self._require()
            pid = ""
            if target:
                pid = self.person_by_name(chat_id, target) or ""
                if not pid:
                    digits = "".join(ch for ch in target if ch.isdigit())
                    if digits:
                        row = conn.execute(
                            "SELECT person_id FROM people WHERE person_id=?", (digits,)
                        ).fetchone()
                        pid = str(row["person_id"]) if row is not None else ""
            if pid:
                rows = conn.execute(
                    "SELECT person_id FROM sulks WHERE chat_id=? AND person_id=?", (chat_id, pid)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT person_id FROM sulks WHERE chat_id=?", (chat_id,)
                ).fetchall()
            people = [str(row["person_id"]) for row in rows]
            conn.execute("DELETE FROM sulks WHERE chat_id=?", (chat_id,))
            for item in people:
                conn.execute("UPDATE people SET appease=0 WHERE person_id=?", (item,))
            mood = self._mood(conn, chat_id)
            mood["valence"] = _clamp(float(mood["valence"]) + 0.20, -1.0, 1.0)
            mood["arousal"] = _clamp(float(mood["arousal"]) - 0.20, -1.0, 1.0)
            mood["annoyance"] = 0.0
            mood["sadness"] = _clamp(float(mood["sadness"]) * 0.5)
            occ = {key: value for key, value in _load(mood["occ"], {}).items() if key not in {"reproach", "distress"}}
            mood["occ"] = json.dumps(occ)
            active = {key: value for key, value in self._active(mood).items() if key not in {"reproach", "distress"}}
            mood["active"] = json.dumps(active)
            self._write_mood(conn, chat_id, mood)
            conn.commit()
        return bool(people)

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

    def neglect(self, chat_id: str, now: float | None = None) -> bool:
        """Sanka yang ditinggal diam: sedih sedikit, maksimal sekali per 60 menit per chat."""
        moment = time.time() if now is None else float(now)
        key = f"neglect:{chat_id}"
        with self._lock:
            conn = self._require()
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            last = float(row["value"]) if row is not None else 0.0
            if last and moment - last < 3600:
                return False
            mood = self._mood(conn, chat_id)
            mood["valence"] = _clamp(float(mood["valence"]) - 0.10, -1.0, 1.0)
            mood["sadness"] = _clamp(float(mood["sadness"]) + 0.10)
            mood["arousal"] = _clamp(float(mood["arousal"]) - 0.05, -1.0, 1.0)
            self._write_mood(conn, chat_id, mood)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value",
                (key, str(moment)),
            )
            conn.commit()
        return True

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
        with self._lock:
            rows = self._require().execute(
                "SELECT * FROM people ORDER BY last_seen DESC LIMIT ?", (max(1, int(limit)),)
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

    def prompt(self, chat_id: str, person_id: str = "") -> str:
        """Blok prompt ringkas: label mood, status diam, dan hubungan dengan orang terdekat."""
        modes = self.modes(chat_id, person_id)
        with self._lock:
            conn = self._require()
            mood = self._mood(conn, chat_id)
        parts = [
            (
                f"Keadaan hatimu sekarang: {modes['label']} "
                f"(kesal {float(mood['annoyance']):.1f}, sedih {float(mood['sadness']):.1f})."
            )
        ]
        if modes["level"] >= 1 or modes["crying"]:
            parts.append("Kamu sedang diam karena kesal; kamu tidak wajib membalas.")
        elif float(mood["valence"]) > 0.3:
            parts.append("Kamu sedang senang dan terbuka.")
        people = self.known_people(chat_id, 6)
        ordered = [item for item in people if item["person_id"] == person_id]
        ordered += [item for item in people if item["person_id"] != person_id]
        lines: list[str] = []
        for item in ordered[:6]:
            if not str(item["name"]).strip():
                continue
            labels = [str(item["role"]) or "member"]
            labels += [str(note) for note in item["notes"][:4]]
            relation = _relation(
                float(item["affection"]),
                float(item["annoyance"]),
                float(item["trust"]),
                str(item["stage"]),
            )
            lines.append(
                f"- {item['name']} ({', '.join(labels)}): {relation}, "
                f"sayang {float(item['affection']):.1f}, kesal {float(item['annoyance']):.1f}."
            )
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
        """Sekali saja: bangun tabel orang dari arsip, dengan catatan trait agregat."""
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
            text = str(item.get("text") or "")
            bucket = grouped.setdefault(person_id, {"names": {}, "texts": [], "last": 0.0})
            name = str(item.get("sender_name") or "").strip()
            if name:
                bucket["names"][name] = bucket["names"].get(name, 0) + 1
            bucket["texts"].append(text)
            bucket["last"] = max(bucket["last"], float(item.get("ts") or 0.0))
        with self._lock:
            conn = self._require()
            for person_id, bucket in grouped.items():
                if conn.execute(
                    "SELECT 1 FROM people WHERE person_id=?", (person_id,)
                ).fetchone() is not None:
                    continue
                count = len(bucket["texts"])
                name = max(bucket["names"], key=bucket["names"].get) if bucket["names"] else ""
                notes = mine_notes(bucket["texts"])
                conn.execute(
                    "INSERT INTO people(person_id, name, role, stage, trust, affection, respect, "
                    "familiarity, interactions, last_seen, notes) VALUES(?, ?, 'member', ?, 0.1, 0, "
                    "0.5, ?, ?, ?, ?)",
                    (
                        person_id,
                        name,
                        stage_for(count, 0.1, 0.0),
                        min(1.0, count / 200.0),
                        count,
                        bucket["last"],
                        json.dumps(notes),
                    ),
                )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('seeded', '1') ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value"
            )
            conn.commit()
        return len(grouped)

    def stats(self) -> dict:
        with self._lock:
            conn = self._require()
            def _count(table: str) -> int:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                return int(row[0])

            return {
                "people": _count("people"),
                "moods": _count("moods"),
                "sulks": _count("sulks"),
                "topics": _count("topics"),
                "events": _count("events"),
            }


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
