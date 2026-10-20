"""Command registry and basic command tests."""

from __future__ import annotations

from sanka.commands.basic import build_registry
from sanka.commands.registry import (
    FORBIDDEN_COMMAND_REPLY,
    UNKNOWN_COMMAND_REPLY,
    CommandContext,
)
from sanka.config import SankaConfig
from sanka.policy import Role


def context(role: Role, config: SankaConfig) -> CommandContext:
    return CommandContext(
        role=role,
        config=config,
        sender_id="628000000000",
        chat_id="chat",
    )


def test_help_filters_commands_by_role(config: SankaConfig) -> None:
    registry = build_registry()

    participant_help = registry.dispatch("/help", context(Role.PARTICIPANT, config))
    owner_help = registry.dispatch("/help", context(Role.OWNER, config))

    assert "/ping" in participant_help
    assert "/whoami" in participant_help
    assert "/status" not in participant_help
    assert "/status" in owner_help


def test_unknown_command_points_to_help(config: SankaConfig) -> None:
    registry = build_registry()

    assert (
        registry.dispatch("/nope", context(Role.PARTICIPANT, config))
        == UNKNOWN_COMMAND_REPLY
    )
    assert "/help" in UNKNOWN_COMMAND_REPLY


def test_forbidden_command_is_rejected(config: SankaConfig) -> None:
    registry = build_registry()

    reply = registry.dispatch("/status", context(Role.PARTICIPANT, config))

    assert reply == FORBIDDEN_COMMAND_REPLY
    assert config.llm.model not in reply


def test_status_is_deterministic_and_shows_model(config: SankaConfig) -> None:
    registry = build_registry()

    reply = registry.dispatch("/status", context(Role.COMMITTEE, config))

    assert "deterministic" in reply
    assert config.llm.model in reply


def test_ping_and_whoami(config: SankaConfig) -> None:
    registry = build_registry()
    ctx = context(Role.COMMITTEE, config)

    assert registry.dispatch("/ping", ctx) == "pong"
    assert registry.dispatch("/whoami", ctx) == "Peran kamu: panitia."


def test_duplicate_command_is_rejected() -> None:
    from sanka.commands.registry import Command, CommandRegistry
    from sanka.policy import ALL_ROLES

    registry = CommandRegistry()
    command = Command("ping", "Cek", ALL_ROLES, lambda ctx, args: "pong")
    registry.register(command)

    try:
        registry.register(command)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate registration should fail")
