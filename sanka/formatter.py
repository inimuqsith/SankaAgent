"""Reply formatting: the only exit path to WhatsApp text.

Removes internal reasoning/tool blocks and enforces message length. It contains
no LLM logic and is fully testable without any external service.
"""

from __future__ import annotations

import re
from enum import Enum

_INTERNAL_BLOCK = re.compile(
    r"<(reasoning|thought|tool_call)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_INTERNAL_SELF_CLOSING = re.compile(
    r"<(reasoning|thought|tool_call)\b[^>]*?/>",
    re.IGNORECASE,
)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_ELLIPSIS = "\u2026"


class ResponseKind(str, Enum):
    TEXT = "text"
    RECAP = "recap"


class Formatter:
    """Turn raw model output into one or more WhatsApp-ready messages."""

    def __init__(self, max_chars: int) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars

    def format(
        self, text: str, kind: ResponseKind = ResponseKind.TEXT
    ) -> list[str]:
        """Return the messages to send, in order.

        ``TEXT`` is truncated to ``max_chars``. ``RECAP`` is split at paragraph
        boundaries so long reports keep all their content.
        """

        kind = ResponseKind(kind)
        cleaned = strip_internal_blocks(text).strip()
        if not cleaned:
            return []

        if kind is ResponseKind.RECAP:
            return _split_messages(cleaned, self.max_chars)
        return [_truncate(cleaned, self.max_chars)]


def strip_internal_blocks(text: str) -> str:
    """Remove reasoning, thought, and tool-call blocks from ``text``."""

    stripped = _INTERNAL_BLOCK.sub("", text)
    return _INTERNAL_SELF_CLOSING.sub("", stripped)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit == 1:
        return _ELLIPSIS
    return text[: limit - 1].rstrip() + _ELLIPSIS


def _split_messages(text: str, limit: int) -> list[str]:
    paragraphs = [
        paragraph.strip()
        for paragraph in _PARAGRAPH_BREAK.split(text)
        if paragraph.strip()
    ]

    messages: list[str] = []
    current = ""
    for paragraph in paragraphs:
        for piece in _wrap_paragraph(paragraph, limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    messages.append(current)
                current = piece
    if current:
        messages.append(current)
    return messages


def _wrap_paragraph(paragraph: str, limit: int) -> list[str]:
    """Split one paragraph into pieces no longer than ``limit``."""

    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > limit:
        cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        piece = remaining[:cut].rstrip()
        if piece:
            pieces.append(piece)
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces
