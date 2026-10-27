"""Tool send_to: kirim teks/file ke grup lain yang sudah ada di arsip."""

from __future__ import annotations

import logging
import sqlite3

from ..registry import Handler
from . import OWNER_ONLY, ToolContext, _resolve

log = logging.getLogger(__name__)
LIST_LIMIT = 20


def _label(chat_id: str, name: str) -> str:
    return f"{name} ({chat_id})" if name else chat_id


def _known(ctx: ToolContext) -> str:
    try:
        groups = ctx.history.groups("", LIST_LIMIT)
        people = ctx.history.contacts("", LIST_LIMIT)
    except (sqlite3.Error, RuntimeError) as exc:
        return f"error: gagal membaca daftar target: {type(exc).__name__}: {exc}"
    parts: list[str] = []
    if groups:
        parts.append(f"Grup di arsip ({len(groups)}, terbaru dulu):")
        parts.extend(f"- {_label(chat_id, name)}" for chat_id, name in groups)
    if people:
        parts.append(f"Kontak di arsip ({len(people)}, terbaru dulu):")
        parts.extend(f"- {_label(chat_id, name)}" for chat_id, name in people)
    return "\n".join(parts) or "Belum ada grup atau kontak di arsip."


def _record(ctx: ToolContext, target: str, target_name: str, kind: str, summary: str) -> None:
    """Tulis ledger aksi; kegagalan catat tidak boleh membatalkan kiriman."""
    if ctx.memory is None or not ctx.chat_id or ctx.chat_id == target:
        return
    try:
        source_name = ctx.history.chat_name(ctx.chat_id) if ctx.history is not None else ""
        ctx.memory.log_action(
            ctx.chat_id,
            target,
            kind,
            summary,
            source_name=source_name,
            target_name=target_name,
            request=ctx.request,
        )
    except Exception:  # ledger best-effort, kiriman sudah terjadi
        log.exception("gagal mencatat aksi lintas chat ke %s", target)


def _resolve_target(groups: list[tuple[str, str]], contacts: list[tuple[str, str]], raw: str):
    """Cocokkan JID, nama persis, lalu potongan nama; kembalikan (target, kandidat)."""
    items = [*groups, *contacts]
    for item in items:
        if item[0] == raw:
            return item, []
    folded = raw.casefold()
    exact = [item for item in items if item[1].strip().casefold() == folded]
    if len(exact) == 1:
        return exact[0], []
    partial = [item for item in items if folded in item[1].casefold() or folded in item[0].casefold()]
    if len(partial) == 1:
        return partial[0], []
    return None, exact or partial


async def _send_to(args: dict, ctx: ToolContext) -> str:
    if ctx.history is None:
        return "error: arsip riwayat tidak tersedia"
    text = str(args.get("text") or "").strip()
    raw_path = str(args.get("path") or "").strip()
    if not text and not raw_path:
        return _known(ctx)
    raw_chat = str(args.get("chat") or "").strip()
    if not raw_chat:
        return "error: chat wajib diisi dengan nama grup atau JID"
    try:
        groups = ctx.history.groups(raw_chat, 50)
        contacts = ctx.history.contacts(raw_chat, 50)
    except (sqlite3.Error, RuntimeError) as exc:
        return f"error: gagal membaca daftar target: {type(exc).__name__}: {exc}"
    target, candidates = _resolve_target(groups, contacts, raw_chat)
    if target is None and "@" in raw_chat and len(contacts) == 1:
        target, candidates = contacts[0], []
    if target is None:
        if candidates:
            listing = "\n".join(f"- {_label(chat_id, name)}" for chat_id, name in candidates[:LIST_LIMIT])
            return f'error: beberapa target cocok dengan "{raw_chat}"; pakai nama lengkap atau JID:\n{listing}'
        return f'error: target "{raw_chat}" tidak ada di arsip.\n{_known(ctx)}'
    if ctx.send_file is None or ctx.send_message is None:
        return "error: pengiriman ke chat lain tidak tersedia"
    chat_id, name = target
    path = None
    if raw_path:
        path = _resolve(ctx, raw_path)
        if not path.is_file():
            return f"error: file tidak ditemukan: {raw_path}"
    caption = str(args.get("caption") or "").strip()[:1024]
    results = []
    if text:
        result = await ctx.send_message(chat_id, text)
        results.append(result)
        if not result.startswith("error:"):
            _record(ctx, chat_id, name, "teks", text)
    if path is not None:
        result = await ctx.send_file(chat_id, path, caption)
        results.append(result)
        if not result.startswith("error:"):
            summary = f"{path.name}{f' — {caption}' if caption else ''}"
            _record(ctx, chat_id, name, "file", summary)
    label = f'"{name}" ({chat_id})' if name else chat_id
    if any(result.startswith("error:") for result in results):
        return "\n".join([f"error: pengiriman ke {label} gagal:", *results])
    return "\n".join([f"ok: terkirim ke {label}", *results])


TOOL = Handler(
    name="send_to",
    family="write",
    description=(
        "Kirim teks dan/atau file ke grup atau chat pribadi (CP) yang sudah ada di arsip. chat = "
        "nama grup/kontak (boleh potongan) atau JID; chat kosong menampilkan daftar grup dan kontak. "
        "Untuk chat aktif pakai send."
    ),
    fn=_send_to,
    aliases=("kirim_ke",),
    parameters={
        "type": "object",
        "properties": {
            "chat": {
                "type": "string",
                "description": "Nama grup/kontak, potongan nama, atau JID @g.us/@s.whatsapp.net/@lid.",
            },
            "text": {"type": "string", "description": "Teks yang dikirim (opsional bila ada path)."},
            "path": {"type": "string", "description": "Path file di dalam workspace (opsional)."},
            "caption": {"type": "string", "description": "Caption untuk file (opsional)."},
        },
        "additionalProperties": False,
    },
    defaults=OWNER_ONLY,
)
