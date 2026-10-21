"""Kontrak konteks: log 20 pesan, lampiran, kutipan, dan pemotongan teks panjang."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

from neonize.proto.Neonize_pb2 import Message

from sanka.chat import (
    FOOTER,
    ChatMessage,
    Quote,
    Sessions,
    build_user_content,
    describe_media,
    message_text,
)
from sanka.config import (
    Config,
    LLMConfig,
    RoleConfig,
    SandboxConfig,
    WhatsAppConfig,
)
from sanka.tools import REGISTRY as TOOLS
from sanka.tools import ToolContext, execute

WHEN = datetime(2026, 10, 21, 23, 23)
PNG = b"\x89PNG\r\n\x1a\n" + b"sanka"


def make_config(workspace: Path) -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=workspace,
        whatsapp=WhatsAppConfig(session_db=workspace / "sanka.db"),
        roles={"member": RoleConfig("member")},
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )


def chat_message(
    message_id: str,
    text: str = "",
    *,
    when: datetime | None = None,
    media=None,
    quote: Quote | None = None,
) -> ChatMessage:
    return ChatMessage(
        id=message_id,
        sender_name="Budi",
        sender_id="6281234567890",
        when=when or WHEN,
        text=text,
        media=media,
        quote=quote,
    )


class FakeClient:
    """Hanya `download_any` yang dipakai payload; isinya ditulis ke path yang diminta."""

    def __init__(self, payload: bytes = PNG):
        self.payload = payload
        self.paths: list[str] = []

    async def download_any(self, message, path=None) -> None:
        self.paths.append(path)
        Path(path).write_bytes(self.payload)


def document(event_id: str, filename: str, mimetype: str, caption: str = "") -> Message:
    ev = Message()
    ev.Info.ID = event_id
    ev.Message.documentMessage.fileName = filename
    ev.Message.documentMessage.mimetype = mimetype
    ev.Message.documentMessage.caption = caption
    return ev


def test_log_keeps_last_20_and_includes_current() -> None:
    sessions = Sessions(limit=20)
    entries = [chat_message(f"m{index}", f"teks {index}") for index in range(25)]
    for entry in entries:
        sessions.append("chat", entry)

    window = sessions.through("chat", entries[-1])

    assert len(window) == 20
    assert window[0] is entries[5]
    assert window[-1] is entries[-1]


def test_payload_has_sender_name_time_and_no_gaps(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    entries = [chat_message(f"m{index}", f"pesan nomor {index}") for index in range(19)]
    current = chat_message("m19", "pertanyaan penutup")

    content = asyncio.run(build_user_content(config, "chat", [*entries, current], current, None))

    assert isinstance(content, str)
    assert "Konteks WhatsApp (20 pesan terakhir, lama → baru" in content
    assert "[2026-10-21 23:23] Budi (6281234567890):" in content
    for entry in [*entries, current]:
        assert entry.text in content
    assert content.index("pesan nomor 18") < content.index("pertanyaan penutup")
    assert content.endswith(FOOTER)


def test_payload_header_includes_chat_name(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    current = chat_message("m1", "halo")

    content = asyncio.run(
        build_user_content(config, "chat", [current], current, None, chat_name="Grup Uji")
    )

    assert content.startswith('Konteks WhatsApp "Grup Uji" (1 pesan terakhir')


def test_long_text_preview_and_read_full(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    body = "\n".join(f"baris {index:02d}" for index in range(1, 41))
    current = chat_message("3EB0LONG", body)

    content = asyncio.run(build_user_content(config, "chat", [current], current, None))

    assert "masih 28 baris" in content
    path = re.search(r'read path="([^"]+)"', str(content)).group(1)
    assert path.startswith("whatsapp/")
    ctx = ToolContext(config, RoleConfig("member"), ("read",))
    result = asyncio.run(execute(TOOLS.by_name, "read", {"path": path}, ctx))
    assert "baris 40" in result


def test_paths_confined_to_workspace(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (tmp_path / "notes.txt").write_text("aman", encoding="utf-8")
    (tmp_path / "escape").symlink_to("/etc/passwd")
    member = ToolContext(config, RoleConfig("member"), ("read",))
    owner = ToolContext(config, RoleConfig("owner"), ("write",))

    def read(path: str) -> str:
        return asyncio.run(execute(TOOLS.by_name, "read", {"path": path}, member))

    assert "aman" in read("notes.txt")
    assert "aman" in read(str(tmp_path / "notes.txt"))
    for outside in ("/etc/passwd", "../catatan.txt", "escape"):
        assert "di luar workspace" in read(outside)
    assert "sudah dipindah" in read("~/.sanka/workspace/notes.txt")
    result = asyncio.run(execute(TOOLS.by_name, "write", {"path": "../escape.txt", "content": "x"}, owner))
    assert "di luar workspace" in result
    assert not (tmp_path.parent / "escape.txt").exists()


def test_document_png_is_downloaded_and_injected(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ev = document("3EB0IMG", "Sanka.png", "image/png", "warna apa ini?")
    media = describe_media(ev.Message, "3EB0IMG")
    assert media is not None
    assert (media.kind, media.filename, media.mimetype) == ("document", "Sanka.png", "image/png")
    current = chat_message("3EB0IMG", "warna apa ini?", media=media)

    client = FakeClient()
    content = asyncio.run(build_user_content(config, "chat", [current], current, client))

    assert isinstance(content, list)
    text = content[0]["text"]
    assert "[gambar #1]" in text
    assert "attachments/" in text
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert client.paths and Path(client.paths[0]).is_file()


def test_binary_attachment_mentions_sandbox(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ev = document("3EB0PDF", "laporan.pdf", "application/pdf", "ringkas ya")
    media = describe_media(ev.Message, "3EB0PDF")
    current = chat_message("3EB0PDF", "ringkas ya", media=media)

    content = asyncio.run(build_user_content(config, "chat", [current], current, FakeClient(b"%PDF-1.4")))

    assert isinstance(content, str)
    assert "laporan.pdf" in content
    assert "biner; periksa dengan tool sandbox" in content
    assert "/work/attachments/" in content


def test_quote_is_rendered(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    quote = Quote(id="q1", sender_name="Budi", sender_id="", text="jam berapa?")
    current = chat_message("m1", "jam 3", quote=quote)

    content = asyncio.run(build_user_content(config, "chat", [current], current, None))

    assert 'membalas Budi: "jam berapa?"' in str(content)


def test_mention_wrappers_are_stripped_from_text() -> None:
    ev = Message()
    ev.Message.conversation = "@\u2068Sanka\u2069 coba search ini\u00a0ya"

    assert message_text(ev.Message) == "@Sanka coba search ini ya"
