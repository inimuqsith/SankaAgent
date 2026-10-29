"""Kontrak Agent: satu turn otonom tanpa transport, approval, memori, dan bubble."""

from __future__ import annotations

import asyncio

from sanka.engine.config import ApprovalConfig, HumanizeConfig
from sanka.engine.tools import execute
from support import (
    FakeGateway,
    FakeLLM,
    FakeMemory,
    inbound_message,
    make_agent,
    role,
)

DM = "628111@s.whatsapp.net"
GROUP = "123@g.us"


def handle(env, inbound):
    return asyncio.run(env.agent.handle(inbound, env.gateway))


def test_dm_reply_is_delivered_and_recorded(tmp_path) -> None:
    env = make_agent(tmp_path)

    handle(env, inbound_message("halo", chat_id=DM))

    assert env.gateway.sent
    chat_id, text, reply = env.gateway.sent[0]
    assert chat_id == DM and text == "halo juga"
    assert reply is not None
    ids = [item.id for item in env.windows.get(DM)]
    assert ids[0] == "m-halo" and len(ids) == 2
    assert env.llm.calls == 1 and env.llm.decisions == 1


def test_group_without_call_is_ignored_without_llm(tmp_path) -> None:
    env = make_agent(tmp_path)

    handle(env, inbound_message("eh tiket bola", chat_id=GROUP, is_dm=False))

    assert env.gateway.sent == []
    assert env.llm.calls == 0


def test_group_mention_replies(tmp_path) -> None:
    env = make_agent(tmp_path)

    handle(
        env,
        inbound_message("sanka tolong", chat_id=GROUP, is_dm=False, is_mention=True),
    )

    assert env.gateway.sent and env.gateway.sent[0][0] == GROUP


def test_command_skips_decision_and_uses_handler(tmp_path) -> None:
    env = make_agent(tmp_path)

    handle(env, inbound_message("/ping", chat_id=DM))

    assert env.llm.decisions == 0
    assert "Pong" in env.gateway.sent[0][1]


def test_unknown_command_is_reported(tmp_path) -> None:
    env = make_agent(tmp_path)
    handle(env, inbound_message("/tidakada", chat_id=DM))
    assert "tidak dikenal" in env.gateway.sent[0][1]


