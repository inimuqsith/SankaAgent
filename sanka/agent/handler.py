"""Agent handler for the adapted GenericAgent loop."""

from __future__ import annotations

import threading
from typing import Any

from core.agent_loop import BaseHandler, StepOutcome

from sanka.tools.registry import ToolContext, ToolRegistry

_TOOL_BUDGET_REPLY = (
    "Batas pemanggilan tool untuk giliran ini tercapai. "
    "Jawab memakai data yang sudah ada."
)


class SankaHandler(BaseHandler):
    """Plan-5 handler: executes permitted read tools through the registry.

    Only tool names registered in the registry get a ``do_<name>`` method, so
    the loop can reach every registered tool; the registry still enforces the
    role gate on execution. Tools that are not registered never run.
    """

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        *,
        tools: ToolRegistry | None = None,
        context: ToolContext | None = None,
        max_calls_per_turn: int = 6,
    ) -> None:
        self.cancel_event = cancel_event or threading.Event()
        self._done_hooks: list[str] = []
        self._tools = tools
        self._context = context
        self._max_calls = max(0, max_calls_per_turn)
        self._tool_calls = 0
        if tools is not None and context is not None:
            for name in tools.names():
                self._install_tool(name)

    def _install_tool(self, name: str) -> None:
        def run(
            args: dict[str, Any], response: Any, tool_name: str = name
        ) -> StepOutcome:
            return self._run_tool(tool_name, args)

        setattr(self, f"do_{name}", run)

    def _run_tool(self, name: str, args: dict[str, Any]) -> StepOutcome:
        if self.cancel_event.is_set():
            return StepOutcome(None, should_exit=True)
        if self._tool_calls >= self._max_calls:
            return StepOutcome(
                None, next_prompt=_TOOL_BUDGET_REPLY, should_exit=False
            )
        self._tool_calls += 1
        # ``BaseHandler.dispatch`` injects loop-only keys into ``args``.
        clean_args = {
            key: value for key, value in args.items() if not key.startswith("_")
        }
        result = self._tools.execute(name, clean_args, self._context)  # type: ignore[union-attr]
        prompt = f"Hasil tool {name}:\n{result.text}"
        return StepOutcome(result.text, next_prompt=prompt, should_exit=False)

    def do_no_tool(self, args: dict[str, Any], response: Any) -> StepOutcome:
        if self.cancel_event.is_set():
            return StepOutcome(None, should_exit=True)
        content = getattr(response, "content", "") or ""
        return StepOutcome(content, should_exit=True)
