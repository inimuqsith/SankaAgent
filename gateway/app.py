"""Application wiring for the local gateway.

``build_app`` assembles the shared components once and validates the loopback
precondition before anything binds a socket. Imports between the gateway
modules stay one-directional (app -> server/dispatcher/outbound) to avoid
cycles.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gateway.dedup import TtlRegistry
from gateway.dispatcher import AgentHandler, Dispatcher
from gateway.outbound import BridgeClient, HttpBridgeClient, OutboundQueue
from gateway.server import GatewayServer
from sanka.agent.runtime import AgentRuntime
from sanka.attachments import AttachmentStore
from sanka.commands.basic import build_registry
from sanka.commands.session import SessionControl
from sanka.config import LOOPBACK_HOSTS, SankaConfig
from sanka.formatter import Formatter
from sanka.group_context import build_group_context
from sanka.policy import RateLimiter
from sanka.queue import StopOutcome
from sanka.secrets import get_secret
from sanka.tools import build_recap_service, build_registry as build_tool_registry

if TYPE_CHECKING:
    from sanka.commands.registry import CommandRegistry

_NOTICE_TTL_SECONDS = 24 * 3600
_UNSET: object = object()


class _NullSessionControl:
    """No-op session control used when no agent runtime is wired."""

    def reset(self, chat_id: str) -> None:
        return None

    def stop(
        self, chat_id: str, requester_id: str, is_owner: bool
    ) -> StopOutcome:
        return StopOutcome.IDLE


@dataclass(slots=True)
class GatewayApp:
    """Everything one gateway process owns, started and stopped together."""

    dedup: TtlRegistry
    notices: TtlRegistry
    outbound: OutboundQueue
    dispatcher: Dispatcher
    server: GatewayServer
    _stop_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _stopped: bool = field(default=False, init=False, repr=False)
    _stopped_event: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False
    )

    def start(self) -> None:
        """Start the outbound worker; safe to call more than once."""
        self.outbound.start()

    def stop(self) -> None:
        """Stop serving, drain the dispatcher, then stop the outbound worker.

        Idempotent: repeated calls only act once, and a call that arrives while
        the first is still closing waits for it to finish. The server is
        stopped first so no new request can enter the pipeline.
        """
        with self._stop_lock:
            first = not self._stopped
            self._stopped = True

        if not first:
            self._stopped_event.wait(timeout=10.0)
            return

        try:
            self.server.shutdown()
        finally:
            try:
                self.server.close()
            finally:
                try:
                    self.dispatcher.shutdown()
                finally:
                    try:
                        self.outbound.stop()
                    finally:
                        self._stopped_event.set()

    def serve_forever(self) -> None:
        """Start the worker and block in the HTTP loop."""
        self.start()
        self.server.serve_forever()


def build_app(
    config: SankaConfig,
    *,
    secrets_path: str | None = None,
    token: str | None = None,
    registry: CommandRegistry | None = None,
    agent: AgentHandler | None | object = _UNSET,
    limiter: RateLimiter | None = None,
    bridge: BridgeClient | None = None,
) -> GatewayApp:
    """Assemble a gateway app from config and optional injected components.

    ``token`` defaults to the secret named by ``gateway.auth_token_env``.
    ``agent`` defaults to a real :class:`AgentRuntime`; pass ``None`` for the
    degraded, command-only mode. ``bridge`` defaults to an HTTP client aimed at
    ``gateway.bridge_url``.
    """
    if config.gateway.listen_host not in LOOPBACK_HOSTS:
        raise ValueError(
            "gateway.listen_host must be a loopback host "
            "(127.0.0.1, ::1, localhost)"
        )

    resolved_token = token
    if resolved_token is None:
        resolved_token = get_secret(
            config.gateway.auth_token_env, secrets_path=secrets_path
        )
    if not resolved_token or not resolved_token.strip():
        raise ValueError("gateway token must not be empty")

    formatter = Formatter(config.limits.max_reply_chars)
    group_context = build_group_context(config.group_context)
    attachments = AttachmentStore.from_config(config)
    resolved_limiter = limiter or RateLimiter(
        config.limits.rate_limit_per_minute,
        config.limits.rate_limit_per_day,
    )

    resolved_agent: AgentHandler | None
    tool_registry = (
        build_tool_registry(config, attachments=attachments)
        if agent is _UNSET
        else None
    )
    if agent is _UNSET:
        resolved_agent = AgentRuntime(
            config,
            secrets_path=secrets_path,
            limiter=resolved_limiter,
            formatter=formatter,
            tools=tool_registry,
            group_context=group_context,
            attachments=attachments,
        )
    else:
        resolved_agent = agent  # type: ignore[assignment]

    control = _session_control(resolved_agent)
    resolved_registry = (
        registry
        if registry is not None
        else build_registry(
            control,
            recap_service=build_recap_service(config),
        )
    )

    resolved_bridge = (
        bridge
        if bridge is not None
        else HttpBridgeClient(
            config.gateway.bridge_url,
            resolved_token,
            config.gateway.request_timeout_seconds,
        )
    )
    outbound = OutboundQueue(
        resolved_bridge,
        formatter,
        per_minute=config.limits.outbound_per_minute,
    )

    dedup = TtlRegistry(config.limits.inbound_dedup_ttl_minutes * 60)
    notices = TtlRegistry(_NOTICE_TTL_SECONDS)

    dispatcher = Dispatcher(
        config=config,
        registry=resolved_registry,
        formatter=formatter,
        limiter=resolved_limiter,
        outbound=outbound,
        agent=resolved_agent,
        dedup=dedup,
        notices=notices,
        group_context=group_context,
    )

    server = GatewayServer(
        dispatcher,
        token=resolved_token,
        host=config.gateway.listen_host,
        port=config.gateway.listen_port,
        attachments=attachments,
    )

    return GatewayApp(
        dedup=dedup,
        notices=notices,
        outbound=outbound,
        dispatcher=dispatcher,
        server=server,
    )


def _session_control(agent: AgentHandler | None) -> SessionControl:
    """Use the agent as session control when it exposes the whole surface."""
    if callable(getattr(agent, "reset", None)) and callable(
        getattr(agent, "stop", None)
    ):
        return agent  # type: ignore[return-value]
    return _NullSessionControl()
