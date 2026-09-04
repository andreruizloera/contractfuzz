"""Enumerate schema-valid mutations of a baseline instance.

Every mutation candidate produced here is *intended* to stay valid under the
contract; the generator validates each one with a real JSON Schema validator
before it is emitted, so nothing invalid can leave the pipeline.

Mutation classes:
- ``omit_optional``   optional property removed (including nested objects)
- ``set_null``        nullable field set to null
- ``empty_array``     array emptied when minItems allows
- ``empty_string``    string emptied when minLength allows
- ``boundary_min`` / ``boundary_max``  numeric boundary values
- ``boundary_items_min`` / ``boundary_items_max``  minItems/maxItems-sized arrays
- ``enum_alternative`` every other member of an enum
- ``extra_property``  an added property, only when additionalProperties permits
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from contractfuzz.baseline import build_array, build_baseline, normalize
from contractfuzz.errors import UnsupportedSchemaError
from contractfuzz.spec import Schema

Path = tuple[str | int, ...]

EXTRA_PROPERTY_NAME = "x_contractfuzz_extra"

# Danger scores: 3 = very likely to break a naive client, 2 = plausible,
# 1 = worth exercising but usually handled.
DANGER = {
    "omit_optional": 3,
    "set_null": 3,
    "empty_array": 3,
    "empty_string": 2,
    "boundary_min": 2,
    "boundary_max": 2,
    "boundary_items_min": 2,
    "boundary_items_max": 2,
    "enum_alternative": 1,
    "extra_property": 1,
}

_OMIT = object()


def render_path(path: Path) -> str:
    out = "$"
    for seg in path:
        out += f"[{seg}]" if isinstance(seg, int) else f".{seg}"
    return out


def _display_path(path: Path) -> str:
    if not path:
        return "root"
    rendered = render_path(path)
    return rendered[2:] if rendered.startswith("$.") else rendered[1:]


@dataclass(frozen=True)
class Mutation:
    """One schema-valid change to the baseline instance."""

    kind: str
    path: Path
    description: str
    value: Any = field(default=_OMIT)

    @property
    def danger(self) -> int:
        return DANGER[self.kind]

    @property
    def is_omission(self) -> bool:
        return self.value is _OMIT

    def apply(self, baseline: Any) -> Any:
        """Return a deep copy of ``baseline`` with this mutation applied."""
        data = copy.deepcopy(baseline)
        if not self.path:
            if self.is_omission:
                raise ValueError("cannot omit the document root")
            return copy.deepcopy(self.value)
        parent = data
        for seg in self.path[:-1]:
            parent = parent[seg]
        last = self.path[-1]
        if self.is_omission:
            del parent[last]
        else:
            parent[last] = copy.deepcopy(self.value)
        return data

    def record(self) -> dict[str, Any]:
        """Machine-readable description of the mutation."""
        return {
            "kind": self.kind,
            "path": render_path(self.path),
            "description": self.description,
            "danger": self.danger,
        }


def _numeric_boundaries(schema: Schema, value: Any, path: Path, out: list[Mutation]) -> None:
    from contractfuzz.baseline import _int_bounds  # shared bound logic

    lo, hi = _int_bounds(schema)
    multiple = schema.get("multipleOf")
    is_int = isinstance(value, int) and not isinstance(value, bool)
    name = _display_path(path)
    for bound, kind, label in ((lo, "boundary_min", "minimum"), (hi, "boundary_max", "maximum")):
        if bound is None:
            continue
        pick: int | float = bound
        if multiple:
            import math

            if kind == "boundary_min":
                pick = math.ceil(bound / multiple) * multiple
                if hi is not None and pick > hi:
                    continue
            else:
                pick = math.floor(bound / multiple) * multiple
                if lo is not None and pick < lo:
                    continue
        if is_int:
            if pick != int(pick):
                continue
            pick = int(pick)
        if pick == value:
            continue
        out.append(Mutation(kind, path, f"{name} = {pick} ({label})", pick))


def _array_mutations(schema: Schema, value: list[Any], path: Path, out: list[Mutation]) -> None:
    items_schema = schema.get("items")
    min_items = int(schema.get("minItems", 0))
    max_items = schema.get("maxItems")
    unique = bool(schema.get("uniqueItems"))
    name = _display_path(path)

    if min_items == 0 and value != []:
        out.append(Mutation("empty_array", path, f"{name} = []", []))
    if min_items > 0 and len(value) != min_items:
        arr = build_array(items_schema, min_items, unique)
        if arr is not None:
            out.append(
                Mutation(
                    "boundary_items_min",
                    path,
                    f"{name} has exactly {min_items} items (minItems)",
                    arr,
                )
            )
    if max_items is not None and int(max_items) <= 25 and len(value) != int(max_items):
        arr = build_array(items_schema, int(max_items), unique)
        if arr is not None:
            out.append(
                Mutation(
                    "boundary_items_max", path, f"{name} has {max_items} items (maxItems)", arr
                )
            )


def _extra_property_mutation(
    schema: Schema, value: dict[str, Any], path: Path, out: list[Mutation]
) -> None:
    additional = schema.get("additionalProperties", True)
    if additional is False:
        return
    max_props = schema.get("maxProperties")
    if max_props is not None and len(value) + 1 > int(max_props):
        return
    if EXTRA_PROPERTY_NAME in value:
        return
    try:
        extra_value: Any = (
            build_baseline(additional)
            if isinstance(additional, dict) and additional
            else "unexpected"
        )
    except UnsupportedSchemaError:
        return
    name = _display_path(path)
    prefix = "" if not path else f"{name}."
    out.append(
        Mutation(
            "extra_property",
            (*path, EXTRA_PROPERTY_NAME),
            f"{prefix}{EXTRA_PROPERTY_NAME} = {extra_value!r} (undeclared property, permitted)",
            extra_value,
        )
    )


def collect_mutations(schema: Schema, value: Any, path: Path = ()) -> list[Mutation]:
    """Walk ``schema`` and ``value`` together, collecting mutation candidates."""
    out: list[Mutation] = []
    if not isinstance(schema, dict):
        return out
    schema = normalize(schema)
    name = _display_path(path)

    type_ = schema.get("type")
    types = type_ if isinstance(type_, list) else ([type_] if type_ else [])

    if "null" in types and value is not None:
        out.append(Mutation("set_null", path, f"{name} = null", None))

    enum = schema.get("enum")
    if isinstance(enum, list):
        for alt in enum:
            if alt == value or (alt is None and any(m.kind == "set_null" for m in out)):
                continue
            label = "null" if alt is None else repr(alt)
            out.append(
                Mutation("enum_alternative", path, f"{name} = {label} (enum alternative)", alt)
            )
        return out
    if "const" in schema:
        return out

    if isinstance(value, dict):
        properties: dict[str, Schema] = schema.get("properties", {})
        required = set(schema.get("required", []))
        min_props = int(schema.get("minProperties", 0))
        for prop, sub in properties.items():
            if prop not in value:
                continue
            if prop not in required and len(value) - 1 >= min_props:
                kind_note = " (optional object)" if isinstance(value[prop], dict) else ""
                prop_path = _display_path((*path, prop))
                out.append(
                    Mutation("omit_optional", (*path, prop), f"{prop_path} omitted{kind_note}")
                )
            out.extend(collect_mutations(sub, value[prop], (*path, prop)))
        _extra_property_mutation(schema, value, path, out)
    elif isinstance(value, list):
        _array_mutations(schema, value, path, out)
        items_schema = schema.get("items")
        if value and isinstance(items_schema, dict):
            out.extend(collect_mutations(items_schema, value[0], (*path, 0)))
    elif isinstance(value, str):
        min_len = int(schema.get("minLength", 0))
        if value != "" and min_len == 0 and schema.get("pattern") is None:
            out.append(Mutation("empty_string", path, f'{name} = ""', ""))
    elif isinstance(value, bool):
        pass
    elif isinstance(value, int | float):
        _numeric_boundaries(schema, value, path, out)
    return out
