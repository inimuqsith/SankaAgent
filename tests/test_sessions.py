"""SessionStore tests: TTL, reset, stale appends, and history trimming."""

from __future__ import annotations

from collections.abc import Iterator

from sanka.sessions import SessionStore


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def id_factory(*values: str) -> Iterator[str]:
    return iter(values)


def test_get_or_create_reuses_session() -> None:
    clock = FakeClock()
    ids = id_factory("session-1", "session-2")
    store = SessionStore(ttl_seconds=60, clock=clock, id_factory=ids.__next__)

    session = store.get_or_create("chat-1")

    assert session.chat_id == "chat-1"
    assert session.opencode_session_id == "session-1"
    assert session.history == []
    assert session.updated_at == clock.now
    assert store.session_count == 1
    assert store.get_or_create("chat-1") is session
    assert store.get("missing") is None


def test_expired_session_evicted_on_get() -> None:
    clock = FakeClock()
    store = SessionStore(
        ttl_seconds=60, clock=clock, id_factory=id_factory("s1").__next__
    )
    store.get_or_create("chat-1")

    clock.advance(59)
    assert store.get("chat-1") is not None

    clock.advance(1)
    assert store.get("chat-1") is None
    assert store.session_count == 0


def test_evict_expired_removes_only_old_sessions() -> None:
    clock = FakeClock()
    ids = id_factory("s1", "s2")
    store = SessionStore(ttl_seconds=60, clock=clock, id_factory=ids.__next__)

    store.get_or_create("old")
    clock.advance(40)
    store.get_or_create("fresh")
    clock.advance(25)

    assert store.evict_expired() == 1
    assert store.get("old") is None
    assert store.get("fresh") is not None
    assert store.session_count == 1


def test_reset_changes_id_and_clears_history() -> None:
    clock = FakeClock()
    ids = id_factory("s1", "s2")
    store = SessionStore(ttl_seconds=60, clock=clock, id_factory=ids.__next__)

    original = store.get_or_create("chat-1")
    assert store.append("chat-1", original.opencode_session_id, "user", "halo")
    clock.advance(5)

    reset = store.reset("chat-1")

    assert reset is not original
    assert reset.opencode_session_id == "s2"
    assert reset.history == []
    assert reset.updated_at == clock.now
    assert store.session_count == 1
    assert store.history("chat-1") == []
    assert store.get("chat-1") is reset


def test_append_with_stale_id_is_ignored() -> None:
    clock = FakeClock()
    ids = id_factory("s1", "s2")
    store = SessionStore(ttl_seconds=60, clock=clock, id_factory=ids.__next__)

    original = store.get_or_create("chat-1")
    store.reset("chat-1")

    assert store.append("chat-1", original.opencode_session_id, "user", "telat") is False
    assert store.history("chat-1") == []


def test_append_to_unknown_chat_is_ignored() -> None:
    store = SessionStore(ttl_seconds=60, clock=FakeClock())
    assert store.append("nobody", "whatever", "user", "halo") is False
    assert store.session_count == 0


def test_append_to_expired_session_is_ignored() -> None:
    clock = FakeClock()
    store = SessionStore(
        ttl_seconds=60, clock=clock, id_factory=id_factory("s1").__next__
    )
    session = store.get_or_create("chat-1")
    clock.advance(61)

    assert store.append("chat-1", session.opencode_session_id, "user", "telat") is False
    assert store.session_count == 0
    assert store.history("chat-1") == []


def test_append_refreshes_updated_at() -> None:
    clock = FakeClock()
    store = SessionStore(
        ttl_seconds=60, clock=clock, id_factory=id_factory("s1").__next__
    )
    session = store.get_or_create("chat-1")
    clock.advance(30)

    assert store.append("chat-1", session.opencode_session_id, "user", "halo")
    assert session.updated_at == clock.now

    clock.advance(30)
    assert store.get("chat-1") is session


def test_history_returns_copy() -> None:
    store = SessionStore(
        ttl_seconds=60, clock=FakeClock(), id_factory=id_factory("s1").__next__
    )
    session = store.get_or_create("chat-1")
    store.append("chat-1", session.opencode_session_id, "user", "halo")

    snapshot = store.history("chat-1")
    snapshot[0]["content"] = "diubah"
    snapshot.append({"role": "assistant", "content": "palsu"})

    assert store.history("chat-1") == [{"role": "user", "content": "halo"}]


def test_trim_drops_oldest_then_leading_assistant() -> None:
    store = SessionStore(
        ttl_seconds=60,
        clock=FakeClock(),
        max_history_chars=10,
        id_factory=id_factory("s1").__next__,
    )
    session = store.get_or_create("chat-1")
    sid = session.opencode_session_id

    assert store.append("chat-1", sid, "user", "123")
    assert store.append("chat-1", sid, "assistant", "456")
    assert store.append("chat-1", sid, "user", "789")
    # 12 chars total: the oldest user message is dropped, leaving an assistant
    # turn first, which is then removed as well.
    assert store.append("chat-1", sid, "assistant", "012")

    assert store.history("chat-1") == [
        {"role": "user", "content": "789"},
        {"role": "assistant", "content": "012"},
    ]


def test_trim_removes_all_leading_assistant_messages() -> None:
    store = SessionStore(
        ttl_seconds=60,
        clock=FakeClock(),
        max_history_chars=100,
        id_factory=id_factory("s1").__next__,
    )
    session = store.get_or_create("chat-1")
    sid = session.opencode_session_id

    store.append("chat-1", sid, "assistant", "satu")
    store.append("chat-1", sid, "assistant", "dua")
    store.append("chat-1", sid, "user", "tanya")

    assert store.history("chat-1") == [{"role": "user", "content": "tanya"}]


def test_trim_keeps_newest_message_even_when_oversized() -> None:
    store = SessionStore(
        ttl_seconds=60,
        clock=FakeClock(),
        max_history_chars=5,
        id_factory=id_factory("s1").__next__,
    )
    session = store.get_or_create("chat-1")

    store.append("chat-1", session.opencode_session_id, "user", "123456789")

    assert store.history("chat-1") == [
        {"role": "user", "content": "123456789"}
    ]
