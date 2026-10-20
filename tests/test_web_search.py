"""Tests for the ``web_search`` tool and the SearXNG provider."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx
import pytest

from sanka.config import SankaConfig, SearchConfig
from sanka.tools.registry import ToolError
from sanka.tools.web import (
    SearxngSearchProvider,
    SearchHit,
    make_web_search_tool,
)
from tests.fakes import FakeSearchProvider

BASE_URL = "http://127.0.0.1:8888"


def _search_config(**overrides: Any) -> SearchConfig:
    values: dict[str, Any] = {
        "base_url": BASE_URL,
        "max_results": 5,
        "timeout_seconds": 5,
        "max_chars": 200,
    }
    values.update(overrides)
    return SearchConfig(**values)


def _provider(config: SearchConfig, handler: Any) -> SearxngSearchProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SearxngSearchProvider(config, client=client)


def _json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def test_search_sends_q_and_json_without_api_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response({"results": []})

    provider = _provider(_search_config(base_url=f"{BASE_URL}/"), handler)
    provider.search("sanka test", 3)

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/search"
    assert request.url.params["q"] == "sanka test"
    assert request.url.params["format"] == "json"
    assert "x-api-key" not in request.headers


def test_search_sends_api_key_header_when_env_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANKA_SEARCH_KEY", "top-secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response({"results": []})

    provider = _provider(_search_config(api_key_env="SANKA_SEARCH_KEY"), handler)
    provider.search("q", 1)

    assert seen[0].headers["x-api-key"] == "top-secret"


def test_search_missing_api_key_env_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANKA_SEARCH_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("request must not be sent")

    provider = _provider(_search_config(api_key_env="SANKA_SEARCH_KEY"), handler)
    with pytest.raises(ToolError) as raised:
        provider.search("q", 1)
    assert raised.value.code == "not_configured"


def test_search_parses_and_skips_invalid_entries() -> None:
    payload = {
        "results": [
            "not-a-mapping",
            {"title": "First", "url": "http://example.test/a", "content": "alpha"},
            {"title": "No URL", "content": "ignored"},
            {"url": "http://example.test/b"},
            {"title": "Third", "url": "http://example.test/c", "content": None},
        ]
    }

    provider = _provider(_search_config(max_results=5), lambda request: _json_response(payload))
    hits = provider.search("q", 5)

    assert hits == (
        SearchHit("First", "http://example.test/a", "alpha"),
        SearchHit("", "http://example.test/b", ""),
        SearchHit("Third", "http://example.test/c", ""),
    )


def test_search_caps_results_to_limit_and_config_max() -> None:
    payload = {
        "results": [
            {"title": str(index), "url": f"http://example.test/{index}", "content": ""}
            for index in range(8)
        ]
    }

    provider = _provider(
        _search_config(max_results=3),
        lambda request: _json_response(payload),
    )

    assert len(provider.search("q", 2)) == 2
    assert len(provider.search("q", 10)) == 3


def test_search_truncates_snippets() -> None:
    payload = {
        "results": [
            {
                "title": "Long",
                "url": "http://example.test/long",
                "content": "x" * 50,
            }
        ]
    }

    provider = _provider(
        _search_config(max_chars=10),
        lambda request: _json_response(payload),
    )

    hit = provider.search("q", 1)[0]
    assert len(hit.snippet) == 10


def test_empty_base_url_is_not_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("request must not be sent")

    provider = _provider(_search_config(base_url=""), handler)
    with pytest.raises(ToolError) as raised:
        provider.search("q", 1)
    assert raised.value.code == "not_configured"


def test_upstream_500_is_upstream_error() -> None:
    provider = _provider(
        _search_config(),
        lambda request: _json_response({"error": "boom"}, status=500),
    )

    with pytest.raises(ToolError) as raised:
        provider.search("q", 1)
    assert raised.value.code == "upstream_error"
    assert "boom" not in raised.value.message


def test_timeout_maps_to_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    provider = _provider(_search_config(), handler)
    with pytest.raises(ToolError) as raised:
        provider.search("q", 1)
    assert raised.value.code == "timeout"


def test_invalid_json_is_upstream_error() -> None:
    provider = _provider(
        _search_config(),
        lambda request: httpx.Response(200, text="<html>not json</html>"),
    )

    with pytest.raises(ToolError) as raised:
        provider.search("q", 1)
    assert raised.value.code == "upstream_error"


def test_tool_handler_returns_data_and_numbered_text(config: SankaConfig) -> None:
    sanka_config = replace(
        config,
        tools=replace(config.tools, web_search=_search_config()),
    )
    fake = FakeSearchProvider(
        [
            SearchHit("Alpha", "http://example.test/a", "first snippet"),
            SearchHit("Beta", "http://example.test/b", "second snippet"),
        ]
    )
    spec = make_web_search_tool(sanka_config, fake)
    result = spec.handler({"query": "sanka", "limit": 3}, None)  # type: ignore[arg-type]

    assert result.ok is True
    assert result.tool == "web_search"
    assert result.data == {
        "query": "sanka",
        "provider": "searxng",
        "results": [
            {
                "title": "Alpha",
                "url": "http://example.test/a",
                "snippet": "first snippet",
            },
            {
                "title": "Beta",
                "url": "http://example.test/b",
                "snippet": "second snippet",
            },
        ],
    }
    assert fake.calls == [("sanka", 3)]
    assert "1. Alpha" in result.text
    assert "2. Beta" in result.text
    assert "http://example.test/b" in result.text
    assert "<data" not in result.text


def test_tool_handler_defaults_limit(config: SankaConfig) -> None:
    sanka_config = replace(
        config,
        tools=replace(config.tools, web_search=_search_config()),
    )
    fake = FakeSearchProvider()
    spec = make_web_search_tool(sanka_config, fake)
    result = spec.handler({"query": "kosong"}, None)  # type: ignore[arg-type]

    assert fake.calls == [("kosong", 5)]
    assert result.data["results"] == []
    assert "Tidak ada hasil" in result.text
