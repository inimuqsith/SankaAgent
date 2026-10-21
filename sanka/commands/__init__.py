"""Registry command. Command baru = taruh modul di folder ini dengan `COMMAND = Handler(...)`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..registry import Handler, Registry, discover


@dataclass(frozen=True)
class CommandContext:
    app: Any
    chat_id: str
    role: Any
    args: str
    commands: tuple[Handler, ...]
    tools: tuple[Handler, ...]


REGISTRY = Registry(
    discover(__name__, Path(__file__).parent, "COMMAND", "command"),
    "command",
)
