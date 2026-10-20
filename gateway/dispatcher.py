"""Inbound dispatch: cheap synchronous admission, work on a bounded pool.

``submit()`` runs on the HTTP thread and never touches the model. It
deduplicates, resolves the role, applies the gate, routes, checks the command
rate limit, then hands the work to a fixed-size ``ThreadPoolExecutor``. Command
and agent work runs on worker threads, and every user-visible message leaves
through ``OutboundQueue``.

Logs only carry digests, roles, routes, and outcomes; message text, full JIDs,
and tokens never reach them.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from gateway.gate import GateReason, check_gate
from gateway.normalizer import InboundEvent
from sanka.commands.registry import CommandContext
from sanka.formatter import ResponseKind
from sanka.models import InboundMessage
from sanka.policy import RateLimiter, Role, resolve_role
from sanka.router import Route, route

if TYPE_CHECKING:
    from sanka.agent.runtime import AgentReply
    from sanka.commands.registry import CommandRegistry
    from sanka.config import SankaConfig
    from sanka.formatter import Formatter
    from sanka.group_context import GroupContextStore

    from gateway.dedup import TtlRegistry
    from gateway.outbound import OutboundQueue

LOGGER = logging.getLogger(__name__)

_MESSAGE_KEY = "msg:"
_PAIR_NOTICE_KEY = "pair:"
_RATE_NOTICE_KEY = "rate:"
_RATE_NOTICE_WINDOW_SECONDS = 600
_ACQUIRE_TIMEOUT_SECONDS = 2.0

AI_DISABLED = "Fitur AI belum aktif."
GENERIC_ERROR = "Maaf, ada gangguan di sisiku. Coba lagi sebentar."
PAIRING_NOTICE = "Sanka belum terhubung dengan nomor ini. Minta owner menambahkan nomormu."
RATE_NOTICE = "Batas pesan tercapai. Coba lagi nanti."


class SubmitOutcome(str, Enum):
    """Admission decision for one inbound event."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    OVERLOADED = "overloaded"


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """Result of ``Dispatcher.submit()``; ``reason`` is a stable HTTP code."""

    outcome: SubmitOutcome
    reason: str = ""


class AgentHandler(Protocol):
    """Narrow surface the dispatcher needs from the agent runtime."""

    def handle(self, message: InboundMessage, role: Role) -> AgentReply: ...


