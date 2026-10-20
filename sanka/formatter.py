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

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_HORIZONTAL_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_BOLD = re.compile(r"(?<!\*)\*\*(?!\*)(?=\S)(.+?)(?<=\S)\*\*(?!\*)")
_STRIKETHROUGH = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_INLINE_CODE = re.compile(r"`[^`\n]+`")


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
        cleaned = to_whatsapp(strip_internal_blocks(text)).strip()
        if not cleaned:
            return []

        if kind is ResponseKind.RECAP:
            return _split_messages(cleaned, self.max_chars)
        return [_truncate(cleaned, self.max_chars)]


def strip_internal_blocks(text: str) -> str:
    """Remove reasoning, thought, and tool-call blocks from ``text``."""

    stripped = _INTERNAL_BLOCK.sub("", text)
    return _INTERNAL_SELF_CLOSING.sub("", stripped)


def to_whatsapp(text: str) -> str:
    """Convert common model markdown into WhatsApp-native formatting.

    WhatsApp understands ``*bold*``, ``_italic_``, ``~strike~``, and
    ```monospace``` but not markdown headings, ``**bold**``, or links. The
    conversion is line based and idempotent: text already using WhatsApp
    syntax is left unchanged. Fenced and inline code are preserved verbatim.
    """

    lines: list[str] = []
    in_fence = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(raw):
            in_fence = not in_fence
            lines.append(raw.rstrip())
            continue
        if in_fence:
            lines.append(raw.rstrip())
            continue
        lines.append(_convert_line(raw))
    return "\n".join(lines).strip()


def _convert_line(line: str) -> str:
    stripped = line.rstrip()
    if _HORIZONTAL_RULE.match(stripped):
        return ""

    heading = _HEADING.match(stripped)
    if heading is not None:
        content = _convert_inline(heading.group(1).strip())
        if len(content) > 1 and content.startswith("*") and content.endswith("*"):
            return content
        return f"*{content}*"

    bullet = _BULLET.match(stripped)
    if bullet is not None:
        return f"{bullet.group(1)}• {_convert_inline(bullet.group(2))}"

    return _convert_inline(stripped)


def _convert_inline(text: str) -> str:
    """Convert inline markdown outside inline code spans."""

    pieces: list[str] = []
    last = 0
    for match in _INLINE_CODE.finditer(text):
        pieces.append(_convert_plain(text[last : match.start()]))
        pieces.append(match.group(0))
        last = match.end()
    pieces.append(_convert_plain(text[last:]))
    return "".join(pieces)


def _convert_plain(text: str) -> str:
    converted = _BOLD.sub(r"*\1*", text)
    converted = _STRIKETHROUGH.sub(r"~\1~", converted)
    return _LINK.sub(_replace_link, converted)


def _replace_link(match: re.Match[str]) -> str:
    label, url = match.group(1), match.group(2)
    if label == url:
        return url
    return f"{label} ({url})"


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
