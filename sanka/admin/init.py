"""`sanka init`: scaffold config/gateway/engine/soul/roles/SOUL.md tanpa menimpa diam-diam."""

from __future__ import annotations

import os
from pathlib import Path

from . import templates


def _write(path: Path, content: str, *, force: bool, mode: int | None = None) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        with __import__("contextlib").suppress(OSError):
            path.chmod(mode)
    return True


def init(folder: str | Path, *, force: bool = False) -> tuple[list[Path], list[Path]]:
    """Tulis scaffold; kembalikan (ditulis, dilewati)."""
    base = Path(folder).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    (base / ".sanka" / "workspace").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    skipped: list[Path] = []
    for name, content in templates.files().items():
        path = base / name
        mode = 0o600 if name == ".env" else None
        if _write(path, content, force=force, mode=mode):
            written.append(path)
        else:
            skipped.append(path)
    return written, skipped


def report(base: str | Path, written: list[Path], skipped: list[Path]) -> str:
    lines = [f"Sanka scaffold: {Path(base).expanduser().resolve()}"]
    for path in written:
        lines.append(f"  tulis  {path}")
    for path in skipped:
        lines.append(f"  lewati {path} (sudah ada; --force untuk menimpa)")
    lines.extend(
        [
            "Langkah berikut:",
            "  1. isi .env (OPENCODE_GO_API_KEY, SANKA_DATABASE_URL)",
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
