"""Rapikan teks model agar layak dibaca di WhatsApp."""

from __future__ import annotations

import random
import re

BLOCK = re.compile(r"<(reasoning|thought|tool_call)>.*?</\1>", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"(```.*?```)", re.DOTALL)
FENCE_START = "```"
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


BUBBLE = re.compile(r"(?m)^\s*-{3,}\s*$")  # baris "---" saja = bubble baru; pilihan model


def human_chunks(text: str, *, max_parts: int = 3) -> list[str]:
    """Balasan -> bubble apa adanya: model menandai batas dengan baris `---`.

    Baris kosong bukan pemisah, jadi penjelasan serius dan daftar bernomor tetap utuh. Isi tidak
    pernah dipotong atau diberi elipsis; kelebihan bubble digabung ke yang terakhir.
    """
    body = str(text or "").strip()
    if not body:
        return []
    parts: list[str] = []
    current = ""
    for piece in FENCE.split(body):  # `---` di dalam blok kode tidak boleh memecah pesan
        if not piece.strip():
            continue
        if piece.lstrip().startswith(FENCE_START):
            current = f"{current}\n{piece}".strip() if current else piece.strip()
            continue
        for index, part in enumerate(BUBBLE.split(piece)):
            chunk = part.strip()
            if not chunk:
                continue
            if index == 0:
                current = f"{current}\n{chunk}".strip() if current else chunk
                continue
            if current:
                parts.append(current)
            current = chunk
    if current:
        parts.append(current)
    cap = max(1, int(max_parts))
    if len(parts) > cap:
        parts = [*parts[: cap - 1], "\n\n".join(parts[cap - 1 :])]
    return parts


def humanize_reply(text: str, *, max_parts: int) -> list[str]:
    """Rapikan format WhatsApp, lalu pakai penanda `---` dari model sebagai batas bubble."""
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
