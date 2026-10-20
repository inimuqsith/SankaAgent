"""End-to-end gateway tests: real HTTP server, fake bridge, fake runtime."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Callable

import httpx
import pytest

from gateway.app import GatewayApp, build_app
from sanka.config import SankaConfig
from sanka.policy import Role
from tests.gateway_fakes import FakeBridge, FakeRuntime

TOKEN = "test-token"
DM_CHAT = "628111111111@s.whatsapp.net"
GROUP_CHAT = "120363000000000000@g.us"
PARTICIPANT = "628333333333@s.whatsapp.net"
BOT_ID = "628999999999@s.whatsapp.net"

_DEFAULT = object()


class LiveGateway:
    """One running app plus its client and fakes."""

    def __init__(
        self,
        app: GatewayApp,
        bridge: FakeBridge,
        runtime: FakeRuntime | None,
        client: httpx.Client,
        thread: threading.Thread,
    ) -> None:
        self.app = app
        self.bridge = bridge
        self.runtime = runtime
        self.client = client
        self.thread = thread

    def post(
        self, message_id: str, chat_id: str, sender_id: str, text: str, **extra: object
    ) -> httpx.Response:
        body: dict[str, object] = {
            "message_id": message_id,
            "chat_id": chat_id,
            "sender_id": sender_id,
            "text": text,
        }
        body.update(extra)
        return self.client.post(
            "/v1/inbound", json=body, headers={"X-Sanka-Token": TOKEN}
        )


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    """Poll ``predicate`` until it is true or the deadline passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def settled(predicate: Callable[[], bool], window: float = 0.4) -> bool:
    """Wait up to ``window`` for ``predicate`` to become true."""
    return wait_until(predicate, timeout=window)


def health_ok(client: httpx.Client) -> bool:
    """Return whether ``/healthz`` answers 200, tolerating startup races."""
    try:
        return client.get("/healthz").status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture
def live(config: SankaConfig):
    """Factory building isolated apps on port 0; always cleans them up."""
    created: list[LiveGateway] = []

    def factory(
        *,
        agent: object = _DEFAULT,
        send_read_receipts: bool = False,
        bridge: FakeBridge | None = None,
    ) -> LiveGateway:
        gateway_config = replace(
            config.gateway, listen_port=0, send_read_receipts=send_read_receipts
        )
        app_config = replace(config, gateway=gateway_config)
        resolved_bridge = bridge if bridge is not None else FakeBridge()
        runtime: FakeRuntime | None
        if agent is _DEFAULT:
            runtime = FakeRuntime()
            resolved_agent: object = runtime
        else:
            runtime = agent if isinstance(agent, FakeRuntime) else None
            resolved_agent = agent

        app = build_app(
            app_config,
            token=TOKEN,
            agent=resolved_agent,
            bridge=resolved_bridge,
        )
        app.start()
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{app.server.bound_port}", timeout=5.0
        )
        live_gateway = LiveGateway(app, resolved_bridge, runtime, client, thread)
        created.append(live_gateway)
        assert wait_until(lambda: health_ok(client), timeout=3.0)
        return live_gateway

    yield factory

    for gateway in created:
        gateway.client.close()
        gateway.app.stop()
        gateway.thread.join(timeout=2)
        assert not gateway.thread.is_alive()


def test_dm_command_is_formatted_and_sent(live) -> None:
    gateway = live()

    response = gateway.post("m-ping", DM_CHAT, DM_CHAT, "/ping")

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert wait_until(lambda: len(gateway.bridge.sent) == 1)
    assert gateway.app.outbound.wait_idle(2.0)
    assert len(gateway.bridge.sent) == 1
    chat_id, text, key = gateway.bridge.sent[0]
    assert (chat_id, key) == (DM_CHAT, "m-ping:0")
    assert "pong" in text.lower()
    assert gateway.bridge.typing_events == []
    assert TOKEN not in response.text


def test_dm_text_goes_to_agent_and_strips_reasoning(live) -> None:
    runtime = FakeRuntime(
        replies=("<reasoning>internal</reasoning>Halo dari AI",)
    )
    gateway = live(agent=runtime)

    response = gateway.post("m-agent", DM_CHAT, DM_CHAT, "halo sanka")

    assert response.status_code == 202
    assert wait_until(lambda: len(gateway.bridge.sent) == 1)
    assert gateway.app.outbound.wait_idle(2.0)

    assert len(runtime.calls) == 1
    message, role = runtime.calls[0]
    assert role is Role.OWNER
    assert message.text == "halo sanka"
    assert gateway.bridge.sent == [(DM_CHAT, "Halo dari AI", "m-agent:0")]
    assert ("composing" in [state for _, state in gateway.bridge.typing_events])
    assert ("paused" in [state for _, state in gateway.bridge.typing_events])

    sent_text = gateway.bridge.sent[0][1]
    assert "reasoning" not in sent_text
    assert "internal" not in sent_text


