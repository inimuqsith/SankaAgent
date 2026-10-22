"""Klien MCP: satu Hub untuk semua server stdio/HTTP, tool diekspos cepat gagal-toleran."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from functools import partial

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from .config import MCPServerConfig, RoleConfig
from .permissions import tool_allowed
from .registry import Handler

log = logging.getLogger(__name__)

START_TIMEOUT = 20
CALL_TIMEOUT = 60


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _target(server: MCPServerConfig) -> str | StdioServerParameters:
    """url = Streamable HTTP (SDK v2 menerima URL langsung), selain itu subprocess stdio."""
    if server.url:
        return server.url
    return StdioServerParameters(
        command=server.command or "",
        args=list(server.args),
        env=server.env or None,
    )


class Hub:
    def __init__(self, servers: dict[str, MCPServerConfig]):
        self.servers = servers
        self._stack = contextlib.AsyncExitStack()
        self._handlers: dict[str, Handler] = {}  # exposed name -> handler
        self._clients: dict[str, tuple[object, object]] = {}  # exposed name -> (client, tool)

    async def start(self) -> None:
        for name, server in self.servers.items():
            try:
                client = await asyncio.wait_for(
                    self._stack.enter_async_context(Client(_target(server))),
                    START_TIMEOUT,
                )
                listed = await asyncio.wait_for(client.list_tools(), START_TIMEOUT)
            except Exception as exc:  # server rusak tidak boleh menggagalkan bot
                log.warning("MCP %s gagal: %s", name, exc)
                continue
            for tool in listed.tools:
                exposed = f"mcp__{_slug(name)}__{_slug(tool.name)}"
                self._handlers[exposed] = Handler(
                    name=exposed,
                    family="mcp",
                    description=f"[{name}] {tool.description or tool.name}",
                    fn=partial(self.call, exposed),
                    parameters=tool.input_schema or {"type": "object", "properties": {}},
                    defaults={"owner": True, "member": True},
                    pattern="mcp__*",
                )
                self._clients[exposed] = (client, tool)
                log.info("MCP %s: tool %s", name, exposed)

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self._stack.aclose()

    def specs(self, role: RoleConfig) -> list[Handler]:
        return [handler for handler in self._handlers.values() if tool_allowed(role, handler.name)]

    async def call(self, exposed_name: str, args: dict, ctx: object = None) -> str:
        if exposed_name not in self._clients:
            return f"error: MCP tool tidak dikenal: {exposed_name}"
        client, tool = self._clients[exposed_name]
        result = await asyncio.wait_for(client.call_tool(tool.name, args), CALL_TIMEOUT)
        text = "\n".join(
            block.text for block in result.content if isinstance(block, TextContent) and block.text
        )
        text = text.strip() or "(tanpa output)"
        return f"error: {text}" if result.is_error else text
