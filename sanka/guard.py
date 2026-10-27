"""Batas aman bersama: krisis, injeksi, rahasia, sentimen grup, command operasional."""

from __future__ import annotations

import re

from .memory import SENSITIVE

CRISIS = re.compile(
    r"(bunuh diri|mau mati|pengen mati|ingin mati|self[ -]?harm|nyakitin diri|overdosis|gantung diri)",
    re.IGNORECASE,
)
INJECTION = re.compile(
    r"(ignore (all )?previous|system prompt|jailbreak|abaikan (semua )?instruksi|"
    r"mulai sekarang kamu|developer mode)",
    re.IGNORECASE,
)
# Command yang tetap jalan walau Sanka sedang ngambek/nangis: operasional, bukan obrolan.
EXEMPT_COMMANDS = frozenset({"help", "status", "whoami", "reset", "stop", "ping", "calmdown"})
TENSION = re.compile(
    r"\b(anjir|asu|bangsat|goblok|tolol|jancok|sampah|mampus|diam|gausah)\b", re.IGNORECASE
)
FUN = re.compile(r"\b(wkwk|awok|hehe|haha)\b|🗿|😭|😹", re.IGNORECASE)
COMMAND_TEXT = re.compile(r"^/([a-z0-9_]{1,63})", re.IGNORECASE)


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


def command_name(text: str) -> str:
    """'/help foo' -> 'help'; bukan command -> string kosong."""
    match = COMMAND_TEXT.match(str(text or "").strip())
    return match.group(1).lower() if match else ""
