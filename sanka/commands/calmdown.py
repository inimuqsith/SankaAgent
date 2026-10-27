"""Command /calmdown: owner menenangkan Sanka setelah kesal atau terluka."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def calmdown(ctx: CommandContext) -> str:
    affect = getattr(ctx.app, "affect", None)
    if affect is None:
        return "state emosi belum aktif."
    target = ctx.args.strip()
    try:
        was_upset = affect.calm(ctx.chat_id, target)
    except Exception:
        return "aku belum bisa menenangkan diri sekarang. coba lagi bentar ya."
    if not was_upset:
        return "aku dari tadi tenang kok 🙃"
    if not target:
        return "okeee... aku udah agak tenang 😌 makasih ya."
    return f"yaudah, aku maafin {target} kali ini. tapi jangan diulang ya 🙃"


COMMAND = Handler(
    name="calmdown",
    description="🧊 Tenangkan Sanka setelah kesal atau terluka (owner).",
    fn=calmdown,
    aliases=("tenang",),
    usage="/calmdown [nama atau nomor]",
    defaults={"owner": True},
)