class Dispatcher:
    """Admit inbound events quickly and process them off the HTTP thread."""

    def __init__(
        self,
        *,
        config: SankaConfig,
        registry: CommandRegistry,
        formatter: Formatter,
        limiter: RateLimiter,
        outbound: OutboundQueue,
        agent: AgentHandler | None,
        dedup: TtlRegistry,
        notices: TtlRegistry,
        group_context: GroupContextStore | None = None,
        workers: int = 8,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")

        self._config = config
        self._registry = registry
        # OutboundQueue applies the formatter to every reply, so the dispatcher
        # keeps it only for wiring parity (notices and replies share one exit).
        self._formatter = formatter
        self._limiter = limiter
        self._outbound = outbound
        self._agent = agent
        self._dedup = dedup
        self._notices = notices
        self._group_context = group_context
        self._wall_clock = wall_clock
        self._slots = threading.BoundedSemaphore(workers)
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="sanka-gw"
        )

    def submit(self, event: InboundEvent) -> SubmitResult:
        """Admit one event without blocking on the model.

        A terminal decision (duplicate, ignored) is never reprocessed when the
        bridge retries the same ``message_id``. Overload is the exception: the
        dedup entry is discarded so a retry can be accepted.
        """

        key = _MESSAGE_KEY + event.message_id
        if not self._dedup.register(key):
            _log("duplicate", event, detail="duplicate message")
            return SubmitResult(SubmitOutcome.DUPLICATE)

        message = event.message
        role = resolve_role(message.sender_id, self._config.access)

        gate = check_gate(event, self._config, role, now=self._wall_clock)
        if gate is not GateReason.ALLOWED:
            if gate is GateReason.DM_NOT_ALLOWED:
                self._send_pairing_notice(event, role)
            _log("ignored", event, role=role, detail=gate.value)
            return SubmitResult(SubmitOutcome.IGNORED, gate.value)

        decision = route(
            message,
            bot_ids=self._config.access.bot_ids,
            require_mention=self._config.gateway.require_mention,
        )
        if decision is Route.IGNORE:
            self._record_context(event)
            _log("ignored", event, role=role, detail="not_mentioned")
            return SubmitResult(SubmitOutcome.IGNORED, "not_mentioned")

        if decision is Route.COMMAND and not self._limiter.allow(message.sender_id):
            self._record_context(event)
            self._send_rate_notice(event, role)
            _log("ignored", event, role=role, detail="rate_limited")
            return SubmitResult(SubmitOutcome.IGNORED, "rate_limited")

        if not self._slots.acquire(timeout=_ACQUIRE_TIMEOUT_SECONDS):
            self._dedup.discard(key)
            _log("overloaded", event, role=role, detail="pipeline is full")
            return SubmitResult(SubmitOutcome.OVERLOADED)

        try:
            self._executor.submit(self._process, event, role, decision)
        except RuntimeError:
            self._slots.release()
            _log("overloaded", event, role=role, detail="executor is shut down")
            return SubmitResult(SubmitOutcome.OVERLOADED)

        _log("accepted", event, role=role, detail=decision.value)
        return SubmitResult(SubmitOutcome.ACCEPTED)

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting work and drop queued futures."""

        self._executor.shutdown(wait=wait, cancel_futures=True)

    @property
    def outbound_pending(self) -> int:
        return self._outbound.pending

    def _process(self, event: InboundEvent, role: Role, decision: Route) -> None:
        """Run one admitted event on a worker thread."""

        message = event.message
        try:
            if self._config.gateway.send_read_receipts:
                self._outbound.enqueue_read(message.chat_id, event.message_id)
            if decision is Route.COMMAND:
                self._run_command(event, role)
            else:
                self._run_agent(event, role)
        except Exception as exc:  # a worker must survive any handler failure
            self._report_failure(event, role, exc)
        finally:
            self._record_context(event)
            self._slots.release()

    def _record_context(self, event: InboundEvent) -> None:
        if self._group_context is None:
            return
        self._group_context.record(
            event.message,
            timestamp=event.timestamp,
            message_id=event.message_id,
        )

    def _run_command(self, event: InboundEvent, role: Role) -> None:
        message = event.message
        context = CommandContext(
            role=role,
            config=self._config,
            sender_id=message.sender_id,
            chat_id=message.chat_id,
            is_group=message.is_group,
        )
        dispatch_result = getattr(self._registry, "dispatch_result", None)
        if callable(dispatch_result):
            result = dispatch_result(message.text, context)
            reply, kind = result.text, result.kind
        else:
            # Compatibility for injected registries written before plan-5.
            reply, kind = self._registry.dispatch(message.text, context), ResponseKind.TEXT
        self._outbound.enqueue_reply(
            message.chat_id,
            [reply],
            base_key=event.message_id,
            kind=kind,
        )
        _log("command_done", event, role=role)

    def _run_agent(self, event: InboundEvent, role: Role) -> None:
        message = event.message
        if self._agent is None:
            self._outbound.enqueue_reply(
                message.chat_id, [AI_DISABLED], base_key=event.message_id
            )
            _log("agent_disabled", event, role=role)
            return

        self._outbound.enqueue_typing(message.chat_id, "composing")
        try:
            reply = self._agent.handle(message, role)
        finally:
            # Never leave the indicator hanging, even when the runtime raised.
            self._outbound.enqueue_typing(message.chat_id, "paused")
        self._outbound.enqueue_reply(
            message.chat_id, reply.messages, base_key=event.message_id
        )
        _log("agent_done", event, role=role)

    def _report_failure(
        self, event: InboundEvent, role: Role, exc: BaseException
    ) -> None:
        """Log type and traceback only, then queue one generic reply."""

        LOGGER.error(
            "gateway worker failed: type=%s role=%s msg=%s\n%s",
            type(exc).__name__,
            role.value,
            _digest(event.message_id),
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        try:
            self._outbound.enqueue_reply(
                event.message.chat_id,
                [GENERIC_ERROR],
                base_key=f"{event.message_id}:error",
            )
        except Exception:  # the queue is best effort here
            LOGGER.error(
                "gateway failed to queue the generic reply: msg=%s",
                _digest(event.message_id),
            )

    def _send_pairing_notice(self, event: InboundEvent, role: Role) -> None:
        """Tell an unknown DM sender once per 24 hours, quota permitting."""

        sender = event.message.sender_id
        key = _PAIR_NOTICE_KEY + sender
        if not self._notices.register(key):
            return
        if not self._limiter.allow(sender):
            # Do not burn the 24h window while the sender has no quota left.
            self._notices.discard(key)
            return
        self._outbound.enqueue_reply(
            event.message.chat_id, [PAIRING_NOTICE], base_key=event.message_id
        )
        LOGGER.info(
            "gateway pairing notice queued: role=%s chat=%s msg=%s",
            role.value,
            _digest(event.message.chat_id),
            _digest(event.message_id),
        )

    def _send_rate_notice(self, event: InboundEvent, role: Role) -> None:
        """Send at most one rate-limit notice per sender per 10 minutes."""

        sender = event.message.sender_id
        bucket = int(self._wall_clock() // _RATE_NOTICE_WINDOW_SECONDS)
        key = f"{_RATE_NOTICE_KEY}{sender}:{bucket}"
        if not self._notices.register(key):
            return
        self._outbound.enqueue_reply(
            event.message.chat_id, [RATE_NOTICE], base_key=event.message_id
        )
        LOGGER.info(
            "gateway rate notice queued: role=%s chat=%s msg=%s",
            role.value,
            _digest(event.message.chat_id),
            _digest(event.message_id),
        )


def _log(event: str, inbound: InboundEvent, *, role: Role | None = None, detail: str = "") -> None:
    LOGGER.info(
        "gateway %s: msg=%s chat=%s role=%s detail=%s",
        event,
        _digest(inbound.message_id),
        _digest(inbound.message.chat_id),
        role.value if role is not None else "-",
        detail,
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]
