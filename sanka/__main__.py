"""Entrypoint: `python -m sanka` untuk bot, `python -m sanka selftest` untuk uji cepat."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, GroupInfoEv, HistorySyncEv, MessageEv

from .registry import RegistryError

try:
    from .commands import REGISTRY as COMMANDS
    from .config import Config, RoleConfig, drop_session, load_config, load_env, secure_db
    from .history import HistoryStore, history_props
    from .llm import API_ERROR_TEXT, LLM
    from .mcp import Hub
    from .permissions import commands_for, tool_patterns, tools_for
    from .tools import REGISTRY, ToolContext, execute
    from .wa import App, Sessions, bot_digits, handle_message
except RegistryError as exc:  # plugin invalid menggagalkan start dengan satu pesan
    sys.exit(f"sanka: {exc}")

log = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _role(config: Config) -> RoleConfig:
    for role in config.roles.values():
        if "*" in (role.tools or ()):
            return role
    return config.roles[config.default_role]


def _report(label: str, outcome: str, detail: str = "") -> bool:
    print(f"{outcome} {label}{(' - ' + detail) if detail else ''}", flush=True)
    return outcome != "FAIL"


async def selftest(config: Config) -> int:
    hub = Hub(config.mcp)
    await hub.start()
    try:
        role = _role(config)
        for name, item in config.roles.items():
            tools = [handler.name for handler in tools_for(item)]
            tools += [handler.name for handler in hub.specs(item)]
            commands = [handler.name for handler in commands_for(item)]
            print(f"role {name}: command={', '.join(commands) or '-'} | tool={', '.join(tools) or '-'}", flush=True)

        ctx = ToolContext(config, role, tool_patterns(role))
        specs = {handler.name: handler for handler in REGISTRY.by_name.values() if not handler.hidden}
        results: list[bool] = []

        wrote = await execute(specs, "write", {"path": "selftest.txt", "content": "sanka-selftest"}, ctx)
        read = await execute(specs, "read", {"path": "selftest.txt"}, ctx)
        passed = wrote.startswith("ok:") and "sanka-selftest" in read
        results.append(_report("write/read", "PASS" if passed else "FAIL", "" if passed else f"{wrote} | {read[:80]}"))

        shell = await execute(specs, "shell", {"command": "echo shell-ok"}, ctx)
        passed = "shell-ok" in shell
        results.append(_report("shell", "PASS" if passed else "FAIL", "" if passed else shell[:120]))

        if shutil.which(config.sandbox.runtime) is None:
            _report("sandbox", "SKIP", f"{config.sandbox.runtime} tidak ada")
        else:
            box = await execute(
                specs,
                "sandbox",
                {"command": "echo sandbox-ok && python3 -V", "timeout": 300},
                ctx,
            )
            passed = "sandbox-ok" in box
            results.append(_report("sandbox", "PASS" if passed else "FAIL", "" if passed else box[:200]))

        search = await execute(specs, "web_search", {"query": "python asyncio", "max_results": 3}, ctx)
        passed = bool(search.strip()) and not search.startswith("error:")
        results.append(_report("web_search", "PASS" if passed else "FAIL", "" if passed else search[:200]))

        llm = LLM(config)
        answer = await llm.run("balas: ok", "ping", [], ctx, session="sanka-selftest")
        passed = bool(answer.strip()) and answer != API_ERROR_TEXT
        results.append(_report("llm", "PASS" if passed else "FAIL", "" if passed else answer[:200]))

        for name in config.mcp:
            found = [spec.name for spec in hub.specs(role) if spec.name.startswith(f"mcp__{name}__")]
            print(f"mcp {name}: {', '.join(found) or '(tidak ada tool)'}", flush=True)
    finally:
        await hub.stop()

    if all(results):
        print("SELFTEST OK", flush=True)
        return 0
    print("SELFTEST GAGAL", flush=True)
    return 1


async def run_bot(config: Config) -> None:
    hub = Hub(config.mcp)
    await hub.start()
    log.info("command: %s", ", ".join(COMMANDS.names()) or "-")
    log.info("tool: %s", ", ".join(REGISTRY.names()) or "-")
    for role in config.roles.values():
        if "*" in (role.tools or ()) or "*" in (role.commands or ()):
            log.info("role %s memakai wildcard; handler baru otomatis diizinkan", role.name)
    store: HistoryStore | None = None
    try:
        try:
            path = config.whatsapp.history_db or config.whatsapp.session_db.parent / "history.db"
            store = HistoryStore(path, config.workspace, config.whatsapp.session_db)
        except Exception:  # arsip gagal dibuka tidak boleh mematikan bot
            log.exception("gagal membuka arsip riwayat; konteks hanya di RAM")
        app = App(config, LLM(config), hub, Sessions(store=store))
        client = NewAClient(
            str(config.whatsapp.session_db),
            props=history_props(store.needs_full_sync() if store is not None else True),
        )
        app.client = client

        @client.event(MessageEv)
        async def on_message(accepted: NewAClient, ev) -> None:
            if not app.bot_digits and accepted.me is not None:
                app.bot_digits = bot_digits(accepted.me)
            try:
                await handle_message(accepted, ev, app)
            except Exception:
                log.exception("handler pesan gagal")

        @client.event(HistorySyncEv)
        async def on_history(accepted: NewAClient, ev) -> None:
            if store is None or ev.Data is None:
                return
            try:
                count, chats, done = await asyncio.to_thread(
                    store.ingest_sync,
                    ev.Data,
                    app.config.whatsapp.bot_name,
                )
                for chat_id in chats:
                    app.sessions.invalidate(chat_id)
                log.info(
                    "history sync: %d pesan, %d chat, sync=%s progress=%d%s",
                    count,
                    len(chats),
                    ev.Data.syncType,
                    ev.Data.progress,
                    " (selesai)" if done else "",
                )
            except Exception:
                log.exception("ingest history sync gagal")

        @client.event(GroupInfoEv)
        async def on_group_info(accepted: NewAClient, ev) -> None:
            if store is None or not ev.JID.User:
                return
            name = getattr(ev.Name, "Name", "").strip()
            if not name:
                return
            try:
                store.set_chat_name(f"{ev.JID.User}@{ev.JID.Server}", name)
            except Exception:
                log.exception("gagal menyimpan nama grup %s", ev.JID.User)

        @client.event(ConnectedEv)
        async def on_connected(accepted: NewAClient, ev) -> None:
            if accepted.me is not None:
                app.bot_digits = bot_digits(accepted.me)
            secure_db(config.whatsapp.session_db)
            if store is not None:
                try:
                    await asyncio.to_thread(store.import_contacts, config.whatsapp.session_db)
                except Exception:
                    log.exception("gagal menyegarkan kontak")
            log.info("Sanka tersambung ke WhatsApp")

        log.info("Sanka siap. Scan QR bila ini pairing pertama.")
        await client.connect()
        secure_db(config.whatsapp.session_db)
        await client.idle()
    finally:
        if store is not None:
            store.close()
        await hub.stop()


BACKFILL_TIMEOUT = 900


async def backfill(config: Config) -> int:
    """Pair perangkat sementara untuk menarik history sync, lalu logout tanpa mengganggu bot utama."""
    path = config.whatsapp.history_db or config.whatsapp.session_db.parent / "history.db"
    store = HistoryStore(path, config.workspace, config.whatsapp.session_db)
    session = config.whatsapp.session_db.parent / "backfill.db"
    drop_session(session)
    client = NewAClient(str(session), props=history_props(True))
    done = asyncio.Event()
    paired = False

    @client.event(ConnectedEv)
    async def on_connected(accepted: NewAClient, ev) -> None:
        nonlocal paired
        paired = True
        secure_db(session)
        log.info("backfill tersambung; menunggu history sync dari ponsel")

    @client.event(HistorySyncEv)
    async def on_history(accepted: NewAClient, ev) -> None:
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

    try:
        await client.connect()
        secure_db(session)
        try:
            await asyncio.wait_for(done.wait(), BACKFILL_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("backfill timeout setelah %s detik; menghentikan perangkat sementara", BACKFILL_TIMEOUT)
        if done.is_set():
            await asyncio.sleep(2)  # beri waktu chunk terakhir selesai ditulis
            log.info("backfill selesai; logout perangkat sementara")
    finally:
        logged_out = False
        if paired:
            try:
                await client.logout()
                logged_out = True
            except Exception:
                log.exception("gagal logout perangkat backfill; hapus manual di WhatsApp")
        try:
            await client.stop()
        except Exception:
            log.debug("stop client backfill gagal", exc_info=True)
        store.close()
        if logged_out:
            drop_session(session)
    return 0 if paired and done.is_set() else 1


async def main(argv: list[str]) -> int:
    setup_logging()
    load_env()
    config = load_config()
    if argv and argv[0] == "selftest":
        return await selftest(config)
    if argv and argv[0] == "backfill":
        return await backfill(config)
    await run_bot(config)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(sys.argv[1:])))
    except KeyboardInterrupt:
        sys.exit(0)
