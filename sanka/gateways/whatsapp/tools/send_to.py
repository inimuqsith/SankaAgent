"""Tool send_to: kirim teks/file ke grup atau DM yang sudah ada di arsip."""

from __future__ import annotations

import logging

from ....engine.registry import Handler
from ....engine.tools import ToolContext, _resolve
from .. import directory

log = logging.getLogger(__name__)

LIST_LIMIT = 20


def _known(ctx: ToolContext) -> str:
    if ctx.gateway is None:
        return "error: gateway tidak tersedia"
    groups = directory.groups(ctx.gateway.db, "", LIST_LIMIT)
    people = directory.contacts(ctx.gateway.db, "", LIST_LIMIT)
    parts: list[str] = []
    if groups:
        parts.append(f"Groups in the archive ({len(groups)}, newest first):")
        parts.extend(f"- {directory.label(chat_id, name)}" for chat_id, name in groups)
    if people:
        parts.append(f"Contacts in the archive ({len(people)}, newest first):")
        parts.extend(f"- {directory.label(chat_id, name)}" for chat_id, name in people)
    return "\n".join(parts) or "No groups or contacts in the archive yet."


def _record(ctx: ToolContext, target: str, target_name: str, kind: str, summary: str) -> None:
    if ctx.ledger is None or not ctx.chat_id or ctx.chat_id == target:
        return
    try:
        source_name = ctx.gateway.chat_name(ctx.chat_id) if ctx.gateway else ""
        ctx.ledger.log_action(
            ctx.chat_id,
            target,
            kind,
            summary,
            source_name=source_name,
            target_name=target_name,
            request=ctx.request,
        )
    except Exception:
        log.exception("gagal mencatat aksi lintas chat ke %s", target)


async def _send_to(args: dict, ctx: ToolContext) -> str:
    if ctx.gateway is None or ctx.agent is None:
        return "error: gateway tidak tersedia"
    text = str(args.get("text") or "").strip()
    raw_path = str(args.get("path") or "").strip()
    if not text and not raw_path:
        return _known(ctx)
    raw_chat = str(args.get("chat") or "").strip()
    if not raw_chat:
        return "error: chat wajib diisi dengan nama grup, kontak, atau JID"
    target, candidates = ctx.gateway.resolve_target(raw_chat)
    if target is None:
        if candidates:
            listing = "\n".join(
                f"- {directory.label(chat_id, name)}" for chat_id, name in candidates[:LIST_LIMIT]
            )
            return (
                f'error: beberapa target cocok dengan "{raw_chat}"; '
                f"pakai nama lengkap atau JID:\n{listing}"
            )
        return f'error: target "{raw_chat}" tidak ada di arsip.\n{_known(ctx)}'
    chat_id, name = target
    if chat_id != ctx.chat_id and chat_id not in ctx.reads:
        label = directory.label(chat_id, name)
        return (
            f'error: baca dulu arsip {label} dengan chat_history chat="{raw_chat}" '
            "sebelum mengirim; tidak boleh kirim buta."
        )
    path = None
    if raw_path:
        path = _resolve(ctx, raw_path)
        if not path.is_file():
            return f"error: file tidak ditemukan: {raw_path}"
    caption = str(args.get("caption") or "").strip()[:1024]
    results: list[str] = []
    if text:
        result = await ctx.agent.send_text(chat_id, text, gateway=ctx.gateway)
        results.append(result)
        ok = not result.startswith("error:")
        _record(ctx, chat_id, name, "teks", text if ok else f"gagal — {result}")
        if ok:
            ctx.sent.append(
                {"tool": "send_to", "target": chat_id, "kind": "teks", "summary": text[:200]}
            )
    if path is not None:
        result = await ctx.agent.send_file(chat_id, path, caption, gateway=ctx.gateway)
        results.append(result)
        ok = not result.startswith("error:")
        summary = f"{path.name}{f' — {caption}' if caption else ''}"
        _record(ctx, chat_id, name, "file", summary if ok else f"gagal — {result}")
        if ok:
            ctx.sent.append(
                {"tool": "send_to", "target": chat_id, "kind": "file", "summary": summary}
            )
    label = directory.label(chat_id, name)
    if any(result.startswith("error:") for result in results):
        return "\n".join([f"error: pengiriman ke {label} gagal:", *results])
    return "\n".join([f"ok: terkirim ke {label}", *results])


TOOL = Handler(
    name="send_to",
    family="write",
    description=(
        "Kirim teks dan/atau file ke grup atau chat pribadi (DM) yang sudah ada di arsip. chat = "
        "nama grup/kontak (boleh potongan) atau JID; chat kosong menampilkan daftar grup dan kontak. "
        "Untuk chat aktif pakai send. Baca dulu arsip target dengan chat_history chat=... sebelum "
        "mengirim; kiriman buta ditolak."
    ),
    fn=_send_to,
    aliases=("kirim_ke",),
    parameters={
        "type": "object",
        "properties": {
            "chat": {
                "type": "string",
                "description": "Group/contact name, partial name, or JID @g.us/@s.whatsapp.net/@lid.",
            },
            "text": {"type": "string", "description": "Text to send (optional when a path is given)."},
            "path": {"type": "string", "description": "File path inside the workspace (optional)."},
            "caption": {"type": "string", "description": "Optional file caption."},
        },
        "additionalProperties": False,
    },
)
