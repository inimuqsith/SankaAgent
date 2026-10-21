"""Tool registry with two independent permission gates.

``specs_for`` hides forbidden tools from the model; ``execute`` rejects them
again, independently of anything the model was told. Permissions come only from
:mod:`sanka.policy`; this module never duplicates the role matrix.

Handler text is untrusted. Before it reaches a model it is stripped of internal
blocks and control characters, wrapped in ``<data>`` and truncated. The
structured ``data`` mapping is never truncated because it is the source of
truth for numbers.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sanka.config import SankaConfig
from sanka.formatter import strip_internal_blocks
from sanka.policy import TOOL_PERMISSIONS, Role, can_use_tool
from sanka.tools.schema import ValidationError, check_schema, validate

if TYPE_CHECKING:
    from sanka.attachments import AttachmentStore

logger = logging.getLogger(__name__)

TOOL_ERROR_CODES: frozenset[str] = frozenset(
    {
        "unknown_tool",
        "forbidden",
        "invalid_arguments",
        "not_configured",
        "not_found",
        "too_large",
        "upstream_error",
        "timeout",
    }
)

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_DEFAULT_MAX_CHARS = 8_000
_ATTR_MAX_CHARS = 64
_TRUNCATION_MARKER = " \u2026 dipotong"
_SAFE_MESSAGES: Mapping[str, str] = {
    "unknown_tool": "Tool tidak dikenal.",
    "forbidden": "Tool ini tidak diizinkan untuk peran ini.",
    "invalid_arguments": "Argumen tidak valid.",
    "not_configured": "Tool ini belum dikonfigurasi.",
    "not_found": "Data yang diminta tidak ditemukan.",
    "too_large": "Data yang diminta terlalu besar.",
    "upstream_error": "Tool gagal dijalankan.",
    "timeout": "Permintaan ke layanan eksternal melebihi batas waktu.",
}


class ToolError(Exception):
    """A safe, coded failure a handler may raise.

    ``message`` is shown to the model and must not contain local paths,
    credentials, spreadsheet ids, message text, or raw upstream bodies.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in TOOL_ERROR_CODES:
            raise ValueError(f"unsupported tool error code: {code!r}")
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ToolContext:
    role: Role
    chat_id: str
    sender_id: str
    config: SankaConfig
    now: Callable[[], datetime]
    attachments: AttachmentStore | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: str
    ok: bool
    data: Mapping[str, Any]
    text: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    handler: Callable[[Mapping[str, Any], ToolContext], ToolResult]


