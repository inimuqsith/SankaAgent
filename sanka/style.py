"""Gaya chat: agregat statistik dari arsip, tidak pernah menyimpan isi pesan.

Profil ini yang membuat balasan Sanka mengikuti ritme manusia di chat ini (pendek, huruf
kecil, partikel, emoji sesekali), bukan meniru opini siapa pun.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from .config import StyleConfig

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
    """Samakan bentuk data dari file lama agar field baru tidak hilang."""
    for key, value in _empty().items():
        empty_list = isinstance(value, list) and not isinstance(source[key], list)
        empty_dict = isinstance(value, dict) and not isinstance(source[key], dict)
        if key not in source or empty_list or empty_dict:
            source[key] = value
    return source


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[index]


class StyleStore:
    """Statistik gaya chat persisten; JSON kecil, ditulis atomik, hanya agregat."""

    def __init__(self, path: Path | str, config: StyleConfig | None = None):
        self.config = config or StyleConfig()
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        self._data = self._load()

    # ------------------------------------------------------------------ file

    def _load(self) -> dict:
        data = {"seeded": False, "global": _empty(), "chats": {}, "people": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
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
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            try:
                tmp.chmod(0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError:
            log.warning("gagal menyimpan profil gaya %s", self.path, exc_info=True)

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
            for text in rows:
                body = str(text or "")
                if not body.strip() or body.startswith("/") or len(body) > MAX_TEXT:
                    continue
                if any(marker in body for marker in PLACEHOLDERS):
                    continue
                self._count(self._data["global"], body)
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
            "Gaya chat manusia di sini (tiru ritme dan diksi, bukan isi/opini):",
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
