"""Service-account loading and token caching without any network access."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from sanka import google as google_module
from sanka.config import SankaConfig
from sanka.google import (
    HttpxAuthRequest,
    ServiceAccountToken,
    build_token_provider,
    load_service_account_info,
)
from sanka.tools.registry import ToolError

NOW = datetime(2026, 10, 21, 3, 14, tzinfo=timezone.utc)
SECRET_TOKEN = "ya29.secret-token-value"

INFO: dict[str, Any] = {
    "type": "service_account",
    "project_id": "sanka-test",
    "client_email": "sanka@example.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def _write_info(path: Path, mode: int) -> Path:
    path.write_text(json.dumps(INFO), encoding="utf-8")
    os.chmod(path, mode)
    return path


class FakeCredentials:
    def __init__(
        self,
        *,
        token: str = SECRET_TOKEN,
        expiry: datetime | None = NOW + timedelta(hours=1),
        valid: bool = True,
        fail: bool = False,
    ) -> None:
        self.token = token
        self.expiry = expiry
        self.valid = valid
        self.fail = fail
        self.refresh_calls = 0

    def refresh(self, request: Any) -> None:
        self.refresh_calls += 1
        if self.fail:
            raise RuntimeError(f"refresh failed for {SECRET_TOKEN}")
        self.token = f"ya29.refreshed-{self.refresh_calls}"
        self.expiry = NOW + timedelta(hours=1)
        self.valid = True


def _provider(credentials: FakeCredentials) -> ServiceAccountToken:
    return ServiceAccountToken(
        INFO,
        ("https://www.googleapis.com/auth/spreadsheets",),
        lambda: NOW,
        credentials_factory=lambda info, scopes: credentials,
    )


def test_inline_json_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(INFO))

    info = load_service_account_info("GOOGLE_SERVICE_ACCOUNT_JSON")

    assert info["client_email"] == INFO["client_email"]
    assert info["private_key"].startswith("-----BEGIN")


def test_file_path_with_0600_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_info(tmp_path / "service-account.json", 0o600)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(path))

    info = load_service_account_info("GOOGLE_SERVICE_ACCOUNT_JSON")

    assert info["client_email"] == INFO["client_email"]


def test_file_with_0644_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_info(tmp_path / "service-account.json", 0o644)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(path))

    with pytest.raises(ToolError) as excinfo:
        load_service_account_info("GOOGLE_SERVICE_ACCOUNT_JSON")

    assert excinfo.value.code == "not_configured"
    assert excinfo.value.message == "Akses sheet belum diatur"
    assert str(path) not in excinfo.value.message
    assert str(tmp_path) not in excinfo.value.message


def test_file_inside_repository_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(google_module, "_REPO_ROOT", tmp_path)
    path = _write_info(tmp_path / "service-account.json", 0o600)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(path))

    with pytest.raises(ToolError) as excinfo:
        load_service_account_info("GOOGLE_SERVICE_ACCOUNT_JSON")

    assert excinfo.value.code == "not_configured"


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "{not json", "[]", json.dumps({"client_email": "x"})],
)
def test_missing_or_invalid_env_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", value)

    with pytest.raises(ToolError) as excinfo:
        load_service_account_info("GOOGLE_SERVICE_ACCOUNT_JSON")

    assert excinfo.value.code == "not_configured"
    assert excinfo.value.message == "Akses sheet belum diatur"


def test_missing_file_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON", str(tmp_path / "absent.json")
    )

    with pytest.raises(ToolError) as excinfo:
        load_service_account_info("GOOGLE_SERVICE_ACCOUNT_JSON")

    assert excinfo.value.code == "not_configured"


def test_build_token_provider_uses_config_workspace(
    monkeypatch: pytest.MonkeyPatch, config: SankaConfig
) -> None:
    monkeypatch.setenv(
        config.workspace.service_account_env, json.dumps(INFO)
    )

    provider = build_token_provider(config)

    assert isinstance(provider, ServiceAccountToken)


def test_build_token_provider_without_env_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, config: SankaConfig
) -> None:
    monkeypatch.delenv(config.workspace.service_account_env, raising=False)

    with pytest.raises(ToolError) as excinfo:
        build_token_provider(config)

    assert excinfo.value.code == "not_configured"


def test_token_is_reused_until_near_expiry(
    caplog: pytest.CaptureFixture[str],
) -> None:
    credentials = FakeCredentials(expiry=NOW + timedelta(hours=1))
    provider = _provider(credentials)

    with caplog.at_level(logging.DEBUG):
        assert provider.token() == SECRET_TOKEN
        assert provider.token() == SECRET_TOKEN

    assert credentials.refresh_calls == 0
    assert SECRET_TOKEN not in repr(provider)
    for record in caplog.records:
        assert SECRET_TOKEN not in record.getMessage()


def test_token_refreshes_within_sixty_seconds(
    caplog: pytest.CaptureFixture[str],
) -> None:
    credentials = FakeCredentials(expiry=NOW + timedelta(seconds=30))
    provider = _provider(credentials)

    with caplog.at_level(logging.DEBUG):
        first = provider.token()
        second = provider.token()

    assert first == "ya29.refreshed-1"
    assert second == "ya29.refreshed-1"
    assert credentials.refresh_calls == 1
    for record in caplog.records:
        assert SECRET_TOKEN not in record.getMessage()


def test_token_refreshes_when_expiry_is_missing() -> None:
    credentials = FakeCredentials(expiry=None)
    provider = _provider(credentials)

    assert provider.token() == "ya29.refreshed-1"
    assert credentials.refresh_calls == 1


def test_token_refreshes_when_credentials_invalid() -> None:
    credentials = FakeCredentials(valid=False)
    provider = _provider(credentials)

    assert provider.token() == "ya29.refreshed-1"
    assert credentials.refresh_calls == 1


def test_refresh_failure_does_not_expose_token() -> None:
    credentials = FakeCredentials(fail=True, expiry=NOW + timedelta(seconds=10))
    provider = _provider(credentials)

    with pytest.raises(ToolError) as excinfo:
        provider.token()

    assert excinfo.value.code == "not_configured"
    assert excinfo.value.message == "Akses sheet belum diatur"
    assert SECRET_TOKEN not in excinfo.value.message
    assert excinfo.value.__cause__ is None


def test_repr_never_contains_token() -> None:
    provider = _provider(FakeCredentials())
    provider.token()

    text = repr(provider)

    assert SECRET_TOKEN not in text
    assert "ya29" not in text


def test_httpx_auth_request_adapter_shape() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(
            200,
            json={"access_token": "ya29.adapter"},
            headers={"content-type": "application/json"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    request = HttpxAuthRequest(client)

    response = request(
        "https://oauth2.googleapis.com/token",
        "POST",
        body=b"grant_type=refresh_token",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status == 200
    assert isinstance(response.data, bytes)
    assert b"ya29.adapter" in response.data
    assert response.headers["content-type"] == "application/json"
    assert seen == {
        "method": "POST",
        "url": "https://oauth2.googleapis.com/token",
        "body": b"grant_type=refresh_token",
        "content_type": "application/x-www-form-urlencoded",
    }
