"""Command /language: switch Sanka's reply language to any installed catalog code.

The language lives in the `soul` document and its config.yaml mirror; the catalog in
`sanka/lang/<code>.yaml` supplies every chat-visible string.
"""

from __future__ import annotations

import yaml

from ..i18n import available, normalize_language, t
from ..registry import Handler
from . import CommandContext


def _soul_body(text: str) -> dict:
    if not text.strip():
        return {}
    body = yaml.safe_load(text)
    return body if isinstance(body, dict) else {}


async def language_command(ctx: CommandContext) -> str:
    agent = ctx.agent
    lang = ctx.lang
    raw = ctx.args.strip()
    codes = ", ".join(available())
    if not raw:
        return t(
            "command.language.current", lang, language=agent.soul.language, available=codes
        )
    try:
        language = normalize_language(raw)
    except ValueError:
        return t("command.language.invalid", lang, value=raw, available=codes)
    if agent.documents is None:
        return t("command.language.no_documents", lang)
    body = _soul_body(agent.documents.get("soul") or "")
    body["language"] = language
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
    agent.documents.put("soul", text, actor=ctx.sender)
    agent.documents.export_to_files()
    await agent.reload()
    return t("command.language.set", language, language=language)


COMMAND = Handler(
    name="language",
    description="Switch Sanka's reply language (installed catalog code).",
    fn=language_command,
    aliases=("bahasa",),
    usage="[code]",
)
