"""The core guarantee: every emitted variant validates against the contract."""

from __future__ import annotations

from typing import Any

from hypothesis import assume, given
from hypothesis import strategies as st

from contractfuzz.generator import generate_for_target, generate_variants
from contractfuzz.spec import find_targets
from contractfuzz.validation import validation_errors


def test_every_variant_for_every_example_target_is_valid(example_spec: dict[str, Any]) -> None:
    for endpoint in ("/users", "/users/{id}"):
        for target in find_targets(example_spec, endpoint):
            result = generate_for_target(target)
            assert result.variants, f"no variants for {target.display}"
            for variant in result.variants:
                errors = validation_errors(target.schema, variant.data)
                assert errors == [], (
                    f"{target.display} variant {variant.mutation.description!r}"
                    f" violates the contract: {errors}"
                )


def test_no_candidates_rejected_for_example_spec(example_spec: dict[str, Any]) -> None:
    # For fully supported schemas, the mutation walker should never even
    # propose an invalid candidate.
    for target in find_targets(example_spec, "/users/{id}"):
        assert generate_for_target(target).rejected == 0


def test_variants_are_deduplicated_and_differ_from_baseline(example_spec: dict[str, Any]) -> None:
    import json

    for target in find_targets(example_spec, "/users/{id}"):
        baseline, variants, _ = generate_variants(target.schema)
        keys = [json.dumps(v.data, sort_keys=True) for v in variants]
        assert len(keys) == len(set(keys))
        assert json.dumps(baseline, sort_keys=True) not in keys


def test_tricky_composed_schema_variants_all_valid() -> None:
    schema = {
        "type": "object",
        "required": ["id", "payload"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "integer", "minimum": 1, "maximum": 9, "multipleOf": 3},
            "mode": {"enum": ["fast", "slow", None]},
            "payload": {
                "allOf": [
                    {
                        "type": "object",
                        "required": ["kind"],
                        "properties": {"kind": {"const": "v1"}},
                    },
                    {"type": "object", "properties": {"note": {"type": "string"}}},
                ]
            },
            "tags": {
                "type": "array",
                "maxItems": 3,
                "uniqueItems": True,
                "items": {"type": "string", "maxLength": 8},
            },
        },
    }
    _, variants, _ = generate_variants(schema)
    assert variants
    kinds = {v.mutation.kind for v in variants}
    assert {"omit_optional", "empty_array", "enum_alternative"} <= kinds
    for variant in variants:
        assert validation_errors(schema, variant.data) == []


_property_schemas = st.fixed_dictionaries(
    {},
    optional={
        "count": st.fixed_dictionaries(
            {"type": st.just("integer")},
            optional={
                "minimum": st.integers(-100, 0),
                "maximum": st.integers(1, 100),
            },
        ),
        "name": st.fixed_dictionaries(
            {"type": st.just("string")},
            optional={"minLength": st.integers(0, 3), "maxLength": st.integers(4, 20)},
        ),
        "flag": st.fixed_dictionaries({"type": st.just("boolean")}),
        "link": st.fixed_dictionaries(
            {"type": st.sampled_from([["string", "null"], "string"])},
        ),
        "items": st.fixed_dictionaries(
            {
                "type": st.just("array"),
                "items": st.just({"type": "integer", "minimum": 0}),
            },
            optional={"minItems": st.integers(0, 3), "maxItems": st.integers(4, 6)},
        ),
    },
)


@given(
    properties=_property_schemas,
    required_mask=st.lists(st.booleans(), min_size=5, max_size=5),
    closed=st.booleans(),
)
def test_random_object_schemas_yield_only_valid_variants(
    properties: dict[str, Any], required_mask: list[bool], closed: bool
) -> None:
    assume(properties)
    names = list(properties)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": [n for n, keep in zip(names, required_mask, strict=False) if keep],
        "additionalProperties": not closed,
    }
    _, variants, _ = generate_variants(schema)
    for variant in variants:
        assert validation_errors(schema, variant.data) == []
