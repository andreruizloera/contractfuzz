from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from contractfuzz.baseline import build_baseline, distinct_values, merge_all_of
from contractfuzz.errors import UnsupportedSchemaError
from contractfuzz.spec import Target
from contractfuzz.validation import validation_errors


def test_baseline_satisfies_user_schema(user_target: Target) -> None:
    baseline = build_baseline(user_target.schema)
    assert validation_errors(user_target.schema, baseline) == []


def test_baseline_populates_optional_properties(user_target: Target) -> None:
    baseline = build_baseline(user_target.schema)
    for optional in ("age", "profileImage", "address"):
        assert optional in baseline


def test_string_min_length() -> None:
    schema = {"type": "string", "minLength": 10}
    value = build_baseline(schema)
    assert len(value) >= 10
    assert validation_errors(schema, value) == []


def test_string_pattern_uses_matching_candidate() -> None:
    schema = {"type": "string", "pattern": "^[a-z]+$"}
    value = build_baseline(schema)
    assert validation_errors(schema, value) == []


def test_string_impossible_pattern_raises() -> None:
    with pytest.raises(UnsupportedSchemaError, match="pattern"):
        build_baseline({"type": "string", "pattern": "^ZZZ[0-9]{9}QQ$"})


def test_integer_exclusive_minimum_boolean_style() -> None:
    schema = {"type": "integer", "minimum": 5, "exclusiveMinimum": True}
    assert build_baseline(schema) == 6


def test_integer_exclusive_minimum_numeric_style() -> None:
    schema = {"type": "integer", "exclusiveMinimum": 5}
    assert build_baseline(schema) == 6


def test_integer_multiple_of() -> None:
    schema = {"type": "integer", "minimum": 7, "multipleOf": 5}
    value = build_baseline(schema)
    assert value == 10
    assert validation_errors(schema, value) == []


def test_integer_impossible_multiple_raises() -> None:
    with pytest.raises(UnsupportedSchemaError):
        build_baseline({"type": "integer", "minimum": 7, "maximum": 9, "multipleOf": 5})


def test_unique_items_array_is_distinct() -> None:
    schema = {
        "type": "array",
        "minItems": 3,
        "uniqueItems": True,
        "items": {"type": "integer", "minimum": 0},
    }
    value = build_baseline(schema)
    assert len(value) == 3
    assert len(set(value)) == 3
    assert validation_errors(schema, value) == []


def test_distinct_values_from_enum() -> None:
    assert distinct_values({"enum": ["a", "b", "c"]}, 2) == ["a", "b"]
    assert distinct_values({"enum": ["a"]}, 2) is None


def test_object_min_properties_padding() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "minProperties": 3}
    value = build_baseline(schema)
    assert len(value) >= 3
    assert validation_errors(schema, value) == []


def test_object_max_properties_trims_optionals() -> None:
    schema = {
        "type": "object",
        "required": ["a"],
        "maxProperties": 2,
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "string"},
            "c": {"type": "string"},
        },
    }
    value = build_baseline(schema)
    assert "a" in value
    assert len(value) == 2
    assert validation_errors(schema, value) == []


def test_merge_all_of_takes_stricter_bounds() -> None:
    merged = merge_all_of(
        {
            "allOf": [
                {"type": "integer", "minimum": 1, "maximum": 100},
                {"minimum": 10, "maximum": 50},
            ]
        }
    )
    assert merged["minimum"] == 10
    assert merged["maximum"] == 50


def test_all_of_object_merge_baseline() -> None:
    schema = {
        "allOf": [
            {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}},
            {"type": "object", "required": ["b"], "properties": {"b": {"type": "integer"}}},
        ]
    }
    value = build_baseline(schema)
    assert validation_errors(schema, value) == []
    assert set(value) == {"a", "b"}


def test_one_of_uses_first_branch_but_stays_valid() -> None:
    schema = {
        "oneOf": [
            {"type": "object", "required": ["kind"], "properties": {"kind": {"const": "x"}}},
            {"type": "integer"},
        ]
    }
    value = build_baseline(schema)
    assert validation_errors(schema, value) == []


@given(
    lo=st.integers(min_value=-(10**6), max_value=10**6),
    span=st.integers(min_value=0, max_value=10**6),
)
def test_integer_bounds_property(lo: int, span: int) -> None:
    schema = {"type": "integer", "minimum": lo, "maximum": lo + span}
    assert validation_errors(schema, build_baseline(schema)) == []


@given(
    min_len=st.integers(min_value=0, max_value=40),
    extra=st.integers(min_value=0, max_value=40),
)
def test_string_length_bounds_property(min_len: int, extra: int) -> None:
    schema = {"type": "string", "minLength": min_len, "maxLength": min_len + extra}
    assert validation_errors(schema, build_baseline(schema)) == []


@given(
    min_items=st.integers(min_value=0, max_value=8),
    unique=st.booleans(),
)
def test_array_bounds_property(min_items: int, unique: bool) -> None:
    schema = {
        "type": "array",
        "minItems": min_items,
        "uniqueItems": unique,
        "items": {"type": "integer"},
    }
    assert validation_errors(schema, build_baseline(schema)) == []
