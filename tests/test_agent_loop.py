"""Tests for Sanka's adapted agent loop."""

from __future__ import annotations

from typing import Any, Generator

from core.agent_loop import BaseHandler, StepOutcome, agent_runner_loop


class FakeResponse:
    """Minimal stand-in for the client's returned response object."""

    def __init__(self, content: str, tool_calls: tuple[Any, ...] = ()) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeStreamingClient:
    """Streams a single text chunk and then returns a response."""

    def __init__(self, content: str = "Halo") -> None:
        self.content = content
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    def chat(
        self,
        messages: list[dict[str, Any]] | None = None,
        tools: Any = None,
    ) -> Generator[str, None, FakeResponse]:
        self.calls += 1
        self.requests.append([dict(message) for message in (messages or [])])
        yield self.content
        return FakeResponse(self.content, ())


class ExitHandler(BaseHandler):
    """Exits immediately with the model's answer."""

    def do_no_tool(self, args: dict[str, Any], response: Any) -> StepOutcome:
        return StepOutcome(response.content, should_exit=True)


class NeverExitHandler(BaseHandler):
    """Keeps asking for another turn forever."""

    def do_no_tool(self, args: dict[str, Any], response: Any) -> StepOutcome:
        return StepOutcome(None, next_prompt="again")


class NoHooksHandler(BaseHandler):
    """Ends the task without a next prompt and without ``_done_hooks``."""

    def do_no_tool(self, args: dict[str, Any], response: Any) -> StepOutcome:
        return StepOutcome("selesai", next_prompt=None)


def _run(gen: Generator[Any, None, dict[str, Any]]) -> tuple[list[Any], dict[str, Any]]:
    """Drive a loop generator, returning yielded chunks and its final value."""
    chunks: list[Any] = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as stop:
        return chunks, stop.value


def test_loop_exits_and_sends_history_in_order() -> None:
    client = FakeStreamingClient("Halo")
    history = [
        {"role": "user", "content": "pesan lama"},
        {"role": "assistant", "content": "balasan lama"},
    ]
    handler = ExitHandler()

    chunks, result = _run(
        agent_runner_loop(client, "SYS", "Halo", handler, None, history=history)
    )

    assert result == {"result": "EXITED", "data": "Halo"}
    assert "Halo" in "".join(str(chunk) for chunk in chunks)
    assert client.calls == 1
    assert client.requests == [
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "pesan lama"},
            {"role": "assistant", "content": "balasan lama"},
            {"role": "user", "content": "Halo"},
        ]
    ]


def test_loop_stops_when_max_turns_reached() -> None:
    client = FakeStreamingClient("lagi")
    handler = NeverExitHandler()

    _, result = _run(
        agent_runner_loop(client, "SYS", "mulai", handler, None, max_turns=2)
    )

    assert result == {"result": "MAX_TURNS_EXCEEDED"}
    assert client.calls == 2


def test_handler_without_done_hooks_does_not_crash() -> None:
    client = FakeStreamingClient("ok")
    handler = NoHooksHandler()
    assert not hasattr(handler, "_done_hooks")

    _, result = _run(
        agent_runner_loop(client, "SYS", "halo", handler, None, max_turns=3)
    )

    assert result == {"result": "CURRENT_TASK_DONE", "data": "selesai"}
    assert client.calls == 1
