"""`sanka init`: scaffold config.yaml, roles.yaml, SOUL.md, and .env without overwriting."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from . import templates

PRIVATE_FILES = (".env", "roles.yaml")


def _write(path: Path, content: str, *, force: bool, mode: int | None = None) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        with contextlib.suppress(OSError):
            path.chmod(mode)
    return True


def init(folder: str | Path, *, force: bool = False) -> tuple[list[Path], list[Path]]:
    """Write the scaffold; return (written, skipped)."""
    base = Path(folder).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    (base / ".sanka" / "workspace").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    skipped: list[Path] = []
    for name, content in templates.files().items():
        path = base / name
        mode = 0o600 if name in PRIVATE_FILES else None
        if _write(path, content, force=force, mode=mode):
            written.append(path)
        else:
            skipped.append(path)
    return written, skipped


def report(base: str | Path, written: list[Path], skipped: list[Path]) -> str:
    lines = [f"Sanka scaffold: {Path(base).expanduser().resolve()}"]
    for path in written:
        lines.append(f"  wrote  {path}")
    for path in skipped:
        lines.append(f"  kept   {path} (already exists; --force to overwrite)")
    lines.extend(
        [
            "Next steps:",
            "  1. fill .env (OPENCODE_GO_API_KEY, SANKA_DATABASE_URL)",
            "  2. sanka db upgrade",
            "  3. sanka run",
        ]
    )
    return "\n".join(lines)


def env_mode(path: str | Path) -> int:
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return 0
