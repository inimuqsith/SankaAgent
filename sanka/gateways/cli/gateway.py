"""Gateway CLI: REPL lokal yang memakai jiwa dan DB yang sama."""

from __future__ import annotations

import asyncio
import getpass
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from ...engine.context import ChatMessage
from ...engine.inbound import Inbound
from ...engine.registry import Handler
from .config import CliSettings

log = logging.getLogger(__name__)


class CliGateway:
    name = "cli"
    capabilities = frozenset({"text"})
    backlog_seconds = 0.0

    def __init__(self, config, db, history, windows, *, settings=None) -> None:
        from ...engine.config import GatewaySettings

        raw = settings or config.gateways.get("cli") or GatewaySettings("cli")
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        self.settings = CliSettings.from_mapping(raw.enabled, raw.options)
        self.counter = 0
        self.stopped = False
        self.agent = None

    def tools(self) -> list[Handler]:
        return []

    def commands(self) -> list[Handler]:
        return []

    async def run(self, agent) -> None:
        self.agent = agent
        print(
            f"Sanka console — mode: {agent.mode}; role: {self.settings.role or '-'}; "
            "ketik /help, /quit untuk keluar.",
            flush=True,
        )
        while not self.stopped:
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            text = line.rstrip("\n")
            if text.strip() in {"/quit", "/exit"}:
                break
            if not text.strip():
                continue
            self.counter += 1
            inbound = Inbound(
                chat_id=self.settings.chat_id,
                message=ChatMessage(
                    id=f"cli-{time.time_ns()}",
                    sender_name=self.settings.user or getpass.getuser(),
                    sender_id=self.settings.user or "local",
                    when=datetime.now(),
                    text=text,
                    sender_jid=self.settings.user or "local",
                ),
                is_dm=True,
                person_id=self.settings.user or "local",
                role_override=(self.settings.role,) if self.settings.role else (),
            )
            try:
                await agent.handle(inbound, self)
            except Exception:
                log.exception("console turn gagal")

    async def stop(self) -> None:
        self.stopped = True

    async def send_text(self, chat_id: str, text: str, *, reply: Inbound | None = None) -> str:
        self.counter += 1
        print(f"\nSanka: {text}\n", flush=True)
        return f"cli-{self.counter}"

    async def send_file(
        self, chat_id: str, path: Path, caption: str = "", *, reply: Inbound | None = None
    ) -> str:
        outbox = self.config.workspace / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        target = outbox / Path(path).name
        shutil.copy2(path, target)
        self.counter += 1
        print(f"\nSanka: (file) {target}{f' — {caption}' if caption else ''}\n", flush=True)
        return f"cli-file-{self.counter}"

    async def send_reaction(self, chat_id: str, message_id: str) -> str | None:
        return None

    async def typing(self, chat_id: str, composing: bool) -> None:
        return None

    async def prepare_media(self, chat_id: str, message: ChatMessage) -> None:
        return None

    def render(self, text: str) -> str:
        return str(text or "")

    def prompt_hints(self) -> str:
        return (
            "## CLI interface\n"
            "- You are answering in a local terminal: plain text, no WhatsApp formatting, no "
            "mentions, no quoting.\n"
            "- Stay brief; the operator can see tool results only through your answer.\n"
            "- Files you 'send' are copied to the workspace outbox and the path is printed."
        )

    def self_handles(self) -> set[str]:
        return set()

    def chat_name(self, chat_id: str) -> str:
        return self.settings.chat_name

    def list_targets(self, query: str, limit: int) -> list[tuple[str, str]]:
        return [(self.settings.chat_id, self.settings.chat_name)]

    def resolve_target(self, query: str):
        value = str(query or "").strip().casefold()
        if value in {self.settings.chat_id.casefold(), self.settings.chat_name.casefold()}:
            return (self.settings.chat_id, self.settings.chat_name), []
        return None, []

    def data_paths(self) -> list[Path]:
        return []
