"""Tests for the OpenCode Go client using an in-process HTTP mock."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import pytest

from sanka.agent.client import (
    AgentClientError,
    ChatResponse,
    FunctionCall,
    OpenCodeGoClient,
    ToolCall,
)

API_KEY = "sk-test-super-secret"
SESSION_ID = "session-abc-123"
BASE_URL = "https://api.example.test/v1"
MODEL = "deepseek-v4.1-flash"


def _transport(
    responses: Sequence[tuple[int, Any, dict[str, str] | None]],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Build a transport that replays responses, repeating the last one."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        index = min(len(requests) - 1, len(responses) - 1)
        status, payload, headers = responses[index]
        if isinstance(payload, bytes):
            return httpx.Response(status, content=payload, headers=headers or {})
        return httpx.Response(status, json=payload, headers=headers or {})

    return httpx.MockTransport(handler), requests


def _client(
    transport: httpx.BaseTransport,
    *,
    max_retries: int = 3,
    sleep: Callable[[float], None] | None = None,
    base_url: str = BASE_URL,
) -> OpenCodeGoClient:
    return OpenCodeGoClient(
        api_key=API_KEY,
        base_url=base_url,
        model=MODEL,
        session_id=SESSION_ID,
        max_retries=max_retries,
        transport=transport,
        sleep=sleep or (lambda _seconds: None),
    )


def _collect(
    stream: Any,
) -> tuple[list[str], ChatResponse]:
    """Drain a chat generator and return its yields plus return value."""
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(stream))
        except StopIteration as stop:
            return chunks, stop.value


def _success_payload() -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Halo, aku Sanka.",
                    "reasoning_content": "private chain of thought",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query": "cuaca"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def test_request_shape_headers_and_parsing() -> None:
    transport, requests = _transport([(200, _success_payload(), None)])
    client = _client(transport)
    messages = [
        {"role": "system", "content": "Kamu Sanka."},
        {"role": "user", "content": "Halo"},
    ]
    tools = [{"type": "function", "function": {"name": "web_search"}}]

    chunks, response = _collect(client.chat(messages, tools))

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-opencode-session"] == SESSION_ID
    assert request.headers["user-agent"] == "sanka-agent/0.1"

    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["messages"] == messages
    assert body["stream"] is False
    assert body["tools"] == tools

    assert chunks == ["Halo, aku Sanka."]
    assert response == ChatResponse(
        content="Halo, aku Sanka.",
        tool_calls=(
            ToolCall(
                id="call_1",
                function=FunctionCall(
                    name="web_search", arguments='{"query": "cuaca"}'
                ),
            ),
        ),
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    )
    # reasoning_content must never reach callers.
    assert "private chain of thought" not in response.content


def test_empty_tools_omitted_and_empty_content_not_yielded() -> None:
    transport, requests = _transport(
        [
            (
                200,
                {"choices": [{"message": {"role": "assistant", "content": ""}}]},
                None,
            )
        ]
    )
    client = _client(transport, base_url=f"{BASE_URL}/")

    chunks, response = _collect(client.chat([{"role": "user", "content": "Halo"}], []))

    assert chunks == []
    assert response.content == ""
    assert response.tool_calls == ()
    assert response.usage is None
    body = json.loads(requests[0].content)
    assert "tools" not in body
    assert str(requests[0].url) == f"{BASE_URL}/chat/completions"


def test_429_is_retried_and_numeric_retry_after_is_honored() -> None:
    sleeps: list[float] = []
    transport, requests = _transport(
        [
            (429, {"error": "rate limited"}, {"Retry-After": "2"}),
            (200, _success_payload(), None),
        ]
    )
    client = _client(transport, sleep=sleeps.append)

    chunks, response = _collect(client.chat([{"role": "user", "content": "Halo"}]))

    assert len(requests) == 2
    assert sleeps == [2.0]
    assert chunks == ["Halo, aku Sanka."]
    assert response.content == "Halo, aku Sanka."


def test_429_non_numeric_retry_after_falls_back_to_backoff() -> None:
    sleeps: list[float] = []
    transport, requests = _transport(
        [
            (429, {"error": "slow down"}, {"Retry-After": "later"}),
            (200, _success_payload(), None),
        ]
    )
    client = _client(transport, sleep=sleeps.append)

    _collect(client.chat([{"role": "user", "content": "Halo"}]))

    assert len(requests) == 2
    assert sleeps == [1.0]


def test_500_is_retried_with_exponential_backoff() -> None:
    sleeps: list[float] = []
    transport, requests = _transport(
        [
            (500, {"error": "boom"}, None),
            (500, {"error": "boom"}, None),
            (200, _success_payload(), None),
        ]
    )
    client = _client(transport, sleep=sleeps.append)

    chunks, _response = _collect(client.chat([{"role": "user", "content": "Halo"}]))

    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]
    assert chunks == ["Halo, aku Sanka."]


def test_400_is_not_retried() -> None:
    sleeps: list[float] = []
    transport, requests = _transport([(400, {"error": "bad request"}, None)])
    client = _client(transport, sleep=sleeps.append)

    with pytest.raises(AgentClientError) as excinfo:
        _collect(client.chat([{"role": "user", "content": "Halo"}]))

    assert excinfo.value.status_code == 400
    assert "HTTP 400" in str(excinfo.value)
    assert len(requests) == 1
    assert sleeps == []


def test_exhausted_retries_raise_with_status() -> None:
    sleeps: list[float] = []
    transport, requests = _transport([(500, {"error": "boom"}, None)])
    client = _client(transport, max_retries=2, sleep=sleeps.append)

    with pytest.raises(AgentClientError) as excinfo:
        _collect(client.chat([{"role": "user", "content": "Halo"}]))

    assert excinfo.value.status_code == 500
    assert "HTTP 500" in str(excinfo.value)
    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]


def test_error_text_never_leaks_secret_or_prompt() -> None:
    prompt = "isi pesan pribadi pengguna"
    transport, _requests = _transport(
        [(401, {"error": {"message": f"invalid key {API_KEY}"}}, None)]
    )
    client = _client(transport)

    with pytest.raises(AgentClientError) as excinfo:
        _collect(client.chat([{"role": "user", "content": prompt}]))

    text = str(excinfo.value)
    assert "401" in text
    assert API_KEY not in text
    assert prompt not in text


def test_malformed_json_raises_without_echoing_body() -> None:
    transport, _requests = _transport(
        [
            (
                200,
                f'{{"broken": "{API_KEY}"'.encode(),
                {"content-type": "application/json"},
            )
        ]
    )
    client = _client(transport)

    with pytest.raises(AgentClientError) as excinfo:
        _collect(client.chat([{"role": "user", "content": "Halo"}]))

    text = str(excinfo.value)
    assert "invalid json" in text.lower()
    assert API_KEY not in text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": "not-a-list"},
        {"choices": [{}]},
        {"choices": [{"message": {"content": 42}}]},
        {"choices": [{"message": {"content": "ok", "tool_calls": "nope"}}]},
        {
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "tool_calls": [
                            {"id": "call_1", "function": {"name": "t"}}
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "ok"}}], "usage": "bad"},
    ],
)
def test_malformed_shape_raises(payload: dict[str, Any]) -> None:
    transport, _requests = _transport([(200, payload, None)])
    client = _client(transport)

    with pytest.raises(AgentClientError) as excinfo:
        _collect(client.chat([{"role": "user", "content": "Halo"}]))

    assert "invalid response shape" in str(excinfo.value)
