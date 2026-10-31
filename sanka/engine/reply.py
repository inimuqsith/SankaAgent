"""Pemecahan teks balasan; dialek transport diurus gateway lewat `render`."""

from __future__ import annotations

import random
import re

BLOCK = re.compile(r"<(reasoning|thought|tool_call)>.*?</\1>", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"(```.*?```)", re.DOTALL)
FENCE_START = "```"
BOLD = re.compile(r"\*\*(.+?)\*\*")
BULLET = re.compile(r"^[-*]\s+", re.MULTILINE)
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BUBBLE = re.compile(r"(?m)^\s*-{3,}\s*$")  # baris "---" saja = bubble baru; pilihan model
INLINE_BUBBLE = re.compile(r"[ \t]+-{3,}[ \t]+")  # "satu --- dua" di tengah baris
BLANK = re.compile(r"\n\s*\n")
LIST_LINE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)]|\d\ufe0f?\u20e3)\s")
CASUAL_BUBBLE_MAX = 300


def strip_blocks(text: str) -> str:
    return BLOCK.sub("", text or "")


def _heading(line: str) -> str:
    """'## Judul' -> '*Judul*'; baris lain dikembalikan apa adanya."""
    marks = len(line) - len(line.lstrip("#"))
    rest = line[marks:]
    if not 1 <= marks <= 6 or rest[:1] not in (" ", "\t") or len(rest) < 2:
        return line
    body = rest.lstrip(" \t")
    return f"*{body}*"


def clean_prose(chunk: str) -> str:
    """Markdown -> dialek chat (dipakai gateway WhatsApp; CLI memakai identitas)."""
    chunk = "\n".join(_heading(line) for line in chunk.split("\n"))
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


def split_text(text: str, limit: int = 10000) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", strip_blocks(text).strip())
    return _chunks(cleaned, limit) if cleaned else []


def human_chunks(text: str, *, max_parts: int = 3) -> list[str]:
    """Teks -> bubble apa adanya; model menandai batas dengan baris `---`."""
    body = strip_blocks(str(text or "")).strip()
    if not body:
        return []
    parts: list[str] = []
    current = ""
    for piece in FENCE.split(body):
        if not piece.strip():
            continue
        if piece.lstrip().startswith(FENCE_START):
            current = f"{current}\n{piece}".strip() if current else piece.strip()
            continue
        piece = INLINE_BUBBLE.sub("\n---\n", piece)
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
    if len(parts) == 1 and FENCE_START not in body:
        paragraphs = [part.strip() for part in BLANK.split(parts[0]) if part.strip()]
        casual = (
            len(paragraphs) > 1
            and not LIST_LINE.search(parts[0])
            and all(len(part) <= CASUAL_BUBBLE_MAX for part in paragraphs)
        )
        if casual:
            parts = paragraphs
    cap = max(1, int(max_parts))
    if len(parts) > cap:
        parts = [*parts[: cap - 1], "\n\n".join(parts[cap - 1 :])]
    return parts


def typing_delay(text: str, config, rng=random) -> float:
    """Jeda mengetik wajar (detik) berdasarkan panjang bubble; 0 bila delay dimatikan."""
    if float(config.min_delay) == 0 and float(config.max_delay) == 0:
        return 0.0
    seconds = len(str(text or "")) / max(1.0, float(config.cpms))
    bounded = max(float(config.min_delay), min(float(config.max_delay), seconds))
    return bounded + rng.uniform(0, 0.4)
