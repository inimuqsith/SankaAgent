"""Katalog pola runtime: pilih gerakan karakter dari `sanka/data/patterns.json`.

Katalog itu bukan transkrip. Satu baris = satu gerakan ("kalau ini, maka itu") plus
gatenya: register ruangan, level toxicity/adult, dan target gender. Pemilihan di sini
deterministik: kata kunci dulu, lalu cosine vektor MiniLM bila `pattern_vectors.json`
tersedia; tanpa model, skor kata kunci tetap jalan. Tidak ada undian dan tidak ada
panggilan jaringan di jalur kata kunci.
"""

from __future__ import annotations

import json
import logging
import math
import re
from functools import cache
from pathlib import Path

log = logging.getLogger(__name__)

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DATA = Path(__file__).resolve().parent.parent / "data" / "patterns.json"
VECTORS = Path(__file__).resolve().parent.parent / "data" / "pattern_vectors.json"
DIM = 384
SERIOUS = ("capek", "sedih", "takut", "maaf", "sakit", "meninggal", "putus")
LAUGHTER = ("wkwk", "wkwkwk", "😂", "😹")
ROAST = ("goblok", "anjir", "ngeselin")
MEME = ("jomok", "woilah", "gemoy", "sawit", "ytta", "meme", "stiker")
SERIOUS_REGISTERS = frozenset({"serious", "comfort", "soal"})
TARGETS = frozenset({"male", "female", "unknown"})
WINDOW = 8
CAP = 6
LINE_MAX = 180
MIN_SIMILARITY = 0.20
COVERAGE = 0.8
KEY_WEIGHT, HIT_WEIGHT = 0.7, 0.3
_AUTO = object()
_warned: set[str] = set()


def _warn_once(reason: str, message: str, *args) -> None:
    if reason in _warned:
        return
    _warned.add(reason)
    log.warning(message, *args)


WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _number(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_row(row: dict) -> dict | None:
    ident = str(row.get("id") or "").strip()
    content = str(row.get("content") or "").strip()
    registers = [str(item) for item in row.get("register") or [] if str(item).strip()]
    if not ident or not content or not registers:
        return None
    return {
        "id": ident,
        "register": registers,
        "keys": [str(item).lower() for item in row.get("keys") or [] if str(item).strip()],
        "target": str(row.get("target") or "any").strip().lower(),
        "min_toxicity": _number(row.get("min_toxicity"), 0),
        "max_toxicity": _number(row.get("max_toxicity"), 4),
        "min_adult": _number(row.get("min_adult"), 0),
        "content": content,
        "say": [str(item).strip() for item in row.get("say") or [] if str(item).strip()],
        "priority": _number(row.get("priority"), 1),
        "always": bool(row.get("always")),
        "source": str(row.get("source") or ""),
    }


@cache
def load_patterns(path: str | None = None) -> list[dict]:
    """Baris pola dari paket; file hilang atau rusak = daftar kosong, bukan crash."""
    target = Path(path or DATA)
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _warn_once("patterns", "pola: %s tidak terbaca", target)
        return []
    found = body.get("patterns") if isinstance(body, dict) else None
    if not isinstance(found, list):
        return []
    clean = [_clean_row(row) for row in found if isinstance(row, dict)]
    return [row for row in clean if row]


def room_register(messages: list[str]) -> str:
    """ "serious" | "banter" | "meme" | "neutral" untuk jendela pesan terakhir."""
    body = " ".join(str(item or "") for item in messages[-WINDOW:]).lower()
    if any(marker in body for marker in SERIOUS):
        return "serious"
    if any(marker in body for marker in LAUGHTER) or any(marker in body for marker in ROAST):
        return "banter"
    if any(marker in body for marker in MEME):
        return "meme"
    return "neutral"


def _target(value: str) -> str:
    body = str(value or "").strip().lower()
    return body if body in TARGETS else "unknown"


def _register_ok(found: list[str], register: str) -> bool:
    if register in found:
        return True
    return register == "serious" and any(item in SERIOUS_REGISTERS for item in found)


def _eligible(row: dict, register: str, target: str, limits: tuple[int, int]) -> bool:
    toxic, adult = int(limits[0]), int(limits[1])
    if row["target"] not in ("any", target):
        return False
    if row["min_toxicity"] > toxic or toxic > row["max_toxicity"]:
        return False
    if row["min_adult"] > adult:
        return False
    return _register_ok(row["register"], register)


def _found(key: str, body: str, tokens: set[str]) -> bool:
    """Key pendek dicocokkan per kata supaya "ai" tidak kena "sampai"."""
    return key in tokens if len(key) <= 3 else key in body


def _hits(keys: list[str], body: str) -> int:
    tokens = set(WORD.findall(body))
    return sum(1 for key in keys if _found(key, body, tokens))


@cache
def _load_vectors() -> dict:
    try:
        body = json.loads(VECTORS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(body, dict) or body.get("model") != MODEL or body.get("dim") != DIM:
        return {}
    return body.get("vectors") if isinstance(body.get("vectors"), dict) else {}


def _vectors(ids: list[str]) -> dict[str, list[float]]:
    found = _load_vectors()
    if not found:
        return {}
    usable = {
        ident: found[ident]
        for ident in ids
        if isinstance(found.get(ident), list) and len(found[ident]) == DIM
    }
    if len(usable) < COVERAGE * len(ids):
        return {}
    return usable


def _cosine(left, right) -> float:
    norm_left = math.sqrt(sum(float(value) ** 2 for value in left))
    norm_right = math.sqrt(sum(float(value) ** 2 for value in right))
    if not norm_left or not norm_right:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
    return dot / (norm_left * norm_right)


@cache
def _default_encoder():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        _warn_once("model", "pola: sentence-transformers tidak terpasang, pakai kata kunci")
        return None
    try:
        return SentenceTransformer(MODEL, device="cpu")
    except Exception:
        _warn_once("load", "pola: gagal memuat %s, pakai kata kunci", MODEL)
        return None


def _given(value):
    """Encoder eksplisit: apa pun yang punya `.encode`, bukan hanya objek callable."""
    return value if hasattr(value, "encode") else None


def _cosines(patterns: list[dict], text: str, encoder) -> dict[str, float]:
    body = str(text or "").strip()
    if not body:
        return {}
    vectors = _vectors([row["id"] for row in patterns])
    if not vectors:
        return {}
    model = _given(encoder) or _default_encoder()
    if model is None:
        return {}
    try:
        query = model.encode([body], convert_to_numpy=True)[0]
    except Exception:
        _warn_once("encode", "pola: gagal encode, pakai kata kunci")
        return {}
    return {ident: _cosine(query, vector) for ident, vector in vectors.items()}


def _score(hits: int, similarity: float | None) -> float | None:
    if similarity is None:
        return float(hits)
    if similarity < MIN_SIMILARITY and not hits:
        return None
    return KEY_WEIGHT * similarity + HIT_WEIGHT * min(1.0, hits / 3)


def _should_embed(encoder, ids: list[str]) -> bool:
    """`encoder=None` memaksa jalur kata kunci; default memakai MiniLM bila vektor siap."""
    if encoder is None:
        return False
    return bool(_given(encoder)) or bool(_vectors(ids))


def select_patterns(
    count: int,
    *,
    register: str,
    target: str,
    limits: tuple[int, int],
    messages: list[str],
    encoder=_AUTO,
) -> list[str]:
    """Pilih `count` pola paling relevan untuk turn ini (deterministik, tanpa undian)."""
    limit = max(0, min(CAP, int(count)))
    if not limit:
        return []
    room = _target(target)
    eligible = [row for row in load_patterns() if _eligible(row, register, room, limits)]
    if not eligible:
        return []
    body = " ".join(str(item or "") for item in messages[-WINDOW:]).lower()
    hits = {row["id"]: _hits(row["keys"], body) for row in eligible}
    ids = [row["id"] for row in eligible]
    cosines = _cosines(eligible, body, encoder) if _should_embed(encoder, ids) else {}
    scores: dict[str, float] = {}
    for row in eligible:
        value = _score(hits[row["id"]], None if row["always"] else cosines.get(row["id"]))
        if value is not None:
            scores[row["id"]] = value
    if not scores:
        return []
    ranked = sorted(
        (row for row in eligible if row["id"] in scores),
        key=lambda row: (-scores[row["id"]], -row["priority"], row["id"]),
    )
    return [_render(row) for row in ranked[:limit]]


def _render(row: dict) -> str:
    line = row["content"]
    if row["say"]:
        line += " (contoh: " + "; ".join(f'"{item}"' for item in row["say"]) + ")"
    return line[:LINE_MAX]
