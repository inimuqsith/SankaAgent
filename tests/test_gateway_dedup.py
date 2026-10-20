"""TtlRegistry tests: dedup, TTL expiry, discard, sweep, and capacity."""

from __future__ import annotations

import pytest

from gateway.dedup import TtlRegistry


class FakeClock:
    """A manually advanced clock so TTL tests never sleep."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_ttl_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TtlRegistry(0)
    with pytest.raises(ValueError):
        TtlRegistry(-1)


def test_max_entries_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TtlRegistry(10, max_entries=0)


def test_first_registration_is_new_and_second_is_duplicate() -> None:
    registry = TtlRegistry(60, clock=FakeClock())
    assert registry.register("msg:1") is True
    assert registry.register("msg:1") is False
    assert len(registry) == 1


def test_duplicate_lookup_does_not_refresh_ttl() -> None:
    clock = FakeClock(0.0)
    registry = TtlRegistry(10, clock=clock)

    assert registry.register("msg:1") is True
    clock.advance(9)
    assert registry.register("msg:1") is False
    clock.advance(1)  # exactly TTL after the original registration
    assert registry.register("msg:1") is True


def test_entry_expires_at_ttl_boundary() -> None:
    clock = FakeClock(0.0)
    registry = TtlRegistry(10, clock=clock)

    assert registry.register("k") is True
    clock.advance(9.99)
    assert registry.register("k") is False
    clock.advance(0.01)
    assert registry.register("k") is True


def test_discard_allows_reregistration() -> None:
    registry = TtlRegistry(60, clock=FakeClock())

    assert registry.register("msg:1") is True
    registry.discard("msg:1")
    assert len(registry) == 0
    assert registry.register("msg:1") is True


def test_discard_unknown_key_is_a_noop() -> None:
    registry = TtlRegistry(60, clock=FakeClock())
    registry.discard("missing")
    assert len(registry) == 0


def test_capacity_evicts_oldest_inserted_key() -> None:
    registry = TtlRegistry(60, clock=FakeClock(), max_entries=3)

    for key in ("a", "b", "c"):
        assert registry.register(key) is True
    assert registry.register("d") is True
    assert len(registry) == 3

    # b, c, and d remain; a was evicted as the oldest insertion.
    assert registry.register("b") is False
    assert registry.register("c") is False
    assert registry.register("d") is False
    assert registry.register("a") is True
    assert len(registry) == 3


def test_expired_entries_are_swept_every_256_registrations() -> None:
    clock = FakeClock(0.0)
    registry = TtlRegistry(1, clock=clock, max_entries=1000)

    for index in range(10):
        assert registry.register(f"old:{index}") is True

    clock.advance(2)
    for index in range(256):
        assert registry.register(f"new:{index}") is True

    # The sweep removed all ten expired keys even though capacity was ample.
    assert len(registry) == 256


def test_nearly_full_registry_sweeps_expired_entries() -> None:
    clock = FakeClock(0.0)
    registry = TtlRegistry(1, clock=clock, max_entries=10)

    for index in range(5):
        assert registry.register(f"old:{index}") is True

    clock.advance(2)
    for index in range(5):
        assert registry.register(f"new:{index}") is True

    # Crossing 90% capacity triggered the sweep, dropping the expired entries.
    assert len(registry) == 5
