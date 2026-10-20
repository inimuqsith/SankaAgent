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


def message(
    text: str,
    *,
    sender: str = "628111111111",
    chat_id: str = "chat-1",
    is_group: bool = False,
    sender_name: str = "",
) -> InboundMessage:
    return InboundMessage(
        chat_id=chat_id,
        sender_id=sender,
        text=text,
        is_group=is_group,
        sender_name=sender_name,
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


class FakeGroupContext:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.calls: list[str] = []

    def context(self, chat_id: str) -> str | None:
        self.calls.append(chat_id)
        return self.text


def test_group_turn_injects_ambient_context(config: SankaConfig) -> None:
    client = FakeClient()
    provider = FakeGroupContext("[10:00] Budi: pagi semua")
    agent = runtime(config, client, group_context=provider)

    agent.handle(
        message("halo sanka", chat_id="120363000000000000@g.us", is_group=True),
        Role.COMMITTEE,
    )

    system = client.calls[0][0]["content"]
    assert "<group_context>" in system
    assert "[10:00] Budi: pagi semua" in system
    assert provider.calls == ["120363000000000000@g.us"]
    agent.shutdown()


def test_dm_turn_does_not_read_group_context(config: SankaConfig) -> None:
    client = FakeClient()
    provider = FakeGroupContext("[10:00] Budi: pagi semua")
    agent = runtime(config, client, group_context=provider)

    agent.handle(message("halo"), Role.COMMITTEE)

    system = client.calls[0][0]["content"]
    assert "<group_context>" not in system
    assert provider.calls == []
    agent.shutdown()


def test_image_attachment_becomes_content_parts(
    config: SankaConfig, tmp_path
) -> None:
    import base64

    from sanka.attachments import AttachmentStore
    from sanka.models import InboundAttachment

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8Dwn4GBgYGJAQoAHgQCAZ7F0XQAAAAASUVORK5CYII="
    )
    store = AttachmentStore(
        str(tmp_path / "attachments"),
        max_bytes=1024,
        max_total_bytes=4096,
        retention_seconds=3600,
    )
    chat = "628111111111@s.whatsapp.net"
    record = store.save(
        [png],
        chat_id=chat,
        sender_id=chat,
        message_id="msg-1",
        kind="image",
        mime="image/png",
        name="",
        declared_size=len(png),
    )
    attachment = InboundAttachment(
        attachment_id=record.attachment_id,
        kind="image",
        mime="image/png",
        name=record.name,
        size=record.size,
    )
    client = FakeClient(content="Itu gambar merah.")
    store_sessions = SessionStore(config.limits.session_idle_ttl_minutes * 60)
    agent = runtime(
        config,
        client,
        store=store_sessions,
        attachments=store,
    )

    reply = agent.handle(
        InboundMessage(
            chat_id=chat,
            sender_id=chat,
            text="ini apa?",
            attachments=(attachment,),
        ),
        Role.COMMITTEE,
    )

    assert reply.messages == ("Itu gambar merah.",)
    content = client.calls[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "ini apa?" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    history = store_sessions.history(chat)
    assert isinstance(history[0]["content"], str)
    assert "base64" not in history[0]["content"]
    agent.shutdown()


def test_document_attachment_adds_metadata_block(
    config: SankaConfig, tmp_path
) -> None:
    from sanka.attachments import AttachmentStore
    from sanka.models import InboundAttachment

    store = AttachmentStore(
        str(tmp_path / "attachments"),
        max_bytes=1024,
        max_total_bytes=4096,
        retention_seconds=3600,
    )
    chat = "628111111111@s.whatsapp.net"
    record = store.save(
        [b"# catatan\n"],
        chat_id=chat,
        sender_id=chat,
        message_id="msg-1",
        kind="document",
        mime="text/markdown",
        name="catatan.md",
        declared_size=10,
    )
    attachment = InboundAttachment(
        attachment_id=record.attachment_id,
        kind="document",
        mime="text/markdown",
        name=record.name,
        size=record.size,
    )
    client = FakeClient(content="Siap.")
    agent = runtime(config, client, attachments=store)

    agent.handle(
        InboundMessage(
            chat_id=chat,
            sender_id=chat,
            text="baca ini",
            attachments=(attachment,),
        ),
        Role.COMMITTEE,
    )

    content = client.calls[0][-1]["content"]
    assert isinstance(content, str)
    assert record.attachment_id in content
    assert "attachment_read" in content
    assert "catatan.md" in content
    agent.shutdown()


def test_attachment_errors_reach_the_model(config: SankaConfig) -> None:
    client = FakeClient(content="Maaf, file gagal diunduh.")
    agent = runtime(config, client)

    agent.handle(
        InboundMessage(
            chat_id="628111111111@s.whatsapp.net",
            sender_id="628111111111@s.whatsapp.net",
            text="baca ini",
            attachment_errors=("download_failed",),
        ),
        Role.COMMITTEE,
    )

    content = client.calls[0][-1]["content"]
    assert isinstance(content, str)
    assert "download_failed" in content
    assert "Jangan mengaku" in content
    agent.shutdown()
