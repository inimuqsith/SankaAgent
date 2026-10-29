"""Command /cari: cari pesan lama di arsip chat ini tanpa LLM."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext

MAX_RESULTS = 10
TEXT_LIMIT = 180


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= TEXT_LIMIT else flat[: TEXT_LIMIT - 1] + "…"


def _line(row, lang: str) -> str:
    missing = "attachment" if row.media is not None else "empty"
    text = _clip(row.text) or t(f"command.cari.{missing}", lang)
    who = row.sender_name or row.sender_id or "?"
    stamp = row.when.strftime("%Y-%m-%d %H:%M") if row.when else "?"
    return t("command.cari.line", lang, stamp=stamp, who=who, text=text)


async def cari(ctx: CommandContext) -> str:
    lang = ctx.lang
    query = ctx.args.strip()
    if not query:
        return t("command.cari.usage", lang)
    store = ctx.agent.history
    if store is None:
        return t("command.cari.unavailable", lang)
    try:
        rows = store.search(ctx.chat_id, query, limit=MAX_RESULTS)
    except Exception:
        return t("command.cari.failed", lang)
    if not rows:
        return t("command.cari.none", lang, query=query)
    lines = [t("command.cari.head", lang, query=query)]
    lines.extend(_line(row, lang) for row in rows)
    return "\n".join(lines)


COMMAND = Handler(
    name="cari",
    description="🔎 Cari pesan lama di arsip chat ini (tanpa LLM).",
    fn=cari,
    aliases=("search",),
    usage="<kata>",
)
