"""Emosi persisten: state emotion-engine + hubungan per orang, topik, dan drives.

Matematika emosi memakai `emotion-engine` (MIT) yang di-vendor apa adanya di
`sanka/vendor/emotion_engine.py`; modul ini menyimpan satu dokumen state `emotion`
di tabel `state`, memetakan keputusan LLM ke PAD/relasi, dan menyajikan blok prompt.
Semua kegagalan DB ditangkap pemanggil; store tidak mematikan bot.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time

from sqlalchemy import func, insert, select, update

from ...vendor import emotion_engine as engine
from .. import guard
from ..config import AffectConfig
from ..db import Database
from ..schema import chat_people, drives, people, state, topics

log = logging.getLogger(__name__)

CHARACTER = "sanka"
STATE_KEY = "emotion"
SEEDED_KEY = "seeded"
CHAT_PEOPLE_SEEDED_KEY = "chat_people_seeded"
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
SOOTHE_FLOOR = 0.01  # di bawah ini dianggap lunas, biar tidak menulis angka receh terus
GENDERS = ("male", "female", "unknown", "")

PERSON_DEFAULTS: dict = {
    "name": "",
    "aliases": [],
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
    "notes": [],
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


def _as_list(value: object) -> list:
    """JSON column sudah berupa list; string JSON lama tetap diterima."""
    if isinstance(value, list):
        return [item for item in value]
    if isinstance(value, str):
        loaded = _load(value, [])
        return list(loaded) if isinstance(loaded, list) else []
    return []


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


def _recent_log(state: dict, limit: int = LOG_LINES) -> list[dict]:
    """Entri log yang punya pemicu nyata: decay internal bukan "yang terakhir kamu rasakan"."""
    entries = [item for item in state.get("emotion_log", []) if isinstance(item, dict)]
    real = [item for item in entries if str(item.get("event_type") or "") != "pre_turn_decay"]
    return (real or entries)[-limit:]


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
    """State emosi (emotion-engine) + hubungan per orang, topik, dan drives."""

    def __init__(
        self,
        db: Database,
        config: AffectConfig | None = None,
        soul: str = "",
    ):
        self.db = db
        self.config = config or AffectConfig()
        self.soul = str(soul or "").strip()
        self._lock = threading.RLock()

    def close(self) -> None:
        """Tidak memiliki koneksi sendiri; Database dimiliki Agent."""
        return None

    # ------------------------------------------------------------------ state engine

    def _load_state(self, conn, *, for_update: bool = False) -> dict:
        """Baca satu hati (dokumen `emotion`); default hanya dipakai bila belum terikat."""
        statement = select(state.c.value).where(state.c.key == STATE_KEY)
        if for_update:
            statement = statement.with_for_update()
        row = conn.execute(statement).first()
        raw = None
        if row is not None:
            try:
                raw = json.loads(row[0] or "null")
            except (TypeError, ValueError):
                raw = None
        if isinstance(raw, dict) and raw.get("identity", {}).get("status") == "bound":
            return raw
        fresh = engine.default_state(character_id=CHARACTER, relationship_id=CHARACTER)
        if self.soul:
            fresh = engine.apply_configuration(fresh, self.soul, source="soul")
        fresh["volatility_profile"] = self.config.volatility or "expressive"
        self._save_state(conn, fresh)
        return fresh

    def _save_state(self, conn, data: dict) -> None:
        self.db.upsert(
            conn,
            state,
            [{"key": STATE_KEY, "value": json.dumps(data), "updated_at": time.time()}],
            ["key"],
            update=lambda stmt: {
                "value": stmt.excluded.value,
                "updated_at": stmt.excluded.updated_at,
            },
        )

    def _state(self, conn, *, for_update: bool = False) -> dict:
        """State v3 tunggal (dipakai semua chat); dibuat sekali dengan baseline dari soul."""
        return self._load_state(conn, for_update=for_update)

    def _session(self, state: dict, chat_id: str) -> tuple[dict, str]:
        """Satu sesi berjalan terus untuk semua chat.

        State hasil migrasi bisa masih memegang sesi aktif lama (id chat); sesi itu dipakai lagi
        supaya `record_turn` tidak ditolak sebagai `no_active_session` (emosi jadi beku).
        """
        state, result = engine.session_start(
            state,
            session_id=CHARACTER,
            event_id=f"session:{CHARACTER}",
            occurred_at=engine.now_iso(),
            character_id=CHARACTER,
            relationship_id=CHARACTER,
        )
        active = str(state.get("session", {}).get("active_session_id") or "")
        if result.get("status") == "active_session_conflict" and active:
            return state, active
        return state, CHARACTER

    def turn_start(self, chat_id: str, message_id: str) -> dict:
        """Buka sesi + satu langkah decay sebelum keputusan LLM dibuat."""
        with self._lock, self.db.connect() as conn:
            state = self._state(conn, for_update=True)
            state, session_id = self._session(state, chat_id)
            state, _ = engine.pre_turn_decay(
                state,
                session_id=session_id,
                event_id=f"decay:{message_id}",
                character_id=CHARACTER,
                relationship_id=CHARACTER,
            )
            self._save_state(conn, state)
            emotion = state["emotion"]
            log_lines = []
            for item in _recent_log(state):
                if not isinstance(item, dict):
                    continue
                entry = dict(item)
                entry["other_chat"] = str(entry.get("chat_id") or "") not in ("", chat_id)
                log_lines.append(entry)
            return {
                "summary": engine.emotion_summary(state),
                "pulse": engine.pulse_summary(state),
                "pleasure": float(emotion["pleasure"]),
                "arousal": float(emotion["arousal"]),
                "dominance": float(emotion["dominance"]),
                "trust_tier": engine.public_status(state)["trust_tier"],
                "log": log_lines,
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
        with self._lock, self.db.connect() as conn:
            state = self._state(conn, for_update=True)
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
                state, result = engine.record_turn(
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
                    relationship_id=CHARACTER,
                )
                entries = state.get("emotion_log") or []
                if (
                    entries
                    and isinstance(entries[-1], dict)
                    and entries[-1].get("event_id") == message_id
                ):
                    entries[-1]["chat_id"] = chat_id
                    entries[-1]["person_name"] = " ".join(str(name or "").split())[:80]
                if str(result.get("status")) not in {"recorded", "state_only", "already_closed"}:
                    log.warning(
                        "turn emosi tidak diterapkan chat=%s status=%s aktif=%s",
                        chat_id,
                        result.get("status"),
                        result.get("active_session_id"),
                    )
            person = self._touch_person(conn, person_id, name, role, moment)
            self._touch_chat_person(conn, chat_id, person_id, moment)
            self._touch_drives(conn, chat_id, moment, activity=True)
            self._bump_topic(conn, chat_id, moment)
            if decision is not None and addressed == "me" and act != "ignore":
                self._apply_relationship(conn, person_id, person, decision.relationship, moment)
            if decision is not None:
                self._save_state(conn, state)

    @staticmethod
    def _meaning(decision) -> str:
        note = str(getattr(decision.appraisal, "note", "") or "").strip()
        reason = str(getattr(decision, "reason", "") or "").strip()
        return note or reason

    def _apply_relationship(
        self, conn, person_id: str, person: dict, delta, moment: float
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
            update(people)
            .where(people.c.person_id == person_id)
            .values(
                trust=values["trust"],
                affection=values["affection"],
                respect=values["respect"],
                annoyance=values["annoyance"],
                stage=stage,
                window_start=start,
                window_trust=person["window_trust"],
                window_affection=person["window_affection"],
                window_respect=person["window_respect"],
                window_annoyance=person["window_annoyance"],
            )
        )

    def _person(self, conn, person_id: str) -> dict:
        row = conn.execute(
            select(people).where(people.c.person_id == person_id)
        ).mappings().first()
        if row is None:
            return {"person_id": person_id, **PERSON_DEFAULTS}
        data = dict(row)
        data["aliases"] = _as_list(data.get("aliases"))
        data["notes"] = _as_list(data.get("notes"))
        return data

    def _touch_person(
        self, conn, person_id: str, name: str, role: str, moment: float
    ) -> dict:
        person = self._person(conn, person_id)
        clean_name = " ".join(str(name or "").split())[:80]
        aliases = [str(item) for item in person["aliases"]]
        if clean_name and person["name"] and clean_name != person["name"] and clean_name not in aliases:
            aliases = [*aliases, clean_name][-8:]
        chosen = clean_name or person["name"]
        interactions = int(person["interactions"]) + 1
        familiarity = min(1.0, interactions / 200.0)
        aliases = aliases[-8:]
        self.db.upsert(
            conn,
            people,
            [
                {
                    "person_id": person_id,
                    "name": chosen,
                    "aliases": aliases,
                    "role": role,
                    "interactions": interactions,
                    "familiarity": familiarity,
                    "last_seen": moment,
                }
            ],
            ["person_id"],
            update=lambda stmt: {
                "name": stmt.excluded.name,
                "aliases": stmt.excluded.aliases,
                "role": stmt.excluded.role,
                "interactions": stmt.excluded.interactions,
                "familiarity": stmt.excluded.familiarity,
                "last_seen": stmt.excluded.last_seen,
            },
        )
        person.update(
            name=chosen,
            aliases=aliases,
            role=role,
            interactions=interactions,
            familiarity=familiarity,
            last_seen=moment,
        )
        return person

    def _touch_chat_person(self, conn, chat_id: str, person_id: str, moment: float) -> None:
        """Keanggotaan chat: siapa yang benar-benar ikut bicara di sini, bukan global."""
        self.db.upsert(
            conn,
            chat_people,
            [
                {
                    "chat_id": chat_id,
                    "person_id": person_id,
                    "last_seen": moment,
                    "interactions": 1,
                }
            ],
            ["chat_id", "person_id"],
            update=lambda stmt: {
                "last_seen": stmt.excluded.last_seen,
                "interactions": chat_people.c.interactions + 1,
            },
        )

    def _bump_topic(self, conn, chat_id: str, moment: float) -> None:
        self.db.upsert(
            conn,
            topics,
            [{"chat_id": chat_id, "pending": 1, "last_message": moment}],
            ["chat_id"],
            update=lambda stmt: {
                "pending": topics.c.pending + 1,
                "last_message": stmt.excluded.last_message,
            },
        )

    def _drives(self, conn, chat_id: str) -> dict:
        """Drive milik Sanka sendiri, bukan milik ruangan: satu baris untuk semua chat."""
        row = conn.execute(
            select(drives).where(drives.c.chat_id == CHARACTER)
        ).mappings().first()
        if row is None:
            return {"chat_id": CHARACTER, **DRIVE_DEFAULTS}
        return dict(row)

    def _write_drives(self, conn, chat_id: str, data: dict) -> None:
        self.db.upsert(
            conn,
            drives,
            [
                {
                    "chat_id": CHARACTER,
                    "energy": _clamp(float(data["energy"])),
                    "boredom": _clamp(float(data["boredom"])),
                    "loneliness": _clamp(float(data["loneliness"])),
                    "last_pulse": float(data["last_pulse"]),
                }
            ],
            ["chat_id"],
            update=lambda stmt: {
                "energy": stmt.excluded.energy,
                "boredom": stmt.excluded.boredom,
                "loneliness": stmt.excluded.loneliness,
                "last_pulse": stmt.excluded.last_pulse,
            },
        )

    def _touch_drives(self, conn, chat_id: str, moment: float, *, activity: bool) -> None:
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
        """Drive publik (energi, kebosanan, kesepian) untuk prompt."""
        with self._lock, self.db.connect() as conn:
            data = self._drives(conn, chat_id)
        return {key: float(data[key]) for key in ("energy", "boredom", "loneliness")}

    def _latest_affection(self, conn, chat_id: str) -> float:
        row = conn.execute(
            select(people.c.affection)
            .select_from(
                chat_people.join(people, people.c.person_id == chat_people.c.person_id)
            )
            .where(chat_people.c.chat_id == chat_id)
            .order_by(chat_people.c.last_seen.desc())
            .limit(1)
        ).first()
        return _clamp(row[0]) if row is not None else 0.2

    def pulse(self, chat_id: str) -> dict:
        """Detak 24/7: state meluruh (maks satu langkah per 5 menit) dan drive tumbuh."""
        moment = time.time()
        event_id = f"pulse:{CHARACTER}:{int(moment // 300)}"
        with self._lock, self.db.connect() as conn:
            state_data = self._state(conn, for_update=True)
            if engine.event_already_processed(state_data, event_id):
                # tick di dalam bucket yang sama tidak menulis apa pun: state tetap.
                data = self._drives(conn, chat_id)
                return self._payload(state_data, data)
            state_data, session_id = self._session(state_data, chat_id)
            state_data, _ = engine.pre_turn_decay(
                state_data,
                session_id=session_id,
                event_id=event_id,
                character_id=CHARACTER,
                relationship_id=CHARACTER,
            )
            self._save_state(conn, state_data)
            data = self._drives(conn, chat_id)
            last = float(data["last_pulse"] or 0.0)
            if last <= 0:  # chat lama/DB baru: detak pertama hanya menambatkan waktu
                data["last_pulse"] = moment
                self._write_drives(conn, chat_id, data)
                return self._payload(state_data, data)
            elapsed = min(max(0.0, moment - last), PULSE_MAX_HOURS * 3600.0)
            if elapsed >= PULSE_MINUTES * 60:
                hours = elapsed / 3600.0
                data["energy"] = _clamp(float(data["energy"]) - 0.06 * hours)
                data["boredom"] = _clamp(float(data["boredom"]) + 0.08 * hours)
                attachment = self._latest_affection(conn, chat_id)
                data["loneliness"] = _clamp(
                    float(data["loneliness"]) + (0.03 + 0.05 * attachment) * hours
                )
                self._soothe(conn, hours)
                data["last_pulse"] = moment
                self._write_drives(conn, chat_id, data)
            return self._payload(state_data, data)

    def _soothe(self, conn, hours: float) -> None:
        """Kesal reda sendiri saat tidak ada yang menyulut: label 'musuh' bukan hukuman seumur hidup.

        Paruh waktunya `grudge_half_life_hours` dikali kedekatan: makin sayang/percaya, makin lama
        kesalnya bertahan (sayang bukan berarti cepat lupa). Hanya annoyance yang meluruh; trust,
        afeksi, dan respek tidak dikikis waktu.
        """
        base = float(self.config.grudge_half_life_hours or 0.0)
        if base <= 0 or hours <= 0:
            return
        rows = conn.execute(
            select(
                people.c.person_id,
                people.c.annoyance,
                people.c.affection,
                people.c.trust,
                people.c.familiarity,
            ).where(people.c.annoyance > 0)
        ).all()
        for row in rows:
            # Dekat = sayang, percaya, dan sering ngobrol; tiga-tiganya memperlambat pemaafan.
            life = (
                base
                * (1.0 + 3.0 * float(row.affection) + 2.0 * float(row.trust))
                * (1.0 + 2.0 * float(row.familiarity))
            )
            value = float(row.annoyance) * 0.5 ** (hours / life)
            conn.execute(
                update(people)
                .where(people.c.person_id == str(row.person_id))
                .values(annoyance=0.0 if value < SOOTHE_FLOOR else _clamp(value))
            )

    # ------------------------------------------------------------------ tenang

    def calm(self, chat_id: str, person_id: str = "") -> bool:
        """Repair turn: PAD kembali ke baseline dan kekesalan orang target turun setengah."""
        target = " ".join(str(person_id or "").split())
        with self._lock, self.db.connect() as conn:
            state_data = self._state(conn, for_update=True)
            if target:
                pid = self._person_by_name(conn, target) or ""
                if not pid:
                    digits = "".join(ch for ch in target if ch.isdigit())
                    row = (
                        conn.execute(
                            select(people.c.person_id).where(people.c.person_id == digits)
                        ).first()
                        if digits
                        else None
                    )
                    pid = str(row[0]) if row is not None else ""
                rows = (
                    conn.execute(
                        select(people.c.person_id, people.c.annoyance).where(
                            people.c.person_id == pid
                        )
                    ).all()
                    if pid
                    else []
                )
            else:
                rows = conn.execute(
                    select(people.c.person_id, people.c.annoyance)
                    .select_from(
                        chat_people.join(
                            people, people.c.person_id == chat_people.c.person_id
                        )
                    )
                    .where(chat_people.c.chat_id == chat_id)
                ).all()
            emotion = state_data["emotion"]
            annoyance = max(
                [float(row.annoyance) for row in rows],
                default=max(0.0, -float(emotion["pleasure"])),
            )
            upset = float(emotion["pleasure"]) < 0 or annoyance > 0.1
            for row in rows:
                conn.execute(
                    update(people)
                    .where(people.c.person_id == str(row.person_id))
                    .values(annoyance=_clamp(float(row.annoyance) * 0.5))
                )
            state_data, session_id = self._session(state_data, chat_id)
            baseline = state_data["personality_baseline"]
            state_data, _ = engine.record_turn(
                state_data,
                baseline["pleasure"],
                baseline["arousal"],
                baseline["dominance"],
                appraisal="repair",
                situation="owner menenangkan",
                session_id=session_id,
                event_id=f"calm:{time.time_ns()}",
                host_approved=True,
                character_id=CHARACTER,
                relationship_id=CHARACTER,
            )
            self._save_state(conn, state_data)
        return bool(upset)

    # ------------------------------------------------------------------ topik & orang

    def topic(self, chat_id: str) -> dict:
        with self._lock, self.db.connect() as conn:
            row = conn.execute(
                select(topics).where(topics.c.chat_id == chat_id)
            ).mappings().first()
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
                "research_day": "",
                "research_count": 0,
            }
        data = dict(row)
        data["keywords"] = _as_list(data["keywords"])
        data["unresolved"] = _as_list(data["unresolved"])
        return data

    def topic_block(self, chat_id: str) -> str:
        data = self.topic(chat_id)
        summary = str(data["summary"]).strip()
        if not summary:
            return ""
        lines = [f"Topic currently hot in this chat: {summary}"]
        pending = [str(item) for item in data["unresolved"] if str(item).strip()][:2]
        if pending:
            lines.append(f"Unanswered questions: {'; '.join(pending)}")
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
        with self._lock, self.db.connect() as conn:
            self.db.upsert(
                conn,
                topics,
                [
                    {
                        "chat_id": chat_id,
                        "summary": " ".join(str(summary or "").split())[:240],
                        "keywords": clean_keywords,
                        "unresolved": clean_pending,
                        "last_message": moment,
                    }
                ],
                ["chat_id"],
                update=lambda stmt: {
                    "summary": stmt.excluded.summary,
                    "keywords": stmt.excluded.keywords,
                    "unresolved": stmt.excluded.unresolved,
                    "last_message": stmt.excluded.last_message,
                },
            )

    def pending(self, chat_id: str) -> int:
        return int(self.topic(chat_id)["pending"])

    def mark_digest(self, chat_id: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        with self._lock, self.db.connect() as conn:
            self.db.upsert(
                conn,
                topics,
                [{"chat_id": chat_id, "pending": 0, "last_digest": moment}],
                ["chat_id"],
                update=lambda stmt: {
                    "pending": 0,
                    "last_digest": stmt.excluded.last_digest,
                },
            )

    def mark_research(self, chat_id: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        day = time.strftime("%Y-%m-%d", time.localtime(moment))
        with self._lock, self.db.connect() as conn:
            row = conn.execute(
                select(topics.c.research_count, topics.c.research_day).where(
                    topics.c.chat_id == chat_id
                )
            ).first()
            if row is None:
                count = 1
            else:
                count = int(row.research_count) + 1 if row.research_day == day else 1
            self.db.upsert(
                conn,
                topics,
                [
                    {
                        "chat_id": chat_id,
                        "last_research": moment,
                        "research_day": day,
                        "research_count": count,
                    }
                ],
                ["chat_id"],
                update=lambda stmt: {
                    "last_research": stmt.excluded.last_research,
                    "research_day": stmt.excluded.research_day,
                    "research_count": stmt.excluded.research_count,
                },
            )

    def _person_by_name(self, conn, name: str) -> str | None:
        target = " ".join(str(name or "").split()).casefold()
        if not target:
            return None
        rows = conn.execute(
            select(people.c.person_id, people.c.name, people.c.aliases)
            .order_by(people.c.last_seen.desc())
            .limit(500)
        ).all()
        for row in rows:
            if str(row.name).casefold() == target:
                return str(row.person_id)
        for row in rows:
            aliases = [str(item).casefold() for item in _as_list(row.aliases)]
            if target in aliases:
                return str(row.person_id)
        for row in rows:
            if target and target in str(row.name).casefold():
                return str(row.person_id)
        return None

    def person_by_name(self, chat_id: str, name: str) -> str | None:
        with self._lock, self.db.connect() as conn:
            return self._person_by_name(conn, name)

    def _known_people(self, conn, chat_id: str, limit: int) -> list[dict]:
        rows = conn.execute(
            select(
                people,
                chat_people.c.interactions.label("chat_interactions"),
                chat_people.c.last_seen.label("chat_last_seen"),
            )
            .select_from(
                chat_people.join(people, people.c.person_id == chat_people.c.person_id)
            )
            .where(chat_people.c.chat_id == chat_id)
            .order_by(chat_people.c.last_seen.desc())
            .limit(max(1, int(limit)))
        ).mappings().all()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            item["aliases"] = _as_list(item["aliases"])
            item["notes"] = _as_list(item["notes"])
            result.append(item)
        return result

    def known_people(self, chat_id: str, limit: int = 8) -> list[dict]:
        """Orang yang benar-benar ikut chat ini, terbaru dulu; chat lain tidak bocor ke sini."""
        with self._lock, self.db.connect() as conn:
            return self._known_people(conn, chat_id, limit)

    def note_person(self, person_id: str, note: str) -> None:
        text = guard.safe_text(note, NOTES_TEXT)
        if not person_id or not text:
            return
        with self._lock, self.db.connect() as conn:
            person = self._person(conn, person_id)
            notes = [str(item) for item in person["notes"]]
            if text in notes:
                return
            notes = [*notes, text][-NOTES_KEEP:]
            conn.execute(
                update(people)
                .where(people.c.person_id == person_id)
                .values(notes=notes)
            )

    def gender(self, person_id: str) -> tuple[str, float, str]:
        """(label, keyakinan, sumber) tersimpan; kosong kalau orangnya belum dikenal."""
        if not person_id:
            return ("", 0.0, "")
        with self._lock, self.db.connect() as conn:
            row = conn.execute(
                select(people.c.gender, people.c.gender_confidence, people.c.gender_source).where(
                    people.c.person_id == person_id
                )
            ).first()
        if row is None:
            return ("", 0.0, "")
        label = str(row[0] or "")
        return (
            label if label in GENDERS else "",
            float(row[1] or 0.0),
            str(row[2] or ""),
        )

    def set_gender(
        self, person_id: str, gender: str, confidence: float = 0.0, source: str = ""
    ) -> None:
        """Simpan label gender; id yang belum dikenal tidak dibuatkan baris baru."""
        label = str(gender or "").strip().lower()
        if not person_id or label not in GENDERS:
            return
        try:
            score = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            score = 0.0
        with self._lock, self.db.connect() as conn:
            exists = conn.execute(
                select(people.c.person_id).where(people.c.person_id == person_id)
            ).first()
            if exists is None:
                return
            conn.execute(
                update(people)
                .where(people.c.person_id == person_id)
                .values(
                    gender=label,
                    gender_confidence=score,
                    gender_source=str(source or "")[:16],
                )
            )

    # ------------------------------------------------------------------ prompt

    def _payload(self, state_data: dict, drives_data: dict, person_id: str = "", chat_id: str = "") -> dict:
        emotion = state_data["emotion"]
        trust_tier = engine.public_status(state_data)["trust_tier"]
        person = None
        if person_id:
            person = next(
                (
                    item
                    for item in self.known_people(chat_id, 50)
                    if item["person_id"] == person_id
                ),
                None,
            )
        return {
            "summary": engine.emotion_summary(state_data),
            "pulse": engine.pulse_summary(state_data),
            "pleasure": float(emotion["pleasure"]),
            "arousal": float(emotion["arousal"]),
            "dominance": float(emotion["dominance"]),
            "trust_tier": trust_tier,
            "trust": float(state_data.get("trust", 0.0)),
            "energy": float(drives_data["energy"]),
            "boredom": float(drives_data["boredom"]),
            "loneliness": float(drives_data["loneliness"]),
            "turns": int(state_data.get("total_turns", 0)),
            "annoyance": float(person["annoyance"]) if person is not None else 0.0,
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
        with self._lock, self.db.connect() as conn:
            state_data = self._state(conn)
            drives_data = self._drives(conn, chat_id)
        return self._payload(state_data, drives_data, person_id, chat_id)

    def pad(self, chat_id: str) -> dict:
        """PAD saat ini: dipakai untuk menandai valence/arousal memori yang ditulis."""
        with self._lock, self.db.connect() as conn:
            state_data = self._state(conn)
        emotion = state_data["emotion"]
        return {
            "pleasure": float(emotion["pleasure"]),
            "arousal": float(emotion["arousal"]),
            "dominance": float(emotion["dominance"]),
        }

    def prompt(self, chat_id: str, person_id: str = "") -> str:
        """Blok prompt: ringkasan state, pulse, PAD, log terakhir, dan sikap ke orang di chat."""
        with self._lock, self.db.connect() as conn:
            state_data = self._state(conn)
            drives_data = self._drives(conn, chat_id)
            people = self._known_people(conn, chat_id, 6)
        emotion = state_data["emotion"]
        trust_tier = engine.public_status(state_data)["trust_tier"]
        parts = [
            (
                f"Your mood: {engine.emotion_summary(state_data)}. "
                f"P {float(emotion['pleasure']):.1f}, A {float(emotion['arousal']):.1f}, "
                f"D {float(emotion['dominance']):.1f}, trust {trust_tier}. "
                f"Pulse: {engine.pulse_summary(state_data)}."
            )
        ]
        log_lines: list[str] = []
        for entry in _recent_log(state_data):
            if not isinstance(entry, dict):
                continue
            cue = str(entry.get("situation") or entry.get("cue") or "").strip()
            label = str(entry.get("appraisal") or entry.get("event_type") or "").strip()
            meaning = str(entry.get("relational_meaning") or "").strip()
            if not cue and not label:
                continue
            who = str(entry.get("person_name") or "").strip()
            other = str(entry.get("chat_id") or "") not in ("", chat_id)
            tag = ""
            if who or other:
                tag = f" [{who or 'other chat'}{', other chat' if who and other else ''}]"
            line = f"- {label or 'event'}{tag}: {cue}"
            if meaning:
                line += f" ({meaning})"
            log_lines.append(line)
        if log_lines:
            parts.append("What you recently felt:\n" + "\n".join(log_lines))
        urges: list[str] = []
        if float(drives_data["boredom"]) >= 0.65:
            urges.append("bored")
        if float(drives_data["loneliness"]) >= 0.65:
            urges.append("lonely")
        if float(drives_data["energy"]) <= 0.25:
            urges.append("tired")
        if urges:
            parts.append(f"You are {', '.join(urges)}; it may show in how you talk.")
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
                f"trust {float(item['trust']):.1f}, affection {float(item['affection']):.1f}, "
                f"annoyance {float(item['annoyance']):.1f}, "
                f"{int(item['chat_interactions'])} interactions, "
                f"last seen {_ago(item['chat_last_seen'], moment)}."
            )
            notes = [str(note) for note in item["notes"][:3] if str(note).strip()]
            if notes:
                line += f" Notes: {'; '.join(notes)}."
            lines.append(line)
        if lines:
            parts.append("Your relationships:")
            parts.extend(lines)
        parts.append(
            "Express this through word choice and reply length. Never mention these numbers or state."
        )
        text = "\n".join(parts)
        if len(text) > PROMPT_CHARS:
            text = text[: PROMPT_CHARS - 1].rstrip() + "…"
        return text

    # ------------------------------------------------------------------ seed

    def _flag(self, conn, key: str) -> str | None:
        row = conn.execute(select(state.c.value).where(state.c.key == key)).first()
        return None if row is None else str(row[0])

    def _set_flag(self, conn, key: str) -> None:
        self.db.upsert(
            conn,
            state,
            [{"key": key, "value": "1", "updated_at": time.time()}],
            ["key"],
            update=lambda stmt: {
                "value": stmt.excluded.value,
                "updated_at": stmt.excluded.updated_at,
            },
        )

    @staticmethod
    def _resolve_person(history, jid: str) -> str:
        resolver = getattr(history, "resolve_person", None)
        if not callable(resolver) or not jid:
            return ""
        try:
            return str(resolver(jid) or "")
        except Exception:
            return ""

    def seed_from_history(self, history) -> int:
        """Sekali saja: bangun tabel orang dari arsip."""
        with self._lock, self.db.connect() as conn:
            if self._flag(conn, SEEDED_KEY) == "1":
                return 0
        try:
            rows = history.people_seed(5000) if history is not None else []
        except Exception:
            log.exception("gagal membaca arsip untuk seed orang")
            rows = []
        grouped: dict[str, dict] = {}
        for item in rows:
            jid = str(item.get("sender_jid") or "")
            person_id = self._resolve_person(history, jid) or str(item.get("sender_id") or "")
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
        with self._lock, self.db.connect() as conn:
            for person_id, bucket in grouped.items():
                self._seed_chat_rows(conn, person_id, bucket["chats"])
                if conn.execute(
                    select(people.c.person_id).where(people.c.person_id == person_id)
                ).first() is not None:
                    continue
                count = int(bucket["count"])
                name = max(bucket["names"], key=bucket["names"].get) if bucket["names"] else ""
                conn.execute(
                    insert(people).values(
                        person_id=person_id,
                        name=name,
                        role="member",
                        stage=stage_for(count, 0.1, 0.0),
                        trust=0.1,
                        affection=0.0,
                        respect=0.5,
                        familiarity=min(1.0, count / 200.0),
                        interactions=count,
                        last_seen=bucket["last"],
                        notes=[],
                    )
                )
            self._set_flag(conn, SEEDED_KEY)
        return len(grouped)

    @staticmethod
    def _seed_chat_rows(conn, person_id: str, chats: dict) -> None:
        for chat_id, (seen, count) in chats.items():
            existing = conn.execute(
                select(chat_people.c.last_seen, chat_people.c.interactions).where(
                    chat_people.c.chat_id == chat_id,
                    chat_people.c.person_id == person_id,
                )
            ).first()
            if existing is None:
                conn.execute(
                    insert(chat_people).values(
                        chat_id=chat_id,
                        person_id=person_id,
                        last_seen=float(seen),
                        interactions=int(count),
                    )
                )
                continue
            conn.execute(
                update(chat_people)
                .where(
                    chat_people.c.chat_id == chat_id,
                    chat_people.c.person_id == person_id,
                )
                .values(
                    last_seen=max(float(existing.last_seen), float(seen)),
                    interactions=max(int(existing.interactions), int(count)),
                )
            )

    def seed_chat_people(self, history) -> int:
        """Sekali saja untuk DB emosi lama: lengkapi anggota tiap chat dari arsip."""
        with self._lock, self.db.connect() as conn:
            if self._flag(conn, CHAT_PEOPLE_SEEDED_KEY) == "1":
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
            person_id = self._resolve_person(history, jid) or str(item.get("sender_id") or "")
            if not chat_id or not person_id:
                continue
            stamp = float(item.get("ts") or 0.0)
            chats = grouped.setdefault(person_id, {})
            seen, count = chats.get(chat_id, (0.0, 0))
            chats[chat_id] = (max(seen, stamp), count + 1)
        with self._lock, self.db.connect() as conn:
            for person_id, chats in grouped.items():
                self._seed_chat_rows(conn, person_id, chats)
            self._set_flag(conn, CHAT_PEOPLE_SEEDED_KEY)
        return sum(len(chats) for chats in grouped.values())

    def stats(self) -> dict:
        with self._lock, self.db.connect() as conn:
            def _count(table) -> int:
                return int(conn.execute(select(func.count()).select_from(table)).scalar_one())

            states = conn.execute(
                select(state.c.key).where(state.c.key == STATE_KEY)
            ).first()
            return {
                "people": _count(people),
                "topics": _count(topics),
                "drives": _count(drives),
                "chat_people": _count(chat_people),
                "states": 1 if states is not None else 0,
            }
