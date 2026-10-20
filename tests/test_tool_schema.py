"""Tests for the closed JSON Schema subset used by tool specs."""

from __future__ import annotations

from typing import Any

import pytest

from sanka.tools.schema import (
    SchemaError,
    ValidationError,
    check_schema,
    validate,
)


def test_unsupported_keyword_is_rejected_when_registered() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
        },
    }

    with pytest.raises(SchemaError) as error:
        check_schema(schema)

    assert error.value.path.endswith("properties.query.minLength")
    assert "tidak didukung" in str(error.value)


def test_unsupported_top_level_keyword_is_rejected() -> None:
    with pytest.raises(SchemaError) as error:
        check_schema({"type": "object", "format": "uri"})

    assert error.value.path == "schema.format"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": 3},
        {"type": ["string", "unknown"]},
        {"required": "query"},
        {"properties": ["query"]},
        {"additionalProperties": "yes"},
        {"enum": []},
        {"items": {"type": "string", "minLength": 1}},
        {"maxLength": -1},
        {"maxLength": True},
        {"pattern": "("},
        {"minimum": "1"},
        {"description": 3},
    ],
)
def test_keyword_arguments_are_type_checked(schema: dict[str, Any]) -> None:
    with pytest.raises(SchemaError):
        check_schema(schema)


def test_supported_subset_is_accepted() -> None:
    check_schema(
        {
            "type": "object",
            "title": "Search",
            "description": "A query.",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "pattern": r"^\S.*$",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["a", "b"]},
                    "minItems": 1,
                    "maxItems": 3,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }
    )


@pytest.mark.parametrize(
    ("schema", "value"),
    [
        ({"type": "string"}, "text"),
        ({"type": "integer"}, 3),
        ({"type": "number"}, 2.5),
        ({"type": "number"}, 4),
        ({"type": "boolean"}, True),
        ({"type": "null"}, None),
        ({"type": "array"}, [1, 2]),
        ({"type": "object"}, {"a": 1}),
        ({"type": ["string", "null"]}, None),
    ],
)
def test_supported_types_accept_matching_values(
    schema: dict[str, Any], value: Any
) -> None:
    validate(schema, value)


@pytest.mark.parametrize(
    ("schema", "value"),
    [
        ({"type": "string"}, 3),
        ({"type": "integer"}, "3"),
        ({"type": "integer"}, True),
        ({"type": "number"}, True),
        ({"type": "number"}, float("nan")),
        ({"type": "boolean"}, 1),
        ({"type": "null"}, ""),
        ({"type": "array"}, "12"),
        ({"type": "object"}, [1]),
        ({"type": ["string", "null"]}, 1),
    ],
)
def test_types_reject_mismatched_values(
    schema: dict[str, Any], value: Any
) -> None:
    with pytest.raises(ValidationError):
        validate(schema, value)


def test_required_and_additional_properties() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    validate(schema, {"query": "hello", "limit": 3})

    with pytest.raises(ValidationError) as missing:
        validate(schema, {"limit": 3})
    assert missing.value.path == "arguments.query"

    with pytest.raises(ValidationError) as extra:
        validate(schema, {"query": "hello", "spreadsheet_id": "abc"})
    assert extra.value.path == "arguments.spreadsheet_id"
    assert "abc" not in str(extra.value)

    permissive = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "additionalProperties": True,
    }
    validate(permissive, {"query": "hello", "extra": 1})


def test_enum_is_strict_about_boolean_and_numbers() -> None:
    validate({"type": "string", "enum": ["a", "b"]}, "a")

    with pytest.raises(ValidationError):
        validate({"type": "string", "enum": ["a", "b"]}, "c")

    with pytest.raises(ValidationError):
        validate({"enum": [True]}, 1)

    validate({"enum": [1]}, 1.0)


def test_numeric_and_length_bounds() -> None:
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "query": {"type": "string", "maxLength": 5, "pattern": "^[a-z]+$"},
        },
        "additionalProperties": False,
    }

    validate(schema, {"limit": 1, "query": "abcde"})
    validate(schema, {"limit": 10, "query": "a"})

    with pytest.raises(ValidationError) as low:
        validate(schema, {"limit": 0, "query": "a"})
    assert low.value.path == "arguments.limit"

    with pytest.raises(ValidationError) as high:
        validate(schema, {"limit": 11, "query": "a"})
    assert high.value.path == "arguments.limit"

    with pytest.raises(ValidationError) as long:
        validate(schema, {"limit": 1, "query": "abcdef"})
    assert long.value.path == "arguments.query"

    with pytest.raises(ValidationError) as pattern:
        validate(schema, {"limit": 1, "query": "ABC"})
    assert pattern.value.path == "arguments.query"


def test_array_items_validate_with_indexed_paths() -> None:
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }

    validate(schema, {"results": [{"title": "ok"}]})

    with pytest.raises(ValidationError) as missing:
        validate(schema, {"results": [{"title": "ok"}, {}]})
    assert missing.value.path == "arguments.results[1].title"

    with pytest.raises(ValidationError):
        validate(schema, {"results": []})

    with pytest.raises(ValidationError):
        validate(schema, {"results": [{"title": "a"}, {"title": "b"}, {}]})


def test_error_never_echoes_the_rejected_value() -> None:
    value = "sup3r-secret-token-value"

    with pytest.raises(ValidationError) as error:
        validate({"type": "integer"}, value)

    assert error.value.path == "arguments"
    assert value not in str(error.value)
    assert value not in error.value.message


def test_object_keywords_apply_even_without_explicit_type() -> None:
    schema = {
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
        "additionalProperties": False,
    }

    validate(schema, {"count": 2})

    with pytest.raises(ValidationError) as bad:
        validate(schema, {"count": "2"})
    assert bad.value.path == "arguments.count"
