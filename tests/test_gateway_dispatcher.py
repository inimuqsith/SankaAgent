"""Dispatcher tests: admission decisions, command/agent pipelines, and logging."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace

import pytest

from gateway.dedup import TtlRegistry
from gateway.dispatcher import (
    AI_DISABLED,
    GENERIC_ERROR,
    PAIRING_NOTICE,
    RATE_NOTICE,
    Dispatcher,
    SubmitOutcome,
    SubmitResult,
)
from gateway.normalizer import InboundEvent
from gateway.outbound import OutboundQueue
from sanka.commands.registry import CommandContext
from sanka.config import SankaConfig
from sanka.formatter import Formatter
from sanka.models import InboundMessage
from sanka.policy import RateLimiter, Role
from tests.gateway_fakes import FakeBridge, FakeClock, FakeRuntime

OWNER = "628111111111@s.whatsapp.net"
PARTICIPANT = "628333333333@s.whatsapp.net"
GROUP = "120363000000000000@g.us"


class FakeRegistry:
    """Command registry stand-in that records contexts and returns fixed text."""

    def __init__(self, reply: str = "pong") -> None:
        self.reply = reply
        self.calls: list[tuple[str, CommandContext]] = []

    def dispatch(self, text: str, context: CommandContext) -> str:
        self.calls.append((text, context))
        return self.reply


@dataclass
class Harness:
    dispatcher: Dispatcher
    outbound: OutboundQueue
    bridge: FakeBridge
    runtime: FakeRuntime | None
    registry: FakeRegistry
    dedup: TtlRegistry
    notices: TtlRegistry
    limiter: RateLimiter

    def wait_sends(self, count: int, timeout: float = 5.0) -> None:
        assert _wait_until(lambda: len(self.bridge.sent) >= count, timeout), (
            f"expected at least {count} send(s), got {len(self.bridge.sent)}"
        )
        assert self.outbound.wait_idle(timeout), "outbound queue did not drain"


def make_event(
    message_id: str,
    *,
    chat_id: str = PARTICIPANT,
    sender_id: str = PARTICIPANT,
    text: str = "halo sanka",
    is_group: bool = False,
    mentions: tuple[str, ...] = (),
    reply_to_bot: bool = False,
    timestamp: float | None = None,
) -> InboundEvent:
    return InboundEvent(
        message_id=message_id,
        message=InboundMessage(
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            is_group=is_group,
            mentions=mentions,
            reply_to_bot=reply_to_bot,
        ),
        timestamp=time.time() if timestamp is None else timestamp,
    )


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def make_harness(config: SankaConfig):
    created: list[Harness] = []

    def factory(
        *,
        active_config: SankaConfig | None = None,
        registry: FakeRegistry | None = None,
        limiter: RateLimiter | None = None,
        agent: object = "default",
        send_read_receipts: bool | None = None,
    ) -> Harness:
        active = active_config or config
        if send_read_receipts is not None:
            active = replace(
                active,
                gateway=replace(active.gateway, send_read_receipts=send_read_receipts),
            )

        formatter = Formatter(active.limits.max_reply_chars)
        bridge = FakeBridge()
        outbound = OutboundQueue(
            bridge,
            formatter,
            per_minute=active.limits.outbound_per_minute,
        )
        outbound.start()
        runtime = FakeRuntime() if agent == "default" else agent
        registry_obj = registry if registry is not None else FakeRegistry()
        limiter_obj = limiter or RateLimiter(
            active.limits.rate_limit_per_minute,
            active.limits.rate_limit_per_day,
        )
        dedup = TtlRegistry(active.limits.inbound_dedup_ttl_minutes * 60)
        notices = TtlRegistry(24 * 3600)
        dispatcher = Dispatcher(
            config=active,
            registry=registry_obj,
            formatter=formatter,
            limiter=limiter_obj,
            outbound=outbound,
            agent=runtime,
            dedup=dedup,
            notices=notices,
        )
        harness = Harness(
            dispatcher=dispatcher,
            outbound=outbound,
            bridge=bridge,
            runtime=runtime,
            registry=registry_obj,
            dedup=dedup,
            notices=notices,
            limiter=limiter_obj,
        )
        created.append(harness)
        return harness

    yield factory

    for harness in created:
        harness.dispatcher.shutdown(wait=True)
        harness.outbound.stop(timeout=5.0)


def test_duplicate_message_is_not_processed_again(make_harness):
    harness = make_harness()
    event = make_event("dup-1")

    first = harness.dispatcher.submit(event)
    assert first == SubmitResult(SubmitOutcome.ACCEPTED)
    harness.wait_sends(1)

    second = harness.dispatcher.submit(event)
    assert second == SubmitResult(SubmitOutcome.DUPLICATE)
    assert second.reason == ""

    harness.wait_sends(1)
    assert len(harness.runtime.calls) == 1
    assert len(harness.bridge.sent) == 1


def test_group_without_mention_is_ignored(make_harness):
    harness = make_harness()
    event = make_event(
        "grp-1",
        chat_id=GROUP,
        sender_id=PARTICIPANT,
        text="halo semua",
        is_group=True,
    )

    result = harness.dispatcher.submit(event)
    assert result == SubmitResult(SubmitOutcome.IGNORED, "not_mentioned")

    assert harness.outbound.wait_idle(1.0)
    assert harness.runtime.calls == []
    assert harness.bridge.sent == []
    assert harness.bridge.typing_events == []

    # A terminal decision is not reprocessed when the bridge retries.
    retry = harness.dispatcher.submit(event)
    assert retry.outcome is SubmitOutcome.DUPLICATE


def test_group_with_mention_reaches_the_agent(make_harness, config):
    harness = make_harness()
    event = make_event(
        "grp-2",
        chat_id=GROUP,
        sender_id=PARTICIPANT,
        text="halo @sanka",
        is_group=True,
        mentions=(config.access.bot_ids[0],),
    )

    assert harness.dispatcher.submit(event).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(1)
    assert len(harness.runtime.calls) == 1
    assert harness.runtime.calls[0][1] is Role.PARTICIPANT
    assert harness.bridge.sent[0][0] == GROUP


def test_command_uses_resolved_role_and_formats_reply(make_harness):
    registry = FakeRegistry(reply="<reasoning>internal</reasoning>pong")
    harness = make_harness(registry=registry)

    event = make_event("cmd-1", chat_id=OWNER, sender_id=OWNER, text="/ping")
    assert harness.dispatcher.submit(event).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(1)

    assert len(registry.calls) == 1
    text, context = registry.calls[0]
    assert text == "/ping"
    assert context.role is Role.OWNER
    assert context.sender_id == OWNER
    assert context.chat_id == OWNER
    assert context.is_group is False
    assert context.config.limits.max_reply_chars > 0

    assert harness.bridge.sent == [(OWNER, "pong", "cmd-1:0")]
    assert harness.bridge.typing_events == []
    assert harness.runtime.calls == []


@pytest.mark.parametrize(
    ("sender", "expected_role"),
    [(OWNER, Role.OWNER), (PARTICIPANT, Role.PARTICIPANT)],
)
def test_dm_agent_reply_with_typing(make_harness, sender, expected_role):
    harness = make_harness()
    event = make_event("dm-1", chat_id=sender, sender_id=sender, text="halo sanka")

    assert harness.dispatcher.submit(event).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(1)

    assert len(harness.runtime.calls) == 1
    message, role = harness.runtime.calls[0]
    assert role is expected_role
    assert message.text == "halo sanka"
    assert harness.bridge.typing_events == [(sender, "composing"), (sender, "paused")]
    assert harness.bridge.sent == [(sender, "Balasan agent.", "dm-1:0")]


def test_command_rate_limit_sends_one_notice(make_harness):
    clock = FakeClock()
    limiter = RateLimiter(1, 100, clock=clock)
    harness = make_harness(limiter=limiter, registry=FakeRegistry(reply="pong"))

    first = make_event("rl-1", chat_id=OWNER, sender_id=OWNER, text="/ping")
    assert harness.dispatcher.submit(first).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(1)

    second = make_event("rl-2", chat_id=OWNER, sender_id=OWNER, text="/ping")
    assert harness.dispatcher.submit(second) == SubmitResult(
        SubmitOutcome.IGNORED, "rate_limited"
    )
    harness.wait_sends(2)

    third = make_event("rl-3", chat_id=OWNER, sender_id=OWNER, text="/ping")
    assert harness.dispatcher.submit(third) == SubmitResult(
        SubmitOutcome.IGNORED, "rate_limited"
    )
    assert harness.outbound.wait_idle(2.0)

    assert [text for _, text, _ in harness.bridge.sent] == ["pong", RATE_NOTICE]
    assert harness.runtime.calls == []


def test_agent_path_does_not_consume_command_quota(make_harness):
    clock = FakeClock()
    limiter = RateLimiter(1, 100, clock=clock)
    harness = make_harness(limiter=limiter)

    agent_event = make_event("quota-1", text="halo sanka")
    assert harness.dispatcher.submit(agent_event).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(1)

    command_event = make_event("quota-2", chat_id=OWNER, sender_id=OWNER, text="/ping")
    assert harness.dispatcher.submit(command_event).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(2)

    assert len(harness.runtime.calls) == 1
    assert len(harness.registry.calls) == 1
    assert [text for _, text, _ in harness.bridge.sent] == [
        "Balasan agent.",
        "pong",
    ]


def test_runtime_exception_sends_generic_reply_and_worker_survives(make_harness):
    harness = make_harness()
    harness.runtime.error = RuntimeError("boom")

    first = make_event("err-1", text="halo sanka")
    assert harness.dispatcher.submit(first).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(1)
    assert harness.bridge.sent == [(PARTICIPANT, GENERIC_ERROR, "err-1:error:0")]
    assert harness.bridge.typing_events == [
        (PARTICIPANT, "composing"),
        (PARTICIPANT, "paused"),
    ]

    harness.runtime.error = None
    second = make_event("err-2", text="halo lagi")
    assert harness.dispatcher.submit(second).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(2)
    assert harness.bridge.sent[-1] == (PARTICIPANT, "Balasan agent.", "err-2:0")
    # The failed call never reached ``calls``; the worker handled the next one.
    assert len(harness.runtime.calls) == 1


def test_missing_agent_replies_with_degraded_text(make_harness):
    harness = make_harness(agent=None)
    event = make_event("noai-1", text="halo sanka")

    assert harness.dispatcher.submit(event).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(1)

    assert harness.bridge.sent == [(PARTICIPANT, AI_DISABLED, "noai-1:0")]
    assert harness.bridge.typing_events == []
    assert harness.runtime is None


def test_pairing_notice_is_sent_once_per_sender(make_harness, config):
    pairing = replace(
        config,
        gateway=replace(config.gateway, dm_policy="pairing"),
    )
    harness = make_harness(active_config=pairing)

    first = make_event("pair-1", text="halo")
    assert harness.dispatcher.submit(first) == SubmitResult(
        SubmitOutcome.IGNORED, "dm_not_allowed"
    )
    harness.wait_sends(1)
    assert harness.bridge.sent == [(PARTICIPANT, PAIRING_NOTICE, "pair-1:0")]

    second = make_event("pair-2", text="halo lagi")
    assert harness.dispatcher.submit(second) == SubmitResult(
        SubmitOutcome.IGNORED, "dm_not_allowed"
    )
    assert harness.outbound.wait_idle(2.0)
    assert [text for _, text, _ in harness.bridge.sent] == [PAIRING_NOTICE]
    assert harness.runtime.calls == []

    # A known number bypasses the pairing gate.
    owner_event = make_event("pair-3", chat_id=OWNER, sender_id=OWNER, text="halo")
    assert harness.dispatcher.submit(owner_event).outcome is SubmitOutcome.ACCEPTED
    harness.wait_sends(2)
    assert harness.bridge.sent[-1] == (OWNER, "Balasan agent.", "pair-3:0")


def test_pairing_notice_skipped_when_quota_is_gone(make_harness, config):
    pairing = replace(
        config,
        gateway=replace(config.gateway, dm_policy="pairing"),
    )
    harness = make_harness(
        active_config=pairing,
        limiter=RateLimiter(1, 100, clock=FakeClock()),
    )
    assert harness.limiter.allow(PARTICIPANT) is True  # exhaust the quota

    event = make_event("pair-quota", text="halo")
    assert harness.dispatcher.submit(event) == SubmitResult(
        SubmitOutcome.IGNORED, "dm_not_allowed"
    )
    assert harness.outbound.wait_idle(1.0)
    assert harness.bridge.sent == []
    assert len(harness.notices) == 0


def test_read_receipts_are_enqueued_when_enabled(make_harness):
    enabled = make_harness(send_read_receipts=True)
    enabled.dispatcher.submit(
        make_event("read-1", chat_id=OWNER, sender_id=OWNER, text="/ping")
    )
    enabled.wait_sends(1)
    assert enabled.bridge.reads == [(OWNER, "read-1")]

    disabled = make_harness(send_read_receipts=False)
    disabled.dispatcher.submit(
        make_event("read-2", chat_id=OWNER, sender_id=OWNER, text="/ping")
    )
    disabled.wait_sends(1)
    assert disabled.bridge.reads == []


def test_shutdown_rejects_new_work(make_harness):
    harness = make_harness()
    harness.dispatcher.shutdown(wait=True)

    result = harness.dispatcher.submit(make_event("late-1"))
    assert result == SubmitResult(SubmitOutcome.OVERLOADED)


def test_logs_never_contain_message_text_jids_or_token(make_harness, caplog):
    harness = make_harness()
    secret_text = "jangan-bocor-9f8e7d"
    token = "tok-rahasia-123"

    with caplog.at_level(logging.DEBUG):
        assert (
            harness.dispatcher.submit(
                make_event("log-1", text=secret_text)
            ).outcome
            is SubmitOutcome.ACCEPTED
        )
        harness.wait_sends(1)

        harness.runtime.error = ValueError(f"gagal memproses {secret_text} token={token}")
        assert (
            harness.dispatcher.submit(
                make_event("log-2", text=secret_text)
            ).outcome
            is SubmitOutcome.ACCEPTED
        )
        harness.wait_sends(2)

        # Ignored paths log metadata too.
        harness.dispatcher.submit(
            make_event(
                "log-3",
                chat_id=GROUP,
                text=secret_text,
                is_group=True,
            )
        )
        harness.outbound.wait_idle(2.0)

    captured = caplog.text
    assert secret_text not in captured
    assert token not in captured
    assert "@s.whatsapp.net" not in captured
    assert "@g.us" not in captured
