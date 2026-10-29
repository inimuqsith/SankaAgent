"""Kontrak gateway: transport masuk sebagai Inbound, keluar lewat method gateway."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .context import ChatMessage
from .registry import Handler


class GatewayError(RuntimeError):
    """Kegagalan transport yang aman ditampilkan ke model/user."""


@dataclass(frozen=True)
class Inbound:
    chat_id: str
    message: ChatMessage
    is_dm: bool
    is_mention: bool = False
    is_reply: bool = False
    is_self: bool = False
    person_id: str = ""
    role_override: tuple[str, ...] = ()
    raw: object = None  # event transport, dipakai gateway untuk quote/kutipan


class Gateway(Protocol):
    name: str
    capabilities: frozenset[str]
    backlog_seconds: float

    def tools(self) -> list[Handler]: ...

    def commands(self) -> list[Handler]: ...

    async def run(self, agent) -> None: ...

    async def stop(self) -> None: ...

    async def send_text(
        self, chat_id: str, text: str, *, reply: Inbound | None = None
    ) -> str: ...

    async def send_file(
        self, chat_id: str, path: Path, caption: str = "", *, reply: Inbound | None = None
    ) -> str: ...

    async def send_reaction(self, chat_id: str, message_id: str) -> str | None: ...

    async def typing(self, chat_id: str, composing: bool) -> None: ...

    async def prepare_media(self, chat_id: str, message: ChatMessage) -> None: ...

    def render(self, text: str) -> str: ...

    def prompt_hints(self) -> str: ...

    def self_handles(self) -> set[str]: ...

    def chat_name(self, chat_id: str) -> str: ...

    def list_targets(self, query: str, limit: int) -> list[tuple[str, str]]: ...

    def resolve_target(
        self, query: str
    ) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]: ...

    def data_paths(self) -> list[Path]: ...
