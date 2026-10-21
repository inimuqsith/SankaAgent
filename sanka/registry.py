"""Registry handler: metadata (nama, alias, deskripsi, default izin) + auto-discovery folder."""

from __future__ import annotations

import fnmatch
import importlib
import pkgutil
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HandlerFn = Callable[..., Awaitable[str]]
DEFAULT_ROLE = "*"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RegistryError(RuntimeError):
    """Handler melanggar kontrak, atau ada nama/alias duplikat. Semua error dikumpulkan."""


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
    family: str = ""

    def allowed_by_default(self, role: str) -> bool:
        return bool(self.defaults.get(role, self.defaults.get(DEFAULT_ROLE, False)))

    @property
    def perm(self) -> str:
        return self.pattern or self.name


def allows(patterns: tuple[str, ...], name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _schema_errors(parameters: object) -> list[str]:
    if not isinstance(parameters, dict):
        return ["parameters harus object schema"]
    errors: list[str] = []
    if parameters.get("type") != "object":
        errors.append("parameters.type harus 'object'")
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        errors.append("parameters.properties harus object")
        properties = {}
    for key, value in properties.items():
        if not isinstance(value, dict):
            errors.append(f"parameters.properties['{key}'] harus object")
    required = parameters.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            errors.append("parameters.required harus list[str]")
        else:
            unknown = [item for item in required if item not in properties]
            if unknown:
                errors.append(f"parameters.required memuat properti tak dikenal: {', '.join(unknown)}")
    return errors


def _defaults_errors(defaults: object) -> list[str]:
    ok = isinstance(defaults, dict) and all(
        isinstance(key, str) and key and isinstance(value, bool) for key, value in defaults.items()
    )
    return [] if ok else ["defaults harus dict[str, bool]"]


def validate(
    handler: Handler,
    source: str,
    kind: str,
    families: frozenset[str] = frozenset(),
) -> list[str]:
    """Periksa kontrak handler; kembalikan daftar error berprefix `<source>.py: `."""
    errors: list[str] = []

    def add(message: str) -> None:
        errors.append(f"{source}.py: {message}")

    if handler.name != source:
        add(f"nama handler '{handler.name}' harus sama dengan nama file '{source}'")
    if not NAME.fullmatch(handler.name):
        add(f"nama tidak valid: '{handler.name}'")
    if not handler.description.strip():
        add("deskripsi wajib diisi")
    for alias in handler.aliases:
        if not NAME.fullmatch(alias):
            add(f"alias tidak valid: '{alias}'")
        elif alias == handler.name:
            add(f"alias '{alias}' sama dengan nama handler")
    if handler.fn is not None and not callable(handler.fn):
        add("fn harus callable")
    if handler.fn is None:
        if kind == "command":
            add("command butuh fn")
        elif kind == "tool" and not handler.pattern:
            add("tool butuh fn")
    if kind == "tool":
        if handler.family not in families:
            choices = ", ".join(sorted(families))
            add(f"family tidak valid: '{handler.family}'; pilih salah satu: {choices}")
        if handler.fn is not None:
            errors.extend(f"{source}.py: {message}" for message in _schema_errors(handler.parameters))
    errors.extend(f"{source}.py: {message}" for message in _defaults_errors(handler.defaults))
    return errors


class Registry:
    """Nama kanonik + alias -> Handler; pola izin diturunkan dari role dan default."""

    def __init__(self, handlers: list[Handler], kind: str):
        self.kind = kind
        self.by_name: dict[str, Handler] = {}
        self.aliases: dict[str, str] = {}
        errors: list[str] = []
        for handler in handlers:
            key = handler.name.lower()
            if key in self.by_name:
                errors.append(f"{kind} duplikat: {key}")
                continue
            self.by_name[key] = handler
            for alias in handler.aliases:
                alias = alias.lower()
                if alias in self.aliases or alias in self.by_name:
                    errors.append(f"alias {kind} duplikat: {alias}")
                    continue
                self.aliases[alias] = key
        if errors:
            raise RegistryError("handler tidak valid:\n- " + "\n- ".join(errors))

    def get(self, name: str) -> Handler | None:
        key = name.lower()
        return self.by_name.get(key) or self.by_name.get(self.aliases.get(key, ""))

    def names(self) -> list[str]:
        return sorted(self.by_name)

    def patterns(self, role: str, explicit: tuple[str, ...] | None) -> tuple[str, ...]:
        """None = pakai default handler; `()` (dari `[]`) = tolak semua; daftar = menang."""
        if explicit is not None:
            return explicit
        return tuple(dict.fromkeys(h.perm for h in self.by_name.values() if h.allowed_by_default(role)))

    def entries(self, patterns: tuple[str, ...]) -> list[Handler]:
        return [h for h in self.by_name.values() if not h.hidden and allows(patterns, h.name)]


def discover(
    package: str,
    folder: Path,
    attribute: str,
    kind: str,
    families: frozenset[str] = frozenset(),
) -> list[Handler]:
    """Impor semua modul di folder (urut nama), validasi, gabungkan error jadi satu pesan."""
    found: list[Handler] = []
    errors: list[str] = []
    for info in sorted(pkgutil.iter_modules([str(folder)]), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{package}.{info.name}")
        except Exception as exc:  # plugin rusak dilaporkan bersama plugin tak valid lain
            errors.append(f"{info.name}.py: gagal diimpor: {type(exc).__name__}: {exc}")
            continue
        handler = getattr(module, attribute, None)
        if handler is None:
            errors.append(f"{info.name}.py: tidak mendefinisikan {attribute}")
            continue
        if not isinstance(handler, Handler):
            errors.append(f"{info.name}.py: {attribute} bukan Handler")
            continue
        handler_errors = validate(handler, info.name, kind, families)
        errors.extend(handler_errors)
        if not handler_errors:
            found.append(handler)
    if errors:
        raise RegistryError("handler tidak valid:\n- " + "\n- ".join(errors))
    return found
