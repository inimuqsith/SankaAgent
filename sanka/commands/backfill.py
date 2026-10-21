"""Command /backfill: pair perangkat sementara untuk menarik riwayat lama ke arsip."""

from __future__ import annotations

import asyncio
import io
import logging

import segno
from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, HistorySyncEv
from neonize.utils.jid import build_jid

from ..config import drop_session, secure_db
from ..history import history_props
from ..registry import Handler
from . import CommandContext

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


async def _run(ctx: CommandContext) -> None:
    app = ctx.app
    config = app.config
    main = app.client
    store = app.sessions.store
    if main is None or store is None:
        return
    session = config.whatsapp.session_db.parent / "backfill.db"
    drop_session(session)
    client = NewAClient(str(session), props=history_props(True))
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
                caption="Scan QR ini dari WhatsApp > Perangkat tertaut. QR cepat kedaluwarsa.",
            )
        except Exception:
            log.exception("gagal mengirim QR backfill")

    @client.event(ConnectedEv)
    async def on_connected(accepted: NewAClient, ev) -> None:
        nonlocal connected
        connected = True
        secure_db(session)
        await _notify(main, ctx.chat_id, "Perangkat sementara tersambung. Menunggu history sync dari ponsel...")

    @client.event(HistorySyncEv)
    async def on_history(accepted: NewAClient, ev) -> None:
        nonlocal total
        if ev.Data is None:
            return
        try:
            count, chats, finished = await asyncio.to_thread(
                store.ingest_sync,
                ev.Data,
                config.whatsapp.bot_name,
            )
        except Exception:
            log.exception("backfill gagal menyimpan history sync")
            return
        total += count
        for chat_id in chats:
            app.sessions.invalidate(chat_id)
        log.info(
            "backfill: %d pesan, %d chat, sync=%s progress=%d%s",
            count,
            len(chats),
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
                main,
                ctx.chat_id,
                f"Backfill selesai: {total} pesan masuk ke arsip. Perangkat sementara akan logout.",
            )
        else:
            await _notify(
                main,
                ctx.chat_id,
                "Backfill tidak menerima history sync. Pastikan QR dipindai dan ponsel online.",
            )
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
        app.backfill = None


async def backfill(ctx: CommandContext) -> str:
    app = ctx.app
    if app.client is None or app.sessions.store is None:
        return "Arsip atau client WhatsApp belum siap."
    if ctx.chat_id.endswith("@g.us"):
        return "Kirim /backfill lewat DM supaya QR tidak terekspos di grup."
    if app.backfill is not None and not app.backfill.done():
        return "Backfill masih berjalan."
    app.backfill = asyncio.create_task(_run(ctx))
    return "Menyiapkan perangkat sementara. QR akan dikirim di DM ini; scan dari WhatsApp > Perangkat tertaut."


COMMAND = Handler(
    name="backfill",
    description="Tarik riwayat lama lewat perangkat sementara (scan QR sekali).",
    fn=backfill,
    defaults={"owner": True, "member": False},
)
