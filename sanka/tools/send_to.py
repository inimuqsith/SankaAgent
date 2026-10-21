"""Tool send_to: kirim teks/file ke grup lain yang sudah ada di arsip."""

from __future__ import annotations

import sqlite3

from ..registry import Handler
from . import OWNER_ONLY, ToolContext, _resolve

LIST_LIMIT = 20


def _label(chat_id: str, name: str) -> str:
    return f"{name} ({chat_id})" if name else chat_id


def _known(ctx: ToolContext) -> str:
    try:
        groups = ctx.history.groups("", LIST_LIMIT)
    except (sqlite3.Error, RuntimeError) as exc:
        return f"error: gagal membaca daftar grup: {type(exc).__name__}: {exc}"
    if not groups:
        return "Belum ada grup di arsip."
    header = f"Grup di arsip ({len(groups)}, terbaru dulu):"
    return "\n".join([header, *[f"- {_label(chat_id, name)}" for chat_id, name in groups]])


def _resolve_group(groups: list[tuple[str, str]], raw: str):
    """Cocokkan JID, nama persis, lalu potongan nama; kembalikan (target, kandidat)."""
    for item in groups:
        if item[0] == raw:
            return item, []
    folded = raw.casefold()
    exact = [item for item in groups if item[1].strip().casefold() == folded]
    if len(exact) == 1:
        return exact[0], []
    partial = [item for item in groups if folded in item[1].casefold() or folded in item[0].casefold()]
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
    except (sqlite3.Error, RuntimeError) as exc:
        return f"error: gagal membaca daftar grup: {type(exc).__name__}: {exc}"
    target, candidates = _resolve_group(groups, raw_chat)
    if target is None:
        if candidates:
            listing = "\n".join(f"- {_label(chat_id, name)}" for chat_id, name in candidates[:LIST_LIMIT])
            return f'error: beberapa grup cocok dengan "{raw_chat}"; pakai nama lengkap atau JID:\n{listing}'
        return f'error: grup "{raw_chat}" tidak ada di arsip.\n{_known(ctx)}'
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
        results.append(await ctx.send_message(chat_id, text))
    if path is not None:
        results.append(await ctx.send_file(chat_id, path, caption))
    label = f'"{name}" ({chat_id})' if name else chat_id
    if any(result.startswith("error:") for result in results):
        return "\n".join([f"error: pengiriman ke {label} gagal:", *results])
    return "\n".join([f"ok: terkirim ke {label}", *results])


TOOL = Handler(
    name="send_to",
    family="write",
    description=(
        "Kirim teks dan/atau file ke grup lain yang sudah ada di arsip. chat = nama grup (boleh "
        "potongan) atau JID; chat kosong menampilkan daftar grup. Untuk chat aktif pakai send."
    ),
    fn=_send_to,
    aliases=("kirim_ke",),
    parameters={
        "type": "object",
        "properties": {
            "chat": {"type": "string", "description": "Nama grup, potongan nama, atau JID @g.us."},
            "text": {"type": "string", "description": "Teks yang dikirim (opsional bila ada path)."},
            "path": {"type": "string", "description": "Path file di dalam workspace (opsional)."},
            "caption": {"type": "string", "description": "Caption untuk file (opsional)."},
        },
        "additionalProperties": False,
    },
    defaults=OWNER_ONLY,
)
