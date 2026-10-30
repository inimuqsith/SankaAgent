"""Kontrak send_to: target harus eksplisit; satu kontak tidak pernah jadi fallback."""

from __future__ import annotations

import asyncio

from sanka.engine.tools import execute
from sanka.engine.context import ChatMessage
from sanka.gateways.whatsapp import directory
from support import make_agent, role

from test_whatsapp_gateway import make_gateway


def test_unknown_target_never_falls_back_to_the_only_contact(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, "628999999999@s.whatsapp.net", "Budi", "")
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("send_to",), commands=("*",))},
        default_role="owner",
    )
    ctx = env.agent.tool_context("111@g.us", "628111", (env.config.roles.get("owner"),), gateway)

    result = asyncio.run(
        execute(env.agent.tools.by_name, "send_to", {"chat": "tidak-ada@g.us", "text": "x"}, ctx)
    )

    assert result.startswith("error:")
    assert "tidak ada di arsip" in result
    assert gateway.client is None  # tidak ada transport yang terpakai


def test_send_to_listing_without_chat(tmp_path) -> None:
    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, "628999999999@s.whatsapp.net", "Budi", "")
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=("send_to",), commands=("*",))},
        default_role="owner",
    )
    ctx = env.agent.tool_context("111@g.us", "628111", (env.config.roles.get("owner"),), gateway)

    result = asyncio.run(execute(env.agent.tools.by_name, "send_to", {}, ctx))

    assert "Contacts in the archive" in result
    assert "Budi" in result


TARGET = "628999999999@s.whatsapp.net"


class FailingClient:
    async def send_message(self, jid, payload):
        raise RuntimeError("koneksi putus")


def cross_env(tmp_path, role_tools=("send_to", "chat_history")):
    from sanka.engine.context import ChatMessage

    gateway = make_gateway(tmp_path)
    directory._upsert_contact(gateway.db, TARGET, "Budi", "")
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"owner": role("owner", tools=role_tools, commands=("*",))},
        default_role="owner",
    )
    env.history.add(
        TARGET,
        ChatMessage(
            id="b1",
            sender_name="Budi",
            sender_id="628999999999",
            when=__import__("datetime").datetime(2026, 1, 1, 10),
            text="halo dari budi",
            author="manual_owner",
            visibility=3,
        ),
    )
    ctx = env.agent.tool_context(
        "111@g.us", "628111", (env.config.roles.get("owner"),), gateway
    )
    return env, ctx


def test_cross_chat_send_refused_before_reading_the_target(tmp_path) -> None:
    env, ctx = cross_env(tmp_path)

    result = asyncio.run(
        execute(env.agent.tools.by_name, "send_to", {"chat": "budi", "text": "halo"}, ctx)
    )

    assert "baca dulu arsip Budi (628999999999@s.whatsapp.net)" in result
    assert "chat_history" in result
    assert env.gateway.client is None


def test_cross_chat_send_works_after_reading_the_target(tmp_path) -> None:
    from test_whatsapp_gateway import FakeClient

    env, ctx = cross_env(tmp_path)
    env.gateway.client = FakeClient()

    read = asyncio.run(execute(env.agent.tools.by_name, "chat_history", {"chat": "budi"}, ctx))
    result = asyncio.run(
        execute(env.agent.tools.by_name, "send_to", {"chat": "budi", "text": "halo"}, ctx)
    )

    assert "halo dari budi" in read
    assert result.startswith("ok: terkirim ke Budi")
    assert [call[0] for call in env.gateway.client.calls] == ["message"]


def test_failed_cross_chat_send_is_logged_as_failure(tmp_path) -> None:
    env, ctx = cross_env(tmp_path)
    env.gateway.client = FailingClient()
    ctx.reads.add(TARGET)

    result = asyncio.run(
        execute(env.agent.tools.by_name, "send_to", {"chat": "budi", "text": "halo"}, ctx)
    )

    assert result.startswith("error: pengiriman ke Budi (628999999999@s.whatsapp.net) gagal:")
    assert "koneksi putus" in result
    actions = env.agent.ledger.actions_context()
    assert "gagal — error: gagal mengirim teks: RuntimeError: koneksi putus" in actions
    assert ctx.sent == []
