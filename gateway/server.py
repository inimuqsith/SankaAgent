"""Loopback HTTP server for the WhatsApp bridge (ADR-0018).

The handler validates the shared token before touching the request body,
enforces a hard body cap, normalizes one inbound payload, and hands it to the
dispatcher. It never calls the model or the bridge: outbound delivery happens
on worker threads. Request logs carry method, path, status, and duration only.
"""

from __future__ import annotations

import hmac
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from gateway.dispatcher import SubmitOutcome, SubmitResult
from gateway.normalizer import MalformedPayload, normalize_inbound
from sanka.attachments import AttachmentError, AttachmentStore, decode_filename_header
from sanka.config import LOOPBACK_HOSTS

if TYPE_CHECKING:
    from gateway.dispatcher import Dispatcher

LOGGER = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024

_POLL_INTERVAL_SECONDS = 0.1
_SHUTDOWN_WAIT_SECONDS = 2.0
_JSON_MEDIA_TYPE = "application/json"
_OCTET_STREAM_MEDIA_TYPE = "application/octet-stream"
_MAX_LOGGED_PATH = 200
_ATTACHMENT_CHUNK_BYTES = 64 * 1024

_ERROR_NOT_FOUND = {"error": "not_found"}
_ERROR_METHOD_NOT_ALLOWED = {"error": "method_not_allowed"}
_ERROR_UNAUTHORIZED = {"error": "unauthorized"}
_ERROR_UNSUPPORTED_MEDIA_TYPE = {"error": "unsupported_media_type"}
_ERROR_LENGTH_REQUIRED = {"error": "length_required"}
_ERROR_PAYLOAD_TOO_LARGE = {"error": "payload_too_large"}
_ERROR_OVERLOADED = {"error": "overloaded"}
_ERROR_INTERNAL = {"error": "internal"}
_ERROR_INVALID_METADATA = {"error": "invalid_metadata"}
_ERROR_UNSUPPORTED_MEDIA = {"error": "unsupported_media"}
_ERROR_ATTACHMENTS_DISABLED = {"error": "attachments_disabled"}


class _GatewayHTTPServer(ThreadingHTTPServer):
    """Threaded server carrying the shared dispatcher and token."""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        dispatcher: Dispatcher,
        token: str,
        attachments: AttachmentStore | None = None,
    ) -> None:
        # Pick the socket family from the (already validated) host so ``::1``
        # and ``127.0.0.1`` both work. Setting it before ``__init__`` lets
        # ``TCPServer`` create the right socket type.
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.dispatcher = dispatcher
        self.token = token
        self.attachments = attachments
        self.started_at = time.monotonic()
        super().__init__(address, handler)


