"""Kontrak send_to: target harus eksplisit; satu kontak tidak pernah jadi fallback."""

from __future__ import annotations

import asyncio

from sanka.engine.tools import execute
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
