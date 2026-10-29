"""Antrean approval: aksi sensitif menunggu /approve, terikat chat+pengirim+TTL."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from sqlalchemy import select, update

from .config import ApprovalConfig
from .db import Database
from .schema import approvals


@dataclass(frozen=True)
class Approval:
    id: int
    created_at: float
    expires_at: float
    chat_id: str
    sender: str
    gateway: str
    kind: str
    tool: str
    args: dict
    payload: dict
    summary: str
    diff: str
    status: str
    decided_by: str
    decided_at: float | None
    result: str

    @property
    def roles(self) -> tuple[str, ...]:
        found = self.payload.get("roles") if isinstance(self.payload, dict) else None
        return tuple(str(item) for item in found) if isinstance(found, list) else ()


class ApprovalStore:
    def __init__(self, db: Database, config: ApprovalConfig) -> None:
        self.db = db
        self.configure(config)

    def configure(self, config: ApprovalConfig) -> None:
        self.config = config

    def _approval(self, row: dict) -> Approval:
        return Approval(
            id=int(row["id"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            chat_id=str(row["chat_id"]),
            sender=str(row["sender"]),
            gateway=str(row["gateway"]),
            kind=str(row["kind"]),
            tool=str(row["tool"]),
            args=row["args"] if isinstance(row["args"], dict) else {},
            payload=row["payload"] if isinstance(row["payload"], dict) else {},
            summary=str(row["summary"]),
            diff=str(row["diff"]),
            status=str(row["status"]),
            decided_by=str(row["decided_by"]),
            decided_at=float(row["decided_at"]) if row["decided_at"] else None,
            result=str(row["result"]),
        )

    @staticmethod
    def _fingerprint(kind: str, tool: str, args: dict, payload: dict) -> str:
        return json.dumps(
            {"kind": kind, "tool": tool, "args": args, "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

    async def create(
        self,
        *,
        kind: str,
        chat_id: str,
        sender: str,
        gateway: str = "",
        tool: str = "",
        args: dict | None = None,
        payload: dict | None = None,
        summary: str = "",
        diff: str = "",
        roles: tuple[str, ...] = (),
    ) -> Approval:
        self.expire()
        args = dict(args or {})
        payload = dict(payload or {})
        if roles:
            payload["roles"] = list(roles)
        wanted = self._fingerprint(kind, tool, args, payload)
        for existing in self.pending(chat_id, sender):
            if self._fingerprint(existing.kind, existing.tool, existing.args, existing.payload) == wanted:
                return existing
        now = time.time()
        row = {
            "created_at": now,
            "expires_at": now + float(self.config.ttl_seconds),
            "chat_id": chat_id,
            "sender": sender,
            "gateway": gateway,
            "kind": kind,
            "tool": tool,
            "args": args,
            "payload": payload,
            "summary": summary,
            "diff": diff,
            "status": "pending",
            "decided_by": "",
            "decided_at": None,
            "result": "",
        }
        with self.db.connect() as conn:
            result = conn.execute(approvals.insert().values(row))
            new_id = int(result.inserted_primary_key[0])
        found = self.get(new_id)
        if found is None:  # pragma: no cover - baris baru selalu terbaca
            raise RuntimeError("approval baru tidak terbaca")
        return found

    def get(self, approval_id: int | str) -> Approval | None:
        try:
            key = int(approval_id)
        except (TypeError, ValueError):
            return None
        found = self.db.fetch_one(select(approvals).where(approvals.c.id == key))
        return self._approval(found) if found else None

    def pending(self, chat_id: str, sender: str) -> list[Approval]:
        now = time.time()
        statement = (
            select(approvals)
            .where(
                approvals.c.chat_id == chat_id,
                approvals.c.sender == sender,
                approvals.c.status == "pending",
                approvals.c.expires_at >= now,
            )
            .order_by(approvals.c.created_at)
        )
        return [self._approval(row) for row in self.db.fetch_all(statement)]

    def latest(self, chat_id: str, sender: str) -> Approval | None:
        found = self.pending(chat_id, sender)
        return found[-1] if found else None

    def count_pending(self) -> int:
        found = self.db.fetch_all(
            select(approvals.c.id).where(
                approvals.c.status == "pending", approvals.c.expires_at >= time.time()
            )
        )
        return len(found)

    def mark(self, approval_id: int | str, status: str, by: str, result: str = "") -> bool:
        try:
            key = int(approval_id)
        except (TypeError, ValueError):
            return False
        with self.db.connect() as conn:
            outcome = conn.execute(
                update(approvals)
                .where(approvals.c.id == key, approvals.c.status == "pending")
                .values(
                    status=status,
                    decided_by=by,
                    decided_at=time.time(),
                    result=str(result or ""),
                )
            )
            return int(outcome.rowcount or 0) == 1

    def expire(self) -> int:
        with self.db.connect() as conn:
            outcome = conn.execute(
                update(approvals)
                .where(approvals.c.status == "pending", approvals.c.expires_at < time.time())
                .values(status="expired", decided_at=time.time())
            )
            return int(outcome.rowcount or 0)
