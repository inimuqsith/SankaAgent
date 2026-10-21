"""Tool sandbox: jalankan perintah di container Podman dengan workspace ter-mount."""

from __future__ import annotations

import asyncio
import shutil

from ..registry import Handler
from . import OWNER_ONLY, ToolContext, _int


async def _sandbox(args: dict, ctx: ToolContext) -> str:
    cfg = ctx.config.sandbox
    if shutil.which(cfg.runtime) is None:
        return f"error: {cfg.runtime} tidak ditemukan; pasang podman"
    command = str(args.get("command", ""))
    timeout = max(1, _int(args, "timeout", int(cfg.timeout_seconds)))
    argv = [
        cfg.runtime,
        "run",
        "--rm",
        "-i",
        "--userns=keep-id",
        "-v",
        f"{ctx.config.workspace}:/work:Z",
        "-w",
        "/work",
        cfg.image,
        "bash",
        "-lc",
        command,
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv,
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
    name="sandbox",
    family="sandbox",
    description="Jalankan perintah di container Podman dengan workspace ter-mount di /work.",
    fn=_sandbox,
    aliases=("container",),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Perintah shell di dalam container."},
            "timeout": {"type": "integer", "description": "Batas detik (default dari config)."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    defaults=OWNER_ONLY,
)
