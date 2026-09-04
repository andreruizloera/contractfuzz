"""Build a single "baseline" instance that satisfies a JSON Schema.

The baseline is the friendly, fully-populated document: every optional
property present, arrays non-empty where allowed, strings non-empty.
Mutations are then applied to the baseline, so the interesting variants
(omissions, nulls, empties, boundaries) are always a delta from a maximal
happy-path value.
"""

from __future__ import annotations

import re
from typing import Any

from contractfuzz.errors import UnsupportedSchemaError
from contractfuzz.spec import Schema

_FORMAT_SAMPLES: dict[str, str] = {
    "email": "user@example.com",
    "idn-email": "user@example.com",
    "uri": "https://example.com/resource",
    "uri-reference": "/resource/1",
    "url": "https://example.com/resource",
    "uuid": "3f7f9a2e-8b1c-4a5d-9e6f-0a1b2c3d4e5f",
    "date": "2024-06-15",
    "date-time": "2024-06-15T10:30:00Z",
    "time": "10:30:00Z",
    "hostname": "example.com",
    "ipv4": "192.0.2.10",
    "ipv6": "2001:db8::1",
    "password": "correct-horse",
    "byte": "aGVsbG8=",
}

_MIN_TIGHTEN = ("minimum", "minLength", "minItems", "minProperties")
_MAX_TIGHTEN = ("maximum", "maxLength", "maxItems", "maxProperties")


def merge_all_of(schema: Schema) -> Schema:
    """Shallow-merge an ``allOf`` schema into one object.

    Properties and required lists are unioned; min/max constraints take the
    stricter side; other conflicting keywords keep the first value seen.
    This covers the common "base model + extension" pattern; exotic allOf
    combinations are a roadmap item.
    """
    merged: Schema = {k: v for k, v in schema.items() if k != "allOf"}
    for sub in schema.get("allOf", []):
        if not isinstance(sub, dict):
            continue
        if "allOf" in sub:
            sub = merge_all_of(sub)
        for key, value in sub.items():
            if key == "properties" and isinstance(value, dict):
                merged["properties"] = {**merged.get("properties", {}), **value}
            elif key == "required" and isinstance(value, list):
                merged["required"] = sorted(set(merged.get("required", [])) | set(value))
            elif key in _MIN_TIGHTEN and key in merged:
                merged[key] = max(merged[key], value)
            elif key in _MAX_TIGHTEN and key in merged:
                merged[key] = min(merged[key], value)
            elif key not in merged:
                merged[key] = value
    return merged


def normalize(schema: Schema) -> Schema:
    """Collapse allOf and pick the first branch of oneOf/anyOf for generation."""
    if "allOf" in schema:
        schema = merge_all_of(schema)
    for combinator in ("oneOf", "anyOf"):
        branches = schema.get(combinator)
        if isinstance(branches, list) and branches:
            rest = {k: v for k, v in schema.items() if k != combinator}
            return normalize(merge_all_of({"allOf": [rest, branches[0]]}))
    return schema


def _int_bounds(schema: Schema) -> tuple[float | None, float | None]:
    lo = schema.get("minimum")
    hi = schema.get("maximum")
    ex_lo = schema.get("exclusiveMinimum")
    ex_hi = schema.get("exclusiveMaximum")
    is_int = schema.get("type") == "integer" or (
        isinstance(schema.get("type"), list) and "integer" in schema["type"]
    )
    step = 1 if is_int else 0.5
    if isinstance(ex_lo, bool):  # OpenAPI 3.0 style
        if ex_lo and lo is not None:
            lo = lo + step
    elif isinstance(ex_lo, int | float):  # JSON Schema 2020-12 style
        candidate = ex_lo + step
        lo = candidate if lo is None else max(lo, candidate)
    if isinstance(ex_hi, bool):
        if ex_hi and hi is not None:
            hi = hi - step
    elif isinstance(ex_hi, int | float):
        candidate = ex_hi - step
        hi = candidate if hi is None else min(hi, candidate)
    return lo, hi


def _numeric_baseline(schema: Schema, is_int: bool) -> int | float:
    lo, hi = _int_bounds(schema)
    multiple = schema.get("multipleOf")
    if lo is not None:
        pick: int | float = lo
    elif hi is not None:
        pick = min(hi, 1)
    else:
        pick = 1
    if multiple:
        import math

        pick = math.ceil(pick / multiple) * multiple
        if hi is not None and pick > hi:
            raise UnsupportedSchemaError(f"no multiple of {multiple} fits in [{lo}, {hi}]")
    if is_int:
        pick = int(pick)
    if (lo is not None and pick < lo) or (hi is not None and pick > hi):
        raise UnsupportedSchemaError(f"could not pick a number within bounds [{lo}, {hi}]")
    return pick


