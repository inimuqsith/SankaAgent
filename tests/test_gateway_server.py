"""HTTP-level tests against a real loopback server on port 0."""

from __future__ import annotations

import socket
import threading
from collections import deque
from collections.abc import Iterator, Sequence

import httpx
import pytest

from gateway.dispatcher import SubmitOutcome, SubmitResult
from gateway.normalizer import InboundEvent
from gateway.server import MAX_BODY_BYTES, GatewayServer

TOKEN = "test-token"
DM_CHAT = "628111111111@s.whatsapp.net"


class StubDispatcher:
    """Scriptable dispatcher double; defaults to ACCEPTED."""

    def __init__(
        self, results: Sequence[SubmitResult] = (), pending: int = 3
    ) -> None:
        self.results: deque[SubmitResult] = deque(results)
        self.events: list[InboundEvent] = []
        self.pending = pending
        self.error: Exception | None = None
        self._lock = threading.Lock()

    @property
    def outbound_pending(self) -> int:
        return self.pending

    def submit(self, event: InboundEvent) -> SubmitResult:
        with self._lock:
            self.events.append(event)
            if self.error is not None:
                raise self.error
            if self.results:
                return self.results.popleft()
        return SubmitResult(SubmitOutcome.ACCEPTED)


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "message_id": "msg-1",
        "chat_id": DM_CHAT,
        "sender_id": DM_CHAT,
        "text": "halo",
    }
    body.update(overrides)
    return body


def _auth(token: str | None = TOKEN) -> dict[str, str]:
    return {} if token is None else {"X-Sanka-Token": token}


def _raw_status(request: bytes, port: int) -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as sock:
        sock.sendall(request)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks: list[bytes] = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    response = b"".join(chunks)
    status_line = response.split(b"\r\n", 1)[0]
    return int(status_line.split(b" ")[1]), response


@pytest.fixture
def gateway() -> Iterator[tuple[httpx.Client, StubDispatcher, GatewayServer]]:
    dispatcher = StubDispatcher()
    server = GatewayServer(dispatcher, token=TOKEN, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{server.bound_port}", timeout=5.0
    )
    try:
        yield client, dispatcher, server
    finally:
        client.close()
        server.shutdown()
        server.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_healthz_needs_no_auth(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, dispatcher, _ = gateway
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["uptime_seconds"], int)
    assert body["outbound_pending"] == dispatcher.pending
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["server"].startswith("sanka-gateway")
    assert "Python" not in response.headers["server"]


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_inbound_rejects_missing_or_wrong_token(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
    token: str | None,
) -> None:
    client, dispatcher, _ = gateway
    response = client.post(
        "/v1/inbound",
        json=payload(),
        headers=_auth(token),
    )

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert dispatcher.events == []
    assert response.headers["connection"] == "close"


