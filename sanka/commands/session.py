"""Session commands: /new and /stop."""

from __future__ import annotations

from typing import Protocol

from sanka.commands.registry import Command, CommandContext, CommandRegistry
from sanka.policy import ALL_ROLES, Role
from sanka.queue import StopOutcome


class SessionControl(Protocol):
    """Narrow surface the command layer needs from the agent runtime."""

    def reset(self, chat_id: str) -> None: ...

    def stop(
        self, chat_id: str, requester_id: str, is_owner: bool
    ) -> StopOutcome: ...


def register_session_commands(
    registry: CommandRegistry, control: SessionControl
) -> None:
    registry.register(
        Command(
            "new",
            "🧹 Mulai percakapan baru di chat ini",
            ALL_ROLES,
            lambda context, args: _new(context, control),
        )
    )
    registry.register(
        Command(
            "stop",
            "⏹️ Hentikan pekerjaan yang sedang berjalan",
            ALL_ROLES,
            lambda context, args: _stop(context, control),
        )
    )


def _new(context: CommandContext, control: SessionControl) -> str:
    control.reset(context.chat_id)
    return "🧹 Siap! Konteks percakapan di chat ini sudah direset."


def _stop(context: CommandContext, control: SessionControl) -> str:
    outcome = control.stop(
        context.chat_id,
        requester_id=context.sender_id,
        is_owner=context.role is Role.OWNER,
    )
    if outcome is StopOutcome.STOPPED:
        return "⏹️ Oke, pekerjaan di chat ini sudah dihentikan."
    if outcome is StopOutcome.FORBIDDEN:
        return "🔒 Kamu tidak bisa menghentikan pekerjaan ini."
    return "🤔 Tidak ada pekerjaan yang sedang berjalan di chat ini."
