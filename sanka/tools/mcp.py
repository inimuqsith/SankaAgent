"""Keluarga tool MCP: tool nyata didaftarkan sanka/mcp.py saat Hub.start()."""

from __future__ import annotations

from ..registry import Handler
from . import OWNER_MEMBER

TOOL = Handler(
    name="mcp",
    family="mcp",
    description="Tool dari server MCP stdio (satu keluarga, nama mcp__<server>__<tool>).",
    pattern="mcp__*",
    defaults=OWNER_MEMBER,
    hidden=True,
)
