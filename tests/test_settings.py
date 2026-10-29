"""Kontrak settings: baca/ubah dokumen, validasi, mirror, audit, approval, reload."""

from __future__ import annotations

import asyncio

import yaml
from sqlalchemy import select

from sanka.engine.schema import config_changes
from sanka.engine.tools import execute
from support import make_runtime

CHAT = "cli:local"


def run(env, action: str, **args) -> str:
    ctx = env.agent.tool_context(
        CHAT, "local", (env.config.roles.default_role(),), env.gateway
    )
    return asyncio.run(execute(env.agent.tools.by_name, "settings", {"action": action, **args}, ctx))


def test_schema_and_get(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)

    schema = run(env, "schema")
    assert "engine.llm.model" in schema
    assert "engine.memory.max_items" in schema

    assert run(env, "get", file="engine", path="default_role").strip() == "member"
    assert "Kamu Sanka" in run(env, "get", file="persona")


def test_set_soul_language_updates_runtime_and_mirror(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    assert env.agent.soul.language == "id"

    result = run(env, "set", file="soul", path="language", value="en")

    assert result.startswith("ok:")
    assert env.agent.soul.language == "en"
    mirror = yaml.safe_load(env.paths.soul.read_text(encoding="utf-8"))
    assert mirror["language"] == "en"
    assert env.agent.soul.language == "en"


def test_invalid_engine_value_does_not_change_document(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    before = env.documents.get("engine")

    result = run(env, "set", file="engine", path="memory.backend", value="bogus")

    assert result.startswith("error:")
    assert env.documents.get("engine") == before
    assert env.agent.config.memory.backend == "mem0"


def test_write_persona_changes_prompt_and_mirror(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)

    result = run(env, "write", file="persona", content="Persona baru yang tenang.")

    assert result.startswith("ok:")
    assert env.agent.soul.persona == "Persona baru yang tenang."
    assert env.paths.soul_md.read_text(encoding="utf-8") == "Persona baru yang tenang."
    assert "Persona baru yang tenang." in env.agent.soul.persona


def test_write_role_changes_permissions(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)

    content = 'name: member\ntools: ["read"]\ncommands: ["ping"]\n'
    result = run(env, "write", file="role", role="member", content=content)

    assert result.startswith("ok:")
    assert env.agent.config.roles.default_role().tools == ("read",)
    assert (env.paths.roles / "member.yaml").read_text(encoding="utf-8") == content


def test_sensitive_key_rejected(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    result = run(env, "set", file="engine", path="llm.api_key", value="secret")
    assert "sensitif" in result


def test_settings_change_waits_for_approval(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch)
    before = env.documents.get("soul")

    pending = run(env, "set", file="soul", path="language", value="en")

    assert "pending approval #1" in pending
    assert env.documents.get("soul") == before
    assert env.agent.soul.language == "id"

    summary = asyncio.run(env.agent.apply_settings_payload(
        env.agent.approvals.get(1).payload
    ))
    assert env.agent.soul.language == "en"
    assert "reloaded" in summary
    rows = env.db.fetch_all(select(config_changes))
    assert rows and rows[-1]["file"] == "soul"


def test_restart_only_key_reports_restart(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    before = env.agent.config.workspace

    result = run(env, "set", file="host", path="workspace", value="/tmp/lain")

    assert "restart required" in result
    assert env.agent.config.workspace == before
    assert "workspace" in result


def test_registry_has_settings_tool(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch)
    assert "settings" in env.agent.tools.names()
