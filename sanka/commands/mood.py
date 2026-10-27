"""Command /mood: keadaan mood Sanka saat ini."""

from __future__ import annotations

from ..chat import digits
from ..registry import Handler
from . import CommandContext


def _level(value: float) -> str:
    if value < 0.3:
        return "rendah"
    if value < 0.65:
        return "sedang"
    return "tinggi"


def _person_id(app, sender: str) -> str:
    store = getattr(getattr(app, "sessions", None), "store", None)
    if store is not None and sender:
        try:
            found = store.resolve_person(sender)
        except Exception:  # noqa: BLE001
            found = ""
        if found:
            return found
    return digits(sender)


async def mood(ctx: CommandContext) -> str:
    affect = getattr(ctx.app, "affect", None)
    if affect is None:
        return "state emosi belum aktif."
    person_id = _person_id(ctx.app, ctx.sender)
    try:
        data = affect.modes(ctx.chat_id, person_id)
    except Exception:  # noqa: BLE001
        return "aku belum bisa membaca mood-ku sekarang. coba lagi bentar ya."
    valence = float(data["valence"])
    feel = "senang" if valence > 0.2 else "kesal" if valence < -0.2 else "tenang"
    lines = [
        "💭 *Mood Sanka*",
        f"• Lagi: {data['label']}",
        f"• Perasaan: {feel}",
        f"• Energi: {_level(float(data['energy']))}",
        f"• Gabut: {_level(float(data['boredom']))}, kesepian: {_level(float(data['loneliness']))}",
        (
            "• Status: lagi ngambek/nangis"
            if int(data["level"]) or data["crying"]
            else "• Status: nggak ngambek"
        ),
    ]
    name = str(data.get("person_name") or "")
    relation = str(data.get("relation") or "")
    if name and relation:
        lines.append(f"• Ke {name}: {relation}")
    return "\n".join(lines)


COMMAND = Handler(
    name="mood",
    description="💭 Mood, energi, dan perasaan Sanka saat ini.",
    fn=mood,
    defaults={"owner": True, "member": True},
)