def test_group_mention_reaches_agent(live) -> None:
    gateway = live()

    response = gateway.post(
        "m-group",
        GROUP_CHAT,
        PARTICIPANT,
        "halo semua",
        mentions=[BOT_ID],
        is_group=True,
    )

    assert response.status_code == 202
    assert wait_until(lambda: len(gateway.bridge.sent) == 1)
    assert gateway.app.outbound.wait_idle(2.0)
    assert gateway.runtime is not None
    assert len(gateway.runtime.calls) == 1
    _, role = gateway.runtime.calls[0]
    assert role is Role.COMMITTEE
    assert gateway.bridge.sent[0][0] == GROUP_CHAT


def test_group_without_mention_is_ignored(live) -> None:
    gateway = live()

    response = gateway.post(
        "m-group-ignore", GROUP_CHAT, PARTICIPANT, "halo semua"
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "not_mentioned",
    }
    assert not settled(lambda: bool(gateway.bridge.sent))
    assert gateway.bridge.typing_events == []
    assert gateway.runtime is not None
    assert gateway.runtime.calls == []


def test_duplicate_message_id_is_not_reprocessed(live) -> None:
    gateway = live()

    first = gateway.post("m-dup", DM_CHAT, DM_CHAT, "/ping")
    assert first.status_code == 202
    assert wait_until(lambda: len(gateway.bridge.sent) == 1)
    assert gateway.app.outbound.wait_idle(2.0)

    second = gateway.post("m-dup", DM_CHAT, DM_CHAT, "/help")

    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert gateway.app.outbound.wait_idle(1.0)
    assert len(gateway.bridge.sent) == 1
    assert "pong" in gateway.bridge.sent[0][1].lower()


def test_read_receipts_are_optional(live) -> None:
    gateway = live(send_read_receipts=True)

    response = gateway.post("m-read", DM_CHAT, DM_CHAT, "/ping")

    assert response.status_code == 202
    assert wait_until(lambda: len(gateway.bridge.reads) == 1)
    assert gateway.bridge.reads == [(DM_CHAT, "m-read")]
    assert wait_until(lambda: len(gateway.bridge.sent) == 1)


def test_no_reasoning_or_token_in_logs(live, caplog) -> None:
    runtime = FakeRuntime(
        replies=(
            "<thought>secret-plan</thought>"
            "<tool_call>{\"x\":1}</tool_call>Halo!",
        )
    )
    gateway = live(agent=runtime)
    caplog.set_level(logging.DEBUG)

    response = gateway.post(
        "m-secret",
        DM_CHAT,
        DM_CHAT,
        "isi rahasia pengguna",
    )

    assert response.status_code == 202
    assert wait_until(lambda: len(gateway.bridge.sent) == 1)
    assert gateway.app.outbound.wait_idle(2.0)

    assert gateway.bridge.sent[0][1] == "Halo!"
    assert "secret-plan" not in caplog.text
    assert "isi rahasia pengguna" not in caplog.text
    assert TOKEN not in caplog.text
    assert DM_CHAT not in caplog.text


def test_build_app_rejects_non_loopback_host(config: SankaConfig) -> None:
    bad_gateway = replace(config.gateway, listen_host="0.0.0.0", listen_port=0)
    bad_config = replace(config, gateway=bad_gateway)

    with pytest.raises(ValueError):
        build_app(bad_config, token=TOKEN)


def test_degraded_mode_still_answers_commands(live) -> None:
    gateway = live(agent=None)

    ping = gateway.post("m-degraded-ping", DM_CHAT, DM_CHAT, "/ping")
    assert ping.status_code == 202
    assert wait_until(lambda: len(gateway.bridge.sent) == 1)

    text = gateway.post("m-degraded-agent", DM_CHAT, DM_CHAT, "halo")
    assert text.status_code == 202
    assert wait_until(lambda: len(gateway.bridge.sent) == 2)
    assert gateway.app.outbound.wait_idle(2.0)

    sent = [item[1] for item in gateway.bridge.sent]
    assert "pong" in sent[0].lower()
    assert sent[1] == "Fitur AI belum aktif."
    assert gateway.runtime is None


def test_build_app_reads_token_from_environment(
    config: SankaConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.gateway.auth_token_env, "env-token")
    app_config = replace(config, gateway=replace(config.gateway, listen_port=0))
    bridge = FakeBridge()
    app = build_app(app_config, agent=None, bridge=bridge)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    try:
        app.start()
        thread.start()
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{app.server.bound_port}", timeout=5.0
        )
        try:
            assert wait_until(lambda: health_ok(client))
            response = client.post(
                "/v1/inbound",
                json={
                    "message_id": "m-env",
                    "chat_id": DM_CHAT,
                    "sender_id": DM_CHAT,
                    "text": "/ping",
                },
                headers={"X-Sanka-Token": "env-token"},
            )
            assert response.status_code == 202
            assert wait_until(lambda: bool(bridge.sent))
        finally:
            client.close()
    finally:
        app.stop()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_build_app_rejects_empty_token(config: SankaConfig) -> None:
    app_config = replace(config, gateway=replace(config.gateway, listen_port=0))
    with pytest.raises(ValueError):
        build_app(app_config, token="", agent=None)
