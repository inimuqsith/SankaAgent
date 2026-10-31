"""Lapisan pengetahuan: fakta publik hasil riset, dipilih semantik per turn.

Terpisah dari pola karakter: pola mengatur cara bicara, fakta mengatur isi yang boleh
diketahui. Fakta tanpa vektor tetap bisa dipilih lewat kata kunci; berkas rusak = daftar
kosong, bukan crash. Prompt scaffolding English, isi fakta Indonesian seperti pola.
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
DATA = Path(__file__).resolve().parent.parent / "data" / "fakta.json"
VECTORS = Path(__file__).resolve().parent.parent / "data" / "fakta_vectors.json"
DIM = 384
CAP = 6
MIN_SIMILARITY = 0.55  # cosine yang cukup untuk memilih tanpa bantuan kata kunci
KEY_WEIGHT, HIT_WEIGHT = 0.7, 0.3
COVERAGE = 0.8
LINE_MAX = 220
_AUTO = object()
_warned: set[str] = set()

WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _warn_once(reason: str, message: str, *args) -> None:
    if reason in _warned:
        return
    _warned.add(reason)
    log.warning(message, *args)


def _clean(row: dict) -> dict | None:
    ident = str(row.get("id") or "").strip()
    text = str(row.get("text") or "").strip()
    if not ident or not text:
        return None
    return {
        "id": ident,
        "text": text,
        "keys": [str(key).lower() for key in row.get("keys") or [] if str(key).strip()],
        "topic": str(row.get("topic") or ""),
        "domains": [str(item) for item in row.get("domains") or []],
        "sources": [str(item) for item in row.get("sources") or []],
    }


@cache
def load_facts(path: str | None = None) -> list[dict]:
    """Baris fakta dari paket; file hilang atau rusak = daftar kosong, bukan crash."""
    target = Path(path or DATA)
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _warn_once(f"facts:{target}", "fakta: %s tidak terbaca", target)
        return []
    found = body.get("facts") if isinstance(body, dict) else None
    if not isinstance(found, list):
        return []
    return [row for row in (_clean(item) for item in found if isinstance(item, dict)) if row]


@cache
def _load_vectors(path: str | None = None) -> dict:
    target = Path(path or VECTORS)
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if str(body.get("model") or "") != MODEL or int(body.get("dim") or 0) != DIM:
        return {}
    vectors = body.get("vectors")
    return vectors if isinstance(vectors, dict) else {}


def _vectors(ids: list[str]) -> dict[str, list[float]]:
    found = _load_vectors()
    if not found:
        return {}
    usable = {
        ident: found[ident]
        for ident in ids
        if isinstance(found.get(ident), list) and len(found[ident]) == DIM
    }
    return usable if len(usable) >= COVERAGE * len(ids) else {}


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
        _warn_once("model", "fakta: sentence-transformers tidak terpasang, pakai kata kunci")
        return None
    try:
        return SentenceTransformer(MODEL, device="cpu")
    except Exception:
        _warn_once("load", "fakta: gagal memuat %s, pakai kata kunci", MODEL)
        return None


def _given(value):
    """Encoder eksplisit: apa pun yang punya `.encode`, bukan hanya objek callable."""
    return value if hasattr(value, "encode") else None


def _cosines(facts: list[dict], text: str, encoder) -> dict[str, float]:
    body = str(text or "").strip()
    if not body:
        return {}
    vectors = _vectors([row["id"] for row in facts])
    if not vectors:
        return {}
    model = _given(encoder) or _default_encoder()
    if model is None:
        return {}
    try:
        query = model.encode([body], convert_to_numpy=True)[0]
    except Exception:
        _warn_once("encode", "fakta: gagal encode, pakai kata kunci")
        return {}
    return {ident: _cosine(query, vector) for ident, vector in vectors.items()}


def _hits(keys: list[str], body: str, tokens: set[str]) -> int:
    return sum(1 for key in keys if key in tokens or key in body)


def _score(hits: int, similarity: float | None) -> float | None:
    """Satu kata kunci khas sudah cukup; cosine saja butuh kedekatan tinggi."""
    if similarity is None:
        return KEY_WEIGHT * min(1.0, hits / 2) if hits else None
    if not hits and similarity < MIN_SIMILARITY:
        return None
    return KEY_WEIGHT * similarity + HIT_WEIGHT * min(1.0, hits / 2)


def _render(row: dict) -> str:
    return row["text"][:LINE_MAX]


def select_facts(
    count: int,
    *,
    messages: list[str],
    encoder=_AUTO,
) -> list[str]:
    """Pilih `count` fakta paling relevan untuk turn ini (deterministik, tanpa undian)."""
    limit = max(0, min(CAP, int(count)))
    if not limit:
        return []
    facts = load_facts()
    if not facts:
        return []
    body = " ".join(str(item or "") for item in messages[-8:]).lower()
    tokens = set(WORD.findall(body))
    vectors = _vectors([row["id"] for row in facts]) if encoder is not None else {}
    cosines = _cosines(facts, body, encoder) if vectors else {}
    ranked: list[tuple[float, dict]] = []
    for row in facts:
        hits = _hits(row["keys"], body, tokens)
        score = _score(hits, cosines.get(row["id"]))
        if score is None:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [_render(row) for _, row in ranked[:limit]]
