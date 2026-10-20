"""Gate tests: order of checks, group allowlist, and DM policy."""

from __future__ import annotations

from dataclasses import replace

import pytest

from gateway.gate import GateReason, check_gate
from gateway.normalizer import InboundEvent
from sanka.config import SankaConfig
from sanka.models import InboundMessage
from sanka.policy import Role

DM_ID = "628111111111@s.whatsapp.net"
OTHER_DM_ID = "628333333333@s.whatsapp.net"
GROUP_ID = "120363000000000000@g.us"
OTHER_GROUP_ID = "120363999999999999@g.us"


class FakeClock:
    """A manually advanced wall clock for staleness checks."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def event(
    text: str = "halo",
    *,
    chat_id: str = DM_ID,
    sender_id: str = DM_ID,
    timestamp: float = 1000.0,
    is_group: bool | None = None,
) -> InboundEvent:
    group = chat_id.endswith("@g.us") if is_group is None else is_group
    return InboundEvent(
        message_id="msg-1",
        message=InboundMessage(
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            is_group=group,
        ),
        timestamp=timestamp,
    )


def with_gateway(config: SankaConfig, **changes: object) -> SankaConfig:
    return replace(config, gateway=replace(config.gateway, **changes))  # type: ignore[arg-type]


def with_access(config: SankaConfig, **changes: object) -> SankaConfig:
    return replace(config, access=replace(config.access, **changes))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "chat_id"),
    [
        ("", DM_ID),
        ("   ", DM_ID),
        ("\n\t", GROUP_ID),
        ("   ", OTHER_GROUP_ID),
    ],
)
def test_blank_text_is_rejected_first(config: SankaConfig, text: str, chat_id: str) -> None:
    clock = FakeClock()
    stale = event(text, chat_id=chat_id, timestamp=0.0)
    assert check_gate(stale, config, Role.OWNER, now=clock) is GateReason.BLANK_TEXT


def test_fresh_dm_is_allowed(config: SankaConfig) -> None:
    clock = FakeClock()
    assert check_gate(event(), config, Role.PARTICIPANT, now=clock) is GateReason.ALLOWED


def test_stale_event_is_rejected(config: SankaConfig) -> None:
    clock = FakeClock()
    clock.advance(config.gateway.max_inbound_age_seconds + 1)
    assert check_gate(event(), config, Role.OWNER, now=clock) is GateReason.STALE


def test_future_timestamp_is_also_stale(config: SankaConfig) -> None:
    clock = FakeClock()
    future = event(timestamp=clock() + config.gateway.max_inbound_age_seconds + 1)
    assert check_gate(future, config, Role.OWNER, now=clock) is GateReason.STALE


def test_age_equal_to_limit_is_allowed(config: SankaConfig) -> None:
    clock = FakeClock()
    clock.advance(config.gateway.max_inbound_age_seconds)
    assert check_gate(event(), config, Role.OWNER, now=clock) is GateReason.ALLOWED


def test_zero_max_age_disables_staleness(config: SankaConfig) -> None:
    config = with_gateway(config, max_inbound_age_seconds=0)
    clock = FakeClock()
    clock.advance(10_000_000)
    assert check_gate(event(), config, Role.PARTICIPANT, now=clock) is GateReason.ALLOWED


def test_stale_is_checked_before_group_allowlist(config: SankaConfig) -> None:
    clock = FakeClock()
    clock.advance(config.gateway.max_inbound_age_seconds + 1)
    stale_foreign_group = event(chat_id=OTHER_GROUP_ID)
    assert (
        check_gate(stale_foreign_group, config, Role.PARTICIPANT, now=clock)
        is GateReason.STALE
    )


def test_allowlisted_group_is_allowed(config: SankaConfig) -> None:
    assert (
        check_gate(event(chat_id=GROUP_ID), config, Role.PARTICIPANT, now=FakeClock())
        is GateReason.ALLOWED
    )


def test_group_allowlist_is_an_exact_match(config: SankaConfig) -> None:
    config = with_access(config, allowed_group_ids=(GROUP_ID,))
    assert (
        check_gate(
            event(chat_id=f"{GROUP_ID} ", is_group=True),
            config,
            Role.PARTICIPANT,
            now=FakeClock(),
        )
        is GateReason.GROUP_NOT_ALLOWED
    )
    assert (
        check_gate(
            event(chat_id=OTHER_GROUP_ID), config, Role.PARTICIPANT, now=FakeClock()
        )
        is GateReason.GROUP_NOT_ALLOWED
    )


def test_group_open_allows_every_group(config: SankaConfig) -> None:
    config = with_gateway(config, group_policy="open", dm_policy="pairing")
    assert (
        check_gate(
            event(chat_id=OTHER_GROUP_ID), config, Role.PARTICIPANT, now=FakeClock()
        )
        is GateReason.ALLOWED
    )


def test_groups_never_consult_dm_policy(config: SankaConfig) -> None:
    pairing = with_gateway(config, dm_policy="pairing")
    allowed_group = with_access(pairing, allowed_group_ids=(GROUP_ID,))

    assert (
        check_gate(
            event(chat_id=GROUP_ID), allowed_group, Role.PARTICIPANT, now=FakeClock()
        )
        is GateReason.ALLOWED
    )
    assert (
        check_gate(
            event(chat_id=OTHER_GROUP_ID),
            allowed_group,
            Role.PARTICIPANT,
            now=FakeClock(),
        )
        is GateReason.GROUP_NOT_ALLOWED
    )


@pytest.mark.parametrize("role", list(Role))
def test_dm_open_allows_every_role(config: SankaConfig, role: Role) -> None:
    assert (
        check_gate(event(), config, role, now=FakeClock()) is GateReason.ALLOWED
    )


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.OWNER, GateReason.ALLOWED),
        (Role.COMMITTEE, GateReason.ALLOWED),
        (Role.PARTICIPANT, GateReason.DM_NOT_ALLOWED),
    ],
)
def test_dm_pairing_only_allows_owner_and_committee(
    config: SankaConfig, role: Role, expected: GateReason
) -> None:
    config = with_gateway(config, dm_policy="pairing")
    assert check_gate(event(), config, role, now=FakeClock()) is expected


def test_pairing_denial_reports_dm_not_allowed(config: SankaConfig) -> None:
    config = with_gateway(config, dm_policy="pairing")
    denied = event(chat_id=OTHER_DM_ID, sender_id=OTHER_DM_ID)
    assert (
        check_gate(denied, config, Role.PARTICIPANT, now=FakeClock())
        is GateReason.DM_NOT_ALLOWED
    )
