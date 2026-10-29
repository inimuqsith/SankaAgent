"""Lease primary tunggal: satu jantung, instance lain standby dan beku."""

from __future__ import annotations

import os
import socket
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select, text, update

from .db import Database
from .schema import engine_control, engine_lease

TTL = 30.0


@dataclass(frozen=True)
class LeaseInfo:
    instance_id: str
    host: str
    pid: int
    mode: str
    started_at: float
    heartbeat_at: float


class Lease:
    def __init__(
        self,
        db: Database,
        instance_id: str | None = None,
        host: str | None = None,
        pid: int | None = None,
        mode: str = "primary",
        ttl: float = TTL,
    ) -> None:
        self.db = db
        self.instance_id = instance_id or uuid.uuid4().hex
        self.host = host or socket.gethostname()
        self.pid = int(pid if pid is not None else os.getpid())
        self.mode = mode
        self.ttl = float(ttl)

    def _info(self, row) -> LeaseInfo:
        return LeaseInfo(
            instance_id=str(row["instance_id"]),
            host=str(row["host"]),
            pid=int(row["pid"]),
            mode=str(row["mode"]),
            started_at=float(row["started_at"]),
            heartbeat_at=float(row["heartbeat_at"]),
        )

    def acquire(self, *, takeover: bool = False) -> LeaseInfo | None:
        now = time.time()
        with self.db.connect() as conn:
            control = conn.execute(
                select(engine_control).where(engine_control.c.id == 1)
            ).mappings().first()
            # Freeze berlaku untuk semua instance (termasuk yang meminta freeze),
            # sampai ada takeover eksplisit yang membersihkannya.
            if control is not None and bool(control["frozen"]) and not takeover:
                return None
            row = conn.execute(
                select(engine_lease).where(engine_lease.c.id == 1)
            ).mappings().first()
            if row is None:
                conn.execute(
                    engine_lease.insert().values(
                        id=1,
                        instance_id=self.instance_id,
                        host=self.host,
                        pid=self.pid,
                        mode=self.mode,
                        started_at=now,
                        heartbeat_at=now,
                    )
                )
                if takeover:
                    self._clear_freeze(conn, now)
                return LeaseInfo(self.instance_id, self.host, self.pid, self.mode, now, now)
            expired = float(row["heartbeat_at"] or 0.0) < now - self.ttl
            if row["instance_id"] != self.instance_id and not expired and not takeover:
                return None
            conn.execute(
                update(engine_lease)
                .where(engine_lease.c.id == 1)
                .values(
                    instance_id=self.instance_id,
                    host=self.host,
                    pid=self.pid,
                    mode=self.mode,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            if takeover:
                self._clear_freeze(conn, now)
            return LeaseInfo(self.instance_id, self.host, self.pid, self.mode, now, now)

    def renew(self) -> bool:
        now = time.time()
        with self.db.connect() as conn:
            result = conn.execute(
                update(engine_lease)
                .where(
                    and_(
                        engine_lease.c.id == 1,
                        engine_lease.c.instance_id == self.instance_id,
                    )
                )
                .values(heartbeat_at=now)
            )
            return int(result.rowcount or 0) == 1

    def release(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                update(engine_lease)
                .where(
                    and_(
                        engine_lease.c.id == 1,
                        engine_lease.c.instance_id == self.instance_id,
                    )
                )
                .values(instance_id="", heartbeat_at=0.0)
            )

    def holder(self) -> LeaseInfo | None:
        found = self.db.fetch_one(select(engine_lease).where(engine_lease.c.id == 1))
        if not found or not found.get("instance_id"):
            return None
        return self._info(found)

    @staticmethod
    def _clear_freeze(conn, now: float) -> None:
        conn.execute(
            update(engine_control)
            .where(engine_control.c.id == 1)
            .values(frozen=False, frozen_by="", frozen_at=now)
        )

    def request_freeze(self, by: str) -> None:
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                update(engine_control)
                .where(engine_control.c.id == 1)
                .values(frozen=True, frozen_by=by, frozen_at=now)
            )

    def clear_freeze(self) -> None:
        with self.db.connect() as conn:
            self._clear_freeze(conn, time.time())

    def frozen(self) -> bool:
        found = self.db.fetch_one(
            select(engine_control.c.frozen).where(engine_control.c.id == 1)
        )
        return bool(found and found["frozen"])


def lock_data_dir(path) -> object | None:
    """Kunci data dir host-local untuk `run`; None bila platform tidak mendukung fcntl."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(f"data dir sedang dipakai proses lain: {path}")
    handle.write(str(os.getpid()))
    handle.flush()
    return handle  # tutup = lepas lock
