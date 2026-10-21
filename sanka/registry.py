"""Registry handler: metadata (nama, alias, deskripsi, default izin) + auto-discovery folder."""

from __future__ import annotations

import fnmatch
import importlib
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HandlerFn = Callable[..., Awaitable[str]]
DEFAULT_ROLE = "*"


@dataclass(frozen=True)
class Handler:
    """Satu unit yang bisa dipanggil (command atau tool) beserta izin bawaannya."""

    name: str
    description: str
    fn: HandlerFn | None = None
    aliases: tuple[str, ...] = ()
    usage: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, bool] = field(default_factory=dict)
    pattern: str = ""
    hidden: bool = False

    def allowed_by_default(self, role: str) -> bool:
        return bool(self.defaults.get(role, self.defaults.get(DEFAULT_ROLE, False)))

    @property
    def perm(self) -> str:
        return self.pattern or self.name


def allows(patterns: tuple[str, ...], name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


class Registry:
    """Nama kanonik + alias -> Handler; pola izin diturunkan dari role dan default."""

    def __init__(self, handlers: list[Handler], kind: str):
        self.kind = kind
        self.by_name: dict[str, Handler] = {}
        self.aliases: dict[str, str] = {}
        for handler in handlers:
            key = handler.name.lower()
            if key in self.by_name:
                raise RuntimeError(f"{kind} duplikat: {key}")
            self.by_name[key] = handler
            for alias in handler.aliases:
                alias = alias.lower()
                if alias in self.aliases or alias in self.by_name:
                    raise RuntimeError(f"alias {kind} duplikat: {alias}")
                self.aliases[alias] = key

    def get(self, name: str) -> Handler | None:
        key = name.lower()
        return self.by_name.get(key) or self.by_name.get(self.aliases.get(key, ""))

    def names(self) -> list[str]:
        return sorted(self.by_name)

    def patterns(self, role: str, explicit: tuple[str, ...]) -> tuple[str, ...]:
        """Pola eksplisit dari config menang; kalau kosong pakai default tiap handler."""
        if explicit:
            return explicit
        return tuple(dict.fromkeys(h.perm for h in self.by_name.values() if h.allowed_by_default(role)))

    def entries(self, patterns: tuple[str, ...]) -> list[Handler]:
        return [h for h in self.by_name.values() if not h.hidden and allows(patterns, h.name)]


def discover(package: str, folder: Path, attribute: str) -> list[Handler]:
    """Impor semua modul di folder (urut nama) lalu ambil `attribute`."""
    found: list[Handler] = []
    for info in sorted(pkgutil.iter_modules([str(folder)]), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        handler = getattr(module, attribute, None)
        if handler is not None:
            found.append(handler)
    return found
