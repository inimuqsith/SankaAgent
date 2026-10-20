"""Minimal JSON Schema subset used by the tool registry.

The subset is deliberately small and closed: any keyword outside
:data:`SUPPORTED_KEYWORDS` is rejected when a tool is registered. A schema that
uses an unsupported keyword would otherwise look stricter than it really is.
The validator never echoes the rejected value, because values can contain
credentials or document contents.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "maxLength",
        "pattern",
        "description",
        "title",
        "default",
    }
)

TYPE_NAMES: frozenset[str] = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)

_NUMBER_KEYWORDS = frozenset({"minimum", "maximum"})
_NON_NEGATIVE_INT_KEYWORDS = frozenset(
    {"minItems", "maxItems", "maxLength"}
)


class SchemaError(ValueError):
    """Raised when a tool schema is outside the supported subset."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


class ValidationError(ValueError):
    """Raised when a value does not satisfy a schema.

    ``message`` names the path and the violated rule. It never contains the
    rejected value.
    """

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def check_schema(schema: Mapping[str, Any], *, path: str = "schema") -> None:
    """Raise :class:`SchemaError` unless ``schema`` is in the subset.

    Keyword arguments are type-checked too, so a schema such as
    ``{"type": 3}`` fails at registration instead of silently matching
    everything.
    """

    _walk(schema, path)


def validate(
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: str = "arguments",
) -> None:
    """Raise :class:`ValidationError` when ``value`` does not satisfy ``schema``."""

    _validate(schema, value, path)


def _walk(schema: Mapping[str, Any], path: str) -> None:
    if not isinstance(schema, Mapping):
        raise SchemaError(path, "skema harus berupa object")
    for key in schema:
        if key not in SUPPORTED_KEYWORDS:
            raise SchemaError(f"{path}.{key}", "keyword tidak didukung")

    if "type" in schema:
        _check_type(schema["type"], f"{path}.type")

    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, Mapping):
            raise SchemaError(f"{path}.properties", "harus berupa object")
        for name, child in properties.items():
            _walk(child, f"{path}.properties.{name}")

    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, (list, tuple)):
            raise SchemaError(f"{path}.required", "harus berupa array string")
        seen: set[str] = set()
        for name in required:
            if not isinstance(name, str):
                raise SchemaError(
                    f"{path}.required", "setiap entri harus berupa string"
                )
            if name in seen:
                raise SchemaError(
                    f"{path}.required", "tidak boleh memuat duplikat"
                )
            seen.add(name)

    if "additionalProperties" in schema and not isinstance(
        schema["additionalProperties"], bool
    ):
        raise SchemaError(
            f"{path}.additionalProperties", "harus berupa boolean"
        )

    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, (list, tuple)) or not options:
            raise SchemaError(f"{path}.enum", "harus berupa array tidak kosong")

    if "items" in schema:
        _walk(schema["items"], f"{path}.items")

    for key in _NON_NEGATIVE_INT_KEYWORDS:
        if key in schema and (
            not _is_int(schema[key]) or schema[key] < 0
        ):
            raise SchemaError(f"{path}.{key}", "harus berupa integer >= 0")

    for key in _NUMBER_KEYWORDS:
        if key in schema:
            number = schema[key]
            if not _is_number(number) or not math.isfinite(number):
                raise SchemaError(f"{path}.{key}", "harus berupa angka")

    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            raise SchemaError(f"{path}.pattern", "harus berupa string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SchemaError(
                f"{path}.pattern", f"regex tidak valid ({exc})"
            ) from exc

    for key in ("description", "title"):
        if key in schema and not isinstance(schema[key], str):
            raise SchemaError(f"{path}.{key}", "harus berupa string")

    _check_ranges(schema, path)


def _check_ranges(schema: Mapping[str, Any], path: str) -> None:
    if (
        "minItems" in schema
        and "maxItems" in schema
        and schema["minItems"] > schema["maxItems"]
    ):
        raise SchemaError(f"{path}.minItems", "tidak boleh melebihi maxItems")
    if (
        "minimum" in schema
        and "maximum" in schema
        and schema["minimum"] > schema["maximum"]
    ):
        raise SchemaError(f"{path}.minimum", "tidak boleh melebihi maximum")


