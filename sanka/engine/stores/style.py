"""Gaya chat: agregat statistik dari arsip, tidak pernah menyimpan isi pesan.

Profil ini yang membuat balasan Sanka mengikuti ritme manusia di chat ini (pendek, huruf
kecil, partikel, emoji sesekali), bukan meniru opini siapa pun.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time

from sqlalchemy import select

from ..config import StyleConfig
from ..db import Database
from ..schema import state as state_table

log = logging.getLogger(__name__)

PLACEHOLDERS = ("[Stiker]", "[Gambar]", "[Dokumen]", "[Video]", "[Audio]")
MAX_LENGTHS = 200
TOP = 20
MAX_TEXT = 500
EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff]")
LAUGH = re.compile(r"(wkwk|awok|hehe|haha)", re.IGNORECASE)
REPEAT = re.compile(r"([a-zA-Z])\1{2,}")
PARTICLES = (
    "aja", "kok", "sih", "deh", "loh", "banget", "gitu", "gini", "kan", "nih", "tuh",
    "wkwk", "anjir", "jir", "wes", "iki", "iku", "yo", "ya", "eh", "mah", "lah", "dong",
)
EMOJIS = ("😭", "🗿", "🙏", "😹", "😂", "😊", "🥲", "😌", "🙃", "🔥")
SEED = {
    "median": 34,
    "p90": 130,
    "lower": 0.86,
    "emoji": 0.20,
    "particles": ("aja", "kok", "sih", "deh", "loh", "banget", "gitu", "gini", "kan", "nih", "tuh", "wkwk", "anjir", "jir", "wes", "iki", "iku", "yo"),
    "emojis": ("😭", "🗿", "🙏", "😹", "😂", "😊"),
}
SEED_MIN = 20
PERSON_MIN = 8

STATE_KEY = "style"


def _empty() -> dict:
    return {
        "n": 0,
        "lengths": [],
        "lower": 0,
        "upper": 0,
        "emoji": 0,
        "question": 0,
        "exclaim": 0,
        "laugh": 0,
        "repeat": 0,
        "particles": {},
        "emojis": {},
    }


def _merge(target: dict, source: dict) -> dict:
    """Samakan bentuk data lama agar field baru tidak hilang."""
    for key, value in _empty().items():
        item = source.get(key)
        empty_list = isinstance(value, list) and not isinstance(item, list)
        empty_dict = isinstance(value, dict) and not isinstance(item, dict)
        if item is None or empty_list or empty_dict:
            source[key] = value
    return source


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[index]


class StyleStore:
    """Statistik gaya chat persisten di tabel `state`; hanya agregat, tanpa isi pesan."""

    def __init__(self, db: Database, config: StyleConfig | None = None):
        self.config = config or StyleConfig()
        self._db = db
        self._lock = threading.RLock()
        self._data = self._load()

    # ------------------------------------------------------------------ state

    def _load(self) -> dict:
        data = {"seeded": False, "global": _empty(), "chats": {}, "people": {}}
        try:
            row = self._db.fetch_one(
                select(state_table.c.value).where(state_table.c.key == STATE_KEY)
            )
            raw = json.loads(row["value"]) if row is not None else None
        except Exception:
            log.warning("gagal membaca profil gaya dari database", exc_info=True)
            return data
        if not isinstance(raw, dict):
            return data
        data["seeded"] = bool(raw.get("seeded"))
        data["global"] = _merge(data["global"], raw.get("global") or {})
        for scope in ("chats", "people"):
            items = raw.get(scope)
            if isinstance(items, dict):
                data[scope] = {
                    str(key): _merge(_empty(), value)
                    for key, value in items.items()
                    if isinstance(value, dict)
                }
        return data

    def _flush(self) -> None:
        try:
            with self._db.connect() as conn:
                self._db.upsert(
                    conn,
                    state_table,
                    [
                        {
                            "key": STATE_KEY,
                            "value": json.dumps(self._data, ensure_ascii=False),
                            "updated_at": time.time(),
                        }
                    ],
                    ["key"],
                )
        except Exception:
            log.warning("gagal menyimpan profil gaya ke database", exc_info=True)

    def close(self) -> None:
        with self._lock:
            self._flush()

    # ------------------------------------------------------------------ belajar

    @staticmethod
    def _count(bucket: dict, text: str) -> None:
        letters = [char for char in text if char.isalpha()]
        bucket["n"] += 1
        bucket["lengths"] = [*bucket["lengths"], len(text)][-MAX_LENGTHS:]
        if letters:
            lower = sum(1 for char in letters if char.islower()) / len(letters)
            if lower >= 0.6:
                bucket["lower"] += 1
            elif lower <= 0.4:
                bucket["upper"] += 1
        if EMOJI.search(text):
            bucket["emoji"] += 1
        if text.rstrip().endswith("?"):
            bucket["question"] += 1
        if text.rstrip().endswith("!"):
            bucket["exclaim"] += 1
        if LAUGH.search(text):
            bucket["laugh"] += 1
        if REPEAT.search(text):
            bucket["repeat"] += 1
        folded = text.casefold()
        for particle in PARTICLES:
            hits = len(re.findall(rf"\b{particle}\b", folded))
            if hits:
                bucket["particles"][particle] = bucket["particles"].get(particle, 0) + hits
                if len(bucket["particles"]) > TOP:
                    keep = sorted(bucket["particles"].items(), key=lambda item: -item[1])[:TOP]
                    bucket["particles"] = dict(keep)
        for emoji in EMOJI.findall(text):
            bucket["emojis"][emoji] = bucket["emojis"].get(emoji, 0) + 1
        if len(bucket["emojis"]) > TOP:
            keep = sorted(bucket["emojis"].items(), key=lambda item: -item[1])[:TOP]
            bucket["emojis"] = dict(keep)

    def learn(self, text: str, *, chat_id: str = "", person_id: str = "") -> None:
        """Satu pesan manusia; command, placeholder media, dan teks panjang dilewati."""
        body = str(text or "")
        if not body.strip() or body.startswith("/") or len(body) > MAX_TEXT:
            return
        if any(marker in body for marker in PLACEHOLDERS):
            return
        with self._lock:
            self._count(self._data["global"], body)
            if chat_id:
                bucket = self._data["chats"].setdefault(chat_id, _empty())
                self._count(bucket, body)
            if person_id:
                bucket = self._data["people"].setdefault(person_id, _empty())
                self._count(bucket, body)
            self._flush()

    def seed_from_history(self, history) -> bool:
        """Sekali saja: pelajari sampel pesan manusia dari arsip, lalu kunci `seeded`."""
        with self._lock:
            if self._data.get("seeded"):
                return False
        try:
            rows = history.style_sample(2000) if history is not None else []
        except Exception:
            log.exception("gagal membaca sampel gaya dari arsip")
            rows = []
        with self._lock:
            for row in rows:
                item = row if isinstance(row, dict) else {"text": row}
                body = str(item.get("text") or "")
                if not body.strip() or body.startswith("/") or len(body) > MAX_TEXT:
                    continue
                if any(marker in body for marker in PLACEHOLDERS):
                    continue
                self._count(self._data["global"], body)
                chat_id = str(item.get("chat_id") or "")
                if chat_id:
                    self._count(self._data["chats"].setdefault(chat_id, _empty()), body)
                person_id = ""
                jid = str(item.get("sender_jid") or "")
                if jid and history is not None:
                    try:
                        person_id = history.resolve_person(jid)
                    except Exception:
                        person_id = ""
                person_id = person_id or str(item.get("sender_id") or "")
                if person_id:
                    self._count(self._data["people"].setdefault(person_id, _empty()), body)
            self._data["seeded"] = True
            self._flush()
        return True

    # ------------------------------------------------------------------ profil

    def seed_profile(self) -> dict:
        return dict(SEED)

    def _bucket(self, chat_id: str = "") -> dict:
        with self._lock:
            if chat_id:
                bucket = self._data["chats"].get(chat_id)
                if bucket is not None and bucket["n"] >= SEED_MIN:
                    return bucket
            bucket = self._data["global"]
            return bucket if bucket["n"] >= SEED_MIN else {}

    def _person_bucket(self, person_id: str) -> dict:
        with self._lock:
            bucket = self._data["people"].get(person_id) if person_id else None
        return bucket if bucket is not None and bucket["n"] >= PERSON_MIN else {}

    def person_profile(self, person_id: str) -> str:
        """Blok prompt ritme satu orang; kosong bila sampelnya belum cukup."""
        bucket = self._person_bucket(person_id)
        if not bucket:
            return ""
        lengths = [int(value) for value in bucket["lengths"]]
        median = _percentile(lengths, 0.5)
        p90 = _percentile(lengths, 0.9)
        total = max(1, int(bucket["n"]))
        particles = [
            name for name, _ in sorted(bucket["particles"].items(), key=lambda item: -item[1])[:5]
        ]
        emojis = [
            name for name, _ in sorted(bucket["emojis"].items(), key=lambda item: -item[1])[:3]
        ]
        lines = [
            "Cara chat orang ini (tiru ritme, bukan isi/opini):",
            (
                f"- median {median} karakter; 90% di bawah {p90}; "
                f"{round(bucket['lower'] / total * 100)}% huruf kecil."
            ),
            f"- partikel: {', '.join(particles) or '-'}.",
            f"- emoji sesekali: {' '.join(emojis) or '-'}.",
            (
                f"- {round(bucket['laugh'] / total * 100)}% pesan mengandung tawa; "
                f"{round(bucket['question'] / total * 100)}% bertanya."
            ),
        ]
        text = "\n".join(lines)
        limit = max(200, int(self.config.max_chars))
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def profile(self, chat_id: str = "") -> str:
        """Blok prompt gaya chat; kosong tidak pernah terjadi (seed jadi acuan minimal)."""
        bucket = self._bucket(chat_id)
        if not bucket:
            median, p90 = SEED["median"], SEED["p90"]
            lower = SEED["lower"]
            particles = list(SEED["particles"][:6])
            emojis = list(SEED["emojis"][:3])
        else:
            lengths = [int(value) for value in bucket["lengths"]]
            median = _percentile(lengths, 0.5)
            p90 = _percentile(lengths, 0.9)
            total = max(1, int(bucket["n"]))
            lower = bucket["lower"] / total
            particles = [
                name for name, _ in sorted(bucket["particles"].items(), key=lambda item: -item[1])[:6]
            ] or list(SEED["particles"][:6])
            emojis = [
                name for name, _ in sorted(bucket["emojis"].items(), key=lambda item: -item[1])[:3]
            ] or list(SEED["emojis"][:3])
        lines = [
            "How people chat here (mimic rhythm and diction, not content or opinions):",
            f"- median {median} karakter; 90% di bawah {p90}. Balas 1-2 kalimat pendek.",
            (
                f"- {round(lower * 100)}% huruf kecil; huruf besar/huruf berulang hanya buat "
                "nada kaget/ngambek."
            ),
            f"- partikel: {', '.join(particles)}.",
            f"- emoji sesekali: {' '.join(emojis)} (maks 1).",
            "- sering pecah jadi 1-3 pesan; jangan kirim satu blok panjang.",
        ]
        text = "\n".join(lines)
        limit = max(200, int(self.config.max_chars))
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def stats(self) -> dict:
        with self._lock:
            return {
                "seeded": bool(self._data.get("seeded")),
                "global": int(self._data["global"]["n"]),
                "chats": {key: int(value["n"]) for key, value in self._data["chats"].items()},
                "people": len(self._data["people"]),
            }
