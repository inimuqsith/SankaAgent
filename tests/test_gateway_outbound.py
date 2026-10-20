"""Tests for the outbound bridge client and delivery queue (no network)."""

from __future__ import annotations

import logging
import threading

import httpx
import pytest

from gateway.outbound import BridgeError, HttpBridgeClient, OutboundQueue
from sanka.formatter import Formatter, ResponseKind
from tests.gateway_fakes import FakeBridge, FakeClock

CHAT_A = "628111@s.whatsapp.net"
CHAT_B = "628222@s.whatsapp.net"


class SplitFormatter:
    """Formatter double that splits text into fixed-size chunks."""

    def __init__(self, limit: int = 4) -> None:
        self.limit = limit
        self.kinds: list[ResponseKind] = []

    def format(self, text: str, kind: ResponseKind = ResponseKind.TEXT) -> list[str]:
        self.kinds.append(ResponseKind(kind))
        cleaned = text.strip()
        if not cleaned:
            return []
        return [cleaned[i : i + self.limit] for i in range(0, len(cleaned), self.limit)]


class CountingBridge(FakeBridge):
    """FakeBridge that records how many ``send`` calls reached it."""

    def __init__(self) -> None:
        super().__init__()
        self.send_calls = 0

    def send(self, chat_id: str, text: str, idempotency_key: str) -> str | None:
        self.send_calls += 1
        return super().send(chat_id, text, idempotency_key)


