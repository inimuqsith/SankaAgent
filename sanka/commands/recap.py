"""Deterministic ``/rekap`` command: no model, no guessing.

Handler output is built by code from :mod:`sanka.tools.recap`; the command is
registered with ``ResponseKind.RECAP`` so the formatter splits long reports at
paragraph boundaries instead of truncating them.
"""

from __future__ import annotations

from collections.abc import Sequence

from sanka.commands.registry import Command, CommandContext, CommandRegistry
from sanka.formatter import ResponseKind
from sanka.policy import COMMITTEE_ROLES
from sanka.tools.recap import RecapService, render_recap
from sanka.tools.registry import ToolError

__all__ = ["register_recap_command"]

_ERROR_REPLIES = {
    "forbidden": "Rekap ini hanya untuk panitia.",
    "not_found": "Rekap tidak tersedia.",
    "not_configured": "Akses sheet belum diatur.",
    "too_large": "Data sheet terlalu besar untuk direkap.",
    "timeout": "Sheet sedang tidak merespons. Coba lagi nanti.",
    "upstream_error": "Sheet sedang tidak bisa dibaca. Coba lagi nanti.",
    "invalid_arguments": "Definisi rekap tidak cocok dengan isi sheet.",
}
_GENERIC_ERROR = "Rekap gagal dihitung. Coba lagi nanti."


def register_recap_command(
    registry: CommandRegistry, service: RecapService
) -> None:
    """Register ``/rekap`` on ``registry`` for committee roles only."""

    registry.register(
        Command(
            name="rekap",
            description="Rekap data dari sheet terdaftar",
            roles=COMMITTEE_ROLES,
            handler=_make_handler(service),
            kind=ResponseKind.RECAP,
        )
    )


def _make_handler(service: RecapService):
    def handler(context: CommandContext, arguments: str) -> str:
        names = service.names()
        parts = arguments.split()
        name = parts[0].lower() if parts else ""
        if not name or name not in names:
            # Identical reply for missing input and unknown names: no aliases,
            # no configured sheet hints.
            return _available_reply(names)
        try:
            result = service.run(name, context.role)
        except ToolError as exc:
            return _ERROR_REPLIES.get(exc.code, _GENERIC_ERROR)
        return render_recap(result)

    return handler


def _available_reply(names: Sequence[str]) -> str:
    if not names:
        return "Belum ada rekap yang terdaftar."
    lines = ["Rekap tersedia:"]
    lines.extend(f"- {name}" for name in names)
    lines.append("Ketik /rekap <nama> untuk menghitung.")
    return "\n".join(lines)
