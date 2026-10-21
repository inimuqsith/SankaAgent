from __future__ import annotations

from pathlib import Path

from neonize.proto.Neonize_pb2 import Message
from neonize.utils import extract_text

from sanka.config import (
    Config,
    LLMConfig,
    RoleConfig,
    SandboxConfig,
    WhatsAppConfig,
)
from sanka.format import format_reply
from sanka.permissions import allows, role_for
from sanka.wa import should_process

BOT = {"628567770837"}


def make_config() -> Config:
    roles = {
        "owner": RoleConfig("owner", users=("628111111111",), tools=("*",), commands=("*",)),
        "member": RoleConfig(
            "member",
            tools=("read", "web_search", "mcp__*"),
            commands=("help", "ping", "reset"),
        ),
    }
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=Path("/tmp"),
        whatsapp=WhatsAppConfig(session_db=Path("/tmp/sanka-test.db")),
        roles=roles,
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )


def test_role_and_patterns() -> None:
    config = make_config()
    assert role_for("628111111111@s.whatsapp.net", config).name == "owner"
    assert role_for("628999999999@s.whatsapp.net", config).name == "member"
    assert role_for("628111111111:12@s.whatsapp.net", config).name == "owner"
    assert allows(("*",), "shell") is True
    assert allows(("read",), "shell") is False
    assert allows(("mcp__*",), "mcp__time__now") is True
    assert allows((), "read") is False


def group_event(text: str, mentioned: tuple[str, ...] = (), participant: str = "") -> Message:
    ev = Message()
    ev.Info.MessageSource.Chat.User = "1234567890"
    ev.Info.MessageSource.Chat.Server = "g.us"
    ev.Info.MessageSource.Sender.User = "628222222222"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.extendedTextMessage.text = text
    if mentioned:
        ev.Message.extendedTextMessage.contextInfo.mentionedJID.extend(mentioned)
    if participant:
        ev.Message.extendedTextMessage.contextInfo.participant = participant
    return ev


def test_group_routing() -> None:
    config = make_config()

    def routed(ev: Message, is_dm: bool = False) -> bool:
        chat = "1234567890@g.us" if not is_dm else "628999999999@s.whatsapp.net"
        return should_process(
            ev,
            chat,
            extract_text(ev.Message),
            is_dm=is_dm,
            bot_name=config.whatsapp.bot_name,
            require_mention=config.whatsapp.require_mention,
            bots=BOT,
        )

    assert routed(dm_event(), is_dm=True) is True
    assert routed(group_event("halo semua")) is False
    assert routed(group_event("pagi", mentioned=("628567770837@s.whatsapp.net",))) is True
    assert routed(group_event("@sanka tolong cek")) is True
    assert routed(group_event("jawab ya", participant="628567770837@s.whatsapp.net")) is True


def dm_event() -> Message:
    ev = Message()
    ev.Info.MessageSource.Chat.User = "628999999999"
    ev.Info.MessageSource.Chat.Server = "s.whatsapp.net"
    ev.Info.MessageSource.Sender.User = "628999999999"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.conversation = "halo"
    return ev


def test_format_reply() -> None:
    assert format_reply("## Judul\n**halo**\n- satu") == ["*Judul*\n*halo*\n• satu"]
    assert format_reply("<reasoning>diam</reasoning>halo") == ["halo"]
    assert format_reply("[dok](https://x.test)") == ["dok (https://x.test)"]
    long_text = "a" * 4000
    chunks = format_reply(long_text)
    assert len(chunks) == 2
    assert all(len(chunk) <= 3500 for chunk in chunks)
    assert format_reply("   ") == []