class GatedBridge(FakeBridge):
    """FakeBridge whose first send blocks until released."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def send(self, chat_id: str, text: str, idempotency_key: str) -> str | None:
        self.entered.set()
        self.release.wait(2.0)
        return super().send(chat_id, text, idempotency_key)


def make_queue(
    bridge: FakeBridge,
    *,
    formatter: object | None = None,
    per_minute: int = 60,
    clock: FakeClock | None = None,
    **kwargs: object,
) -> tuple[OutboundQueue, FakeClock]:
    clock = clock or FakeClock()
    queue = OutboundQueue(
        bridge,  # type: ignore[arg-type]
        formatter or Formatter(3500),  # type: ignore[arg-type]
        per_minute=per_minute,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,  # type: ignore[arg-type]
    )
    return queue, clock


# -- OutboundQueue ---------------------------------------------------------


def test_reply_is_chunked_with_increasing_idempotency_keys() -> None:
    bridge = FakeBridge()
    formatter = SplitFormatter(limit=4)
    queue, _ = make_queue(bridge, formatter=formatter)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["abcdefgh", "ij"], base_key="msg-1")
    assert queue.wait_idle(2.0)

    assert [text for _, text, _ in bridge.sent] == ["abcd", "efgh", "ij"]
    assert [key for _, _, key in bridge.sent] == ["msg-1:0", "msg-1:1", "msg-1:2"]
    assert formatter.kinds == [ResponseKind.TEXT, ResponseKind.TEXT]
    queue.stop()


def test_same_chat_fifo_and_cross_chat_does_not_block() -> None:
    bridge = FakeBridge()
    queue, _ = make_queue(bridge, per_minute=100)

    queue.enqueue_reply(CHAT_A, ["a1"], base_key="a1")
    queue.enqueue_reply(CHAT_A, ["a2"], base_key="a2")
    queue.enqueue_reply(CHAT_A, ["a3"], base_key="a3")
    queue.enqueue_reply(CHAT_B, ["b1"], base_key="b1")
    queue.start()
    assert queue.wait_idle(2.0)

    assert [text for chat, text, _ in bridge.sent if chat == CHAT_A] == [
        "a1",
        "a2",
        "a3",
    ]
    assert [text for chat, text, _ in bridge.sent if chat == CHAT_B] == ["b1"]
    assert {chat for chat, _, _ in bridge.sent[:2]} == {CHAT_A, CHAT_B}
    queue.stop()


def test_typing_and_read_follow_reply_in_chat_order() -> None:
    bridge = FakeBridge()
    queue, _ = make_queue(bridge)

    queue.start()
    queue.enqueue_typing(CHAT_A, "composing")
    queue.enqueue_read(CHAT_A, "wamid-in")
    queue.enqueue_reply(CHAT_A, ["halo"], base_key="m")
    queue.enqueue_typing(CHAT_A, "paused")
    assert queue.wait_idle(2.0)

    assert bridge.typing_events == [(CHAT_A, "composing"), (CHAT_A, "paused")]
    assert bridge.reads == [(CHAT_A, "wamid-in")]
    assert [text for _, text, _ in bridge.sent] == ["halo"]
    queue.stop()


def test_per_minute_limit_waits_for_a_slot() -> None:
    bridge = FakeBridge()
    queue, clock = make_queue(bridge, per_minute=2)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["one"], base_key="m1")
    queue.enqueue_reply(CHAT_A, ["two"], base_key="m2")
    queue.enqueue_reply(CHAT_A, ["three"], base_key="m3")
    assert queue.wait_idle(2.0)

    assert len(bridge.sent) == 3
    # The third send had to wait for the 60s window to slide.
    assert clock() == pytest.approx(1060.0)
    queue.stop()


def test_typing_dropped_when_window_full_while_send_waits() -> None:
    bridge = FakeBridge()
    queue, clock = make_queue(bridge, per_minute=1)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["one"], base_key="m1")
    assert queue.wait_idle(2.0)

    queue.enqueue_reply(CHAT_A, ["two"], base_key="m2")
    queue.enqueue_typing(CHAT_B, "composing")
    assert queue.wait_idle(2.0)

    assert len(bridge.sent) == 2
    assert clock() == pytest.approx(1060.0)
    assert bridge.typing_events == []
    queue.stop()


@pytest.mark.parametrize("status", [429, 500, 503, 504])
def test_retryable_failure_honors_retry_after_then_succeeds(status: int) -> None:
    bridge = CountingBridge()
    bridge.failures = [
        BridgeError("busy", retryable=True, status=status, retry_after=2.0)
    ]
    queue, clock = make_queue(bridge)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["halo"], base_key="m")
    assert queue.wait_idle(2.0)

    assert bridge.send_calls == 2
    assert [text for _, text, _ in bridge.sent] == ["halo"]
    assert clock() == pytest.approx(1002.0)
    queue.stop()


def test_retryable_failure_without_retry_after_uses_backoff() -> None:
    bridge = CountingBridge()
    bridge.failures = [BridgeError("down", retryable=True, status=502)]
    queue, clock = make_queue(bridge)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["halo"], base_key="m")
    assert queue.wait_idle(2.0)

    assert bridge.send_calls == 2
    assert len(bridge.sent) == 1
    assert clock() == pytest.approx(1000.5)
    queue.stop()


def test_retry_after_is_clamped_to_thirty_seconds() -> None:
    bridge = CountingBridge()
    bridge.failures = [
        BridgeError("busy", retryable=True, status=429, retry_after=90.0)
    ]
    queue, clock = make_queue(bridge)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["halo"], base_key="m")
    assert queue.wait_idle(2.0)

    assert clock() == pytest.approx(1030.0)
    assert len(bridge.sent) == 1
    queue.stop()


def test_retries_exhausted_drops_item_without_text_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bridge = CountingBridge()
    bridge.failures = [
        BridgeError("down", retryable=True, status=503),
        BridgeError("down", retryable=True, status=503),
        BridgeError("down", retryable=True, status=503),
    ]
    queue, clock = make_queue(bridge)
    caplog.set_level(logging.WARNING)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["RAHASIA-PESAN"], base_key="m")
    assert queue.wait_idle(2.0)

    assert bridge.send_calls == 3
    assert bridge.sent == []
    assert clock() == pytest.approx(1001.5)
    assert "outbound_send_dropped" in caplog.text
    assert "RAHASIA-PESAN" not in caplog.text
    assert CHAT_A not in caplog.text
    queue.stop()


def test_non_retryable_failure_is_dropped_without_retry() -> None:
    bridge = CountingBridge()
    bridge.failures = [BridgeError("bad", retryable=False, status=400)]
    queue, clock = make_queue(bridge)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["halo"], base_key="m")
    assert queue.wait_idle(2.0)

    assert bridge.send_calls == 1
    assert bridge.sent == []
    assert clock() == pytest.approx(1000.0)

    # The worker stays healthy and delivers later items.
    queue.enqueue_reply(CHAT_A, ["lagi"], base_key="m2")
    assert queue.wait_idle(2.0)
    assert [text for _, text, _ in bridge.sent] == ["lagi"]
    queue.stop()


def test_conflict_none_response_is_success() -> None:
    class ConflictBridge(FakeBridge):
        def send(self, chat_id: str, text: str, idempotency_key: str) -> str | None:
            self.sent.append((chat_id, text, idempotency_key))
            return None

    bridge = ConflictBridge()
    queue, _ = make_queue(bridge)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["halo"], base_key="m")
    queue.enqueue_reply(CHAT_A, ["lagi"], base_key="m2")
    assert queue.wait_idle(2.0)

    assert len(bridge.sent) == 2
    queue.stop()


def test_reasoning_blocks_are_stripped_before_send() -> None:
    bridge = FakeBridge()
    queue, _ = make_queue(bridge, formatter=Formatter(100))

    queue.start()
    queue.enqueue_reply(
        CHAT_A,
        ["<reasoning>secret</reasoning>Halo <thought>noise</thought>dunia"],
        base_key="m",
    )
    assert queue.wait_idle(2.0)

    assert [text for _, text, _ in bridge.sent] == ["Halo dunia"]

    queue.enqueue_reply(CHAT_A, ["<reasoning>only noise</reasoning>"], base_key="m2")
    assert queue.wait_idle(2.0)
    assert len(bridge.sent) == 1
    queue.stop()


def test_overflow_drops_send_at_error_and_typing_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bridge = FakeBridge()
    queue, _ = make_queue(bridge, max_per_chat=2)
    caplog.set_level(logging.WARNING)

    queue.enqueue_reply(CHAT_A, ["one"], base_key="m1")
    queue.enqueue_reply(CHAT_A, ["two"], base_key="m2")
    queue.enqueue_reply(CHAT_A, ["three"], base_key="m3")
    queue.enqueue_typing(CHAT_A, "paused")

    assert queue.pending == 2
    assert "outbound_overflow" in caplog.text
    assert any(
        record.levelno == logging.ERROR and "kind=send" in record.getMessage()
        for record in caplog.records
    )
    assert any(
        record.levelno == logging.WARNING and "kind=typing" in record.getMessage()
        for record in caplog.records
    )


def test_stop_drops_pending_and_ignores_later_enqueues() -> None:
    bridge = FakeBridge()
    queue, _ = make_queue(bridge)

    queue.enqueue_reply(CHAT_A, ["one"], base_key="m1")
    queue.enqueue_typing(CHAT_A, "composing")
    assert queue.pending == 2

    queue.stop()

    assert queue.pending == 0
    queue.enqueue_reply(CHAT_A, ["two"], base_key="m2")
    assert queue.pending == 0
    assert bridge.sent == []
    assert bridge.typing_events == []


def test_stop_drains_items_while_one_send_is_in_flight() -> None:
    bridge = GatedBridge()
    queue, _ = make_queue(bridge)

    queue.start()
    queue.enqueue_reply(CHAT_A, ["one"], base_key="m1")
    queue.enqueue_reply(CHAT_A, ["two"], base_key="m2")
    queue.enqueue_reply(CHAT_A, ["three"], base_key="m3")
    assert bridge.entered.wait(2.0)

    queue.stop(timeout=0.05)

    assert queue.pending == 0
    bridge.release.set()
    queue.wait_idle(2.0)
    assert len(bridge.sent) <= 1


def test_wait_idle_times_out_when_queue_never_runs() -> None:
    bridge = FakeBridge()
    queue, _ = make_queue(bridge)

    queue.enqueue_reply(CHAT_A, ["one"], base_key="m1")
    assert queue.wait_idle(0.02) is False


def test_start_is_idempotent() -> None:
    bridge = FakeBridge()
    queue, _ = make_queue(bridge)

    queue.start()
    queue.start()
    worker = queue._thread  # noqa: SLF001 - identity check only
    assert worker is not None and worker.is_alive()

    queue.start()
    assert queue._thread is worker  # noqa: SLF001
    queue.stop()


# -- HttpBridgeClient ------------------------------------------------------


def _http_client(handler: object, *, token: str = "test-token") -> HttpBridgeClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.Client(transport=transport, base_url="http://bridge.local")
    return HttpBridgeClient("http://bridge.local", token, 5.0, client=http)


def test_send_posts_payload_with_token_and_returns_message_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"message_id": "wamid-1"})

    client = _http_client(handler)
    assert client.send(CHAT_A, "halo", "m:0") == "wamid-1"

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/send"
    assert request.headers["X-Sanka-Token"] == "test-token"
    body = httpx.Response(200, content=request.content).json()
    assert body == {
        "chat_id": CHAT_A,
        "text": "halo",
        "idempotency_key": "m:0",
    }


def test_send_conflict_is_success() -> None:
    client = _http_client(
        lambda request: httpx.Response(409, json={"error": "duplicate"})
    )
    assert client.send(CHAT_A, "halo", "m:0") is None


def test_send_429_parses_retry_after_ms_from_body() -> None:
    client = _http_client(
        lambda request: httpx.Response(
            429, json={"error": "rate_limited", "retry_after_ms": 1500}
        )
    )
    with pytest.raises(BridgeError) as excinfo:
        client.send(CHAT_A, "halo", "m:0")

    error = excinfo.value
    assert error.retryable is True
    assert error.status == 429
    assert error.retry_after == pytest.approx(1.5)


def test_send_429_falls_back_to_retry_after_header() -> None:
    client = _http_client(
        lambda request: httpx.Response(
            429, headers={"Retry-After": "2"}, json={"error": "rate_limited"}
        )
    )
    with pytest.raises(BridgeError) as excinfo:
        client.send(CHAT_A, "halo", "m:0")

    assert excinfo.value.retry_after == pytest.approx(2.0)


def test_send_400_is_not_retryable() -> None:
    client = _http_client(lambda request: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(BridgeError) as excinfo:
        client.send(CHAT_A, "halo", "m:0")

    error = excinfo.value
    assert error.retryable is False
    assert error.status == 400
    assert error.retry_after is None


def test_send_503_is_retryable() -> None:
    client = _http_client(
        lambda request: httpx.Response(503, json={"error": "not_connected"})
    )
    with pytest.raises(BridgeError) as excinfo:
        client.send(CHAT_A, "halo", "m:0")

    assert excinfo.value.retryable is True
    assert excinfo.value.status == 503


def test_connection_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    client = _http_client(handler)
    with pytest.raises(BridgeError) as excinfo:
        client.send(CHAT_A, "halo", "m:0")

    assert excinfo.value.retryable is True
    assert excinfo.value.status is None


def test_typing_posts_state_and_accepts_204() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    client = _http_client(handler)
    client.typing(CHAT_A, "composing")

    assert len(requests) == 1
    assert requests[0].url.path == "/v1/typing"
    body = httpx.Response(200, content=requests[0].content).json()
    assert body == {"chat_id": CHAT_A, "state": "composing"}


def test_typing_error_raises_bridge_error() -> None:
    client = _http_client(lambda request: httpx.Response(500, json={"error": "x"}))
    with pytest.raises(BridgeError) as excinfo:
        client.typing(CHAT_A, "paused")

    assert excinfo.value.retryable is True


def test_read_posts_message_id_and_accepts_204() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    client = _http_client(handler)
    client.read(CHAT_A, "wamid-in")

    assert requests[0].url.path == "/v1/read"
    body = httpx.Response(200, content=requests[0].content).json()
    assert body == {"chat_id": CHAT_A, "message_id": "wamid-in"}


def test_close_is_safe_with_injected_client() -> None:
    client = _http_client(lambda request: httpx.Response(204))
    client.close()
    client.close()
