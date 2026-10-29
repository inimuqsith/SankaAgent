"""Tool sandbox: jalankan perintah di container Podman/Docker dengan workspace ter-mount."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from ..config import SandboxConfig
from ..registry import Handler
from . import ToolContext, _int

MAX_OUTPUT_BYTES = 64 * 1024
TRUNCATED = "(dipotong)\n"
# Variabel yang boleh masuk ke container; sisanya (API key, dsb.) tidak diwariskan.
CONTAINER_ENV = ("TZ", "LANG")
# Variabel klien yang dibutuhkan CLI runtime itu sendiri (bukan env container).
CLIENT_ENV = ("PATH", "HOME", "XDG_RUNTIME_DIR", "TERM")
RUNTIMES = ("podman", "docker")


def resolve_runtime(cfg: SandboxConfig) -> str | None:
    """Pakai runtime dari config bila ada, kalau kosong coba podman lalu docker."""
    if cfg.runtime:
        return cfg.runtime if shutil.which(cfg.runtime) else None
    for name in RUNTIMES:
        if shutil.which(name):
            return name
    return None


def build_argv(
    runtime: str, cfg: SandboxConfig, workspace: Path, command: str
) -> list[str]:
    """Susun argv per runtime: flag Podman-only tidak boleh ikut ke Docker."""
    source = cfg.host_workspace or workspace
    args = [runtime, "run", "--rm", "-i"]
    if Path(runtime).name == "podman":
        args.append("--userns=keep-id")
    elif Path(runtime).name == "docker":
        # File di /work harus milik uid bot, bukan root daemon.
        args += ["--user", f"{os.getuid()}:{os.getgid()}"]
    args += [
        "--network",
        cfg.network or "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev",
    ]
    for name in dict.fromkeys((*CONTAINER_ENV, *cfg.env)):
        if name in os.environ:
            args += ["--env", f"{name}={os.environ[name]}"]
    mount = f"{source}:/work" + (":Z" if Path(runtime).name == "podman" else "")
    args += ["-v", mount, "-w", "/work", cfg.image, "bash", "-lc", command]
    return args


def client_env() -> dict[str, str]:
    """Env minimal untuk proses CLI; API key di env bot tidak ikut."""
    return {name: os.environ[name] for name in CLIENT_ENV if name in os.environ}


def _runtime_error(cfg: SandboxConfig) -> str:
    wanted = cfg.runtime or "podman/docker"
    return (
        f"error: runtime sandbox '{wanted}' tidak ditemukan di PATH; "
        "pasang podman atau docker (atau set sandbox.runtime di config.yaml)"
    )


def _source_error(source: Path) -> str:
    return f"error: workspace '{source}' bukan direktori"


DOCKER_SOCKET_HINT = (
    "error: docker.sock tidak bisa diakses oleh uid container (permission denied).\n"
    "Grup 'docker' di image harus memakai gid yang sama dengan socket di host. "
    "Build ulang dengan:\n"
    "  --build-arg DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)\n"
    "Periksa di host: stat -c '%g %a' /var/run/docker.sock\n"
    "Pastikan user sanka ada di grup itu lalu jalankan ulang container."
)


def _tail(raw: bytes) -> str:
    if len(raw) <= MAX_OUTPUT_BYTES:
        return raw.decode("utf-8", errors="replace")
    tail = raw[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
    return TRUNCATED + tail.lstrip("\n")


async def _sandbox(args: dict, ctx: ToolContext) -> str:
    cfg = ctx.config.sandbox
    runtime = resolve_runtime(cfg)
    if runtime is None:
        return _runtime_error(cfg)
    source = cfg.host_workspace or ctx.config.workspace
    # host_workspace hidup di namespace host (daemon Docker), jadi tidak selalu terlihat
    # dari container bot. Jangan tolak di sini; daemon yang melaporkan path salah.
    if cfg.host_workspace is None and not source.is_dir():
        return _source_error(source)
    command = str(args.get("command", ""))
    timeout = max(1, _int(args, "timeout", int(cfg.timeout_seconds)))
    argv = build_argv(runtime, cfg, ctx.config.workspace, command)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=client_env(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"error: timeout setelah {timeout} detik"
    if proc.returncode != 0:
        low = out.lower()
        if b"permission denied" in low and b"docker.sock" in low:
            return DOCKER_SOCKET_HINT
    return f"exit={proc.returncode}\n{_tail(out).strip()}".strip()


TOOL = Handler(
    name="sandbox",
    family="sandbox",
    description=(
        "Run a command in a sandbox container (Podman/Docker) with the workspace at /work."
    ),
    fn=_sandbox,
    aliases=("container",),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command inside the container."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default from config)."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
)
