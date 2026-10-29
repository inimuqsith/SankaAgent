"""Deterministic privacy policy: level maths, veto precedence, and disclosure verdicts.

Nothing here calls an LLM. Levels are 0-4 (public, team, personal, sensitive, secret);
level 5/credentials are never stored. Owner means the speaker who stated the fact; the
subject is the person the fact is about and only they may veto it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import PrivacyConfig
from . import MemoryHit

LEVELS = ("public", "team", "personal", "sensitive", "secret", "credential")
MAX_LEVEL = 4


def clamp_level(value: object, default: int = 2) -> int:
    """Clamp to 0..4; level 5 (credentials) is rejected by storage, never clamped in."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(MAX_LEVEL, number))


@dataclass(frozen=True)
class PrivacyVerdict:
    level: int = 2
    subject: str = ""
    ask: bool = False
    question: str = ""
    reason: str = ""


@dataclass(frozen=True)
class AuditResult:
    mentions: tuple[str, ...] = ()
    verdict: str = "allow"  # allow|ask|redact|deny
    reply: str = ""
    question: str = ""
    reason: str = ""


class PrivacyPolicy:
    """Pure functions over a hit plus the configured ceilings."""

    def __init__(self, config: PrivacyConfig) -> None:
        self.config = config

    def show(self, hit: MemoryHit, *, is_dm: bool) -> bool:
        """Whether raw tool output may show this hit to the current chat."""
        if hit.vetoed:
            return False
        ceiling = self.config.dm_max_level if is_dm else self.config.group_max_level
        return hit.level <= ceiling

    def disclose(self, hit: MemoryHit, *, requester: str, is_dm: bool) -> str:
        """allow|ask|deny for this hit when a draft mentions it."""
        if hit.vetoed:
            return "deny"
        if hit.level <= self.config.group_max_level:
            return "allow"
        if not is_dm:
            return "ask" if requester == hit.owner_id and hit.confirmed else "deny"
        if requester != hit.owner_id:
            return "deny"
        if hit.level > self.config.dm_max_level:
            return "deny"
        return "allow" if hit.confirmed else "ask"