def test_inbound_rejects_unsupported_media_type(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, _, _ = gateway
    response = client.post(
        "/v1/inbound",
        content=b"{}",
        headers={**_auth(), "Content-Type": "text/plain"},
    )

    assert response.status_code == 415
    assert response.json() == {"error": "unsupported_media_type"}


def test_inbound_accepts_json_with_charset(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, _, _ = gateway
    response = client.post(
        "/v1/inbound",
        content=b'{"message_id":"m-cs","chat_id":"628111111111@s.whatsapp.net",'
        b'"sender_id":"628111111111@s.whatsapp.net","text":"hi"}',
        headers={**_auth(), "Content-Type": "application/json; charset=utf-8"},
    )

    assert response.status_code == 202


def test_inbound_rejects_oversized_body(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    _, dispatcher, server = gateway
    request = (
        b"POST /v1/inbound HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"X-Sanka-Token: test-token\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {MAX_BODY_BYTES + 1}\r\n\r\n".encode()
    )

    status, response = _raw_status(request, server.bound_port)

    assert status == 413
    assert b"payload_too_large" in response
    assert dispatcher.events == []


@pytest.mark.parametrize("transfer_encoding", [None, "chunked"])
def test_inbound_requires_content_length(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
    transfer_encoding: str | None,
) -> None:
    _, dispatcher, server = gateway
    headers = (
        b"POST /v1/inbound HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"X-Sanka-Token: test-token\r\n"
        b"Content-Type: application/json\r\n"
    )
    if transfer_encoding is not None:
        headers += f"Transfer-Encoding: {transfer_encoding}\r\n".encode()

    status, response = _raw_status(headers + b"\r\n", server.bound_port)

    assert status == 411
    assert b"length_required" in response
    assert dispatcher.events == []


def test_unknown_path_returns_404(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, _, _ = gateway
    response = client.get("/v1/send")

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


def test_wrong_methods_return_405(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, _, _ = gateway

    get_inbound = client.get("/v1/inbound")
    post_health = client.post("/healthz")

    assert get_inbound.status_code == 405
    assert get_inbound.headers["allow"] == "POST"
    assert post_health.status_code == 405
    assert post_health.headers["allow"] == "GET"

    put_inbound = client.put("/v1/inbound")
    delete_unknown = client.delete("/nope")

    assert put_inbound.status_code == 405
    assert put_inbound.headers["allow"] == "POST"
    assert delete_unknown.status_code == 404


def test_valid_inbound_returns_accepted(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, dispatcher, _ = gateway
    response = client.post(
        "/v1/inbound", json=payload(), headers=_auth()
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert len(dispatcher.events) == 1
    event = dispatcher.events[0]
    assert event.message_id == "msg-1"
    assert event.message.chat_id == DM_CHAT
    assert event.message.text == "halo"
    assert event.message.is_group is False
    assert event.timestamp > 0


def test_duplicate_returns_200(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, dispatcher, _ = gateway
    dispatcher.results.append(SubmitResult(SubmitOutcome.DUPLICATE))

    response = client.post("/v1/inbound", json=payload(), headers=_auth())

    assert response.status_code == 200
    assert response.json() == {"status": "duplicate"}


def test_ignored_includes_reason(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, dispatcher, _ = gateway
    dispatcher.results.append(
        SubmitResult(SubmitOutcome.IGNORED, "not_mentioned")
    )

    response = client.post("/v1/inbound", json=payload(), headers=_auth())

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "not_mentioned",
    }


def test_overloaded_returns_429(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, dispatcher, _ = gateway
    dispatcher.results.append(SubmitResult(SubmitOutcome.OVERLOADED))

    response = client.post("/v1/inbound", json=payload(), headers=_auth())

    assert response.status_code == 429
    assert response.json() == {"error": "overloaded"}


@pytest.mark.parametrize(
    ("raw_body", "reason"),
    [
        (b"{not json", "invalid_json"),
        (b"[]", "invalid_json"),
        (b'"text"', "invalid_json"),
    ],
)
def test_invalid_json_returns_400(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
    raw_body: bytes,
    reason: str,
) -> None:
    client, dispatcher, _ = gateway
    response = client.post(
        "/v1/inbound",
        content=raw_body,
        headers={**_auth(), "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "malformed_payload",
        "reason": reason,
    }
    assert dispatcher.events == []


def test_malformed_payload_uses_normalizer_reason(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, _, _ = gateway
    response = client.post(
        "/v1/inbound",
        json=payload(chat_id="not-a-jid"),
        headers=_auth(),
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "malformed_payload",
        "reason": "chat_id_invalid",
    }


def test_dispatcher_failure_returns_500(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, dispatcher, _ = gateway
    dispatcher.error = RuntimeError("boom")

    response = client.post("/v1/inbound", json=payload(), headers=_auth())

    assert response.status_code == 500
    assert response.json() == {"error": "internal"}


def test_request_bodies_are_not_echoed(
    gateway: tuple[httpx.Client, StubDispatcher, GatewayServer],
) -> None:
    client, _, _ = gateway
    secret_text = "very-secret-message-text"
    response = client.post(
        "/v1/inbound",
        json=payload(text=secret_text),
        headers=_auth(),
    )

    assert secret_text not in response.text
    assert TOKEN not in response.text


def test_non_loopback_host_is_rejected() -> None:
    with pytest.raises(ValueError):
        GatewayServer(StubDispatcher(), token=TOKEN, host="0.0.0.0", port=0)


def test_empty_token_is_rejected() -> None:
    with pytest.raises(ValueError):
        GatewayServer(StubDispatcher(), token="   ", host="127.0.0.1", port=0)
