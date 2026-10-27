"""Command /calmdown: owner menenangkan Sanka setelah ngambek atau nangis."""

from __future__ import annotations

from ..registry import Handler
from . import CommandContext


async def calmdown(ctx: CommandContext) -> str:
    affect = getattr(ctx.app, "affect", None)
    if affect is None:
        return "state emosi belum aktif."
    target = ctx.args.strip()
    try:
        was_sulking = affect.calm(ctx.chat_id, target)
    except Exception:
        return "aku belum bisa menenangkan diri sekarang. coba lagi bentar ya."
    if not was_sulking:
        return "aku dari tadi tenang kok 🙃"
    if not target:
        return "okeee... aku udah agak tenang 😌 makasih ya."
    return f"yaudah, aku maafin {target} kali ini. tapi jangan diulang ya 🙃"


COMMAND = Handler(
    name="calmdown",
    description="🧊 Tenangkan Sanka setelah ngambek/nangis (owner).",
    fn=calmdown,
    aliases=("tenang",),
    usage="/calmdown [nama atau nomor]",
    defaults={"owner": True},
)
