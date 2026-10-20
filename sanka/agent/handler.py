"""Agent handler for the adapted GenericAgent loop."""

from __future__ import annotations

import threading
from typing import Any

from core.agent_loop import BaseHandler, StepOutcome


class SankaHandler(BaseHandler):
    """Plan-2 handler: no tools, one final answer per turn.

    Tool methods (`do_<name>`) will be added by the tool registry in plan-4.
    """

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        self.cancel_event = cancel_event or threading.Event()
        self._done_hooks: list[str] = []

    def do_no_tool(self, args: dict[str, Any], response: Any) -> StepOutcome:
        if self.cancel_event.is_set():
            return StepOutcome(None, should_exit=True)
        content = getattr(response, "content", "") or ""
        return StepOutcome(content, should_exit=True)
