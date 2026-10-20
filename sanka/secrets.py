"""Secret lookup from the environment or a permission-checked file.

Secrets come from environment variables first, then from an optional
``config/secrets.env`` or ``.env`` file (ADR-0014). Error messages name the
missing variable or the offending line, never a secret value.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

DEFAULT_SECRETS_PATHS = (Path("config/secrets.env"), Path(".env"))

# Any group or other permission bit is too permissive for a secrets file.
_INSECURE_PERMISSION_BITS = 0o077


class SecretError(ValueError):
    """Raised when a secrets file is invalid or a secret is missing."""


def _check_permissions(path: Path) -> None:
    """Reject secrets files readable or writable by group/others on POSIX."""
    if os.name != "posix":
        return

    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise SecretError(f"cannot stat secrets file: {path}") from exc

    if mode & _INSECURE_PERMISSION_BITS:
        raise SecretError(
            f"insecure permissions on secrets file {path}: "
            f"{mode:04o} (expected 0600)"
        )


def load_secrets_file(path: str | Path) -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from a secrets file.

    Blank lines and lines whose first non-space character is ``#`` are
    ignored. Keys and values are stripped of surrounding whitespace. On POSIX
    the file must not grant any group or other permission bits (``0600`` is
    expected). Missing files raise :class:`SecretError`; nothing is created.
    """
    secrets_path = Path(path)
    if not secrets_path.is_file():
        raise SecretError(f"secrets file not found: {secrets_path}")

    _check_permissions(secrets_path)

    try:
        with secrets_path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        raise SecretError(f"cannot read secrets file: {secrets_path}") from exc

    parsed: dict[str, str] = {}
    for number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator:
            raise SecretError(f"{secrets_path}: line {number}: missing '='")
        if not key:
            raise SecretError(f"{secrets_path}: line {number}: empty key")
        parsed[key] = value.strip()

    return parsed


def get_secret(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    secrets_path: str | Path | None = None,
) -> str:
    """Return the secret named ``name``.

    The environment wins over the secrets file, and an empty environment value
    is treated as unset. When ``secrets_path`` is omitted,
    ``config/secrets.env`` is tried first, then ``.env``. Raises
    :class:`SecretError` when the secret is absent; messages never include
    secret values.
    """
    source = os.environ if env is None else env
    value = source.get(name)
    if value:
        return value

    paths = (
        DEFAULT_SECRETS_PATHS
        if secrets_path is None
        else (Path(secrets_path),)
    )
    for path in paths:
        if not path.is_file():
            continue
        file_values = load_secrets_file(path)
        if name in file_values:
            return file_values[name]

    raise SecretError(f"missing secret: {name}")
