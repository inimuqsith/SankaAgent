"""Roles, tool permissions, and in-memory rate limiting.

Policy is the single source of truth for permissions. Command handlers must not
check roles themselves, and tool execution must be rejected here as well as
hidden from the LLM.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Collection, Mapping
from enum import Enum
from types import MappingProxyType

from sanka.config import AccessConfig

MINUTE_SECONDS = 60.0
DAY_SECONDS = 86_400.0


class Role(str, Enum):
    OWNER = "owner"
    COMMITTEE = "panitia"


ALL_ROLES: frozenset[Role] = frozenset(Role)
COMMITTEE_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.COMMITTEE})

# Mirrors docs/SECURITY.md. Unknown tools are denied by default.
TOOL_PERMISSIONS: Mapping[str, frozenset[Role]] = MappingProxyType(
    {
        "web_search": ALL_ROLES,
        "doc_read": COMMITTEE_ROLES,
        "attachment_read": COMMITTEE_ROLES,
        "sheet_read": COMMITTEE_ROLES,
        "recap": COMMITTEE_ROLES,
        "sheet_write": frozenset({Role.OWNER}),
        "proposal_send": frozenset({Role.OWNER}),
        "browser": frozenset({Role.OWNER}),
        "code_run": frozenset({Role.OWNER}),
        "file_write": frozenset({Role.OWNER}),
        "shell": frozenset({Role.OWNER}),
    }
)


def resolve_role(sender_id: str, access: AccessConfig) -> Role:
    """Map a sender number/JID to a role using config, not display names.

    Owner numbers take precedence when an entry appears in both lists. Anyone
    not explicitly listed is treated as panitia (ADR-0020); there is no
    separate participant role.
    """

    if any(_same_number(sender_id, number) for number in access.owner_numbers):
        return Role.OWNER
    return Role.COMMITTEE


def can_use_tool(role: Role, tool: str, *, shell_enabled: bool) -> bool:
    """Return whether ``role`` may execute ``tool``.

    The shell has a second gate: ``tools.shell_enabled`` must be true. Even
    then, only the owner is allowed.
    """

    allowed = TOOL_PERMISSIONS.get(tool)
    if allowed is None or role not in allowed:
        return False
    if tool == "shell":
        return shell_enabled
    return True


def can_run_command(required_roles: Collection[Role], role: Role) -> bool:
    return role in required_roles


class RateLimiter:
    """In-memory rolling-window limiter per sender.

    Windows are rolling (last 60 seconds / last 24 hours), not calendar based.
    State is lost on restart; that is acceptable for plan-1.
    """

    def __init__(
        self,
        per_minute: int,
        per_day: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_minute < 1 or per_day < 1:
            raise ValueError("rate limits must be positive")
        self._per_minute = per_minute
        self._per_day = per_day
        self._clock = clock
        self._events: dict[str, deque[float]] = {}

    @property
    def per_minute(self) -> int:
        return self._per_minute

    @property
    def per_day(self) -> int:
        return self._per_day

    def allow(self, sender_id: str) -> bool:
        """Consume one slot for ``sender_id`` and return whether it was allowed."""

        now = self._clock()
        events = self._events.setdefault(sender_id, deque())

        day_cutoff = now - DAY_SECONDS
        while events and events[0] <= day_cutoff:
            events.popleft()

        if len(events) >= self._per_day:
            return False

        minute_cutoff = now - MINUTE_SECONDS
        recent = sum(1 for timestamp in events if timestamp > minute_cutoff)
        if recent >= self._per_minute:
            return False

        events.append(now)
        return True

    def reset(self, sender_id: str | None = None) -> None:
        """Clear one sender's history, or everyone's when no id is given."""

        if sender_id is None:
            self._events.clear()
        else:
            self._events.pop(sender_id, None)


def _same_number(left: str, right: str) -> bool:
    left_digits = _digits(left)
    right_digits = _digits(right)
    if left_digits and right_digits:
        return left_digits == right_digits
    return left == right


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())
