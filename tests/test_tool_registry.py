"""Tests for the tool registry: gates, validation, and text sanitation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from sanka.config import SankaConfig
from sanka.policy import Role
from sanka.tools.registry import (
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from sanka.tools.schema import SchemaError

NOW = datetime(2026, 10, 21, 3, 14, tzinfo=timezone.utc)
TOOL_NAMES = ("web_search", "doc_read", "sheet_read", "recap")

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "alias": {"type": "string"},
        "range": {"type": "string"},
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA = {"type": "object"}

Handler = Callable[[Mapping[str, Any], ToolContext], ToolResult]


def make_spec(
    name: str,
    handler: Handler | None = None,
    *,
    input_schema: Mapping[str, Any] | None = None,
    output_schema: Mapping[str, Any] | None = None,
) -> tuple[ToolSpec, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def default_handler(
        arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        calls.append(dict(arguments))
        return ToolResult(
            tool=name,
            ok=True,
            data={"echo": dict(arguments)},
            text="ok",
        )

    spec = ToolSpec(
        name=name,
        description=f"Fake {name} tool.",
        input_schema=input_schema or _INPUT_SCHEMA,
        output_schema=output_schema or _OUTPUT_SCHEMA,
        handler=handler or default_handler,
    )
    return spec, calls


def make_registry(config: SankaConfig) -> tuple[ToolRegistry, dict[str, list]]:
    registry = ToolRegistry(config)
    calls: dict[str, list] = {}
    for name in TOOL_NAMES:
        spec, recorded = make_spec(name)
        registry.register(spec)
        calls[name] = recorded
    return registry, calls


def context(config: SankaConfig, role: Role) -> ToolContext:
    return ToolContext(
        role=role,
        chat_id="chat-1",
        sender_id="628000000000",
        config=config,
        now=lambda: NOW,
    )


def _with_shell(config: SankaConfig, enabled: bool) -> SankaConfig:
    return replace(
        config,
        tools=replace(config.tools, shell_enabled=enabled),
    )


def test_specs_for_filters_by_role(config: SankaConfig) -> None:
    registry, _ = make_registry(config)

    expected = ["doc_read", "recap", "sheet_read", "web_search"]
    committee = registry.specs_for(Role.COMMITTEE)
    owner = registry.specs_for(Role.OWNER)
    assert [item["function"]["name"] for item in committee] == expected
    assert [item["function"]["name"] for item in owner] == expected

    assert registry.names() == TOOL_NAMES
    assert "shell" not in registry.names()


def test_specs_for_returns_function_calling_shape(config: SankaConfig) -> None:
    registry, _ = make_registry(config)

    schemas = {
        item["function"]["name"]: item
        for item in registry.specs_for(Role.COMMITTEE)
    }
    schema = schemas["web_search"]

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "web_search"
    assert schema["function"]["description"] == "Fake web_search tool."
    assert schema["function"]["parameters"]["type"] == "object"
    assert schema["function"]["parameters"]["additionalProperties"] is False


def test_committee_cannot_execute_owner_only_tool(config: SankaConfig) -> None:
    registry, _ = make_registry(config)
    registry.register(
        ToolSpec(
            name="sheet_write",
            description="Owner-only write tool.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            handler=lambda args, ctx: ToolResult(
                tool="sheet_write",
                ok=True,
                data={},
                text="<data />",
            ),
        )
    )

    result = registry.execute(
        "sheet_write", {}, context(config, Role.COMMITTEE)
    )

    assert result.ok is False
    assert result.error == "forbidden"
    assert result.data == {"error": "forbidden"}
    assert "tidak diizinkan" in result.text
    assert result.text.startswith('<data tool="sheet_write">')
    assert result.text.endswith("</data>")


def test_unknown_tool_is_reported_without_crashing(
    config: SankaConfig,
) -> None:
    registry, _ = make_registry(config)

    result = registry.execute(
        "not_a_tool", {}, context(config, Role.OWNER)
    )

    assert result.ok is False
    assert result.error == "unknown_tool"
    assert result.data == {"error": "unknown_tool"}
    assert "tidak dikenal" in result.text


def test_shell_gate_uses_policy_not_registration(
    config: SankaConfig,
) -> None:
    disabled = _with_shell(config, False)
    registry, _ = make_registry(disabled)

    owner_denied = registry.execute(
        "shell", {}, context(disabled, Role.OWNER)
    )
    assert owner_denied.error == "forbidden"

    enabled = _with_shell(config, True)
    enabled_registry, _ = make_registry(enabled)

    owner_unknown = enabled_registry.execute(
        "shell", {}, context(enabled, Role.OWNER)
    )
    assert owner_unknown.error == "unknown_tool"

    participant_denied = enabled_registry.execute(
        "shell", {}, context(enabled, Role.COMMITTEE)
    )
    assert participant_denied.error == "forbidden"

    assert "shell" not in enabled_registry.names()
    assert "shell" not in [
        item["function"]["name"]
        for item in enabled_registry.specs_for(Role.OWNER)
    ]


def test_extra_arguments_are_rejected_before_the_handler(
    config: SankaConfig,
) -> None:
    registry, calls = make_registry(config)

    result = registry.execute(
        "web_search",
        {"query": "cuaca", "spreadsheet_id": "SHOULD-NOT-LEAK"},
        context(config, Role.COMMITTEE),
    )

    assert result.ok is False
    assert result.error == "invalid_arguments"
    assert result.data == {"error": "invalid_arguments"}
    assert "arguments.spreadsheet_id" in result.text
    assert "SHOULD-NOT-LEAK" not in result.text
    assert calls["web_search"] == []


def test_invalid_argument_type_names_the_field_only(
    config: SankaConfig,
) -> None:
    registry, calls = make_registry(config)

    result = registry.execute(
        "web_search",
        {"query": 12345},
        context(config, Role.COMMITTEE),
    )

    assert result.error == "invalid_arguments"
    assert "arguments.query" in result.text
    assert "12345" not in result.text
    assert calls["web_search"] == []


def test_text_is_wrapped_sanitized_and_truncated(
    config: SankaConfig,
) -> None:
    limited = replace(
        config,
        tools=replace(
            config.tools,
            sheets=replace(config.tools.sheets, max_chars=160),
        ),
    )
    long_value = "x" * 500

    def handler(
        arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        return ToolResult(
            tool="sheet_read",
            ok=True,
            data={"rows": [["Ani", long_value]], "row_count": 1},
            text=(
                "<reasoning>jangan tampil</reasoning>\n"
                "baris\x07data " + long_value
            ),
        )

    registry = ToolRegistry(limited)
    spec, _ = make_spec("sheet_read", handler)
    registry.register(spec)

    result = registry.execute(
        "sheet_read",
        {"alias": "kehadiran", "range": "Kehadiran!A1:F10"},
        context(limited, Role.COMMITTEE),
    )

    assert result.ok is True
    assert result.text.startswith(
        '<data tool="sheet_read" alias="kehadiran">'
    )
    assert result.text.endswith("</data>")
    assert "\u2026 dipotong" in result.text
    assert len(result.text) <= 160
    assert "jangan tampil" not in result.text
    assert "\x07" not in result.text
    assert result.data["rows"][0][1] == long_value
    assert result.data["row_count"] == 1


def test_unpaired_tags_are_escaped(config: SankaConfig) -> None:
    def handler(
        arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        return ToolResult(
            tool="sheet_read",
            ok=True,
            data={"rows": [["<tool_call> tanpa penutup"]]},
            text="<tool_call> tanpa penutup",
        )

    registry = ToolRegistry(config)
    spec, _ = make_spec("sheet_read", handler)
    registry.register(spec)

    result = registry.execute(
        "sheet_read",
        {"alias": "kehadiran", "range": "Kehadiran!A1:F10"},
        context(config, Role.COMMITTEE),
    )

    assert "<tool_call>" not in result.text
    assert "&lt;tool_call&gt;" in result.text
    assert result.text.count("<data ") == 1
    assert result.text.count("</data>") == 1


def test_handler_exception_is_mapped_without_leaking(
    config: SankaConfig,
) -> None:
    def handler(
        arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        raise RuntimeError(
            "/home/muqsith/Projects/SankaAgent/config/sanka.yaml "
            "token=sk-live-SECRET"
        )

    registry = ToolRegistry(config)
    spec, _ = make_spec("sheet_read", handler)
    registry.register(spec)

    result = registry.execute(
        "sheet_read",
        {"alias": "kehadiran", "range": "Kehadiran!A1:F10"},
        context(config, Role.COMMITTEE),
    )

    assert result.ok is False
    assert result.error == "upstream_error"
    assert result.data == {"error": "upstream_error"}
    assert "Tool gagal dijalankan." in result.text
    assert "/home" not in result.text
    assert "sanka.yaml" not in result.text
    assert "sk-live-SECRET" not in result.text
    assert result.text.startswith('<data tool="sheet_read"')
    assert result.text.endswith("</data>")


def test_handler_tool_error_keeps_its_safe_code(
    config: SankaConfig,
) -> None:
    def handler(
        arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        raise ToolError("not_found", "Sheet tidak ditemukan.")

    registry = ToolRegistry(config)
    spec, _ = make_spec("sheet_read", handler)
    registry.register(spec)

    result = registry.execute(
        "sheet_read",
        {"alias": "kehadiran", "range": "Kehadiran!A1:F10"},
        context(config, Role.COMMITTEE),
    )

    assert result.ok is False
    assert result.error == "not_found"
    assert result.data == {"error": "not_found"}
    assert "Sheet tidak ditemukan." in result.text


def test_output_schema_violation_becomes_upstream_error(
    config: SankaConfig,
) -> None:
    def handler(
        arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        return ToolResult(
            tool="recap",
            ok=True,
            data={"count": "many"},
            text="ok",
        )

    registry = ToolRegistry(config)
    spec, _ = make_spec(
        "recap",
        handler,
        output_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )
    registry.register(spec)

    result = registry.execute(
        "recap", {}, context(config, Role.COMMITTEE)
    )

    assert result.ok is False
    assert result.error == "upstream_error"
    assert result.data == {"error": "upstream_error"}


def test_handler_returning_wrong_type_is_an_upstream_error(
    config: SankaConfig,
) -> None:
    def handler(
        arguments: Mapping[str, Any], context: ToolContext
    ) -> Any:
        return {"ok": True}

    registry = ToolRegistry(config)
    spec, _ = make_spec("recap", handler)
    registry.register(spec)

    result = registry.execute(
        "recap", {}, context(config, Role.COMMITTEE)
    )

    assert result.ok is False
    assert result.error == "upstream_error"


def test_register_rejects_invalid_names_and_duplicates(
    config: SankaConfig,
) -> None:
    registry = ToolRegistry(config)

    with pytest.raises(ValueError):
        registry.register(make_spec("Bad Name")[0])
    with pytest.raises(ValueError):
        registry.register(make_spec("tool.name")[0])

    spec, _ = make_spec("web_search")
    registry.register(spec)
    with pytest.raises(ValueError):
        registry.register(make_spec("web_search")[0])


def test_register_rejects_schemas_outside_the_subset(
    config: SankaConfig,
) -> None:
    registry = ToolRegistry(config)

    with pytest.raises(SchemaError):
        registry.register(
            make_spec(
                "web_search",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                },
            )[0]
        )

    with pytest.raises(SchemaError):
        registry.register(
            make_spec(
                "web_search",
                output_schema={"type": "object", "format": "uri"},
            )[0]
        )

    assert registry.names() == ()


def test_tool_error_rejects_unknown_codes() -> None:
    with pytest.raises(ValueError):
        ToolError("made_up_code", "x")
