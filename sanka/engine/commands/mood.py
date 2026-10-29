"""Command /mood: keadaan emosi Sanka saat ini."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


def _level(value: float) -> str:
    if value < 0.3:
        return "rendah"
    if value < 0.65:
        return "sedang"
    return "tinggi"


async def mood(ctx: CommandContext) -> str:
    affect = ctx.agent.affect
    if affect is None:
        return "state emosi belum aktif."
    person_id = ctx.inbound.person_id if ctx.inbound is not None else ctx.sender
    try:
        data = affect.status(ctx.chat_id, person_id)
    except Exception:
        return "aku belum bisa membaca mood-ku sekarang. coba lagi bentar ya."
    pleasure = float(data["pleasure"])
    feel = "senang" if pleasure > 0.2 else "kesal" if pleasure < -0.2 else "tenang"
    lines = [
        "💭 *Mood Sanka*",
        f"• Lagi: {data['summary']}",
        (
            f"• Perasaan: {feel} (P {pleasure:.1f}, A {float(data['arousal']):.1f}, "
            f"D {float(data['dominance']):.1f})"
        ),
        f"• Pulse: {data['pulse']}",
        f"• Trust: {data['trust_tier']}",
        f"• Energi: {_level(float(data['energy']))}",
        f"• Gabut: {_level(float(data['boredom']))}, kesepian: {_level(float(data['loneliness']))}",
    ]
    name = str(data.get("person_name") or "")
    relation = str(data.get("relation") or "")
    if name and relation:
        annoyance = float(data.get("annoyance") or 0.0)
        extra = f", kesal {annoyance:.1f}" if annoyance > 0.05 else ""
        lines.append(f"• Ke {name}: {relation}{extra}")
    return "\n".join(lines)


COMMAND = Handler(
    name="mood",
    description="💭 Mood, energi, dan perasaan Sanka saat ini.",
    fn=mood,
)
