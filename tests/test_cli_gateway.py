"""Kontrak gateway CLI: stdin -> Inbound, output ke stdout, /quit berhenti."""

from __future__ import annotations

import asyncio
import io

from sanka.engine.config import GatewaySettings
from sanka.engine.context import WorkingMemory
from sanka.engine.stores.history import HistoryStore
from support import build_config, database


class RecordingAgent:
    mode = "primary"

    def __init__(self) -> None:
        self.inbounds = []

    async def handle(self, inbound, gateway) -> None:
        self.inbounds.append(inbound)


def make_cli(tmp_path):
    from sanka.gateways.cli.gateway import CliGateway

    config = build_config(tmp_path)
    db = database(tmp_path)
    gateway = CliGateway(
        config,
        db,
        HistoryStore(db),
        WorkingMemory(),
        settings=GatewaySettings(
            "cli",
            True,
            {"role": "owner", "user": "local", "chat_id": "cli:local", "chat_name": "Terminal"},
        ),
    )
    return gateway


def test_stdin_line_becomes_inbound_and_quit_stops(tmp_path, monkeypatch) -> None:
    gateway = make_cli(tmp_path)
    agent = RecordingAgent()
    monkeypatch.setattr("sys.stdin", io.StringIO("halo\n/quit\n"))

    asyncio.run(gateway.run(agent))

    assert len(agent.inbounds) == 1
    inbound = agent.inbounds[0]
    assert inbound.chat_id == "cli:local"
    assert inbound.message.text == "halo"
    assert inbound.is_dm is True
    assert inbound.role_override == ("owner",)


def test_send_text_prints_with_prefix(tmp_path, capsys) -> None:
    gateway = make_cli(tmp_path)

    message_id = asyncio.run(gateway.send_text("cli:local", "halo dunia"))

    out = capsys.readouterr().out
    assert "Sanka: halo dunia" in out
    assert message_id.startswith("cli-")


def test_render_is_identity_and_hints_are_plain(tmp_path) -> None:
    gateway = make_cli(tmp_path)
    assert gateway.render("**tebal**") == "**tebal**"
    assert "plain text" in gateway.prompt_hints()
    assert gateway.self_handles() == set()
