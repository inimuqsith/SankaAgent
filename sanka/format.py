"""Rapikan teks model agar layak dibaca di WhatsApp."""

from __future__ import annotations

import random
import re

BLOCK = re.compile(r"<(reasoning|thought|tool_call)>.*?</\1>", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"(```.*?```)", re.DOTALL)
HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
BOLD = re.compile(r"\*\*(.+?)\*\*")
BULLET = re.compile(r"^[-*]\s+", re.MULTILINE)
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _clean_prose(chunk: str) -> str:
    chunk = HEADING.sub(r"*\1*", chunk)
    chunk = BOLD.sub(r"*\1*", chunk)
    chunk = BULLET.sub("• ", chunk)
    return LINK.sub(r"\1 (\2)", chunk)


def _chunks(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest.strip():
        parts.append(rest.strip())
    return [part for part in parts if part]


def format_reply(text: str, limit: int = 3500) -> list[str]:
    text = BLOCK.sub("", text or "")
    pieces = FENCE.split(text)
    cleaned = "".join(piece if piece.startswith("```") else _clean_prose(piece) for piece in pieces)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return _chunks(cleaned, limit) if cleaned else []


BUBBLE = re.compile(r"\n\s*\n")  # baris kosong = bubble baru; pilihan model, bukan heuristik


def human_chunks(text: str, *, max_parts: int = 3) -> list[str]:
    """Balasan -> bubble apa adanya: satu paragraf = satu bubble.

    Isi tidak pernah dipotong atau diberi elipsis; kalau paragraf melebihi `max_parts`, sisa
    paragraf digabung ke bubble terakhir supaya tidak ada kalimat yang hilang.
    """
    body = str(text or "").strip()
    if not body:
        return []
    parts = [part.strip() for part in BUBBLE.split(body) if part.strip()]
    cap = max(1, int(max_parts))
    if len(parts) > cap:
        parts = [*parts[: cap - 1], "\n\n".join(parts[cap - 1 :])]
    return parts


def humanize_reply(text: str, *, max_parts: int) -> list[str]:
    """Rapikan format WhatsApp, lalu pakai baris kosong dari model sebagai batas bubble."""
    blocks = format_reply(text, limit=10000)
    if not blocks:
        return []
    return human_chunks("\n\n".join(blocks), max_parts=max_parts)


def typing_delay(text: str, config, rng=random) -> float:
    """Jeda mengetik wajar (detik) berdasarkan panjang bubble; 0 bila delay dimatikan."""
    if float(config.min_delay) == 0 and float(config.max_delay) == 0:
        return 0.0
    seconds = len(str(text or "")) / max(1.0, float(config.cpms))
    bounded = max(float(config.min_delay), min(float(config.max_delay), seconds))
    return bounded + rng.uniform(0, 0.4)
