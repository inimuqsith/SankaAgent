"""Command /mood: keadaan emosi Sanka saat ini."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from . import CommandContext


def _level(value: float, lang: str) -> str:
    if value < 0.3:
        return t("command.mood.level_low", lang)
    if value < 0.65:
        return t("command.mood.level_mid", lang)
    return t("command.mood.level_high", lang)


def _feel(pleasure: float, lang: str) -> str:
    if pleasure > 0.2:
        return t("command.mood.feel_happy", lang)
    if pleasure < -0.2:
        return t("command.mood.feel_upset", lang)
    return t("command.mood.feel_calm", lang)


async def mood(ctx: CommandContext) -> str:
    lang = ctx.lang
    affect = ctx.agent.affect
    if affect is None:
        return t("command.mood.unavailable", lang)
    person_id = ctx.inbound.person_id if ctx.inbound is not None else ctx.sender
    try:
        data = affect.status(ctx.chat_id, person_id)
    except Exception:
        return t("command.mood.failed", lang)
    pleasure = float(data["pleasure"])
    lines = [
        t("command.mood.title", lang),
        t("command.mood.now", lang, summary=data["summary"]),
        t(
            "command.mood.feeling",
            lang,
            feel=_feel(pleasure, lang),
            pleasure=pleasure,
            arousal=float(data["arousal"]),
            dominance=float(data["dominance"]),
        ),
        t("command.mood.pulse", lang, pulse=data["pulse"]),
        t("command.mood.trust", lang, trust=data["trust_tier"]),
        t("command.mood.energy", lang, energy=_level(float(data["energy"]), lang)),
        t(
            "command.mood.drives",
            lang,
            boredom=_level(float(data["boredom"]), lang),
            loneliness=_level(float(data["loneliness"]), lang),
        ),
    ]
    name = str(data.get("person_name") or "")
    relation = str(data.get("relation") or "")
    if name and relation:
        annoyance = float(data.get("annoyance") or 0.0)
        extra = t("command.mood.extra_annoyed", lang, annoyance=annoyance) if annoyance > 0.05 else ""
        lines.append(t("command.mood.relation", lang, name=name, relation=relation, extra=extra))
    return "\n".join(lines)


COMMAND = Handler(
    name="mood",
    description="💭 Mood, energi, dan perasaan Sanka saat ini.",
    fn=mood,
)
