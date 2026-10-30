"""Direktori WhatsApp: kontak, LID, nama chat, grup, dan resolusi target."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

from sqlalchemy import and_, func, select, update

from ...engine.context import ChatMessage
from ...engine.db import Database
from ...engine.permissions import normalize
from ...engine.schema import chats, messages
from .schema import wa_contacts, wa_lid_map

log = logging.getLogger(__name__)

GROUP_LIST_TIMEOUT = 30.0
LIST_LIMIT = 20


def _jid(value: str, default: str = "s.whatsapp.net") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if "@" in text else f"{text}@{default}"


def _upsert_contact(db: Database, jid: str, name: str, push_name: str) -> None:
    if not jid:
        return
    name = " ".join(str(name or "").split())[:200]
    push_name = " ".join(str(push_name or "").split())[:200]
    if not name and not push_name:
        return
    with db.connect() as conn:
        def merge(stmt):
            return {
                "name": func.coalesce(func.nullif(stmt.excluded.name, ""), wa_contacts.c.name),
                "push_name": func.coalesce(
                    func.nullif(stmt.excluded.push_name, ""), wa_contacts.c.push_name
                ),
                "updated_at": time.time(),
            }

        db.upsert(
            conn,
            wa_contacts,
            [{"jid": jid, "name": name, "push_name": push_name, "updated_at": time.time()}],
            ["jid"],
            update=merge,
        )


def set_lid(db: Database, lid: str, pn: str) -> None:
    if not lid or not pn:
        return
    lid = _jid(lid, "lid")
    pn = _jid(pn)
    with db.connect() as conn:
        db.upsert(conn, wa_lid_map, [{"lid": lid, "pn": pn}], ["lid"], update={"pn": pn})


def contact_name(db: Database, jid: str) -> str:
    if not jid:
        return ""
    direct = db.fetch_one(select(wa_contacts).where(wa_contacts.c.jid == jid))
    if jid.endswith("@lid"):
        mapped = db.fetch_one(
            select(wa_contacts)
            .select_from(wa_contacts.join(wa_lid_map, wa_contacts.c.jid == wa_lid_map.c.pn))
            .where(wa_lid_map.c.lid == jid)
        )
    else:
        mapped = db.fetch_one(
            select(wa_contacts)
            .select_from(wa_contacts.join(wa_lid_map, wa_contacts.c.jid == wa_lid_map.c.lid))
            .where(wa_lid_map.c.pn == jid)
        )
    for row in (direct, mapped):
        if row is None:
            continue
        if row.get("name"):
            return str(row["name"])
    for row in (direct, mapped):
        if row is not None and row.get("push_name"):
            return str(row["push_name"])
    return ""


def pn_jid(db: Database, jid: str) -> str:
    """JID nomor (PN) untuk sebuah LID; "" bila tidak ada di wa_lid_map."""
    if not jid.endswith("@lid"):
        return ""
    found = db.fetch_one(select(wa_lid_map.c.pn).where(wa_lid_map.c.lid == jid))
    return str(found["pn"]) if found is not None and found["pn"] else ""


def resolve_person(db: Database, jid: str) -> str:
    raw = str(jid or "")
    if not raw:
        return ""
    if raw.endswith("@lid"):
        mapped = db.fetch_one(select(wa_lid_map.c.pn).where(wa_lid_map.c.lid == raw))
        if mapped is not None and str(mapped["pn"]):
            return normalize(str(mapped["pn"]))
    return normalize(raw)


def refresh_sender_names(db: Database) -> int:
    rows = db.fetch_all(
        select(messages.c.id, messages.c.sender_address, messages.c.sender_name, messages.c.sender_id)
        .where(and_(messages.c.sender_address != "", messages.c.outgoing.is_(False)))
    )
    changed = 0
    for row in rows:
        name = contact_name(db, str(row["sender_address"]))
        if not name or name == row["sender_name"]:
            continue
        current = str(row["sender_name"] or "")
        if current and current not in {"", str(row["sender_id"] or "")}:
            continue
        with db.connect() as conn:
            conn.execute(
                update(messages).where(messages.c.id == row["id"]).values(sender_name=name)
            )
        changed += 1
    return changed


def import_contacts(db: Database, session_db: Path | str) -> int:
    """Salin kontak dari DB sesi Neonize (read-only) lalu rapikan nama pengirim lama."""
    path = Path(session_db).expanduser()
    if not path.is_file():
        return 0
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        source.row_factory = sqlite3.Row
        contacts = source.execute(
            "SELECT their_jid, first_name, full_name, push_name FROM whatsmeow_contacts"
        ).fetchall()
        mappings = source.execute("SELECT lid, pn FROM whatsmeow_lid_map").fetchall()
        source.close()
    except sqlite3.Error:
        log.debug("kontak sesi tidak bisa dibaca: %s", path, exc_info=True)
        return 0
    for row in mappings:
        set_lid(db, str(row["lid"]), str(row["pn"]))
    with db.connect() as conn:
        conn.execute(wa_lid_map.delete().where(~wa_lid_map.c.lid.like("%@%")))
    for row in contacts:
        _upsert_contact(
            db,
            str(row["their_jid"] or ""),
            str(row["full_name"] or row["first_name"] or ""),
            str(row["push_name"] or ""),
        )
    refresh_sender_names(db)
    return len(contacts)


def chat_name(db: Database, chat_id: str) -> str:
    if not chat_id:
        return ""
    found = db.fetch_one(select(chats.c.name).where(chats.c.chat_id == chat_id))
    name = str(found["name"]) if found else ""
    if name or not chat_id.endswith(("@s.whatsapp.net", "@lid")):
        return name
    fallback = contact_name(db, chat_id)
    if fallback:
        set_chat_name(db, chat_id, fallback)
    return fallback


def set_chat_name(db: Database, chat_id: str, name: str) -> None:
    value = " ".join(str(name or "").split())[:200]
    if not chat_id or not value:
        return
    with db.connect() as conn:
        db.upsert(
            conn,
            chats,
            [{"chat_id": chat_id, "name": value, "reset_ts": 0.0, "kind": "group" if chat_id.endswith("@g.us") else "dm"}],
            ["chat_id"],
            update={"name": value},
        )


def groups(db: Database, query: str = "", limit: int = LIST_LIMIT) -> list[tuple[str, str]]:
    limit = max(1, min(int(limit or LIST_LIMIT), 50))
    term = str(query or "").strip().casefold()
    rows = db.fetch_all(select(chats).where(chats.c.chat_id.like("%@g.us")))
    last = {
        row["chat_id"]: row["last"]
        for row in db.fetch_all(
            select(messages.c.chat_id, func.max(messages.c.ts).label("last"))
            .where(messages.c.chat_id.like("%@g.us"))
            .group_by(messages.c.chat_id)
        )
    }
    found: dict[str, tuple[str, float]] = {}
    for row in rows:
        chat_id = str(row["chat_id"])
        name = str(row["name"] or "")
        found[chat_id] = (name, float(last.get(chat_id, 0.0)))
    for chat_id, stamp in last.items():
        found.setdefault(str(chat_id), ("", float(stamp)))
    items = [
        (chat_id, name, stamp)
        for chat_id, (name, stamp) in found.items()
        if not term or term in name.casefold() or term in chat_id.casefold()
    ]
    items.sort(key=lambda item: (-item[2], item[0]))
    return [(chat_id, name) for chat_id, name, _stamp in items[:limit]]


def contacts(db: Database, query: str = "", limit: int = LIST_LIMIT) -> list[tuple[str, str]]:
    limit = max(1, min(int(limit or LIST_LIMIT), 50))
    term = str(query or "").strip().casefold()
    rows = db.fetch_all(
        select(wa_contacts, wa_lid_map.c.pn)
        .select_from(wa_contacts.outerjoin(wa_lid_map, wa_lid_map.c.lid == wa_contacts.c.jid))
        .order_by(wa_contacts.c.updated_at.desc())
    )
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        jid = str(row.get("pn") or row["jid"] or "")
        if "@" not in jid:
            jid = f"{jid}@s.whatsapp.net"
        if not jid or jid in seen:
            continue
        name = str(row.get("name") or row.get("push_name") or "")
        if term and term not in name.casefold() and term not in jid.casefold():
            continue
        seen.add(jid)
        found.append((jid, name))
        if len(found) >= limit:
            break
    return found


def list_targets(db: Database, query: str = "", limit: int = LIST_LIMIT) -> list[tuple[str, str]]:
    return [*groups(db, query, limit), *contacts(db, query, limit)]


def resolve_target(
    targets: list[tuple[str, str]], raw: str
) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]:
    """Cocokkan JID, nama persis, lalu potongan unik; tanpa fallback kontak tunggal."""
    value = str(raw or "").strip()
    if not value:
        return None, []
    for item in targets:
        if item[0] == value:
            return item, []
    folded = value.casefold()
    exact = [item for item in targets if item[1].strip().casefold() == folded]
    if len(exact) == 1:
        return exact[0], []
    partial = [
        item for item in targets if folded in item[1].casefold() or folded in item[0].casefold()
    ]
    if len(partial) == 1:
        return partial[0], []
    return None, exact or partial


def group_name(info) -> str:
    return " ".join(str(getattr(getattr(info, "GroupName", None), "Name", "") or "").split())


async def sync_group_names(client, db: Database, timeout: float = GROUP_LIST_TIMEOUT) -> int:
    getter = getattr(client, "get_joined_groups", None)
    if not callable(getter):
        return 0
    try:
        found = await asyncio.wait_for(getter(), timeout)
    except Exception:
        log.warning("gagal mengambil daftar grup", exc_info=True)
        return 0
    saved = 0
    for info in found:
        name = group_name(info)
        if not getattr(info.JID, "User", "") or not name:
            continue
        set_chat_name(db, f"{info.JID.User}@{info.JID.Server}", name)
        saved += 1
    return saved


def label(chat_id: str, name: str) -> str:
    return f"{name} ({chat_id})" if name else chat_id


def message_jid(message: ChatMessage) -> str:
    return message.sender_jid
