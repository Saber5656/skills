#!/usr/bin/env python3
"""Validate a generated result against the tracked canonical schema subset."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


class CanonicalValidationError(RuntimeError):
    """Raised when a result violates the canonical contract."""


SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema", "$defs", "$ref", "type", "additionalProperties", "required",
        "properties", "const", "enum", "minLength", "pattern", "items",
        "minItems", "maxItems", "uniqueItems", "allOf", "anyOf", "oneOf",
        "if", "then", "else",
    }
)


def validate_schema_keywords(value: Any) -> None:
    """Reject canonical keywords this dependency-free validator cannot enforce."""
    if isinstance(value, list):
        for item in value:
            validate_schema_keywords(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key not in SUPPORTED_KEYWORDS:
            raise CanonicalValidationError(f"unsupported canonical keyword: {key}")
        if key in {"properties", "$defs"} and isinstance(item, dict):
            for child_schema in item.values():
                validate_schema_keywords(child_schema)
        elif key not in {"required", "enum"}:
            validate_schema_keywords(item)


def matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise CanonicalValidationError(f"unsupported schema type: {expected}")


def resolves(schema_root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/$defs/"):
        raise CanonicalValidationError(f"unsupported schema reference: {reference}")
    name = reference.removeprefix("#/$defs/")
    try:
        target = schema_root["$defs"][name]
    except (KeyError, TypeError) as exc:
        raise CanonicalValidationError(f"unresolved schema reference: {reference}") from exc
    if not isinstance(target, dict):
        raise CanonicalValidationError(f"schema reference is not an object: {reference}")
    return target


def validate(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if schema is root:
        validate_schema_keywords(root)
    if "$ref" in schema:
        validate(value, resolves(root, schema["$ref"]), root, path)

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(matches_type(value, choice) for choice in choices):
            raise CanonicalValidationError(f"{path}: type mismatch")
    if "const" in schema and value != schema["const"]:
        raise CanonicalValidationError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise CanonicalValidationError(f"{path}: enum mismatch")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise CanonicalValidationError(f"{path}: string is too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise CanonicalValidationError(f"{path}: pattern mismatch")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise CanonicalValidationError(f"{path}: array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise CanonicalValidationError(f"{path}: array has too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise CanonicalValidationError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate(item, item_schema, root, f"{path}[{index}]")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise CanonicalValidationError(f"{path}: missing required properties")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise CanonicalValidationError(f"{path}: additional properties are forbidden")
        for name, child_schema in properties.items():
            if name in value:
                validate(value[name], child_schema, root, f"{path}.{name}")

    for branch in schema.get("allOf", []):
        validate(value, branch, root, path)
    if "anyOf" in schema:
        successes = sum(is_valid(value, branch, root, path) for branch in schema["anyOf"])
        if successes == 0:
            raise CanonicalValidationError(f"{path}: no anyOf branch matched")
    if "oneOf" in schema:
        successes = sum(is_valid(value, branch, root, path) for branch in schema["oneOf"])
        if successes != 1:
            raise CanonicalValidationError(f"{path}: expected exactly one oneOf match")
    if "if" in schema:
        branch = schema.get("then") if is_valid(value, schema["if"], root, path) else schema.get("else")
        if branch is not None:
            validate(value, branch, root, path)


def is_valid(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> bool:
    try:
        validate(value, schema, root, path)
    except CanonicalValidationError:
        return False
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: validate-canonical-result.py SCHEMA RESULT", file=sys.stderr)
        return 64
    try:
        schema = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        result = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise CanonicalValidationError("schema root must be an object")
        validate(result, schema, schema)
    except (OSError, json.JSONDecodeError, TypeError, CanonicalValidationError) as exc:
        print(f"canonical result validation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
