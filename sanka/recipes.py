"""Resep user: command dinamis `/nama` yang didaftarkan agent via tool recipe.

Satu resep satu file `<root>/<name>.yaml`. Dua kind: `recipe` (langkah tool
berurutan, deterministik) dan `prompt` (delegasi satu turn LLM). Terpisah tegas
dari registry harness `sanka/commands/`: nama yang bentrok ditolak.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .skills import MAX_CONTENT, MAX_DESCRIPTION, NAME

if TYPE_CHECKING:
    from .tools import ToolContext

log = logging.getLogger(__name__)

KINDS = ("recipe", "prompt")
MAX_STEPS = 10
DEFAULT_ROLES = ("owner", "member")


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _builtin(name: str) -> bool:
    from .commands import REGISTRY as COMMANDS  # lazy: hindari circular saat discovery

    return COMMANDS.get(name) is not None


@dataclass(frozen=True)
class Recipe:
    name: str
    kind: str
    description: str
    usage: str
    roles: tuple[str, ...]
    steps: tuple[dict, ...]
    prompt: str
    path: Path


def _parse_roles(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    roles = tuple(str(item).strip() for item in value)
    if not all(roles) or any(not isinstance(item, str) for item in value):
        return None
    return roles


def _parse_steps(value: Any) -> list[dict] | None:
    if not isinstance(value, list) or not value or len(value) > MAX_STEPS:
        return None
    steps: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        tool = str(item.get("tool") or "").strip()
        args = item.get("args", {})
        if not tool or not isinstance(args, dict):
            return None
        steps.append({"tool": tool, "args": args})
    return steps


def _parse(path: Path, data: Any) -> Recipe | None:
    if not isinstance(data, dict):
        log.warning("resep %s bukan mapping; dilewati", path)
        return None
    name = str(data.get("name") or "").strip().lower()
    if not NAME.fullmatch(name):
        log.warning("resep %s nama tidak valid; dilewati", path)
        return None
    if name != path.stem:
        log.warning("resep %s nama '%s' tidak cocok dengan file; dilewati", path, name)
        return None
    if _builtin(name):
        log.warning("resep %s bentrok dengan command bawaan; dilewati", path)
        return None
    kind = str(data.get("kind") or "").strip()
    if kind not in KINDS:
        log.warning("resep %s kind tidak valid; dilewati", path)
        return None
    description = _clip(data.get("description") or "", MAX_DESCRIPTION)
    if not description:
        log.warning("resep %s tanpa description; dilewati", path)
        return None
    usage = " ".join(str(data.get("usage") or "").split())
    roles = _parse_roles(data.get("roles", list(DEFAULT_ROLES)))
    if roles is None:
        log.warning("resep %s roles tidak valid; dilewati", path)
        return None
    steps: tuple[dict, ...] = ()
    prompt = ""
    if kind == "recipe":
        parsed = _parse_steps(data.get("steps"))
        if parsed is None:
            log.warning("resep %s steps tidak valid; dilewati", path)
            return None
        steps = tuple(parsed)
    else:
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            log.warning("resep %s tanpa prompt; dilewati", path)
            return None
    return Recipe(
        name=name,
        kind=kind,
        description=description,
        usage=usage,
        roles=roles,
        steps=steps,
        prompt=prompt,
        path=path,
    )


class RecipeStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self._recipes: dict[str, Recipe] = {}
        self._sig: tuple = ()
        self._lock = threading.Lock()
        self._refresh()

    def _signature(self) -> tuple:
        if not self.root.is_dir():
            return ()
        found = []
        for path in sorted(self.root.glob("*.yaml")):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(found)

    def _load(self) -> None:
        recipes: dict[str, Recipe] = {}
        for path in sorted(self.root.glob("*.yaml")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.warning("gagal membaca resep %s: %s", path, exc)
                continue
            if len(text) > MAX_CONTENT:
                log.warning("resep %s terlalu besar (%d karakter); dilewati", path, len(text))
                continue
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                log.warning("resep %s YAML rusak; dilewati: %s", path, exc)
                continue
            recipe = _parse(path, data)
            if recipe is None:
                continue
            if recipe.name in recipes:
                log.warning("resep duplikat %s dari %s; dilewati", recipe.name, path)
                continue
            recipes[recipe.name] = recipe
        self._recipes = recipes

    def _refresh(self) -> None:
        signature = self._signature()
        if signature != self._sig:
            self._load()
            self._sig = signature

    def names(self) -> list[str]:
        self._refresh()
        return sorted(self._recipes)

    def get(self, name: str) -> Recipe | None:
        self._refresh()
        return self._recipes.get(str(name or "").strip().lower())

    def index(self, roles: tuple[str, ...] | None = None) -> str:
        """Daftar `- /nama usage — description`; difilter role eksekutor bila diberikan."""
        self._refresh()
        lines = []
        for name in sorted(self._recipes):
            recipe = self._recipes[name]
            if roles is not None and not (set(recipe.roles) & set(roles)):
                continue
            usage = f" {recipe.usage}" if recipe.usage else ""
            lines.append(f"- /{name}{usage} — {recipe.description}")
        return "\n".join(lines)

    def save(
        self,
        name: str,
        kind: str,
        description: str,
        *,
        usage: str = "",
        roles: tuple[str, ...] | list[str] = DEFAULT_ROLES,
        steps: list[dict] | tuple[dict, ...] = (),
        prompt: str = "",
        overwrite: bool = False,
    ) -> Path:
        slug = str(name or "").strip().lower()
        if not NAME.fullmatch(slug):
            raise ValueError("nama resep harus lowercase [a-z0-9_-], maksimal 64 karakter")
        if _builtin(slug):
            raise ValueError(f"nama dipakai command bawaan: {slug}")
        kind = str(kind or "").strip()
        if kind not in KINDS:
            raise ValueError("kind harus recipe atau prompt")
        desc = _clip(description, MAX_DESCRIPTION)
        if not desc:
            raise ValueError("description wajib diisi")
        parsed_roles = _parse_roles(list(roles) if isinstance(roles, tuple) else roles)
        if parsed_roles is None:
            raise ValueError("roles harus daftar string tak kosong")
        payload: dict[str, Any] = {
            "name": slug,
            "kind": kind,
            "description": desc,
            "roles": list(parsed_roles),
        }
        clean_usage = " ".join(str(usage or "").split())
        if clean_usage:
            payload["usage"] = clean_usage
        if kind == "recipe":
            if isinstance(steps, tuple):
                steps = list(steps)
            if len(steps) > MAX_STEPS:
                raise ValueError(f"maksimal {MAX_STEPS} langkah")
            parsed = _parse_steps(steps)
            if parsed is None:
                raise ValueError("steps wajib daftar tak kosong berisi {tool, args}")
            payload["steps"] = parsed
        else:
            text = str(prompt or "").strip()
            if not text:
                raise ValueError("prompt wajib diisi untuk kind=prompt")
            payload["prompt"] = text
        target = self.root / f"{slug}.yaml"
        if target.exists() and not overwrite:
            raise FileExistsError(f"resep sudah ada: {slug} (pakai overwrite=true)")
        target.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        self._load()
        self._sig = self._signature()
        return target

    def remove(self, name: str) -> None:
        slug = str(name or "").strip().lower()
        target = self.root / f"{slug}.yaml"
        if not target.is_file():
            raise KeyError(f"resep tidak ditemukan: {slug}")
        target.unlink()
        self._load()
        self._sig = self._signature()


def _render(value: Any, args: str) -> Any:
    if isinstance(value, str):
        return value.replace("{{args}}", args)
    if isinstance(value, dict):
        return {key: _render(item, args) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, args) for item in value]
    return value


async def run_recipe(
    recipe: Recipe,
    args: str,
    ctx: ToolContext,
    specs: dict | None = None,
) -> str:
    """Jalankan kind=recipe: tiap step lewat `execute` (izin = role eksekutor)."""
    from .tools import REGISTRY as TOOLS
    from .tools import execute

    table = specs if specs is not None else TOOLS.by_name
    cleaned = str(args or "").strip()
    last = ""
    for i, step in enumerate(recipe.steps, 1):
        result = await execute(table, step["tool"], _render(step["args"], cleaned), ctx)
        if result.startswith("error:"):
            return f"error: langkah {i} ({step['tool']}) gagal:\n{result}"
        last = result
    return last
