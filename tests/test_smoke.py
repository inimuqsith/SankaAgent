from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from neonize.proto.Neonize_pb2 import Message
from neonize.utils import extract_text

from sanka.commands import REGISTRY as COMMANDS
from sanka.commands.help import help_command
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
from sanka.wa import named, should_process, system_prompt

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
    assert routed(group_event("@SANKAAAAAAAAA tolong cek")) is True
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


def test_help_renders_whatsapp_command_card() -> None:
    ctx = SimpleNamespace(commands=(COMMANDS.get("help"), COMMANDS.get("ping")), args="")
    lines = asyncio.run(help_command(ctx)).splitlines()

    assert lines[0] == "🧭 *Command Sanka*"
    assert "/ping (p) - 🏓 Cek Sanka hidup." in lines
    assert lines[-1] == "Ketik /help <command> untuk detail, atau ngobrol biasa tanpa slash."
    assert not any(line.startswith("Tool:") for line in lines)


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


def load_mcp_config(tmp_path: Path, monkeypatch, block: str) -> Config:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    target = tmp_path / "config.yaml"
    target.write_text(
        f"""
persona: p
llm:
  base_url: "http://x"
  model: m
  api_key_env: SANKA_TEST_KEY
workspace: ".sanka/workspace"
roles:
  owner:
    tools: ["*"]
default_role: owner
mcp:
  servers:
{block}
""",
        encoding="utf-8",
    )
    return load_config(str(target))


def test_mcp_server_accepts_stdio_and_url(tmp_path: Path, monkeypatch) -> None:
    config = load_mcp_config(
        tmp_path,
        monkeypatch,
        """    time:
      command: uvx
      args: ["mcp-server-time"]
    gworkspace:
      url: http://google-workspace-mcp:8080/mcp
      args: ["diabaikan"]
""",
    )

    assert config.mcp["time"].command == "uvx"
    assert config.mcp["time"].url is None
    assert config.mcp["gworkspace"].command is None
    assert config.mcp["gworkspace"].url == "http://google-workspace-mcp:8080/mcp"
    assert config.mcp["gworkspace"].args == ()


def test_mcp_server_rejects_url_without_http_scheme(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="gworkspace.*http:// atau https://"):
        load_mcp_config(
            tmp_path,
            monkeypatch,
            """    gworkspace:
      url: google-workspace-mcp:8080/mcp
""",
        )


def test_mcp_server_rejects_command_and_url_together(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="gworkspace.*jangan keduanya"):
        load_mcp_config(
            tmp_path,
            monkeypatch,
            """    gworkspace:
      command: uvx
      url: http://google-workspace-mcp:8080/mcp
""",
        )


def test_mcp_server_requires_command_or_url(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="gworkspace.*butuh 'command' atau 'url'"):
        load_mcp_config(
            tmp_path,
            monkeypatch,
            """    gworkspace:
      args: []
""",
        )


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


def test_human_config_sections_from_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    target = tmp_path / "config.yaml"
    target.write_text(
        "persona: p\n"
        "llm:\n"
        "  api_key_env: SANKA_TEST_KEY\n"
        "  temperature: 0.7\n"
        "  appraise_temperature: 0.2\n"
        f"workspace: \"{tmp_path / 'ws'}\"\n"
        f"whatsapp:\n  session_db: \"{tmp_path / 's.db'}\"\n"
        "roles:\n  member: {}\n"
        "default_role: member\n"
        "affect:\n"
        "  enabled: true\n"
        "  path: \".sanka/affect.db\"\n"
        "  sulk_minutes: 12\n"
        "  silence_max_ignores: 5\n"
        "  allow_owner_sulk: false\n"
        "  decay_hours: 2.5\n"
        "  max_appraisals_per_minute: 4\n"
        "gate:\n"
        "  enabled: true\n"
        "  reply_threshold: 0.5\n"
        "  llm_decide: true\n"
        "  decide_temperature: 0.4\n"
        "  max_decisions_per_minute: 7\n"
        "  reply_cooldown_seconds: 12\n"
        "humanize:\n"
        "  enabled: true\n"
        "  normal_chars: 180\n"
        "  normal_parts: 2\n"
        "  max_bubbles: 5\n"
        "  reaction_stickers: [\"stickers/a.webp\"]\n"
        "style:\n"
        "  enabled: true\n"
        "  path: \".sanka/style.json\"\n"
        "  max_chars: 640\n"
        "ambient:\n"
        "  enabled: true\n"
        "  research_per_day: 3\n"
        "  proactive: false\n",
        encoding="utf-8",
    )

    config = load_config(str(target))

    assert config.affect.enabled is True
    assert config.affect.path == tmp_path / ".sanka/affect.db"
    assert config.affect.sulk_minutes == 12
    assert config.affect.silence_max_ignores == 5
    assert config.affect.allow_owner_sulk is False
    assert config.affect.decay_hours == 2.5
    assert config.affect.max_appraisals_per_minute == 4
    assert config.gate.reply_threshold == 0.5
    assert config.gate.llm_decide is True
    assert config.gate.decide_temperature == 0.4
    assert config.gate.max_decisions_per_minute == 7
    assert config.gate.reply_cooldown_seconds == 12
    assert config.humanize.normal_chars == 180
    assert config.humanize.normal_parts == 2
    assert config.humanize.max_bubbles == 5
    assert config.humanize.reaction_stickers == ("stickers/a.webp",)
    assert not hasattr(config.humanize, "followups")
    assert not hasattr(config.humanize, "max_followups")
    assert not hasattr(config.gate, "max_consecutive_ignores")
    assert config.llm.temperature == 0.7
    assert config.llm.appraise_temperature == 0.2
    assert config.style.path == tmp_path / ".sanka/style.json"
    assert config.style.max_chars == 640
    assert config.ambient.research_per_day == 3
    assert config.ambient.proactive is False


def test_human_features_default_off() -> None:
    config = make_config()

    assert config.affect.enabled is False
    assert config.gate.enabled is False
    assert config.humanize.enabled is False
    assert config.style.enabled is False
    assert config.ambient.enabled is False


def test_config_false_string_disables_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    target = tmp_path / "config.yaml"
    target.write_text(
        "persona: p\n"
        "llm:\n  api_key_env: SANKA_TEST_KEY\n"
        f"workspace: \"{tmp_path / 'ws'}\"\n"
        f"whatsapp:\n  session_db: \"{tmp_path / 's.db'}\"\n"
        "roles:\n  member: {}\n"
        "default_role: member\n"
        "humanize:\n  enabled: \"false\"\n"
        "ambient:\n  proactive: \"false\"\n",
        encoding="utf-8",
    )

    config = load_config(str(target))

    assert config.humanize.enabled is False
    assert config.ambient.proactive is False


def test_elongated_name_is_detected() -> None:
    assert named("SANKAAAAAAAAA") is True
    assert named("SANNNKAAAAAA") is True
    assert named("sankaaaaaa") is True
    assert named("sankas") is False


def test_system_prompt_marks_dm_as_always_for_bot() -> None:
    config = make_config()

    dm = system_prompt(config, "member", BOT, is_dm=True)
    group = system_prompt(config, "member", BOT)

    assert "Chat ini pesan pribadi (DM): semua pesan di sini untukmu." in dm
    assert "Chat ini pesan pribadi (DM)" not in group