def _check_type(expected: Any, path: str) -> None:
    names = _type_names(expected, path=path, error=SchemaError)
    if not names:
        raise SchemaError(path, "harus memuat minimal satu tipe")
    if len(set(names)) != len(names):
        raise SchemaError(path, "tidak boleh memuat duplikat")


def _type_names(
    expected: Any,
    *,
    path: str,
    error: type[ValueError],
) -> tuple[str, ...]:
    if isinstance(expected, str):
        names = (expected,)
    elif isinstance(expected, (list, tuple)):
        names = tuple(expected)
    else:
        raise error(path, "harus berupa string atau array string")
    for name in names:
        if not isinstance(name, str) or name not in TYPE_NAMES:
            raise error(path, "memuat tipe yang tidak didukung")
    return names


def _validate(schema: Mapping[str, Any], value: Any, path: str) -> None:
    if not isinstance(schema, Mapping):
        raise ValidationError(path, "skema tidak valid")

    if "type" in schema:
        names = _type_names(
            schema["type"], path=path, error=ValidationError
        )
        if not names:
            raise ValidationError(path, "skema memuat minimal satu tipe")
        if not any(_matches_type(value, name) for name in names):
            raise ValidationError(
                path, f"harus berupa {' atau '.join(names)}"
            )

    if "enum" in schema and not any(
        _json_equal(value, option) for option in schema["enum"]
    ):
        raise ValidationError(path, "nilai tidak termasuk pilihan")

    if isinstance(value, Mapping):
        _validate_object(schema, value, path)
    elif isinstance(value, (list, tuple)):
        _validate_array(schema, value, path)
    elif isinstance(value, str):
        _validate_string(schema, value, path)
    elif _is_number(value):
        _validate_number(schema, value, path)


def _validate_object(
    schema: Mapping[str, Any], value: Mapping[Any, Any], path: str
) -> None:
    required = schema.get("required", ())
    if isinstance(required, (list, tuple)):
        for name in required:
            if name not in value:
                raise ValidationError(f"{path}.{name}", "wajib diisi")

    properties = schema.get("properties")
    declared: Mapping[Any, Any] = (
        properties if isinstance(properties, Mapping) else {}
    )
    for name, child in declared.items():
        if name in value:
            _validate(child, value[name], f"{path}.{name}")

    if schema.get("additionalProperties") is False:
        for name in value:
            if name not in declared:
                raise ValidationError(
                    f"{path}.{name}", "properti tidak dikenal"
                )


def _validate_array(
    schema: Mapping[str, Any], value: list[Any] | tuple[Any, ...], path: str
) -> None:
    minimum = schema.get("minItems")
    if _is_int(minimum) and len(value) < minimum:
        raise ValidationError(path, f"minimal {minimum} item")
    maximum = schema.get("maxItems")
    if _is_int(maximum) and len(value) > maximum:
        raise ValidationError(path, f"maksimal {maximum} item")
    items = schema.get("items")
    if isinstance(items, Mapping):
        for index, item in enumerate(value):
            _validate(items, item, f"{path}[{index}]")


def _validate_string(
    schema: Mapping[str, Any], value: str, path: str
) -> None:
    maximum = schema.get("maxLength")
    if _is_int(maximum) and len(value) > maximum:
        raise ValidationError(path, f"panjang maksimal {maximum} karakter")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            matched = re.search(pattern, value) is not None
        except re.error:
            raise ValidationError(path, "pattern skema tidak valid") from None
        if not matched:
            raise ValidationError(path, "tidak cocok dengan pola")


def _validate_number(
    schema: Mapping[str, Any], value: int | float, path: str
) -> None:
    minimum = schema.get("minimum")
    if _is_number(minimum) and value < minimum:
        raise ValidationError(path, f"minimal {minimum}")
    maximum = schema.get("maximum")
    if _is_number(maximum) and value > maximum:
        raise ValidationError(path, f"maksimal {maximum}")


def _matches_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, Mapping)
    if name == "array":
        return isinstance(value, (list, tuple))
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return _is_int(value)
    if name == "number":
        return _is_number(value) and math.isfinite(value)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return False


def _json_equal(value: Any, option: Any) -> bool:
    if isinstance(value, bool) or isinstance(option, bool):
        return type(value) is type(option) and value == option
    if _is_number(value) and _is_number(option):
        return value == option
    return type(value) is type(option) and value == option


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