class ToolRegistry:
    """Registered tools, gated by :func:`sanka.policy.can_use_tool`."""

    def __init__(self, config: SankaConfig | None = None) -> None:
        self._config = config
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Add ``spec`` after checking its name and both schemas."""

        if not isinstance(spec, ToolSpec):
            raise TypeError("spec must be a ToolSpec")
        if _TOOL_NAME.fullmatch(spec.name) is None:
            raise ValueError(f"invalid tool name: {spec.name!r}")
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        check_schema(spec.input_schema, path=f"{spec.name}.input_schema")
        check_schema(spec.output_schema, path=f"{spec.name}.output_schema")
        self._specs[spec.name] = spec

    def names(self) -> tuple[str, ...]:
        """Registered tool names in registration order."""

        return tuple(self._specs)

    def specs_for(self, role: Role) -> tuple[dict[str, Any], ...]:
        """Return OpenAI function-calling schemas for tools ``role`` may use."""

        role = Role(role)
        shell_enabled = self._shell_enabled(self._config)
        selected = [
            self._specs[name]
            for name in sorted(self._specs)
            if can_use_tool(role, name, shell_enabled=shell_enabled)
        ]
        return tuple(_function_schema(spec) for spec in selected)

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """Run one tool call and always return a sanitized result.

        Known-but-unregistered tools produce ``forbidden`` when policy denies
        the role and ``unknown_tool`` otherwise. Registered tools pass the
        policy gate, then input validation, then the handler.
        """

        started = time.perf_counter()
        spec = self._specs.get(name)
        if spec is None:
            denied = name in TOOL_PERMISSIONS and not can_use_tool(
                context.role,
                name,
                shell_enabled=self._shell_enabled(context.config),
            )
            code = "forbidden" if denied else "unknown_tool"
            return self._failure(
                name, code, arguments, context, started
            )

        if not can_use_tool(
            context.role,
            name,
            shell_enabled=self._shell_enabled(context.config),
        ):
            return self._failure(
                name, "forbidden", arguments, context, started
            )

        try:
            validate(spec.input_schema, arguments)
        except ValidationError as exc:
            return self._failure(
                name,
                "invalid_arguments",
                arguments,
                context,
                started,
                detail=exc.path,
            )

        try:
            result = spec.handler(arguments, context)
        except ToolError as exc:
            return self._failure(
                name,
                exc.code,
                arguments,
                context,
                started,
                message=exc.message,
            )
        except Exception as exc:  # noqa: BLE001 - never leak handler details
            logger.warning(
                "tool %s failed with %s", name, type(exc).__name__
            )
            return self._failure(
                name, "upstream_error", arguments, context, started
            )

        if not isinstance(result, ToolResult):
            logger.warning(
                "tool %s returned %s instead of ToolResult",
                name,
                type(result).__name__,
            )
            return self._failure(
                name, "upstream_error", arguments, context, started
            )

        if result.ok:
            try:
                validate(spec.output_schema, result.data, path="data")
            except ValidationError as exc:
                logger.warning(
                    "tool %s output schema violation at %s", name, exc.path
                )
                return self._failure(
                    name, "upstream_error", arguments, context, started
                )
            error: str | None = None
            ok = True
        else:
            ok = False
            error = result.error or "upstream_error"
            if error not in TOOL_ERROR_CODES:
                error = "upstream_error"

        text = self._render(name, result.text, arguments, context.config)
        _log_metadata(name, context.role, error, started)
        return ToolResult(
            tool=name,
            ok=ok,
            data=result.data,
            text=text,
            error=error,
        )

    def _failure(
        self,
        name: str,
        code: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        started: float,
        *,
        message: str | None = None,
        detail: str | None = None,
    ) -> ToolResult:
        if message is None:
            message = _SAFE_MESSAGES[code]
        if detail is not None:
            message = f"{message} Field: {detail}"
        text = self._render(name, message, arguments, context.config)
        _log_metadata(name, context.role, code, started)
        return ToolResult(
            tool=name,
            ok=False,
            data={"error": code},
            text=text,
            error=code,
        )

    def _render(
        self,
        name: str,
        raw_text: str,
        arguments: Mapping[str, Any],
        config: SankaConfig | None,
    ) -> str:
        body = _sanitize(raw_text)
        opening = _opening_tag(name, arguments)
        closing = "</data>"
        limit = max(1, _max_chars(name, config))
        full = f"{opening}\n{body}\n{closing}"
        if len(full) <= limit:
            return full

        available = (
            limit
            - len(opening)
            - len(_TRUNCATION_MARKER)
            - len(closing)
            - 2
        )
        if available < 0:
            available = 0
        cut = body[:available].rstrip()
        return f"{opening}\n{cut}{_TRUNCATION_MARKER}\n{closing}"

    @staticmethod
    def _shell_enabled(config: SankaConfig | None) -> bool:
        tools = getattr(config, "tools", None)
        return bool(getattr(tools, "shell_enabled", False))


def _function_schema(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": _copy_mapping(spec.input_schema),
        },
    }


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            copied[key] = _copy_mapping(item)
        elif isinstance(item, (list, tuple)):
            copied[key] = [
                _copy_mapping(entry)
                if isinstance(entry, Mapping)
                else entry
                for entry in item
            ]
        else:
            copied[key] = item
    return copied


def _sanitize(text: str) -> str:
    """Strip internal blocks, control characters, and tag delimiters."""

    if not isinstance(text, str):
        text = str(text)
    without_blocks = strip_internal_blocks(text)
    cleaned = "".join(
        char
        for char in without_blocks
        if char in "\n\t" or not _is_control(char)
    )
    # Content must never be able to close or open the <data> wrapper.
    return cleaned.replace("<", "&lt;").replace(">", "&gt;")


def _is_control(char: str) -> bool:
    code = ord(char)
    return code < 0x20 or 0x7F <= code <= 0x9F


def _opening_tag(name: str, arguments: Mapping[str, Any]) -> str:
    attributes = [f'tool="{_attribute(name)}"']
    if isinstance(arguments, Mapping):
        for key in ("alias", "name"):
            value = arguments.get(key)
            if isinstance(value, str):
                attributes.append(f'{key}="{_attribute(value)}"')
                break
    return f"<data {' '.join(attributes)}>"


def _attribute(value: str) -> str:
    cleaned = "".join(char for char in value if not _is_control(char))
    return html.escape(cleaned[:_ATTR_MAX_CHARS], quote=True)


def _max_chars(name: str, config: SankaConfig | None) -> int:
    tools = getattr(config, "tools", None)
    try:
        if name == "web_search":
            return int(tools.web_search.max_chars)  # type: ignore[union-attr]
        if name == "doc_read":
            return int(tools.docs.max_chars)  # type: ignore[union-attr]
        if name in ("attachment_read", "attachment_search", "attachment_outline"):
            return int(tools.docs.max_chars)  # type: ignore[union-attr]
        if name == "sheet_read":
            return int(tools.sheets.max_chars)  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return _DEFAULT_MAX_CHARS
    return _DEFAULT_MAX_CHARS


def _log_metadata(
    tool: str, role: Role, code: str | None, started: float
) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.debug(
        "tool=%s role=%s status=%s duration_ms=%s",
        tool,
        getattr(role, "value", str(role)),
        code or "ok",
        duration_ms,
    )
