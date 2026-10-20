"""Google service-account credentials for read-only Sheets access.

The service-account JSON is supplied through an environment variable that is
named in the config. It may hold either inline JSON or a path to a file stored
outside the repository with mode ``0600``. Tokens live only in memory: they
never appear in ``repr``, logs, or exception messages.

The OAuth refresh call is issued through a thin adapter over ``httpx`` so the
already installed HTTP client can satisfy ``google.auth.transport.Request``
without adding ``requests`` (ADR-0019).
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from sanka.config import SankaConfig
from sanka.tools.registry import ToolError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFRESH_MARGIN = timedelta(seconds=60)
_INSECURE_PERMISSION_BITS = 0o077
_NOT_CONFIGURED_MESSAGE = "Akses sheet belum diatur"
_REFRESH_FAILED_MESSAGE = "Akses sheet belum diatur"


class TokenProvider(Protocol):
    """Anything that can supply a bearer token for a Google API call."""

    def token(self) -> str: ...


@dataclass(frozen=True, slots=True)
class AuthResponse:
    """The subset of ``google.auth.transport.Response`` google-auth reads."""

    status: int
    data: bytes
    headers: Mapping[str, str]


class HttpxAuthRequest:
    """``google.auth.transport.Request`` implemented over ``httpx``.

    ``google-auth`` only needs the callable to accept keyword arguments
    ``url``, ``method``, ``body``, ``headers`` and ``timeout`` and to return an
    object exposing ``status``, ``data`` and ``headers``.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        self._client = client
        self._timeout = timeout

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> AuthResponse:
        owns_client = self._client is None
        client = self._client or httpx.Client()
        try:
            kwargs: dict[str, Any] = {
                "content": body,
                "headers": dict(headers or {}),
            }
            effective_timeout = timeout if timeout is not None else self._timeout
            if effective_timeout is not None:
                kwargs["timeout"] = effective_timeout
            response = client.request(method.upper(), url, **kwargs)
        finally:
            if owns_client:
                client.close()
        return AuthResponse(
            status=response.status_code,
            data=response.content,
            headers=dict(response.headers),
        )


class ServiceAccountToken:
    """Cached service-account access token.

    The token is refreshed when it is missing, when the credentials are not
    valid, or when it expires within the next 60 seconds. Otherwise the cached
    token is reused so each fetch does not pay for an OAuth round trip.
    """

    def __init__(
        self,
        info: Mapping[str, Any],
        scopes: Sequence[str],
        clock: Callable[[], datetime],
        *,
        transport: Callable[..., Any] | None = None,
        credentials_factory: Callable[[Mapping[str, Any], Sequence[str]], Any]
        | None = None,
    ) -> None:
        self._info = dict(info)
        self._scopes = tuple(scopes)
        self._clock = clock
        self._transport = transport
        self._credentials_factory = credentials_factory
        self._credentials: Any | None = None

    def __repr__(self) -> str:
        return (
            f"ServiceAccountToken(scopes={len(self._scopes)}, "
            f"loaded={self._credentials is not None})"
        )

    def token(self) -> str:
        credentials = self._get_credentials()
        if self._needs_refresh(credentials):
            try:
                credentials.refresh(self._request())
            except ToolError:
                raise
            except Exception:
                raise ToolError(
                    "not_configured", _REFRESH_FAILED_MESSAGE
                ) from None
        token = getattr(credentials, "token", None)
        if not isinstance(token, str) or not token:
            raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)
        return token

    def _get_credentials(self) -> Any:
        if self._credentials is None:
            factory = self._credentials_factory or _default_credentials_factory
            try:
                self._credentials = factory(self._info, self._scopes)
            except ToolError:
                raise
            except ImportError:
                raise ToolError(
                    "not_configured", _NOT_CONFIGURED_MESSAGE
                ) from None
            except Exception:
                raise ToolError(
                    "not_configured", _NOT_CONFIGURED_MESSAGE
                ) from None
        return self._credentials

    def _needs_refresh(self, credentials: Any) -> bool:
        token = getattr(credentials, "token", None)
        if not token:
            return True
        if getattr(credentials, "valid", True) is False:
            return True
        expiry = getattr(credentials, "expiry", None)
        if expiry is None:
            return True
        if not isinstance(expiry, datetime):
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        try:
            remaining = expiry - self._clock()
        except TypeError:
            return True
        return remaining < _REFRESH_MARGIN

    def _request(self) -> Callable[..., Any]:
        if self._transport is not None:
            return self._transport
        return HttpxAuthRequest()


def _default_credentials_factory(
    info: Mapping[str, Any], scopes: Sequence[str]
) -> Any:
    from google.oauth2.service_account import Credentials

    return Credentials.from_service_account_info(dict(info), scopes=list(scopes))


def _is_inside_repo(path: Path) -> bool:
    return path == _REPO_ROOT or _REPO_ROOT in path.parents


def _parse_info(raw: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE) from None
    if not isinstance(parsed, Mapping):
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)
    for key in ("client_email", "private_key"):
        if not isinstance(parsed.get(key), str) or not parsed.get(key):
            raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)
    return dict(parsed)


def load_service_account_info(env_name: str) -> Mapping[str, Any]:
    """Read service-account JSON from ``env_name``.

    The value is either inline JSON or a path to a file outside the repository
    with no group/other permission bits. Any failure is reported as
    ``not_configured`` without naming the path or echoing the content.
    """

    if not isinstance(env_name, str) or not env_name:
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)

    raw_value = os.environ.get(env_name, "").strip()
    if not raw_value:
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)

    if raw_value.startswith("{"):
        return _parse_info(raw_value)

    path = Path(raw_value).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE) from None

    if not resolved.is_file() or _is_inside_repo(resolved):
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)

    try:
        mode = stat.S_IMODE(resolved.stat().st_mode)
    except OSError:
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE) from None
    if mode & _INSECURE_PERMISSION_BITS:
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)

    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError:
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE) from None
    return _parse_info(content)


def build_token_provider(
    config: SankaConfig,
    *,
    clock: Callable[[], datetime] | None = None,
    transport: Callable[..., Any] | None = None,
) -> ServiceAccountToken:
    """Build a token provider from the workspace section of ``config``."""

    info = load_service_account_info(config.workspace.service_account_env)
    return ServiceAccountToken(
        info,
        config.workspace.scopes,
        clock or (lambda: datetime.now(timezone.utc)),
        transport=transport,
    )
