"""Runtime tests with a fake client; no network, no WhatsApp."""

from __future__ import annotations

from dataclasses import dataclass, field

from sanka.agent.client import AgentClientError, ChatResponse
from sanka.agent.runtime import AgentRuntime
from sanka.config import SankaConfig
from sanka.models import InboundMessage
from sanka.policy import RateLimiter, Role
from sanka.sessions import SessionStore


@dataclass
class FakeClient:
    content: str = "Halo dari Sanka"
    error: AgentClientError | None = None
    calls: list[list[dict[str, str]]] = field(default_factory=list)

    def chat(self, messages, tools=None):
        self.calls.append([dict(message) for message in messages])
        if self.error is not None:
            raise self.error
        if self.content:
            yield self.content
        return ChatResponse(content=self.content, tool_calls=())


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def message(text: str, *, sender: str = "628111111111") -> InboundMessage:
    return InboundMessage(
        chat_id="chat-1", sender_id=sender, text=text, is_group=False
    )


def runtime(
    config: SankaConfig, client: FakeClient, **kwargs
) -> AgentRuntime:
    return AgentRuntime(
        config,
        client_factory=lambda session_id: client,
        **kwargs,
    )


def test_handle_returns_formatted_text_and_stores_history(
    config: SankaConfig,
) -> None:
    client = FakeClient()
    store = SessionStore(config.limits.session_idle_ttl_minutes * 60)
    agent = runtime(config, client, store=store)

    reply = agent.handle(message("Halo"), Role.COMMITTEE)

    assert reply.messages == ("Halo dari Sanka",)
    assert client.calls[0][0]["role"] == "system"
    assert client.calls[0][-1] == {"role": "user", "content": "Halo"}
    assert store.history("chat-1") == [
        {"role": "user", "content": "Halo"},
        {"role": "assistant", "content": "Halo dari Sanka"},
    ]
    agent.shutdown()


def test_second_turn_receives_previous_history(config: SankaConfig) -> None:
    client = FakeClient(content="Jawaban")
    agent = runtime(config, client)

    agent.handle(message("pertama"), Role.COMMITTEE)
    agent.handle(message("kedua"), Role.COMMITTEE)

    messages = client.calls[1]
    assert messages[-1] == {"role": "user", "content": "kedua"}
    assert {"role": "assistant", "content": "Jawaban"} in messages
    assert {"role": "user", "content": "pertama"} in messages
    agent.shutdown()


def test_reasoning_is_not_stored_or_resent(config: SankaConfig) -> None:
    client = FakeClient(content="<reasoning>rahasia</reasoning>Halo")
    store = SessionStore(config.limits.session_idle_ttl_minutes * 60)
    agent = runtime(config, client, store=store)

    reply = agent.handle(message("Halo"), Role.COMMITTEE)
    agent.handle(message("lagi"), Role.COMMITTEE)

    assert reply.messages == ("Halo",)
    assert store.history("chat-1")[1] == {
        "role": "assistant",
        "content": "Halo",
    }
    history_text = " ".join(
        str(entry.get("content", "")) for entry in client.calls[1]
    )
    assert "rahasia" not in history_text
    agent.shutdown()


def test_rate_limit_stops_before_calling_client(config: SankaConfig) -> None:
    limiter = RateLimiter(1, 10, clock=FakeClock())
    client = FakeClient()
    agent = runtime(config, client, limiter=limiter)

    first = agent.handle(message("satu"), Role.COMMITTEE)
    second = agent.handle(message("dua"), Role.COMMITTEE)

    assert first.messages == ("Halo dari Sanka",)
    assert "Batas pesan" in second.messages[0]
    assert len(client.calls) == 1
    agent.shutdown()


def test_client_error_returns_friendly_reply(config: SankaConfig) -> None:
    client = FakeClient(error=AgentClientError("upstream failed", status_code=500))
    store = SessionStore(config.limits.session_idle_ttl_minutes * 60)
    agent = runtime(config, client, store=store)

    reply = agent.handle(message("Halo"), Role.COMMITTEE)

    assert "bermasalah" in reply.messages[0]
    assert "upstream" not in reply.messages[0]
    assert store.history("chat-1") == []
    agent.shutdown()


def test_reset_clears_context(config: SankaConfig) -> None:
    client = FakeClient(content="Jawaban")
    store = SessionStore(config.limits.session_idle_ttl_minutes * 60)
    agent = runtime(config, client, store=store)

    agent.handle(message("pertama"), Role.COMMITTEE)
    agent.reset("chat-1")
    agent.handle(message("kedua"), Role.COMMITTEE)

    messages = client.calls[1]
    assert {"role": "user", "content": "pertama"} not in messages
    assert [entry["role"] for entry in messages] == ["system", "user"]
    agent.shutdown()
