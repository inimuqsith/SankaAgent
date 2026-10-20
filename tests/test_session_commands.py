"""Tests for /new and /stop wiring."""

from __future__ import annotations

from sanka.commands.basic import build_registry
from sanka.commands.registry import CommandContext
from sanka.config import SankaConfig
from sanka.policy import Role
from sanka.queue import StopOutcome


class FakeControl:
    def __init__(self, outcome: StopOutcome = StopOutcome.IDLE) -> None:
        self.outcome = outcome
        self.reset_calls: list[str] = []
        self.stop_calls: list[tuple[str, str, bool]] = []

    def reset(self, chat_id: str) -> None:
        self.reset_calls.append(chat_id)

    def stop(
        self, chat_id: str, requester_id: str, is_owner: bool
    ) -> StopOutcome:
        self.stop_calls.append((chat_id, requester_id, is_owner))
        return self.outcome


def context(
    role: Role, config: SankaConfig, sender_id: str = "628000000000"
) -> CommandContext:
    return CommandContext(
        role=role,
        config=config,
        sender_id=sender_id,
        chat_id="chat-1",
    )


def test_new_resets_the_chat(config: SankaConfig) -> None:
    control = FakeControl()
    registry = build_registry(control)

    reply = registry.dispatch("/new", context(Role.PARTICIPANT, config))

    assert "direset" in reply
    assert control.reset_calls == ["chat-1"]


def test_stop_reports_stopped_idle_and_forbidden(config: SankaConfig) -> None:
    registry = build_registry(FakeControl(StopOutcome.STOPPED))
    assert "dihentikan" in registry.dispatch(
        "/stop", context(Role.PARTICIPANT, config)
    )

    registry = build_registry(FakeControl(StopOutcome.IDLE))
    assert "Tidak ada pekerjaan" in registry.dispatch(
        "/stop", context(Role.PARTICIPANT, config)
    )

    registry = build_registry(FakeControl(StopOutcome.FORBIDDEN))
    assert "tidak bisa" in registry.dispatch(
        "/stop", context(Role.PARTICIPANT, config)
    )


def test_stop_passes_requester_and_owner_flag(config: SankaConfig) -> None:
    control = FakeControl(StopOutcome.STOPPED)
    registry = build_registry(control)

    registry.dispatch(
        "/stop",
        context(Role.OWNER, config, sender_id="628111111111"),
    )

    assert control.stop_calls == [("chat-1", "628111111111", True)]


def test_help_lists_session_commands(config: SankaConfig) -> None:
    registry = build_registry(FakeControl())

    reply = registry.dispatch("/help", context(Role.PARTICIPANT, config))

    assert "/new" in reply
    assert "/stop" in reply
