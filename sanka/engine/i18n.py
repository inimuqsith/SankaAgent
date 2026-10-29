"""Language catalogs: chat-visible strings live in `sanka/lang/<code>.yaml`."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

CATALOG_DIR = Path(__file__).resolve().parents[1] / "lang"
FALLBACK = "en"
ALIASES = {
    "en": "en",
    "english": "en",
    "id": "id",
    "in": "id",
    "indonesian": "id",
    "auto": "id",
}


def available() -> tuple[str, ...]:
    """Language codes with a catalog file, sorted."""
    if not CATALOG_DIR.is_dir():
        return ()
    return tuple(sorted(path.stem for path in CATALOG_DIR.glob("*.yaml") if path.is_file()))


@cache
def catalog(code: str) -> dict[str, str]:
    """Load and validate one catalog; raise ValueError when unusable."""
    path = CATALOG_DIR / f"{code}.yaml"
    if not path.is_file():
        raise ValueError(f"language '{code}' has no catalog; available: {', '.join(available()) or 'none'}")
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{path.name} is not valid YAML: {exc}") from exc
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, dict):
        raise ValueError(f"{path.name} must contain a 'messages' mapping")
    return {str(key): str(value) for key, value in messages.items()}


def reload_catalogs() -> None:
    """Drop the parsed catalogs so edited YAML files are re-read."""
    catalog.cache_clear()


def normalize_language(value: object, default: str = "id") -> str:
    """Map aliases to an installed catalog code; raise ValueError otherwise."""
    text = str(value or "").strip().lower() or default
    code = ALIASES.get(text, text)
    if code not in available():
        raise ValueError(
            f"language '{text}' has no catalog; available: {', '.join(available()) or 'none'}"
        )
    catalog(code)
    return code


def t(key: str, lang: str, default: str | None = None, **kwargs: object) -> str:
    """Format one catalog message, falling back to `en`, the default, or the key."""
    want = str(lang or "").strip().lower()
    for code in (ALIASES.get(want, want), FALLBACK):
        try:
            messages = catalog(code)
        except ValueError:
            continue
        template = messages.get(key)
        if template is not None:
            try:
                return template.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return template
    return key if default is None else default
