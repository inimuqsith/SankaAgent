"""Attachment store and ``attachment_read`` tests (plan-12)."""

from __future__ import annotations

import base64
import stat
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sanka.attachments import (
    AttachmentError,
    AttachmentStore,
    canonical_mime,
    decode_filename_header,
    sanitize_filename,
)
from sanka.config import SankaConfig
from sanka.models import InboundAttachment
from sanka.policy import Role
from sanka.tools.attachments import (
    make_attachment_outline_tool,
    make_attachment_read_tool,
    make_attachment_search_tool,
)
from sanka.tools.registry import ToolContext, ToolError

CHAT = "628111111111@s.whatsapp.net"
SENDER = "628111111111@s.whatsapp.net"
MESSAGE = "msg-1"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8Dwn4GBgYGJAQoAHgQCAZ7F0XQAAAAASUVORK5CYII="
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_store(
    tmp_path: Path,
    *,
    max_bytes: int = 1024,
    max_total_bytes: int = 10_000,
    retention_seconds: int = 3600,
    clock: FakeClock | None = None,
) -> AttachmentStore:
    return AttachmentStore(
        str(tmp_path / "attachments"),
        max_bytes=max_bytes,
        max_total_bytes=max_total_bytes,
        retention_seconds=retention_seconds,
        clock=clock or FakeClock(),
    )


def save_text(
    store: AttachmentStore,
    content: bytes = b"halo",
    *,
    name: str = "catatan.md",
    mime: str = "text/markdown",
    chat_id: str = CHAT,
    sender_id: str = SENDER,
    message_id: str = MESSAGE,
):
    return store.save(
        [content],
        chat_id=chat_id,
        sender_id=sender_id,
        message_id=message_id,
        kind="document",
        mime=mime,
        name=name,
        declared_size=len(content),
    )


def tool_context(config: SankaConfig, store: AttachmentStore) -> ToolContext:
    return ToolContext(
        role=Role.COMMITTEE,
        chat_id=CHAT,
        sender_id=SENDER,
        config=config,
        now=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc),
        attachments=store,
    )


