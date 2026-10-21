"""Rapikan teks model agar layak dibaca di WhatsApp."""

from __future__ import annotations

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
