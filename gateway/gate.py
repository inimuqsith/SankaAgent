"""Inbound filtering: blank text, staleness, group allowlist, and DM policy."""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum

from sanka.config import SankaConfig
from sanka.policy import COMMITTEE_ROLES, Role

from gateway.normalizer import InboundEvent


class GateReason(str, Enum):
    ALLOWED = "allowed"
    BLANK_TEXT = "blank_text"
    STALE = "stale"
    DM_NOT_ALLOWED = "dm_not_allowed"
    GROUP_NOT_ALLOWED = "group_not_allowed"


def check_gate(
    event: InboundEvent,
    config: SankaConfig,
    role: Role,
    *,
    now: Callable[[], float] = time.time,
) -> GateReason:
    """Decide whether an inbound event may proceed to routing.

    Checks run in contract order: blank text, staleness, group policy, then DM
    policy. Groups never consult the DM policy.
    """
    if (
        not event.message.text.strip()
        and not event.message.attachments
        and not event.message.attachment_errors
    ):
        return GateReason.BLANK_TEXT

    max_age = config.gateway.max_inbound_age_seconds
    if max_age > 0 and abs(now() - event.timestamp) > max_age:
        return GateReason.STALE

    if event.message.is_group:
        if config.gateway.group_policy == "open":
            return GateReason.ALLOWED
        if event.message.chat_id in config.access.allowed_group_ids:
            return GateReason.ALLOWED
        return GateReason.GROUP_NOT_ALLOWED

    if config.gateway.dm_policy == "open":
        return GateReason.ALLOWED
    if role in COMMITTEE_ROLES:
        return GateReason.ALLOWED
    return GateReason.DM_NOT_ALLOWED
