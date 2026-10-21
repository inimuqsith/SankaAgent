"""Registry tool bawaan: read, write, shell, web_search, sandbox. MCP dilengkapi sanka/mcp.py.

Semua eksekusi lewat `execute`: satu titik untuk permission, error, dan pemotongan output.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from .config import Config, RoleConfig
from .registry import Handler, Registry, allows

log = logging.getLogger(__name__)

MAX_OUTPUT = 20_000
MAX_ERROR = 500
TRUNCATED = "\n...[dipotong]"
WEB_BACKENDS = ("wikipedia", "yahoo", "startpage", "brave")

OWNER_MEMBER = {"owner": True, "member": True}
OWNER_ONLY = {"owner": True}


class ToolContext:
    def __init__(self, config: Config, role: RoleConfig, allowed: tuple[str, ...]):
        self.config = config
        self.role = role
        self.allowed = allowed


def _int(args: dict, key: str, default: int) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return default


def _resolve(ctx: ToolContext, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else ctx.config.workspace / path


def truncate(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + TRUNCATED


async def execute(specs: dict[str, Handler], name: str, args: dict, ctx: ToolContext) -> str:
    """Titik masuk tunggal: cek izin, jalan, tangkap error, potong output."""
    spec = specs.get(name) or next((item for item in specs.values() if name in item.aliases), None)
    if spec is None:
        return f"error: tool tidak dikenal: {name}"
    if not allows(ctx.allowed, spec.name):
        return f"error: tool '{name}' tidak diizinkan untuk role '{ctx.role.name}'"
    try:
        result = await spec.fn(args, ctx)
    except Exception as exc:  # tool apa pun tidak boleh mematikan bot
        log.exception("tool %s gagal", name)
        result = f"error: {type(exc).__name__}: {exc}"[:MAX_ERROR]
    return truncate(str(result))


async def _read(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    if not path.is_file():
        return f"error: file tidak ditemukan: {args.get('path')}"
    start = max(1, _int(args, "start", 1))
    limit = max(1, _int(args, "limit", 400))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(file kosong)"
    chosen = lines[start - 1 : start - 1 + limit]
    if not chosen:
        return f"(tidak ada baris di rentang {start}-{start + limit - 1}; file {len(lines)} baris)"
    return "\n".join(f"{start + i}: {line}" for i, line in enumerate(chosen))


async def _write(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"ok: {len(content)} karakter ditulis ke {path}"


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


def _search_sync(query: str, limit: int) -> list[dict]:
    from ddgs import DDGS

    engine = DDGS(timeout=10)
    found: list[dict] = []
    seen: set[str] = set()
    for backend in WEB_BACKENDS:
        try:
            hits = engine.text(query, max_results=limit, backend=backend)
        except Exception:  # satu backend blokir/limit tidak boleh menggagalkan pencarian
            continue
        for hit in hits:
            href = str(hit.get("href") or hit.get("url") or "")
            if href and href in seen:
                continue
            seen.add(href)
            found.append(hit)
        if len(found) >= limit:
            break
    return found[:limit]


async def _web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: query kosong"
    limit = min(10, max(1, _int(args, "max_results", 5)))
    results = await asyncio.to_thread(_search_sync, query, limit)
    if not results:
        return "tidak ada hasil"
    return "\n".join(
        f"{item.get('title', '')}\n{item.get('href', '')}\n{item.get('body', '')}\n"
        for item in results
    ).strip()


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


HANDLERS = [
    Handler(
        name="read",
        description="Baca file teks di workspace dengan nomor baris.",
        fn=_read,
        aliases=("cat",),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path file (relatif ke workspace atau absolut)."},
                "start": {"type": "integer", "description": "Baris awal (default 1)."},
                "limit": {"type": "integer", "description": "Jumlah baris (default 400)."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        defaults=OWNER_MEMBER,
    ),
    Handler(
        name="write",
        description="Tulis file teks di workspace (menimpa bila ada).",
        fn=_write,
        aliases=("save",),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path file (relatif ke workspace atau absolut)."},
                "content": {"type": "string", "description": "Isi file penuh."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        defaults=OWNER_ONLY,
    ),
    Handler(
        name="shell",
        description="Jalankan perintah shell di host pada direktori workspace.",
        fn=_shell,
        aliases=("bash", "exec"),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Perintah shell."},
                "timeout": {"type": "integer", "description": "Batas detik (default 60)."},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        defaults=OWNER_ONLY,
    ),
    Handler(
        name="web_search",
        description="Cari di web dan kembalikan judul, URL, dan cuplikan.",
        fn=_web_search,
        aliases=("search", "cari"),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Kata kunci pencarian."},
                "max_results": {"type": "integer", "description": "Jumlah hasil 1-10 (default 5)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        defaults=OWNER_MEMBER,
    ),
    Handler(
        name="sandbox",
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
    ),
    Handler(
        name="mcp",
        description="Tool dari server MCP stdio (satu keluarga, nama mcp__<server>__<tool>).",
        pattern="mcp__*",
        defaults=OWNER_MEMBER,
        hidden=True,
    ),
]

REGISTRY = Registry(HANDLERS, "tool")
