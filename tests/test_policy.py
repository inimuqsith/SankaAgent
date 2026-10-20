"""Policy tests: roles, tool matrix, command permissions, rate limits."""

from __future__ import annotations

import pytest

from sanka.config import AccessConfig
from sanka.policy import (
    COMMITTEE_ROLES,
    RateLimiter,
    Role,
    can_run_command,
    can_use_tool,
    resolve_role,
)


def access() -> AccessConfig:
    return AccessConfig(
        owner_numbers=("628111111111",),
        committee_numbers=("628222222222",),
        allowed_group_ids=(),
    )


@pytest.mark.parametrize(
    ("sender_id", "expected"),
    [
        ("628111111111", Role.OWNER),
        ("628111111111@s.whatsapp.net", Role.OWNER),
        ("628222222222", Role.COMMITTEE),
        ("628333333333", Role.COMMITTEE),
    ],
)
def test_resolve_role_from_number(sender_id: str, expected: Role) -> None:
    assert resolve_role(sender_id, access()) is expected


def test_owner_wins_when_listed_twice() -> None:
    both = AccessConfig(
        owner_numbers=("628111111111",),
        committee_numbers=("628111111111",),
        allowed_group_ids=(),
    )
    assert resolve_role("628111111111", both) is Role.OWNER


@pytest.mark.parametrize(
    ("role", "tool", "expected"),
    [
        (Role.COMMITTEE, "web_search", True),
        (Role.COMMITTEE, "sheet_read", True),
        (Role.COMMITTEE, "doc_read", True),
        (Role.COMMITTEE, "recap", True),
        (Role.COMMITTEE, "sheet_write", False),
        (Role.COMMITTEE, "code_run", False),
        (Role.OWNER, "sheet_write", True),
        (Role.OWNER, "code_run", True),
        (Role.OWNER, "file_write", True),
        (Role.OWNER, "proposal_send", True),
        (Role.OWNER, "unknown_tool", False),
    ],
)
def test_tool_permissions(role: Role, tool: str, expected: bool) -> None:
    assert can_use_tool(role, tool, shell_enabled=False) is expected


def test_shell_requires_owner_and_toggle() -> None:
    assert can_use_tool(Role.OWNER, "shell", shell_enabled=True) is True
    assert can_use_tool(Role.OWNER, "shell", shell_enabled=False) is False
    assert can_use_tool(Role.COMMITTEE, "shell", shell_enabled=True) is False


def test_command_permissions() -> None:
    assert can_run_command(COMMITTEE_ROLES, Role.OWNER) is True
    assert can_run_command(COMMITTEE_ROLES, Role.COMMITTEE) is True


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_rate_limit_per_minute_and_day_then_reset() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=2, per_day=3, clock=clock)

    assert limiter.allow("628111111111") is True
    assert limiter.allow("628111111111") is True
    assert limiter.allow("628111111111") is False  # minute limit

    clock.advance(61)
    assert limiter.allow("628111111111") is True  # new minute, day total is 3
    assert limiter.allow("628111111111") is False  # day limit

    clock.advance(86_401)
    assert limiter.allow("628111111111") is True  # both windows rolled over

    limiter.reset("628111111111")
    assert limiter.allow("628111111111") is True


def test_rate_limit_is_per_sender() -> None:
    limiter = RateLimiter(per_minute=1, per_day=10, clock=FakeClock())

    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False


def test_rate_limit_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        RateLimiter(per_minute=0, per_day=1)
