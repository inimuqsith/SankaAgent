"""Kontrak guard lintas ruang: kutipan verbatim dari room lain tidak boleh terkirim."""

from __future__ import annotations

import asyncio
from datetime import datetime

from support import inbound_message, make_agent, role
from test_whatsapp_gateway import make_gateway

from sanka.engine import guard
from sanka.engine.context import ChatMessage
from sanka.engine.i18n import t
from sanka.engine.tools import execute
from sanka.gateways.whatsapp import directory

TARGET = "628999999999@s.whatsapp.net"
SPAN = "alpha beta gamma delta epsilon zeta eta theta"


def test_cross_chat_leak_detects_an_eight_word_span() -> None:
    reply = f"katanya begini: {SPAN} lalu selesai"

    assert guard.cross_chat_leak(reply, [SPAN]) is True
    assert guard.cross_chat_leak("alpha beta gamma delta epsilon zeta eta", [SPAN]) is False


def test_cross_chat_leak_ignores_a_paraphrase() -> None:
    reply = "Menurut obrolan itu, rencananya dibahas lagi minggu depan."

    assert guard.cross_chat_leak(reply, ["rapat anggaran dipindah ke hari kamis pagi"]) is False


def archive_env(tmp_path):
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, TARGET, "Budi", "")
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("chat_history",), commands=("*",))},
        default_role="owner",
    )
    env.history.add(
        TARGET,
        ChatMessage(
            id="b1",
            sender_name="Budi",
            sender_id="628999999999",
            when=datetime(2026, 1, 1, 10),
            text=SPAN,
            author="manual_owner",
            visibility=3,
        ),
    )
    return env


def context(env, chat_id: str = "111@g.us"):
    return env.agent.tool_context(
        chat_id, "628111", (env.config.roles.get("owner"),), env.gateway
    )


def test_cross_room_read_fills_cross_reads(tmp_path) -> None:
    env = archive_env(tmp_path)
    ctx = context(env)

    text = asyncio.run(
        execute(env.agent.tools.by_name, "chat_history", {"chat": "budi"}, ctx)
    )

    assert SPAN in text
    assert any(SPAN in item for item in ctx.cross_reads)
    assert ctx.reads == {TARGET}


def test_current_chat_read_does_not_fill_cross_reads(tmp_path) -> None:
    env = archive_env(tmp_path)
    env.history.add(
        "111@g.us",
        ChatMessage(
            id="c1",
            sender_name="Budi",
            sender_id="628111",
            when=datetime(2026, 1, 1, 10),
            text="rapat jam tiga",
        ),
    )
    ctx = context(env)

    text = asyncio.run(execute(env.agent.tools.by_name, "chat_history", {}, ctx))

    assert "rapat jam tiga" in text
    assert ctx.cross_reads == []


def test_agent_replaces_a_verbatim_cross_room_reply(tmp_path) -> None:
    env = make_agent(tmp_path, answer=SPAN)

    async def run(system, content, specs, ctx, session=None):
        ctx.cross_reads.append(SPAN)
        return SPAN

    env.llm.run = run
    asyncio.run(env.agent.handle(inbound_message("halo", chat_id="chat"), env.gateway))

    assert env.gateway.sent[-1][1] == t("agent.cross_chat_blocked", env.config.soul.language)
