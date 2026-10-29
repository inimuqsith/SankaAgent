"""Command /language: switch Sanka's reply language between English and Indonesian.

The language lives in the `soul` document and its config.yaml mirror; nothing else changes.
"""

from __future__ import annotations

import yaml

from ..registry import Handler
from ..soul import normalize_language
from . import CommandContext


def _soul_body(text: str) -> dict:
    if not text.strip():
        return {}
    body = yaml.safe_load(text)
    return body if isinstance(body, dict) else {}


async def language_command(ctx: CommandContext) -> str:
    agent = ctx.agent
    raw = ctx.args.strip()
    if not raw:
        return f"🗣️ Bahasa balasan: {agent.soul.language}."
    try:
        language = normalize_language(raw)
    except ValueError:
        return "error: bahasa harus 'en' atau 'id'."
    if agent.documents is None:
        return "error: dokumen config tidak tersedia"
    body = _soul_body(agent.documents.get("soul") or "")
    body["language"] = language
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
    agent.documents.put("soul", text, actor=ctx.sender)
    agent.documents.export_to_files()
    await agent.reload()
    return f"🗣️ Bahasa balasan diatur ke {language}."


COMMAND = Handler(
    name="language",
    description="Switch Sanka's reply language between English and Indonesian (en/id).",
    fn=language_command,
    aliases=("bahasa",),
    usage="[en|id]",
)
