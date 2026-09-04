from __future__ import annotations

from typing import Any

from contractfuzz.baseline import build_baseline
from contractfuzz.mutations import (
    EXTRA_PROPERTY_NAME,
    Mutation,
    collect_mutations,
    render_path,
)


def _kinds(schema: dict[str, Any]) -> dict[str, list[Mutation]]:
    baseline = build_baseline(schema)
    grouped: dict[str, list[Mutation]] = {}
    for m in collect_mutations(schema, baseline):
        grouped.setdefault(m.kind, []).append(m)
    return grouped


def test_omit_optional_only_for_optional_properties() -> None:
    schema = {
        "type": "object",
        "required": ["keep"],
        "properties": {"keep": {"type": "string"}, "drop": {"type": "string"}},
    }
    omissions = _kinds(schema).get("omit_optional", [])
    assert [m.path for m in omissions] == [("drop",)]


def test_omit_respects_min_properties() -> None:
    schema = {
        "type": "object",
        "minProperties": 2,
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
    }
    assert "omit_optional" not in _kinds(schema)


def test_set_null_for_nullable_type() -> None:
    schema = {
        "type": "object",
        "required": ["x"],
        "properties": {"x": {"type": ["string", "null"]}},
    }
    nulls = _kinds(schema).get("set_null", [])
    assert [m.path for m in nulls] == [("x",)]
    assert nulls[0].description == "x = null"


def test_no_set_null_without_null_type() -> None:
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
    assert "set_null" not in _kinds(schema)


def test_empty_array_only_when_min_items_allows() -> None:
    permissive = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "array", "items": {"type": "integer"}}},
    }
    strict = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "array", "minItems": 1, "items": {"type": "integer"}}},
    }
    assert "empty_array" in _kinds(permissive)
    assert "empty_array" not in _kinds(strict)


def test_empty_string_only_when_min_length_allows() -> None:
    permissive = {"type": "object", "required": ["s"], "properties": {"s": {"type": "string"}}}
    strict = {
        "type": "object",
        "required": ["s"],
        "properties": {"s": {"type": "string", "minLength": 1}},
    }
    patterned = {
        "type": "object",
        "required": ["s"],
        "properties": {"s": {"type": "string", "pattern": "^[a-z]+$"}},
    }
    assert "empty_string" in _kinds(permissive)
    assert "empty_string" not in _kinds(strict)
    assert "empty_string" not in _kinds(patterned)


def test_numeric_boundaries() -> None:
    schema = {
        "type": "object",
        "required": ["n"],
        "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 150}},
    }
    grouped = _kinds(schema)
    # Baseline picks the minimum, so only the maximum boundary is a new variant.
    assert "boundary_min" not in grouped
    maxes = grouped["boundary_max"]
    assert maxes[0].value == 150
    assert "maximum" in maxes[0].description


def test_numeric_boundary_respects_exclusive() -> None:
    schema = {
        "type": "object",
        "required": ["n"],
        "properties": {
            "n": {"type": "integer", "minimum": 0, "maximum": 10, "exclusiveMaximum": True}
        },
    }
    maxes = _kinds(schema)["boundary_max"]
    assert maxes[0].value == 9


def test_boundary_items_min_and_max() -> None:
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "integer"}}
        },
    }
    grouped = _kinds(schema)
    # Baseline has minItems entries already, so only the maxItems variant appears.
    assert "boundary_items_min" not in grouped
    assert len(grouped["boundary_items_max"][0].value) == 4


def test_enum_alternatives_cover_every_other_member() -> None:
    schema = {
        "type": "object",
        "required": ["e"],
        "properties": {"e": {"type": "string", "enum": ["a", "b", "c"]}},
    }
    alts = _kinds(schema)["enum_alternative"]
    assert sorted(m.value for m in alts) == ["b", "c"]


def test_extra_property_requires_permission() -> None:
    closed = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    open_ = {
        "type": "object",
        "additionalProperties": True,
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    assert "extra_property" not in _kinds(closed)
    extras = _kinds(open_)["extra_property"]
    assert extras[0].path == (EXTRA_PROPERTY_NAME,)


def test_extra_property_follows_additional_properties_schema() -> None:
    schema = {
        "type": "object",
        "additionalProperties": {"type": "integer", "minimum": 100},
        "required": ["a"],
        "properties": {"a": {"type": "integer", "minimum": 100}},
    }
    extras = _kinds(schema)["extra_property"]
    assert extras[0].value == 100


def test_extra_property_respects_max_properties() -> None:
    schema = {
        "type": "object",
        "maxProperties": 1,
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    assert "extra_property" not in _kinds(schema)


def test_nested_paths_and_descriptions() -> None:
    schema = {
        "type": "object",
        "required": ["outer"],
        "properties": {
            "outer": {
                "type": "object",
                "properties": {"inner": {"type": ["string", "null"]}},
            }
        },
    }
    mutations = collect_mutations(schema, build_baseline(schema))
    nulls = [m for m in mutations if m.kind == "set_null"]
    assert nulls[0].path == ("outer", "inner")
    assert nulls[0].description == "outer.inner = null"


def test_array_item_mutations_recurse_into_first_element() -> None:
    schema = {
        "type": "object",
        "required": ["tags"],
        "properties": {
            "tags": {"type": "array", "minItems": 1, "items": {"type": ["string", "null"]}}
        },
    }
    mutations = collect_mutations(schema, build_baseline(schema))
    nulls = [m for m in mutations if m.kind == "set_null"]
    assert nulls[0].path == ("tags", 0)


def test_apply_omission_and_set() -> None:
    baseline = {"a": 1, "b": {"c": 2}}
    omitted = Mutation("omit_optional", ("a",), "a omitted").apply(baseline)
    assert omitted == {"b": {"c": 2}}
    changed = Mutation("set_null", ("b", "c"), "b.c = null", None).apply(baseline)
    assert changed == {"a": 1, "b": {"c": None}}
    assert baseline == {"a": 1, "b": {"c": 2}}  # original untouched


def test_record_is_machine_readable() -> None:
    record = Mutation("set_null", ("b", 0, "c"), "b[0].c = null", None).record()
    assert record == {
        "kind": "set_null",
        "path": "$.b[0].c",
        "description": "b[0].c = null",
        "danger": 3,
    }


def test_render_path_root() -> None:
    assert render_path(()) == "$"
