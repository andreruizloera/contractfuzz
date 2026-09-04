"""Validate instances against resolved OpenAPI schemas with a real validator.

This is the enforcement point for the core guarantee: every variant that
contractfuzz emits validates against the contract. ``format`` is treated as
an annotation, matching JSON Schema's default behavior (and OpenAPI's), so
an empty string is a valid ``format: email`` value unless the schema also
constrains ``minLength`` or ``pattern``. That is exactly the sort of
undocumented assumption this tool exists to surface.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from contractfuzz.errors import SpecError
from contractfuzz.spec import Schema


def make_validator(schema: Schema) -> Draft202012Validator:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SpecError(f"resolved schema is not a valid JSON Schema: {exc.message}") from exc
    return Draft202012Validator(schema)


def validation_errors(schema: Schema, instance: Any) -> list[str]:
    """Return human-readable validation errors, empty when the instance is valid."""
    validator = make_validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in validator.iter_errors(instance)
    ]


def is_valid(schema: Schema, instance: Any) -> bool:
    return not validation_errors(schema, instance)
