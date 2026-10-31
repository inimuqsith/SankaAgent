"""Shared path guard for repo scripts: reject anything outside the allowed root."""

from pathlib import Path


def safe_path(root: Path, raw: object) -> Path:
    base = Path(root).resolve()
    path = Path(str(raw)).expanduser()
    path = (path if path.is_absolute() else base / path).resolve()
    if not path.is_relative_to(base):
        raise SystemExit(f"path di luar {base}: {raw}")
    return path
