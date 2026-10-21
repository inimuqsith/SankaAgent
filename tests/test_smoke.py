from __future__ import annotations

from pathlib import Path

import pytest
from neonize.proto.Neonize_pb2 import Message
from neonize.utils import extract_text

from sanka.config import (
    Config,
    LLMConfig,
    RoleConfig,
    SandboxConfig,
    WhatsAppConfig,
    load_config,
)
from sanka.format import format_reply
from sanka.permissions import allows, role_for
from sanka.tools.web_search import searx_hits
from sanka.wa import should_process, system_prompt

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


def media_event(is_dm: bool) -> Message:
    ev = Message()
    ev.Info.MessageSource.Chat.User = "628999999999" if is_dm else "1234567890"
    ev.Info.MessageSource.Chat.Server = "s.whatsapp.net" if is_dm else "g.us"
    ev.Info.MessageSource.Sender.User = "628999999999" if is_dm else "628222222222"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.imageMessage.mimetype = "image/jpeg"
    return ev


def test_captionless_media_routing() -> None:
    config = make_config()

    def routed(ev: Message, is_dm: bool) -> bool:
        chat = "628999999999@s.whatsapp.net" if is_dm else "1234567890@g.us"
        return should_process(
            ev,
            chat,
            extract_text(ev.Message),
            is_dm=is_dm,
            bot_name=config.whatsapp.bot_name,
            require_mention=config.whatsapp.require_mention,
            bots=BOT,
            has_media=True,
        )

    assert routed(media_event(True), is_dm=True) is True
    assert routed(media_event(False), is_dm=False) is False
    addressed = media_event(False)
    addressed.Message.imageMessage.contextInfo.mentionedJID.append("628567770837@s.whatsapp.net")
    assert routed(addressed, is_dm=False) is True


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


def test_load_config_resolves_paths_from_config_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    target = tmp_path / "config.yaml"
    target.write_text(
        """
persona: p
llm:
  base_url: "http://x"
  model: m
  api_key_env: SANKA_TEST_KEY
workspace: ".sanka/workspace"
whatsapp:
  session_db: ".sanka/whatsapp.db"
  history_db: ".sanka/history.db"
roles:
  owner:
    tools: ["*"]
default_role: owner
""",
        encoding="utf-8",
    )

    loaded = load_config(str(target))

    assert loaded.workspace == tmp_path / ".sanka/workspace"
    assert loaded.whatsapp.session_db == tmp_path / ".sanka/whatsapp.db"
    assert loaded.whatsapp.history_db == tmp_path / ".sanka/history.db"
    assert loaded.memory.path == tmp_path / ".sanka/memory.db"
    assert loaded.skills.dir == tmp_path / ".sanka/skills"


def test_load_config_rejects_legacy_home_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    target = tmp_path / "config.yaml"
    target.write_text(
        """
persona: p
llm:
  base_url: "http://x"
  model: m
  api_key_env: SANKA_TEST_KEY
workspace: "~/.sanka/workspace"
roles:
  owner:
    tools: ["*"]
default_role: owner
""",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        load_config(str(target))


def test_system_prompt_lists_bot_numbers() -> None:
    config = make_config()

    prompt = system_prompt(config, "owner", {"628567770837", "73041086251252"})

    assert "628567770837" in prompt
    assert "73041086251252" in prompt


def test_system_prompt_includes_memory_and_skills() -> None:
    config = make_config()

    prompt = system_prompt(
        config,
        "owner",
        BOT,
        memory="Memori tersimpan:\n- #1 [preference] suka kopi",
        skills="Skill lokal:\n- deploy: Deploy aman.",
    )

    assert "suka kopi" in prompt
    assert "deploy: Deploy aman." in prompt
    assert prompt.index("suka kopi") < prompt.index("deploy: Deploy aman.")


def test_system_prompt_places_actions_between_memory_and_skills() -> None:
    config = make_config()

    prompt = system_prompt(
        config,
        "owner",
        BOT,
        memory="Memori tersimpan:\n- suka kopi",
        actions="Aksi lintas chat terakhir:\n- kirim stiker",
        skills="Skill lokal:\n- deploy: Deploy aman.",
    )

    assert "Aksi lintas chat terakhir" in prompt
    assert (
        prompt.index("suka kopi")
        < prompt.index("Aksi lintas chat terakhir")
        < prompt.index("deploy: Deploy aman.")
    )


def test_searx_hits_drop_entries_without_url() -> None:
    payload = {
        "results": [
            {"title": "A", "url": "https://a.test", "content": "isi a", "engine": "brave"},
            {"title": "B", "url": "", "content": "tanpa url"},
            {"title": "C", "url": "https://c.test", "content": "isi c"},
        ]
    }

    assert searx_hits(payload, 1) == [{"title": "A", "href": "https://a.test", "body": "isi a"}]
    assert [hit["href"] for hit in searx_hits(payload, 5)] == ["https://a.test", "https://c.test"]


def test_web_search_config_from_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    target = tmp_path / "config.yaml"
    target.write_text(
        "persona: p\n"
        "llm:\n  api_key_env: SANKA_TEST_KEY\n"
        f"workspace: \"{tmp_path / 'ws'}\"\n"
        f"whatsapp:\n  session_db: \"{tmp_path / 's.db'}\"\n"
        "roles:\n  member: {}\n"
        "default_role: member\n"
        "web_search:\n  base_url: \"http://127.0.0.1:8080/\"\n  timeout_seconds: 7\n",
        encoding="utf-8",
    )

    config = load_config(str(target))

    assert config.web_search.base_url == "http://127.0.0.1:8080"
    assert config.web_search.timeout_seconds == 7


def test_memory_and_skills_config_from_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    target = tmp_path / "config.yaml"
    target.write_text(
        "persona: p\n"
        "llm:\n  api_key_env: SANKA_TEST_KEY\n"
        f"workspace: \"{tmp_path / 'ws'}\"\n"
        f"whatsapp:\n  session_db: \"{tmp_path / 's.db'}\"\n"
        "roles:\n  member: {}\n"
        "default_role: member\n"
        f"memory:\n  path: \"{tmp_path / 'memory.db'}\"\n  max_items: 7\n  max_chars: 900\n  auto_extract: false\n"
        f"skills:\n  dir: \"{tmp_path / 'skills'}\"\n  max_index_chars: 800\n",
        encoding="utf-8",
    )

    config = load_config(str(target))

    assert config.memory.path == tmp_path / "memory.db"
    assert config.memory.max_items == 7
    assert config.memory.max_chars == 900
    assert config.memory.auto_extract is False
    assert config.skills.dir == tmp_path / "skills"
    assert config.skills.max_index_chars == 800