def _string_baseline(schema: Schema) -> str:
    min_len = int(schema.get("minLength", 0))
    max_len = schema.get("maxLength")
    pattern = schema.get("pattern")
    fmt = schema.get("format")

    candidates = []
    if fmt in _FORMAT_SAMPLES:
        candidates.append(_FORMAT_SAMPLES[fmt])
    candidates.extend(["value", "a", "A1", "0", ""])
    for cand in candidates:
        s = cand
        if len(s) < min_len:
            s = s + "a" * (min_len - len(s))
        if max_len is not None and len(s) > int(max_len):
            continue
        if pattern is not None:
            try:
                if re.search(pattern, s) is None:
                    continue
            except re.error as exc:
                raise UnsupportedSchemaError(f"invalid pattern {pattern!r}: {exc}") from exc
        return s
    raise UnsupportedSchemaError(
        f"cannot generate a string for pattern={pattern!r},"
        f" minLength={min_len}, maxLength={max_len} (pattern synthesis is on the roadmap)"
    )


def distinct_values(item_schema: Schema, count: int) -> list[Any] | None:
    """Return ``count`` distinct valid values for ``item_schema``, or None."""
    item_schema = normalize(item_schema) if isinstance(item_schema, dict) else {}
    enum = item_schema.get("enum")
    if isinstance(enum, list):
        seen: list[Any] = []
        for v in enum:
            if v not in seen:
                seen.append(v)
        return seen[:count] if len(seen) >= count else None
    type_ = item_schema.get("type")
    if isinstance(type_, list):
        type_ = next((t for t in type_ if t != "null"), None)
    if type_ == "boolean":
        return [True, False][:count] if count <= 2 else None
    if type_ == "integer":
        lo, hi = _int_bounds(item_schema)
        if item_schema.get("multipleOf"):
            return None
        start = int(lo) if lo is not None else 0
        values = [start + i for i in range(count)]
        if hi is not None and values[-1] > hi:
            return None
        return values
    if type_ == "string":
        if item_schema.get("pattern") is not None:
            return None
        base = _string_baseline(item_schema) or "v"
        max_len = item_schema.get("maxLength")
        values = [f"{base}{i}" for i in range(count)]
        if max_len is not None and any(len(v) > int(max_len) for v in values):
            return None
        return values
    return None


def build_array(item_schema: Schema | None, count: int, unique: bool) -> list[Any] | None:
    """Build an array of ``count`` valid items, distinct when ``unique``."""
    if count == 0:
        return []
    if item_schema is None:
        item_schema = {"type": "string"}
    if unique and count > 1:
        return distinct_values(item_schema, count)
    return [build_baseline(item_schema) for _ in range(count)]


def build_baseline(schema: Schema) -> Any:
    """Produce one instance that satisfies ``schema``, as fully populated as allowed."""
    if not isinstance(schema, dict):
        raise UnsupportedSchemaError(f"schema node is not a mapping: {schema!r}")
    schema = normalize(schema)

    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        for value in enum:
            if value is not None:
                return value
        return None

    type_ = schema.get("type")
    if isinstance(type_, list):
        type_ = next((t for t in type_ if t != "null"), "null")
    if type_ is None:
        if "properties" in schema or "required" in schema or "minProperties" in schema:
            type_ = "object"
        elif "items" in schema or "minItems" in schema or "maxItems" in schema:
            type_ = "array"
        elif any(k in schema for k in ("minimum", "maximum", "multipleOf")):
            type_ = "number"
        else:
            return "value"  # untyped schema accepts anything

    if type_ == "null":
        return None
    if type_ == "boolean":
        return True
    if type_ == "integer":
        return _numeric_baseline(schema, is_int=True)
    if type_ == "number":
        return _numeric_baseline(schema, is_int=False)
    if type_ == "string":
        return _string_baseline(schema)
    if type_ == "array":
        min_items = int(schema.get("minItems", 0))
        max_items = schema.get("maxItems")
        count = max(min_items, 0 if max_items == 0 else 1)
        result = build_array(schema.get("items"), count, bool(schema.get("uniqueItems")))
        if result is None:
            raise UnsupportedSchemaError(
                f"cannot build {count} distinct items for uniqueItems array"
            )
        return result
    if type_ == "object":
        properties: dict[str, Schema] = schema.get("properties", {})
        required = set(schema.get("required", []))
        max_props = schema.get("maxProperties")
        obj: dict[str, Any] = {}
        ordered = sorted(properties, key=lambda n: (n not in required, list(properties).index(n)))
        for name in ordered:
            if max_props is not None and len(obj) >= int(max_props) and name not in required:
                continue
            obj[name] = build_baseline(properties[name])
        min_props = int(schema.get("minProperties", 0))
        if len(obj) < min_props:
            additional = schema.get("additionalProperties", True)
            if additional is False:
                raise UnsupportedSchemaError(
                    "minProperties exceeds declared properties and additionalProperties is false"
                )
            extra_schema = additional if isinstance(additional, dict) else {"type": "string"}
            i = 0
            while len(obj) < min_props:
                obj[f"additional{i}"] = build_baseline(extra_schema)
                i += 1
        return obj
    raise UnsupportedSchemaError(f"unsupported schema type: {type_!r}")
