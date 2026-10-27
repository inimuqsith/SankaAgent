"""Kontrak pengiriman file: workspace, batas ukuran, callback, dan pencatatan konteks."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from neonize.aioze.client import NewAClient

from sanka.chat import ChatMessage
from sanka.commands import CommandContext
from sanka.commands.kirim import COMMAND as KIRIM
from sanka.config import Config, LLMConfig, RoleConfig, SandboxConfig, WhatsAppConfig
from sanka.history import HistoryStore
from sanka.memory import MemoryStore
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
    calls: list[tuple[str, Path, str]] = []

    async def fake(chat_id: str, path: Path, caption: str) -> str:
        calls.append((chat_id, path, caption))
        return f"ok: {path.name}"

    ctx = ToolContext(config, RoleConfig("owner"), ("send",), chat_id="chat", send_file=fake)

    result = asyncio.run(execute(TOOLS.by_name, "send", {"path": "laporan.txt", "caption": "hasil"}, ctx))

    assert result == "ok: laporan.txt"
    assert calls == [("chat", tmp_path / "laporan.txt", "hasil")]
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


def _webp(width: int, height: int, flags: int = 0x1A) -> bytes:
    """Header VP8X minimal: cukup untuk klasifikasi, bukan webp utuh."""
    head = bytearray(30)
    head[0:4] = b"RIFF"
    head[4:8] = (22).to_bytes(4, "little")
    head[8:12] = b"WEBP"
    head[12:16] = b"VP8X"
    head[16:20] = (10).to_bytes(4, "little")
    head[20] = flags
    head[24:27] = (width - 1).to_bytes(3, "little")
    head[27:30] = (height - 1).to_bytes(3, "little")
    return bytes(head)


def test_app_send_file_routes_by_content(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (tmp_path / "stiker.webp").write_bytes(_webp(342, 342))
    (tmp_path / "foto.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 26)
    (tmp_path / "papan.webp").write_bytes(_webp(800, 600, 0x18))
    (tmp_path / "catatan.bin").write_bytes(b"hello")
    client = NewAClient.__new__(NewAClient)
    calls: list[tuple] = []

    async def send_sticker(to, file):
        calls.append(("sticker", to, file))
        return SimpleNamespace(ID="S1")

    async def send_image(to, file, caption=None):
        calls.append(("image", to, file, caption))
        return SimpleNamespace(ID="I1")

    async def send_document(to, file, caption=None, filename=None):
        calls.append(("document", to, file, caption, filename))
        return SimpleNamespace(ID="D1")

    async def send_message(to, message):
        calls.append(("text", to, message))
        return SimpleNamespace(ID="T1")

    client.send_sticker = send_sticker
    client.send_image = send_image
    client.send_document = send_document
    client.send_message = send_message
    app = App(config, None, None, Sessions())
    app.client = client

    result = asyncio.run(app.send_file("123@g.us", tmp_path / "stiker.webp", "stiker keren"))
    assert result == "ok: stiker terkirim: stiker.webp (30 B)"
    assert [call[0] for call in calls] == ["sticker", "text"]
    assert calls[0][2] == str(tmp_path / "stiker.webp")
    assert calls[1][2] == "stiker keren"
    assert app.sessions.get("123@g.us")[-1].text == "(stiker terkirim: stiker.webp — stiker keren)"

    assert asyncio.run(app.send_file("123@g.us", tmp_path / "foto.jpg")) == "ok: foto terkirim: foto.jpg (30 B)"
    assert calls[-1][0] == "image" and calls[-1][2] == str(tmp_path / "foto.jpg") and calls[-1][3] is None

    assert asyncio.run(app.send_file("123@g.us", tmp_path / "papan.webp")) == (
        "ok: foto terkirim: papan.webp (30 B)"
    )
    assert calls[-1][0] == "image"

    assert asyncio.run(app.send_file("123@g.us", tmp_path / "catatan.bin", "lampiran")) == (
        "ok: file terkirim: catatan.bin (5 B)"
    )
    assert calls[-1][0] == "document" and calls[-1][4] == "catatan.bin"


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


def test_send_to_resolves_group_and_logs_target_context(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(tmp_path / "history.db", tmp_path)
    store.set_chat_name("1203@g.us", "ALD XXVII JHEPRET CLUB FOTOGRAFI")
    store.set_chat_name("999@g.us", "Tester Sanka & MapHb")
    store.add(
        "1203@g.us",
        ChatMessage(
            id="m2", sender_name="Fahri", sender_id="6282", when=datetime.now(), text="rapat"
        ),
    )
    client = NewAClient.__new__(NewAClient)
    sent: list[tuple] = []

    async def send_message(to, message, link_preview=False, add_msg_secret=False):
        sent.append((to.User, to.Server, message))
        return SimpleNamespace(ID="T1")

    client.send_message = send_message
    app = App(config, None, None, Sessions(store=store))
    app.client = client
    memory = MemoryStore(tmp_path / "memory.db")
    ctx = ToolContext(
        config,
        RoleConfig("owner"),
        ("send_to",),
        chat_id="999@g.us",
        history=store,
        memory=memory,
        send_file=app.send_file,
        send_message=app.send_text,
        request="kirim rapat ke jhepret",
    )

    result = asyncio.run(
        execute(TOOLS.by_name, "send_to", {"chat": "jhepret", "text": "rapat jam 7"}, ctx)
    )

    assert result.startswith('ok: terkirim ke "ALD XXVII JHEPRET CLUB FOTOGRAFI"')
    assert sent == [("1203", "g.us", "rapat jam 7")]
    stored = app.sessions.get("1203@g.us")[-1]
    assert stored.text == "rapat jam 7" and stored.outgoing is True
    assert store.recent("1203@g.us", 1)[-1].text == "rapat jam 7"  # realtime, bukan hanya cache
    assert not app.sessions.get("999@g.us")  # chat asal tidak ikut dicatat
    action = memory.recent_actions(1)[0]
    assert action["source"] == "999@g.us" and action["source_name"] == "Tester Sanka & MapHb"
    assert action["target"] == "1203@g.us"
    assert action["target_name"] == "ALD XXVII JHEPRET CLUB FOTOGRAFI"
    assert action["kind"] == "teks" and "rapat jam 7" in action["summary"]
    assert action["request"] == "kirim rapat ke jhepret"
    memory.close()
    store.close()


def test_send_to_does_not_log_failed_send(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(tmp_path / "history.db", tmp_path)
    store.set_chat_name("1@g.us", "Grup Foto A")
    memory = MemoryStore(tmp_path / "memory.db")
    client = NewAClient.__new__(NewAClient)

    async def fail(to, message, link_preview=False, add_msg_secret=False):
        raise RuntimeError("transport mati")

    client.send_message = fail
    app = App(config, None, None, Sessions(store=store))
    app.client = client
    ctx = ToolContext(
        config,
        RoleConfig("owner"),
        ("send_to",),
        chat_id="9@g.us",
        history=store,
        memory=memory,
        send_file=app.send_file,
        send_message=app.send_text,
        request="kirim ke grup",
    )

    result = asyncio.run(
        execute(TOOLS.by_name, "send_to", {"chat": "Grup Foto A", "text": "halo"}, ctx)
    )

    assert result.startswith("error:") and "gagal" in result
    assert memory.recent_actions() == []
    memory.close()
    store.close()


def test_send_to_lists_ambiguity_and_is_owner_only(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(tmp_path / "history.db", tmp_path)
    store.set_chat_name("1@g.us", "Grup Foto A")
    store.set_chat_name("2@g.us", "Grup Foto B")
    ctx = ToolContext(config, RoleConfig("owner"), ("send_to",), history=store)

    listing = asyncio.run(execute(TOOLS.by_name, "send_to", {}, ctx))
    assert "Grup di arsip" in listing and "Grup Foto A (1@g.us)" in listing

    ambiguous = asyncio.run(execute(TOOLS.by_name, "send_to", {"chat": "foto", "text": "x"}, ctx))
    assert "beberapa target cocok" in ambiguous
    assert "1@g.us" in ambiguous and "2@g.us" in ambiguous

    missing = asyncio.run(execute(TOOLS.by_name, "send_to", {"chat": "tidak ada", "text": "x"}, ctx))
    assert "tidak ada di arsip" in missing and "Grup Foto A" in missing

    member = ToolContext(config, RoleConfig("member"), ("send",), history=store)
    denied = asyncio.run(execute(TOOLS.by_name, "send_to", {"chat": "Grup Foto A", "text": "x"}, member))
    assert "tidak diizinkan untuk role 'member'" in denied
    store.close()


def test_send_to_resolves_contact_for_private_chat(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = HistoryStore(tmp_path / "history.db", tmp_path)
    store.set_contact("628123456789@s.whatsapp.net", "Ilham JC")
    client = NewAClient.__new__(NewAClient)
    sent: list[tuple] = []

    async def send_message(to, message, link_preview=False, add_msg_secret=False):
        sent.append((to.User, to.Server, message))
        return SimpleNamespace(ID="T2")

    client.send_message = send_message
    app = App(config, None, None, Sessions(store=store))
    app.client = client
    memory = MemoryStore(tmp_path / "memory.db")
    ctx = ToolContext(
        config,
        RoleConfig("owner"),
        ("send_to",),
        chat_id="9@g.us",
        history=store,
        memory=memory,
        send_file=app.send_file,
        send_message=app.send_text,
        request="cp ilham",
    )

    result = asyncio.run(
        execute(TOOLS.by_name, "send_to", {"chat": "ilham", "text": "halo"}, ctx)
    )

    dm = "628123456789@s.whatsapp.net"
    assert result.startswith('ok: terkirim ke "Ilham JC"')
    assert sent == [("628123456789", "s.whatsapp.net", "halo")]
    assert app.sessions.get(dm)[-1].outgoing is True
    assert memory.recent_actions(1)[0]["target"] == dm
    listing = asyncio.run(execute(TOOLS.by_name, "send_to", {}, ctx))
    assert "Kontak di arsip" in listing and "Ilham JC" in listing
    memory.close()
    store.close()