class _GatewayRequestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP/1.1 handler for ``/healthz`` and ``/v1/inbound``."""

    protocol_version = "HTTP/1.1"
    server_version = "sanka-gateway"
    sys_version = ""

    server: _GatewayHTTPServer

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        started = time.monotonic()
        path = self._path()
        if path == "/healthz":
            self._handle_health(started)
            return
        if path in ("/v1/inbound", "/v1/attachments"):
            self._send_json(
                405,
                _ERROR_METHOD_NOT_ALLOWED,
                started,
                allow="POST",
            )
            return
        self._send_json(404, _ERROR_NOT_FOUND, started)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        started = time.monotonic()
        path = self._path()
        if path == "/v1/inbound":
            self._handle_inbound(started)
            return
        if path == "/v1/attachments":
            self._handle_attachment(started)
            return
        if path == "/healthz":
            self._send_json(
                405,
                _ERROR_METHOD_NOT_ALLOWED,
                started,
                allow="GET",
                close=True,
            )
            return
        self._send_json(404, _ERROR_NOT_FOUND, started, close=True)

    # -- endpoints ---------------------------------------------------------

    def _handle_health(self, started: float) -> None:
        try:
            pending = int(self.server.dispatcher.outbound_pending)
        except Exception:  # noqa: BLE001 - health must still answer
            LOGGER.exception("health check failed to read outbound depth")
            self._send_json(500, _ERROR_INTERNAL, started)
            return

        uptime = max(0, int(time.monotonic() - self.server.started_at))
        self._send_json(
            200,
            {
                "status": "ok",
                "uptime_seconds": uptime,
                "outbound_pending": pending,
            },
            started,
        )

    def _handle_inbound(self, started: float) -> None:
        # Authentication runs before any body byte is read.
        if not self._authorized():
            self._send_json(401, _ERROR_UNAUTHORIZED, started, close=True)
            return

        if self._media_type() != _JSON_MEDIA_TYPE:
            self._send_json(
                415, _ERROR_UNSUPPORTED_MEDIA_TYPE, started, close=True
            )
            return

        length = self._content_length()
        if length is None:
            # Chunked or absent length: the body is never read.
            self._send_json(411, _ERROR_LENGTH_REQUIRED, started, close=True)
            return
        if length > MAX_BODY_BYTES:
            self._send_json(
                413, _ERROR_PAYLOAD_TOO_LARGE, started, close=True
            )
            return

        body = self._read_body(length)
        if body is None:
            self._send_json(
                400,
                {"error": "malformed_payload", "reason": "invalid_json"},
                started,
                close=True,
            )
            return

        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            self._send_json(
                400,
                {"error": "malformed_payload", "reason": "invalid_json"},
                started,
            )
            return
        if not isinstance(payload, dict):
            self._send_json(
                400,
                {"error": "malformed_payload", "reason": "invalid_json"},
                started,
            )
            return

        try:
            event = normalize_inbound(
                payload, attachments=self.server.attachments
            )
        except MalformedPayload as exc:
            self._send_json(
                400,
                {"error": "malformed_payload", "reason": exc.reason},
                started,
            )
            return

        try:
            result = self.server.dispatcher.submit(event)
        except Exception:  # noqa: BLE001 - never leak payload values
            LOGGER.exception("inbound dispatch failed")
            self._send_json(500, _ERROR_INTERNAL, started)
            return

        self._respond_submit(result, started)

    def _handle_attachment(self, started: float) -> None:
        if not self._authorized():
            self._send_json(401, _ERROR_UNAUTHORIZED, started, close=True)
            return
        store = self.server.attachments
        if store is None:
            self._send_json(
                503, _ERROR_ATTACHMENTS_DISABLED, started, close=True
            )
            return
        if self._media_type() != _OCTET_STREAM_MEDIA_TYPE:
            self._send_json(
                415, _ERROR_UNSUPPORTED_MEDIA_TYPE, started, close=True
            )
            return

        length = self._content_length()
        if length is None:
            self._send_json(411, _ERROR_LENGTH_REQUIRED, started, close=True)
            return
        if length > store.max_bytes:
            self._send_json(
                413, _ERROR_PAYLOAD_TOO_LARGE, started, close=True
            )
            return
        if length < 1:
            self._send_json(400, _ERROR_INVALID_METADATA, started)
            return

        try:
            kind = self.headers.get("X-Sanka-Kind", "")
            mime = self.headers.get("X-Sanka-Mimetype", "")
            name = decode_filename_header(
                self.headers.get("X-Sanka-Filename-B64")
            )
            record = store.save(
                self._attachment_chunks(length),
                chat_id=self.headers.get("X-Sanka-Chat-Id", ""),
                sender_id=self.headers.get("X-Sanka-Sender-Id", ""),
                message_id=self.headers.get("X-Sanka-Message-Id", ""),
                kind=kind,
                mime=mime,
                name=name,
                declared_size=length,
                expected_sha256=self.headers.get("X-Sanka-Sha256"),
            )
        except AttachmentError as exc:
            status = {
                "too_large": 413,
                "unsupported_media": 415,
                "invalid_metadata": 400,
                "not_found": 404,
            }.get(exc.code, 400)
            payload = (
                _ERROR_PAYLOAD_TOO_LARGE
                if status == 413
                else _ERROR_UNSUPPORTED_MEDIA
                if status == 415
                else _ERROR_INVALID_METADATA
            )
            LOGGER.warning(
                "gateway_attachment_rejected code=%s status=%d",
                exc.code,
                status,
            )
            self._send_json(status, payload, started, close=True)
            return
        except OSError:
            LOGGER.exception("gateway_attachment_write_failed")
            self._send_json(500, _ERROR_INTERNAL, started, close=True)
            return
        except Exception:  # noqa: BLE001 - upload must fail closed
            LOGGER.exception("gateway_attachment_failed")
            self._send_json(500, _ERROR_INTERNAL, started, close=True)
            return

        self._send_json(
            201,
            {
                "attachment_id": record.attachment_id,
                "size": record.size,
                "sha256": record.sha256,
            },
            started,
        )

    def _attachment_chunks(self, length: int):
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, _ATTACHMENT_CHUNK_BYTES))
            if not chunk:
                raise AttachmentError("invalid_metadata")
            remaining -= len(chunk)
            yield chunk

    def _respond_submit(self, result: SubmitResult, started: float) -> None:
        outcome = result.outcome
        if outcome is SubmitOutcome.ACCEPTED:
            self._send_json(202, {"status": "accepted"}, started)
        elif outcome is SubmitOutcome.DUPLICATE:
            self._send_json(200, {"status": "duplicate"}, started)
        elif outcome is SubmitOutcome.IGNORED:
            self._send_json(
                200, {"status": "ignored", "reason": result.reason}, started
            )
        elif outcome is SubmitOutcome.OVERLOADED:
            self._send_json(429, _ERROR_OVERLOADED, started)
        else:  # pragma: no cover - a future outcome must fail closed
            LOGGER.error("unknown submit outcome: %r", outcome)
            self._send_json(500, _ERROR_INTERNAL, started)

    # -- request parsing ---------------------------------------------------

    def _path(self) -> str:
        """Return the path only; query strings are never used."""
        return urlsplit(self.path).path or "/"

    def _authorized(self) -> bool:
        provided = self.headers.get("X-Sanka-Token")
        if not provided:
            return False
        try:
            return hmac.compare_digest(
                provided.encode("utf-8"), self.server.token.encode("utf-8")
            )
        except (UnicodeError, TypeError):
            return False

    def _media_type(self) -> str:
        raw = self.headers.get("Content-Type", "")
        return raw.split(";", 1)[0].strip().lower()

    def _content_length(self) -> int | None:
        if self.headers.get("Transfer-Encoding") is not None:
            return None
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw.strip())
        except ValueError:
            return None
        return value if value >= 0 else None

    def _read_body(self, length: int) -> bytes | None:
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            try:
                chunk = self.rfile.read(min(remaining, 8192))
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- response and logging ---------------------------------------------

    def _send_json(
        self,
        status: int,
        payload: dict[str, object],
        started: float,
        *,
        close: bool = False,
        allow: str | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", _JSON_MEDIA_TYPE)
            self.send_header("Content-Length", str(len(body)))
            if allow is not None:
                self.send_header("Allow", allow)
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            duration_ms = (time.monotonic() - started) * 1000.0
            LOGGER.info(
                "gateway_http method=%s path=%s status=%d duration_ms=%.1f",
                self.command,
                self._path()[:_MAX_LOGGED_PATH],
                status,
                duration_ms,
            )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence the stdlib request log; metadata is logged above."""

    def log_error(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence the stdlib error log; failures are logged with tracebacks."""

    # HEAD is not part of the contract; answer 404/405 like other methods.
    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        started = time.monotonic()
        if self._path() in ("/healthz", "/v1/inbound", "/v1/attachments"):
            self._send_json(405, _ERROR_METHOD_NOT_ALLOWED, started)
        else:
            self._send_json(404, _ERROR_NOT_FOUND, started)

    # Other verbs are outside the contract: 405 on known paths, 404 elsewhere.
    def do_PUT(self) -> None:  # noqa: N802 - stdlib naming
        self._reject_other_method()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
        self._reject_other_method()

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib naming
        self._reject_other_method()

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self._reject_other_method()

    def _reject_other_method(self) -> None:
        started = time.monotonic()
        path = self._path()
        if path in ("/healthz", "/v1/inbound", "/v1/attachments"):
            allow = "GET" if path == "/healthz" else "POST"
            self._send_json(
                405, _ERROR_METHOD_NOT_ALLOWED, started, allow=allow, close=True
            )
        else:
            self._send_json(404, _ERROR_NOT_FOUND, started, close=True)


class GatewayServer:
    """Owns the bound loopback server and its lifecycle."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        *,
        token: str,
        host: str,
        port: int,
        attachments: AttachmentStore | None = None,
    ) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                "gateway.listen_host must be a loopback host "
                "(127.0.0.1, ::1, localhost)"
            )
        if not token or not token.strip():
            raise ValueError("gateway token must not be empty")

        self._httpd = _GatewayHTTPServer(
            (host, port),
            _GatewayRequestHandler,
            dispatcher=dispatcher,
            token=token,
            attachments=attachments,
        )
        self._state_lock = threading.Lock()
        self._serving = False
        self._stopped = threading.Event()
        self._stop_requested = False

    @property
    def bound_port(self) -> int:
        """Port the socket is bound to, useful when constructed with ``0``."""
        return int(self._httpd.server_address[1])

    def serve_forever(self) -> None:
        """Serve until :meth:`shutdown` is called from another thread."""
        try:
            with self._state_lock:
                if self._stop_requested:
                    return
                self._serving = True

            self._httpd.serve_forever(poll_interval=_POLL_INTERVAL_SECONDS)
        finally:
            with self._state_lock:
                self._serving = False
            self._stopped.set()

    def shutdown(self) -> None:
        """Stop the serving loop; safe when the server never started."""
        with self._state_lock:
            self._stop_requested = True
            serving = self._serving
            if not serving:
                self._stopped.set()
                return
        # ``serve_forever`` has already committed to entering the loop, so it
        # cannot notice the flag and return before this call completes.
        self._httpd.shutdown()
        self._stopped.wait(_SHUTDOWN_WAIT_SECONDS)

    def close(self) -> None:
        """Release the listening socket."""
        self._stopped.set()
        self._httpd.server_close()

    @property
    def uptime_seconds(self) -> int:
        return max(0, int(time.monotonic() - self._httpd.started_at))
