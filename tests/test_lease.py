"""Kontrak lease: acquire, renew, TTL, takeover, dan freeze."""

from __future__ import annotations

import time

from sqlalchemy import update

from sanka.engine.lease import Lease
from sanka.engine.schema import engine_lease
from support import database


def lease(db, name: str, host: str = "host-a", pid: int = 1) -> Lease:
    return Lease(db, instance_id=name, host=host, pid=pid)


def test_acquire_renew_release(tmp_path) -> None:
    db = database(tmp_path)
    first = lease(db, "a")
    holder = first.acquire()
    assert holder is not None and holder.instance_id == "a"
    assert first.acquire() is not None
    assert first.renew() is True
    assert first.holder().pid == 1
    first.release()
    assert first.holder() is None
    assert first.renew() is False


def test_second_instance_stands_by_and_takeover(tmp_path) -> None:
    db = database(tmp_path)
    first = lease(db, "a")
    second = lease(db, "b", host="host-b", pid=2)
    assert first.acquire() is not None
    assert second.acquire() is None
    assert second.holder().instance_id == "a"

    taken = second.acquire(takeover=True)
    assert taken is not None and taken.instance_id == "b"
    assert first.renew() is False


def test_expired_heartbeat_allows_acquire(tmp_path) -> None:
    db = database(tmp_path)
    first = lease(db, "a")
    first.acquire()
    with db.connect() as conn:
        conn.execute(update(engine_lease).values(heartbeat_at=time.time() - 120))
    second = lease(db, "b")
    assert second.acquire() is not None


def test_freeze_blocks_acquire_until_cleared(tmp_path) -> None:
    db = database(tmp_path)
    first = lease(db, "a")
    second = lease(db, "b")
    first.acquire()
    first.release()
    first.request_freeze("a")
    assert first.frozen() is True
    assert second.acquire() is None
    assert second.acquire(takeover=True) is not None
    assert second.frozen() is False


def test_ensure_does_not_clear_freeze(tmp_path) -> None:
    db = database(tmp_path)
    lease(db, "a").request_freeze("a")
    db.ensure()
    db.ensure()
    assert lease(db, "b").frozen() is True