def test_backlog_is_archived_not_answered(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.gateway.backlog_seconds = 1.0
    from datetime import datetime, timedelta

    inbound = inbound_message("lama", chat_id=DM)
    old = inbound.message
    object.__setattr__(old, "when", datetime.now() - timedelta(minutes=10))

    handle(env, inbound)

    assert env.gateway.sent == []
    assert env.agent.backlog[DM] == 1


def test_stale_burst_is_not_answered_twice(tmp_path) -> None:
    env = make_agent(tmp_path)
    inbound = inbound_message("halo", chat_id=DM, message_id="same")
    env.agent.answered[DM] = frozenset({"same"})

    handle(env, inbound)

    assert env.gateway.sent == []


def test_auto_capture_runs_for_memory_role(tmp_path) -> None:
    memory = FakeMemory()
    env = make_agent(tmp_path, memory=memory)

    handle(env, inbound_message("aku suka kopi tanpa gula", chat_id=DM))
    if env.agent.tasks:
        asyncio.run(asyncio.gather(*env.agent.tasks))

    assert memory.captures
    call = memory.captures[0]
    assert call["person_id"] == "628111"
    assert call["chat_id"] == DM
    assert call["audience"] == "private"


def test_memory_block_and_runtime_reach_prompt(tmp_path) -> None:
    memory = FakeMemory(block="Long-term memory (cross-chat):\n- [person] suka kopi")
    env = make_agent(tmp_path, memory=memory)

    handle(env, inbound_message("halo", chat_id=DM))

    assert memory.context_calls and memory.context_calls[0]["person_id"] == "628111"
    assert "suka kopi" in env.llm.system
    assert "## Self" in env.llm.system
    assert "Reply in Indonesian" in env.llm.system
    assert "## Fake interface" in env.llm.system
    assert "WhatsApp" not in env.llm.system


def test_humanize_splits_bubbles(tmp_path) -> None:
    env = make_agent(
        tmp_path,
        answer="satu\n\n---\n\ndua",
        humanize=HumanizeConfig(enabled=True, min_delay=0, max_delay=0, between_delay=0),
    )

    handle(env, inbound_message("halo", chat_id=DM))

    assert [text for _chat, text, _reply in env.gateway.sent] == ["satu", "dua"]


def test_shell_requires_approval_then_approve_executes(tmp_path) -> None:
    class ToolLLM(FakeLLM):
        async def run(self, system, content, specs, ctx, session=None):
            self.ctx = ctx
            self.call = await execute(
                {handler.name: handler for handler in specs},
                "shell",
                {"command": "printf sanka-ok"},
                ctx,
            )
            return f"tool: {self.call}"

    env = make_agent(
        tmp_path,
        roles={
            "owner": role("owner", tools=("*",), commands=("*",)),
            "member": role("member", tools=("shell",), commands=("*",)),
        },
        approvals=ApprovalConfig(mode="yolo", sensitive_tools=("shell",)),
    )
    env.agent.llm = ToolLLM()

    handle(env, inbound_message("jalankan shell", chat_id=DM))

    reply = env.gateway.sent[0][1]
    assert "pending approval #1" in reply
    pending = env.agent.approvals.pending(DM, "628111")
    assert len(pending) == 1

    from sanka.engine.commands.approve import COMMAND as APPROVE

    inbound = inbound_message("/approve 1", chat_id=DM)
    ctx = env.agent.command_context(DM, "628111", (role("member"),), env.gateway, "1", inbound)
    result = asyncio.run(APPROVE.fn(ctx))

    assert "sanka-ok" in result
    assert env.agent.approvals.get(1).status == "approved"


def test_approve_rejects_other_sender(tmp_path) -> None:
    env = make_agent(tmp_path)
    async def create():
        return await env.agent.approvals.create(
            kind="tool",
            chat_id=DM,
            sender="999",
            tool="shell",
            args={"command": "echo x"},
            summary="shell",
            roles=("member",),
        )

    approval = asyncio.run(create())
    from sanka.engine.commands.approve import COMMAND as APPROVE

    ctx = env.agent.command_context(DM, "628111", (role("member"),), env.gateway, str(approval.id))
    result = asyncio.run(APPROVE.fn(ctx))
    assert "pengirim lain" in result
    assert env.agent.approvals.get(approval.id).status == "pending"


def test_send_text_tool_wrapper_reports_ok(tmp_path) -> None:
    env = make_agent(tmp_path)
    result = asyncio.run(env.agent.send_text("lain", "halo", gateway=env.gateway))
    assert result.startswith("ok:")
    assert env.gateway.sent[0][0] == "lain"


def test_typing_events_around_turn(tmp_path) -> None:
    env = make_agent(tmp_path)
    handle(env, inbound_message("halo", chat_id=DM))
    assert env.gateway.typing_events[0] == (DM, True)
    assert env.gateway.typing_events[-1] == (DM, False)


def test_partial_send_does_not_mark_answered(tmp_path) -> None:
    from sanka.engine.inbound import GatewayError

    class FailingGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def send_text(self, chat_id, text, *, reply=None):
            self.calls += 1
            if self.calls == 2:
                raise GatewayError("boom")
            return await super().send_text(chat_id, text, reply=reply)

    gateway = FailingGateway()
    env = make_agent(
        tmp_path,
        answer="satu\n\n---\n\ndua",
        gateway=gateway,
        humanize=HumanizeConfig(enabled=True, min_delay=0, max_delay=0, between_delay=0),
    )
    inbound = inbound_message("halo", chat_id=DM, message_id="burst-1")

    handle(env, inbound)

    assert gateway.calls == 2
    assert env.agent.answered.get(DM, frozenset()) == frozenset()
