"""Language selector contract: YAML `soul.language`, /language command, prompt line."""

from __future__ import annotations

import asyncio

import yaml

from sanka.engine.prompts import system_prompt
from sanka.engine.roles import Role
from sanka.engine.soul import Soul, normalize_language
from support import inbound_message, make_agent, make_runtime

CHAT = "cli:local"


def run_command(env, name: str, args: str = "") -> str:
    roles = (env.config.roles.default_role(),)
    return asyncio.run(
        env.agent._command(
            name, args, CHAT, "local", roles, env.gateway, inbound_message(f"/{name}")
        )
    )


def run_language(env, args: str) -> str:
    return run_command(env, "language", args)


def test_language_command_writes_soul_document_and_mirror(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    assert env.agent.soul.language == "id"

    result = run_language(env, "en")

    assert result == "🗣️ Reply language set to en."
    assert env.agent.soul.language == "en"
    assert "language: en" in env.documents.get("soul")
    mirror = yaml.safe_load(env.paths.config.read_text(encoding="utf-8"))
    assert mirror["soul"]["language"] == "en"
    assert run_language(env, "") == "🗣️ Reply language: en. Available: en, id."
    assert run_command(env, "ping") == "🏓 Pong! Sanka is alive and ready."

    assert run_language(env, "id") == "🗣️ Bahasa balasan diatur ke id."
    assert env.agent.soul.language == "id"
    assert run_command(env, "ping") == "🏓 Pong! Sanka hidup dan siap."


def test_language_command_rejects_unknown_language(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    result = run_language(env, "xx")
    assert result == "error: bahasa 'xx' tidak tersedia. Pilih: en, id."
    assert env.agent.soul.language == "id"


def test_member_without_permission_cannot_switch(tmp_path) -> None:
    env = make_agent(
        tmp_path,
        roles={"member": Role("member", tools=("read",), commands=("help",))},
    )
    result = run_language(env, "en")
    assert "tidak diizinkan" in result
    assert env.agent.soul.language == "id"


def test_normalize_language_aliases() -> None:
    assert normalize_language("en") == "en"
    assert normalize_language("English") == "en"
    assert normalize_language("id") == "id"
    assert normalize_language("auto") == "id"


def test_system_prompt_language_follows_the_yaml_value(tmp_path) -> None:
    env = make_agent(tmp_path, soul=Soul(persona="p", language="en"))
    prompt = system_prompt(env.config, (Role("member"),))
    assert "Reply in English." in prompt
    assert "Indonesian" not in prompt
