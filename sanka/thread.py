"""Situasi percakapan terbuka: aturan murni yang dipakai runtime dan harness ablasi."""

from __future__ import annotations

from .chat import followup_utterance, keyword_set

KEYWORD_LIMIT = 10


def open_thread(
    person: str,
    name: str,
    text: str,
    reply: str,
    *,
    seconds: float,
    now: float,
) -> dict:
    """State situasi baru setelah balasan terkirim; `{}` bila fitur mati atau tanpa pengirim."""
    if float(seconds) <= 0 or not person:
        return {}
    keys = keyword_set(text) | keyword_set(reply)
    return {
        "person": person,
        "name": name,
        "until": float(now) + float(seconds),
        "keywords": set(sorted(keys)[:KEYWORD_LIMIT]),
    }


def continues(state: dict | None, *, person: str, text: str, now: float) -> bool:
    """Pesan ini melanjutkan situasi yang sama, bukan konteks baru yang butuh panggilan."""
    if not state:
        return False
    if float(now) > float(state["until"]) or person != state["person"]:
        return False
    body = str(text or "").strip()
    if not body:
        return False
    if keyword_set(body) & state["keywords"]:
        return True
    return followup_utterance(body)
