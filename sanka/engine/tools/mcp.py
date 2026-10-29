"""Keluarga tool MCP: tool nyata didaftarkan sanka/mcp.py saat Hub.start()."""

from __future__ import annotations

from ..registry import Handler

TOOL = Handler(
    name="mcp",
    family="mcp",
    description="Tools exposed by MCP servers (one family, names mcp__<server>__<tool>).",
    pattern="mcp__*",
    hidden=True,
)
