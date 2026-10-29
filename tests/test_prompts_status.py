"""Kontrak prompt/status: scaffolding Inggris, language line, dan kartu status."""

from __future__ import annotations

import asyncio

from sanka.engine.prompts import self_block, system_prompt
from sanka.engine.roles import Role
from sanka.engine.runtime import RuntimeFacts
from sanka.engine.soul import Soul, language_line
from support import inbound_message, make_agent, make_runtime


def test_language_line_variants() -> None:
    assert "Indonesian" in language_line(Soul(language="id"))
    assert language_line(Soul(language="en")) == "Reply in English."
    assert "same language" in language_line(Soul(language="auto"))
    assert language_line(Soul(language="jv")) == "Reply in jv."


def test_system_prompt_is_english_with_self_and_roles(tmp_path) -> None:
    env = make_agent(tmp_path)
    prompt = system_prompt(
        env.config,
        (Role("member", description="Team member", context="Help the team."),),
        runtime=RuntimeFacts(mode="primary", db_label="postgresql://db/sanka"),
        gateway_hints="## Interface\n- test",
    )

    assert "## Self" in prompt
    assert "## Roles" in prompt
    assert "Help the team." in prompt
    assert "## Interface" in prompt
    assert "Reply in Indonesian" in prompt
    assert "You live in chat" in prompt
    assert "WhatsApp" not in prompt


def test_self_block_reports_runtime_facts(tmp_path) -> None:
    env = make_agent(tmp_path)
    runtime = RuntimeFacts(
        mode="primary",
        db_label="postgresql://db:5432/sanka",
        memory_status="mem0 (sanka_memories)",
        pending_approvals=2,
        gateways=("whatsapp", "cli"),
    )
    block = self_block(runtime, (Role("owner"),), env.config)
    assert "Mode: primary" in block
    assert "Surface: whatsapp, cli" in block
    assert "Pending approvals: 2" in block
    assert "postgresql://db:5432/sanka" in block


def test_status_and_whoami(tmp_path, monkeypatch) -> None:
    env = make_runtime(tmp_path, monkeypatch, settings_approval=False)
    agent = env.agent
    roles = (env.config.roles.default_role(),)

    status = asyncio.run(
        agent._command("status", "", "cli:local", "local", roles, env.gateway, inbound_message("/status"))
    )
    assert "Mode: standby" in status
    assert "Lease:" in status
    assert "Uptime:" in status
    assert "Database:" in status
    assert "Memory:" in status
    assert "Approvals: 0 pending" in status
    assert "Soul:" in status and "language id" in status

    who = asyncio.run(
        agent._command("whoami", "", "cli:local", "local", roles, env.gateway, inbound_message("/whoami"))
    )
    assert "Peran: Member" in who
    assert "Surface: fake" in who


def test_commands_requesting_llm_do_not_decide(tmp_path) -> None:
    env = make_agent(tmp_path)
    asyncio.run(env.agent.handle(inbound_message("/status", chat_id="628111@s.whatsapp.net"), env.gateway))
    assert env.llm.decisions == 0
    assert env.llm.calls == 0
