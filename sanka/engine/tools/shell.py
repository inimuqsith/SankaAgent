"""Tool shell: jalankan perintah shell di host pada direktori workspace."""

from __future__ import annotations

import asyncio

from ..registry import Handler
from . import ToolContext, _int


async def _shell(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command", ""))
    timeout = max(1, _int(args, "timeout", 60))
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(ctx.config.workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"error: timeout setelah {timeout} detik"
    text = out.decode("utf-8", errors="replace").strip()
    return f"exit={proc.returncode}\n{text}".strip()


TOOL = Handler(
    name="shell",
    family="shell",
    description="Run a shell command on the host in the workspace directory.",
    fn=_shell,
    aliases=("bash", "exec"),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
)
