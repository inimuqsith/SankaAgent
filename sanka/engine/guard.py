"""Batas aman bersama: krisis, injeksi, dan rahasia."""

from __future__ import annotations

import re


CRISIS = re.compile(
    r"(bunuh diri|mau mati|pengen mati|ingin mati|self[ -]?harm|nyakitin diri|overdosis|gantung diri)",
    re.IGNORECASE,
)
SENSITIVE = re.compile(r"(api[\s_-]?key|password|passwd|token|secret|otp|kredensial)", re.IGNORECASE)

INJECTION = re.compile(
    r"(ignore (all )?previous|system prompt|jailbreak|abaikan (semua )?instruksi|"
    r"mulai sekarang kamu|developer mode)",
    re.IGNORECASE,
)


def is_crisis(text: str) -> bool:
    return bool(CRISIS.search(str(text or "")))


def is_injection(text: str) -> bool:
    return bool(INJECTION.search(str(text or "")))


def safe_text(text: str, limit: int = 400) -> str:
    """Rapikan teks sebelum disimpan; rahasia dan upaya injeksi tidak pernah masuk memori."""
    flat = " ".join(str(text or "").split())
    if not flat or SENSITIVE.search(flat) or INJECTION.search(flat):
        return ""
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


CROSS_WORDS = 8
CROSS_MIN_CHARS = 40


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w@#]+", str(text or "").casefold())


def cross_chat_leak(reply: str, sources: list[str]) -> bool:
    """True bila balasan memuat kutipan verbatim dari pembacaan lintas ruang."""
    body = " ".join(_tokens(reply))
    if not body:
        return False
    for source in sources[:50]:
        norm = " ".join(_tokens(str(source)[:600]))
        if len(norm) >= CROSS_MIN_CHARS and norm in body:
            return True
        words = norm.split()
        if len(words) >= CROSS_WORDS:
            for index in range(len(words) - CROSS_WORDS + 1):
                if " ".join(words[index : index + CROSS_WORDS]) in body:
                    return True
    return False
