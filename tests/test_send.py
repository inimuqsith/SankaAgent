"""Kontrak pengiriman file: workspace, batas ukuran, callback, dan pencatatan konteks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from neonize.aioze.client import NewAClient

from sanka.commands import CommandContext
from sanka.commands.kirim import COMMAND as KIRIM
from sanka.config import Config, LLMConfig, RoleConfig, SandboxConfig, WhatsAppConfig
from sanka.tools import REGISTRY as TOOLS
from sanka.tools import ToolContext, execute
from sanka.wa import App, Sessions


def make_config(root: Path) -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=root,
        whatsapp=WhatsAppConfig(session_db=root / "wa.db"),
        roles={"owner": RoleConfig("owner")},
        default_role="owner",
        sandbox=SandboxConfig(),
        mcp={},
    )


def test_send_tool_uses_callback_and_confines_path(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (tmp_path / "laporan.txt").write_text("isi", encoding="utf-8")
    calls: list[tuple[Path, str]] = []

    async def fake(path: Path, caption: str) -> str:
        calls.append((path, caption))
        return f"ok: {path.name}"

    ctx = ToolContext(config, RoleConfig("owner"), ("send",), chat_id="chat", send_file=fake)

    result = asyncio.run(execute(TOOLS.by_name, "send", {"path": "laporan.txt", "caption": "hasil"}, ctx))

    assert result == "ok: laporan.txt"
    assert calls == [(tmp_path / "laporan.txt", "hasil")]
    assert "tidak ditemukan" in asyncio.run(execute(TOOLS.by_name, "send", {"path": "hilang.txt"}, ctx))
    assert "di luar workspace" in asyncio.run(execute(TOOLS.by_name, "send", {"path": "/etc/passwd"}, ctx))


def test_app_send_file_records_outgoing_message(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    path = tmp_path / "laporan.txt"
    path.write_text("isi", encoding="utf-8")
    client = NewAClient.__new__(NewAClient)
    sent: list[tuple] = []

    async def send_document(to, file, caption=None, filename=None):
        sent.append((to, file, caption, filename))
        return SimpleNamespace(ID="FILE1")

    client.send_document = send_document
    app = App(config, None, None, Sessions())
    app.client = client

    result = asyncio.run(app.send_file("123@g.us", path, "laporan"))

    assert result.startswith("ok: file terkirim: laporan.txt")
    assert sent and sent[0][1] == str(path) and sent[0][2] == "laporan"
    stored = app.sessions.get("123@g.us")
    assert stored[-1].text == "(file terkirim: laporan.txt — laporan)"
    assert stored[-1].outgoing is True
    assert "di luar workspace" in asyncio.run(app.send_file("123@g.us", Path("/etc/passwd")))


def test_kirim_command_parses_path_and_caption(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    path = tmp_path / "laporan.txt"
    path.write_text("isi", encoding="utf-8")
    calls: list[tuple[str, Path, str]] = []

    class FakeApp:
        config = None

        async def send_file(self, chat_id, target, caption=""):
            calls.append((chat_id, target, caption))
            return "ok"

    app = FakeApp()
    app.config = config
    ctx = CommandContext(app, "chat", RoleConfig("owner"), "laporan.txt hasil kerja", (), ())

    assert asyncio.run(KIRIM.fn(ctx)) == "ok"
    assert calls == [("chat", path, "hasil kerja")]
