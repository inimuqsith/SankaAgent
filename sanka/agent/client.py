"""Minimal HTTP client for OpenCode Go chat completions.

Only the non-streaming ``POST {base_url}/chat/completions`` endpoint is
supported (see ADR-0017). The request/response shape follows the
OpenAI-compatible format used by OpenCode Go. This module intentionally does
not reuse GenericAgent's ``llmcore.py``; the upstream loop receives the same
``client.chat(...)`` generator contract and the response mapping lives here.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

_DEFAULT_USER_AGENT = "sanka-agent/0.1"
_MAX_BACKOFF_SECONDS = 4.0


class AgentClientError(RuntimeError):
    """Raised when a chat completion request or response cannot be used.

    Messages carry only an HTTP status and/or a generic reason. They must never
    include the API key, the prompt, or user message content.
    """

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class FunctionCall:
    """A tool function name and its raw JSON arguments string."""

    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A single tool call requested by the model."""

    id: str
    function: FunctionCall


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """A parsed non-streaming chat completion."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, Any] | None = None


class OpenCodeGoClient:
    """Non-streaming client for the OpenCode Go chat completions endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        session_id: str,
        *,
        timeout_seconds: float = 120,
        max_retries: int = 3,
        user_agent: str = _DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not base_url:
            raise ValueError("base_url must not be empty")
        if not model:
            raise ValueError("model must not be empty")
        if not session_id:
            raise ValueError("session_id must not be empty")

        self._api_key = api_key
        self._model = model
        self._session_id = session_id
        self._user_agent = user_agent
        self._max_retries = max(0, max_retries)
        self._sleep = sleep
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._http.close()

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> Generator[str, None, ChatResponse]:
        """Request one completion.

        Yields the response content once when non-empty, then returns the
        parsed :class:`ChatResponse`. Retries only 429 and 5xx responses.
        """
        body: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": False,
        }
        if tools:
            body["tools"] = list(tools)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "x-opencode-session": self._session_id,
            "User-Agent": self._user_agent,
        }

        retries_used = 0
        while True:
            try:
                response = self._http.post(self._endpoint, json=body, headers=headers)
            except httpx.HTTPError:
                # Transport details can contain URLs; keep the surface generic.
                raise AgentClientError("request failed") from None

            status = response.status_code
            if status == 429 or 500 <= status <= 599:
                if retries_used >= self._max_retries:
                    raise AgentClientError(f"HTTP {status}", status_code=status)
                self._sleep(self._retry_delay(retries_used, response))
                retries_used += 1
                continue

            if status >= 400:
                raise AgentClientError(f"HTTP {status}", status_code=status)

            result = self._parse_response(response)
            if result.content:
                yield result.content
            return result

    def _retry_delay(self, retries_used: int, response: httpx.Response) -> float:
        """Return the numeric Retry-After when present, else 1/2/4s backoff."""
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                delay = float(retry_after.strip())
            except ValueError:
                delay = -1.0
            if delay >= 0:
                return delay
        return min(2.0**retries_used, _MAX_BACKOFF_SECONDS)

    def _parse_response(self, response: httpx.Response) -> ChatResponse:
        try:
            payload = response.json()
        except ValueError:
            raise AgentClientError("invalid JSON response") from None

        if not isinstance(payload, dict):
            raise AgentClientError("invalid response shape")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AgentClientError("invalid response shape")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise AgentClientError("invalid response shape")

        message = choice.get("message")
        if not isinstance(message, dict):
            raise AgentClientError("invalid response shape")

        content = message.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            raise AgentClientError("invalid response shape")

        usage = payload.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise AgentClientError("invalid response shape")

        return ChatResponse(
            content=content,
            tool_calls=self._parse_tool_calls(message.get("tool_calls")),
            usage=usage,
        )

    @staticmethod
    def _parse_tool_calls(raw: Any) -> tuple[ToolCall, ...]:
        # reasoning_content is deliberately not read: it must never leave the client.
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise AgentClientError("invalid response shape")

        calls: list[ToolCall] = []
        for item in raw:
            if not isinstance(item, dict):
                raise AgentClientError("invalid response shape")
            function = item.get("function")
            if not isinstance(function, dict):
                raise AgentClientError("invalid response shape")

            call_id = item.get("id")
            name = function.get("name")
            arguments = function.get("arguments")
            if (
                not isinstance(call_id, str)
                or not isinstance(name, str)
                or not isinstance(arguments, str)
            ):
                raise AgentClientError("invalid response shape")

            calls.append(
                ToolCall(
                    id=call_id,
                    function=FunctionCall(name=name, arguments=arguments),
                )
            )
        return tuple(calls)
