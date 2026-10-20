"""Plan-2 agent runtime: session + queue + OpenCode Go + formatter."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import datetime, timezone

from core.agent_loop import agent_runner_loop, exhaust

from sanka.agent.client import AgentClientError, OpenCodeGoClient
from sanka.agent.handler import SankaHandler
from sanka.agent.prompt import build_system_prompt
from sanka.config import SankaConfig
from sanka.formatter import Formatter, ResponseKind, strip_internal_blocks
from sanka.models import InboundMessage
from sanka.policy import RateLimiter, Role
from sanka.queue import (
    AgentCancelled,
    AgentJob,
    ChatQueue,
    QueueBusyError,
    StopOutcome,
)
from sanka.secrets import get_secret
from sanka.sessions import SessionStore
from sanka.tools.registry import ToolContext, ToolRegistry

LOGGER = logging.getLogger(__name__)

_RATE_LIMITED = "Batas pesan tercapai. Coba lagi nanti."
_BUSY = "Sanka sedang sibuk. Coba lagi sebentar."
_STOPPED = "Pekerjaan dihentikan."
_EMPTY = "Maaf, aku belum bisa menjawab itu."
_SERVICE_ERROR = "Maaf, layanan AI sedang bermasalah. Coba lagi sebentar."


@dataclass(frozen=True, slots=True)
class AgentReply:
    """Final, already formatted messages for one inbound message."""

    messages: tuple[str, ...] = ()
    kind: ResponseKind = ResponseKind.TEXT


class AgentRuntime:
    """Run one non-command message through the agent.

    Only final text leaves this class. Reasoning, tool traces, and provider
    errors stay inside; logging contains metadata only.
    """

    def __init__(
        self,
        config: SankaConfig,
        *,
        secrets_path: str | None = None,
        client_factory: Callable[[str], OpenCodeGoClient] | None = None,
        clock: Callable[[], float] = time.monotonic,
        limiter: RateLimiter | None = None,
        store: SessionStore | None = None,
        queue: ChatQueue[AgentReply] | None = None,
        formatter: Formatter | None = None,
        tools: ToolRegistry | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._tools = tools
        self._now = now or (lambda: datetime.now(timezone.utc))
        if client_factory is None:
            api_key = get_secret(
                config.llm.api_key_env, secrets_path=secrets_path
            )

            def default_factory(session_id: str) -> OpenCodeGoClient:
                return OpenCodeGoClient(
                    api_key=api_key,
                    base_url=config.llm.base_url,
                    model=config.llm.model,
                    session_id=session_id,
                    timeout_seconds=config.llm.timeout_seconds,
                )

            client_factory = default_factory
        self._client_factory = client_factory
        self._limiter = limiter or RateLimiter(
            config.limits.rate_limit_per_minute,
            config.limits.rate_limit_per_day,
            clock=clock,
        )
        self._store = store or SessionStore(
            config.limits.session_idle_ttl_minutes * 60, clock=clock
        )
        self._formatter = formatter or Formatter(config.limits.max_reply_chars)
        self._queue = queue or ChatQueue(self._execute, max_active_chats=16)

    def handle(self, message: InboundMessage, role: Role) -> AgentReply:
        """Process one agent message synchronously and return final messages."""

        if not self._limiter.allow(message.sender_id):
            return AgentReply((_RATE_LIMITED,))

        try:
            future = self._queue.submit(message, role)
        except QueueBusyError:
            return AgentReply((_BUSY,))

        try:
            return future.result()
        except (CancelledError, AgentCancelled):
            return AgentReply((_STOPPED,))

    def _build_handler(self, job: AgentJob[AgentReply]) -> SankaHandler:
        if self._tools is None:
            return SankaHandler(job.cancel_event)
        context = ToolContext(
            role=job.role,
            chat_id=job.chat_id,
            sender_id=job.sender_id,
            config=self._config,
            now=self._now,
        )
        return SankaHandler(
            job.cancel_event,
            tools=self._tools,
            context=context,
            max_calls_per_turn=self._config.tools.max_calls_per_turn,
        )

    def reset(self, chat_id: str) -> None:
        """Stop current work and clear the conversation for ``chat_id``."""

        self._queue.stop(chat_id, requester_id="", is_owner=True)
        self._store.reset(chat_id)

    def stop(self, chat_id: str, requester_id: str, is_owner: bool) -> StopOutcome:
        return self._queue.stop(chat_id, requester_id, is_owner)

    def shutdown(self, wait: bool = True) -> None:
        self._queue.shutdown(wait=wait)

    def _execute(self, job: AgentJob) -> AgentReply:
        if job.cancel_event.is_set():
            raise AgentCancelled

        session = self._store.get_or_create(job.chat_id)
        history = self._store.history(job.chat_id)
        client = self._client_factory(session.opencode_session_id)
        handler = self._build_handler(job)
        tools_schema = (
            self._tools.specs_for(job.role) if self._tools is not None else ()
        )

        try:
            exit_reason = exhaust(
                agent_runner_loop(
                    client,
                    build_system_prompt(),
                    job.message.text,
                    handler,
                    tools_schema,
                    max_turns=self._config.llm.max_turns,
                    verbose=False,
                    history=history,
                )
            )
        except AgentClientError as exc:
            LOGGER.warning(
                "agent request failed status=%s", exc.status_code
            )
            return AgentReply((_SERVICE_ERROR,))
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        if job.cancel_event.is_set():
            raise AgentCancelled

        data = exit_reason.get("data") if isinstance(exit_reason, dict) else None
        text = strip_internal_blocks(data).strip() if isinstance(data, str) else ""
        if not text:
            return AgentReply((_EMPTY,))

        if self._store.append(
            job.chat_id, session.opencode_session_id, "user", job.message.text
        ):
            self._store.append(
                job.chat_id, session.opencode_session_id, "assistant", text
            )

        messages = self._formatter.format(text)
        if not messages:
            return AgentReply((_EMPTY,))
        return AgentReply(tuple(messages))