def test_store_persists_bytes_with_private_modes(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = save_text(store, b"# halo\n")

    binary = store.resolve(record)
    assert binary.read_bytes() == b"# halo\n"
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(binary.stat().st_mode) == 0o600
    assert stat.S_IMODE((store.directory / f"{record.attachment_id}.json").stat().st_mode) == 0o600
    assert store.get(record.attachment_id, chat_id=CHAT) == record


def test_store_is_chat_bound(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = save_text(store)

    assert store.get(record.attachment_id, chat_id="628222222222@s.whatsapp.net") is None
    assert store.get(record.attachment_id, chat_id=CHAT) is not None


def test_store_rejects_oversize(tmp_path: Path) -> None:
    store = make_store(tmp_path, max_bytes=8)
    with pytest.raises(AttachmentError) as bad_image:
        store.save(
            [b"not a png"],
            chat_id=CHAT,
            sender_id=SENDER,
            message_id=MESSAGE,
            kind="image",
            mime="image/png",
            name="",
            declared_size=9,
        )
    assert bad_image.value.code == "too_large"


def test_store_rejects_magic_mismatch(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(AttachmentError) as exc:
        store.save(
            [b"not a png"],
            chat_id=CHAT,
            sender_id=SENDER,
            message_id=MESSAGE,
            kind="image",
            mime="image/png",
            name="",
            declared_size=9,
        )
    assert exc.value.code == "unsupported_media"


def test_store_expires_records(tmp_path: Path) -> None:
    clock = FakeClock()
    store = make_store(tmp_path, retention_seconds=10, clock=clock)
    record = save_text(store)

    clock.advance(10)
    assert store.get(record.attachment_id, chat_id=CHAT) is None
    assert not store.resolve(record).exists()


def test_store_enforces_total_quota_oldest_first(tmp_path: Path) -> None:
    clock = FakeClock()
    store = make_store(
        tmp_path, max_bytes=10, max_total_bytes=10, clock=clock
    )
    first = save_text(store, b"123456")
    clock.advance(1)
    second = save_text(store, b"abcdef")

    assert store.get(first.attachment_id, chat_id=CHAT) is None
    assert store.get(second.attachment_id, chat_id=CHAT) is not None


def test_store_accepts_octet_stream_by_extension(tmp_path: Path) -> None:
    assert (
        canonical_mime(
            "document", "application/octet-stream", "laporan.pdf"
        )
        == "application/pdf"
    )
    assert canonical_mime("document", "text/plain", "x.txt") == "text/plain"
    with pytest.raises(AttachmentError):
        canonical_mime("document", "application/msword", "x.doc")


def test_filename_helpers_are_bounded() -> None:
    traversal = sanitize_filename("../../etc/passwd", kind="document")
    assert "/" not in traversal and "\\" not in traversal
    assert sanitize_filename("", kind="image") == "gambar"
    encoded = base64.urlsafe_b64encode("laporan bulanan.pdf".encode()).decode().rstrip("=")
    assert decode_filename_header(encoded) == "laporan bulanan.pdf"
    with pytest.raises(AttachmentError):
        decode_filename_header("!!!")


def test_attachment_read_returns_text(config: SankaConfig, tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = save_text(store, b"baris satu\nbaris dua\n")
    spec = make_attachment_read_tool(store)
    result = spec.handler(
        {"attachment_id": record.attachment_id}, tool_context(config, store)
    )

    assert result.ok is True
    assert result.tool == "attachment_read"
    assert result.data["name"] == "catatan.md"
    assert "baris satu" in result.text


def test_attachment_read_paginates_with_offset(
    config: SankaConfig, tmp_path: Path
) -> None:
    store = make_store(tmp_path)
    record = save_text(store, b"0123456789abcdefghij")
    spec = make_attachment_read_tool(store)
    context = tool_context(config, store)

    first = spec.handler(
        {"attachment_id": record.attachment_id, "offset": 0, "limit": 10},
        context,
    )
    assert first.data["text"] == "0123456789"
    assert first.data["truncated"] is True
    assert first.data["next_offset"] == 10

    second = spec.handler(
        {
            "attachment_id": record.attachment_id,
            "offset": first.data["next_offset"],
            "limit": 100,
        },
        context,
    )
    assert second.data["text"] == "abcdefghij"
    assert second.data["truncated"] is False
    assert second.data["next_offset"] is None


def test_attachment_read_rejects_images_and_other_chats(
    config: SankaConfig, tmp_path: Path
) -> None:
    store = make_store(tmp_path)
    image = store.save(
        [PNG],
        chat_id=CHAT,
        sender_id=SENDER,
        message_id=MESSAGE,
        kind="image",
        mime="image/png",
        name="",
        declared_size=len(PNG),
    )
    spec = make_attachment_read_tool(store)
    with pytest.raises(ToolError) as image_error:
        spec.handler(
            {"attachment_id": image.attachment_id}, tool_context(config, store)
        )
    assert image_error.value.code == "invalid_arguments"

    document = save_text(store)
    other = replace(
        tool_context(config, store),
        chat_id="628222222222@s.whatsapp.net",
    )
    with pytest.raises(ToolError) as chat_error:
        spec.handler({"attachment_id": document.attachment_id}, other)
    assert chat_error.value.code == "not_found"


def test_attachment_read_is_not_configured_without_store(config: SankaConfig) -> None:
    spec = make_attachment_read_tool(None)
    context = ToolContext(
        role=Role.COMMITTEE,
        chat_id=CHAT,
        sender_id=SENDER,
        config=config,
        now=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    with pytest.raises(ToolError) as exc:
        spec.handler({"attachment_id": "a" * 32}, context)
    assert exc.value.code == "not_configured"


def test_attachment_model_type_is_importable() -> None:
    attachment = InboundAttachment(
        attachment_id="a" * 32,
        kind="document",
        mime="text/plain",
        name="x.txt",
        size=3,
    )
    assert attachment.size == 3


def test_attachment_read_extracts_pdf(config: SankaConfig, tmp_path: Path) -> None:
    from tests.test_doc_read import FIXTURES, PDF_MARKER

    store = make_store(tmp_path)
    payload = (FIXTURES / "sample.pdf").read_bytes()
    record = store.save(
        [payload],
        chat_id=CHAT,
        sender_id=SENDER,
        message_id=MESSAGE,
        kind="document",
        mime="application/pdf",
        name="sample.pdf",
        declared_size=len(payload),
    )
    spec = make_attachment_read_tool(store)
    result = spec.handler(
        {"attachment_id": record.attachment_id}, tool_context(config, store)
    )

    assert result.data["kind"] == "pdf"
    assert PDF_MARKER in result.text


def test_attachment_outline_maps_markdown_headings(
    config: SankaConfig, tmp_path: Path
) -> None:
    store = make_store(tmp_path)
    body = b"# Pendahuluan\nalpha\n## Rincian\nbeta\n"
    record = save_text(store, body)
    context = tool_context(config, store)

    outline = make_attachment_outline_tool(store).handler(
        {"attachment_id": record.attachment_id}, context
    )

    labels = [section["label"] for section in outline.data["sections"]]
    assert labels == ["Pendahuluan", "Rincian"]
    detailed = outline.data["sections"][1]
    text = body.decode()
    assert detailed["start"] == text.index("## Rincian")
    assert detailed["end"] == len(text)

    read = make_attachment_read_tool(store).handler(
        {"attachment_id": record.attachment_id, "offset": detailed["start"]},
        context,
    )
    assert read.data["text"].startswith("## Rincian")


def test_attachment_search_reaches_text_beyond_read_window(
    config: SankaConfig, tmp_path: Path
) -> None:
    store = make_store(tmp_path, max_bytes=200_000, max_total_bytes=1_000_000)
    padding = "a" * 60_000
    body = f"{padding}\nPENANDA-UNIK di bagian akhir\n".encode()
    record = save_text(store, body)
    context = tool_context(config, store)

    search = make_attachment_search_tool(store).handler(
        {"attachment_id": record.attachment_id, "query": "penanda-unik"},
        context,
    )

    assert search.data["total_matches"] == 1
    match = search.data["matches"][0]
    assert match["offset"] == len(padding) + 1
    assert "PENANDA-UNIK" in match["snippet"]

    read = make_attachment_read_tool(store).handler(
        {"attachment_id": record.attachment_id, "offset": match["offset"]},
        context,
    )
    assert "PENANDA-UNIK" in read.data["text"]


def test_attachment_search_limits_escapes_and_ignores_case(
    config: SankaConfig, tmp_path: Path
) -> None:
    store = make_store(tmp_path)
    record = save_text(store, b"Kata a.b kata KATA kata\n")
    context = tool_context(config, store)
    search = make_attachment_search_tool(store)

    limited = search.handler(
        {
            "attachment_id": record.attachment_id,
            "query": "kata",
            "limit": 2,
            "context_chars": 0,
        },
        context,
    )
    assert limited.data["total_matches"] == 4
    assert limited.data["returned"] == 2
    assert limited.data["truncated"] is True

    literal = search.handler(
        {"attachment_id": record.attachment_id, "query": "a.b"}, context
    )
    assert literal.data["total_matches"] == 1

    missing = search.handler(
        {"attachment_id": record.attachment_id, "query": "tidak-ada"}, context
    )
    assert missing.data["total_matches"] == 0
    assert missing.data["matches"] == []


def test_attachment_search_and_outline_respect_chat_and_kind(
    config: SankaConfig, tmp_path: Path
) -> None:
    store = make_store(tmp_path)
    document = save_text(store)
    image = store.save(
        [PNG],
        chat_id=CHAT,
        sender_id=SENDER,
        message_id=MESSAGE,
        kind="image",
        mime="image/png",
        name="",
        declared_size=len(PNG),
    )
    other_chat = replace(
        tool_context(config, store), chat_id="628222222222@s.whatsapp.net"
    )
    context = tool_context(config, store)
    specs = (
        (make_attachment_search_tool(store), {"query": "halo"}),
        (make_attachment_outline_tool(store), {}),
    )

    for spec, extra in specs:
        with pytest.raises(ToolError) as not_found:
            spec.handler(
                {"attachment_id": document.attachment_id, **extra}, other_chat
            )
        assert not_found.value.code == "not_found"

        with pytest.raises(ToolError) as image_error:
            spec.handler({"attachment_id": image.attachment_id, **extra}, context)
        assert image_error.value.code == "invalid_arguments"


def test_attachment_search_and_outline_need_store(config: SankaConfig) -> None:
    context = ToolContext(
        role=Role.COMMITTEE,
        chat_id=CHAT,
        sender_id=SENDER,
        config=config,
        now=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc),
    )
    for spec, extra in (
        (make_attachment_search_tool(None), {"query": "halo"}),
        (make_attachment_outline_tool(None), {}),
    ):
        with pytest.raises(ToolError) as exc:
            spec.handler({"attachment_id": "a" * 32, **extra}, context)
        assert exc.value.code == "not_configured"


def test_attachment_outline_pdf_pages(config: SankaConfig, tmp_path: Path) -> None:
    from tests.test_doc_read import FIXTURES

    store = make_store(tmp_path, max_bytes=200_000, max_total_bytes=1_000_000)
    payload = (FIXTURES / "sample.pdf").read_bytes()
    record = store.save(
        [payload],
        chat_id=CHAT,
        sender_id=SENDER,
        message_id=MESSAGE,
        kind="document",
        mime="application/pdf",
        name="sample.pdf",
        declared_size=len(payload),
    )

    outline = make_attachment_outline_tool(store).handler(
        {"attachment_id": record.attachment_id}, tool_context(config, store)
    )

    assert outline.data["kind"] == "pdf"
    assert outline.data["sections"][0]["label"] == "halaman 1"
    assert outline.data["sections"][0]["start"] == 0


def test_attachment_outline_and_search_cover_every_xlsx_sheet(
    config: SankaConfig, tmp_path: Path
) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    first = workbook.active
    first.title = "Ringkasan"
    first.append(["Nama", "Nominal"])
    first.append(["Ayu", 1500])
    second = workbook.create_sheet("Arsip")
    second.append(["Catatan"])
    second.append(["penanda lembar kedua"])
    source = tmp_path / "buku.xlsx"
    workbook.save(source)
    workbook.close()

    store = make_store(tmp_path, max_bytes=200_000, max_total_bytes=1_000_000)
    payload = source.read_bytes()
    record = store.save(
        [payload],
        chat_id=CHAT,
        sender_id=SENDER,
        message_id=MESSAGE,
        kind="document",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        name="buku.xlsx",
        declared_size=len(payload),
    )
    context = tool_context(config, store)

    outline = make_attachment_outline_tool(store).handler(
        {"attachment_id": record.attachment_id}, context
    )
    assert [section["label"] for section in outline.data["sections"]] == [
        "lembar Ringkasan",
        "lembar Arsip",
    ]
    assert outline.data["columns"] == ["Nama", "Nominal"]

    search = make_attachment_search_tool(store).handler(
        {"attachment_id": record.attachment_id, "query": "lembar kedua"}, context
    )
    assert search.data["total_matches"] == 1
    assert search.data["matches"][0]["section"] == "lembar Arsip"
