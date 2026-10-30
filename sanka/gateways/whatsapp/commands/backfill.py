"""Command /backfill: pair perangkat sementara untuk menarik riwayat lama ke arsip."""

from __future__ import annotations

import asyncio
import io
import logging

import segno
from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, HistorySyncEv
from neonize.utils.jid import build_jid

from ....engine.commands import CommandContext
from ....engine.config import drop_session, secure_db
from ....engine.i18n import t
from ....engine.registry import Handler
from .. import directory, history_sync, ingress

log = logging.getLogger(__name__)
TIMEOUT = 900
MAX_QR = 5


def _jid(chat_id: str):
    user, _, server = chat_id.partition("@")
    return build_jid(user, server)


async def _notify(client: NewAClient, chat_id: str, text: str) -> None:
    try:
        await client.send_message(_jid(chat_id), text)
    except Exception:
        log.exception("gagal mengirim notifikasi backfill ke %s", chat_id)


async def _run(ctx: CommandContext, lang: str) -> None:
    gateway = ctx.gateway
    agent = ctx.agent
    main = getattr(gateway, "client", None)
    session_db = getattr(getattr(gateway, "settings", None), "session_db", None)
    if main is None or session_db is None:
        return
    session = session_db.parent / "backfill.db"
    drop_session(session)
    client = NewAClient(str(session), props=history_sync.history_props(True))
    done = asyncio.Event()
    connected = False
    total = 0
    qr_count = 0

    async def on_qr(accepted: NewAClient, data: bytes) -> None:
        nonlocal qr_count
        if qr_count >= MAX_QR:
            return
        qr_count += 1
        try:
            image = io.BytesIO()
            segno.make_qr(data).save(image, kind="png", scale=8)
            await main.send_image(
                _jid(ctx.chat_id),
                image.getvalue(),
                caption=t("gateway.backfill.qr_caption", lang),
            )
        except Exception:
            log.exception("gagal mengirim QR backfill")

    @client.event(ConnectedEv)
    async def on_connected(accepted: NewAClient, ev) -> None:
        nonlocal connected
        connected = True
        secure_db(session)
        try:
            named = await directory.sync_group_names(accepted, gateway.db)
            log.info("backfill: nama grup tersinkron: %d", named)
        except Exception:
            log.exception("backfill gagal menyinkronkan nama grup")
        await _notify(main, ctx.chat_id, t("gateway.backfill.connected", lang))

    @client.event(HistorySyncEv)
    async def on_history(accepted: NewAClient, ev) -> None:
        nonlocal total
        if ev.Data is None:
            return
        try:
            count, chat_ids, finished = history_sync.ingest_sync(
                gateway.db, gateway.history, ev.Data, ingress.self_jid(accepted.me)
            )
        except Exception:
            log.exception("backfill gagal menyimpan history sync")
            return
        total += count
        for chat_id in chat_ids:
            agent.windows.invalidate(chat_id)
        log.info(
            "backfill: %d pesan, %d chat, sync=%s progress=%d%s",
            count,
            len(chat_ids),
            ev.Data.syncType,
            ev.Data.progress,
            " (selesai)" if finished else "",
        )
        if finished:
            done.set()

    client.event.qr(on_qr)
    try:
        await client.connect()
        secure_db(session)
        try:
            await asyncio.wait_for(done.wait(), TIMEOUT)
        except TimeoutError:
            log.warning("backfill timeout setelah %s detik", TIMEOUT)
        if done.is_set():
            await asyncio.sleep(2)
            await _notify(
                main, ctx.chat_id, t("gateway.backfill.finished", lang, count=total)
            )
        else:
            await _notify(main, ctx.chat_id, t("gateway.backfill.no_sync", lang))
    finally:
        logged_out = False
        if connected:
            try:
                await client.logout()
                logged_out = True
            except Exception:
                log.exception("gagal logout perangkat backfill")
        try:
            await client.stop()
        except Exception:
            log.debug("stop client backfill gagal", exc_info=True)
        if logged_out:
            drop_session(session)
        gateway.backfill = None


async def backfill(ctx: CommandContext) -> str:
    gateway = ctx.gateway
    lang = ctx.lang
    if getattr(gateway, "client", None) is None or getattr(gateway, "db", None) is None:
        return t("gateway.backfill.not_ready", lang)
    if ctx.chat_id.endswith("@g.us"):
        return t("gateway.backfill.group_denied", lang)
    running = getattr(gateway, "backfill", None)
    if running is not None and not running.done():
        return t("gateway.backfill.running", lang)
    gateway.backfill = asyncio.create_task(_run(ctx, lang))
    return t("gateway.backfill.started", lang)


COMMAND = Handler(
    name="backfill",
    description="🗂️ Tarik riwayat lama lewat perangkat sementara (scan QR sekali).",
    fn=backfill,
)
