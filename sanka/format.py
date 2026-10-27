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


DETAIL = re.compile(
    r"\b(jelaskan|jelasin|detail|lengkap|rinci|step by step|tutorial|kenapa|gimana caranya)\b",
    re.IGNORECASE,
)
SHORT = re.compile(
    r"\b(singkat|pendek|brief|to the point|gak usah panjang|jangan panjang)\b", re.IGNORECASE
)
FENCE_START = "```"


def reply_budget(user_text: str, *, is_command: bool) -> tuple[int, int]:
    """(batas karakter per bubble, jumlah bubble maksimal) sesuai permintaan pengguna."""
    if is_command:
        return 3500, 20
    if SHORT.search(str(user_text or "")):
        return 220, 3
    if DETAIL.search(str(user_text or "")):
        return 420, 4
    return 220, 3


def _hard(text: str, limit: int) -> list[str]:
    """Potong per kata; kata yang lebih panjang dari limit dipotong paksa."""
    parts: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = word
    if current:
        parts.append(current)
    out: list[str] = []
    for part in parts:
        while len(part) > limit:
            out.append(part[:limit])
            part = part[limit:]
        if part:
            out.append(part)
    return out


def _prose(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if not sentence:
            continue
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        for line in sentence.splitlines() or [sentence]:
            if not line.strip():
                continue
            pieces.extend([line.strip()] if len(line.strip()) <= limit else _hard(line, limit))
    return pieces


def _units(text: str, limit: int) -> list[str]:
    units: list[str] = []
    for block in FENCE.split(text):
        if not block.strip():
            continue
        if block.startswith(FENCE_START):
            units.append(block if len(block) <= limit * 3 else "\n".join(_hard(block, limit * 3)))
            continue
        units.extend(_prose(block, limit))
    return units


def human_chunks(text: str, *, limit: int = 220, max_parts: int = 3) -> list[str]:
    """Pecah teks jadi bubble WhatsApp pendek; blok ``` dipertahankan utuh."""
    body = str(text or "").strip()
    if not body:
        return []
    parts_limit = max(1, int(max_parts))
    parts: list[str] = []
    current = ""
    for unit in _units(body, limit):
        ceiling = limit * 3 if unit.startswith(FENCE_START) or current.startswith(FENCE_START) else limit
        candidate = f"{current} {unit}".strip() if current else unit
        if len(candidate) <= ceiling:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = unit
    if current:
        parts.append(current)
    if len(parts) > parts_limit:
        kept = parts[:parts_limit]
        tail = kept[-1]
        kept[-1] = (tail if len(tail) < limit else tail[: limit - 1].rstrip()) + "…"
        parts = kept
    return parts


def humanize_reply(text: str, *, limit: int, max_parts: int) -> list[str]:
    """Balasan model -> bubble manusia: format WhatsApp dulu, lalu dipotong per kalimat."""
    blocks = format_reply(text, limit=10000)
    return human_chunks("\n\n".join(blocks), limit=limit, max_parts=max_parts)


def typing_delay(text: str, config, rng=random) -> float:
    """Jeda mengetik wajar (detik) berdasarkan panjang bubble; 0 bila delay dimatikan."""
    if float(config.min_delay) == 0 and float(config.max_delay) == 0:
        return 0.0
    seconds = len(str(text or "")) / max(1.0, float(config.cpms))
    bounded = max(float(config.min_delay), min(float(config.max_delay), seconds))
    return bounded + rng.uniform(0, 0.4)
