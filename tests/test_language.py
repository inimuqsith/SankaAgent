"""Language selector contract: YAML `soul.language`, /language command, prompt line."""

from __future__ import annotations

import asyncio

import yaml

from sanka.engine.prompts import system_prompt
from sanka.engine.roles import Role
from sanka.engine.soul import Soul, normalize_language
from support import inbound_message, make_agent, make_runtime

CHAT = "cli:local"


def run_language(env, args: str) -> str:
    roles = (env.config.roles.default_role(),)
    return asyncio.run(
        env.agent._command("language", args, CHAT, "local", roles, env.gateway, inbound_message("/language"))
    )


def test_language_command_writes_soul_document_and_mirror(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    assert env.agent.soul.language == "id"

    result = run_language(env, "en")

    assert result.startswith("🗣️")
    assert env.agent.soul.language == "en"
    assert "language: en" in env.documents.get("soul")
    mirror = yaml.safe_load(env.paths.config.read_text(encoding="utf-8"))
    assert mirror["soul"]["language"] == "en"
    assert "en" in run_language(env, "")
    assert run_language(env, "id").startswith("🗣️")
    assert env.agent.soul.language == "id"


def test_language_command_rejects_unknown_language(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    result = run_language(env, "jv")
    assert result.startswith("error:")
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
