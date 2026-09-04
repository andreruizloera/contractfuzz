from __future__ import annotations

import json
from pathlib import Path

import pytest

from contractfuzz.errors import SpecError
from contractfuzz.spec import (
    find_targets,
    list_endpoints,
    load_spec,
    resolve_refs,
    to_json_schema,
)


def test_load_yaml_spec(example_spec_path: Path) -> None:
    spec = load_spec(example_spec_path)
    assert spec["openapi"].startswith("3")
    assert "/users/{id}" in spec["paths"]


def test_load_json_spec(tmp_path: Path, example_spec) -> None:
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(example_spec))
    spec = load_spec(p)
    assert "/users/{id}" in spec["paths"]


def test_load_missing_file() -> None:
    with pytest.raises(SpecError, match="not found"):
        load_spec("/nonexistent/openapi.yaml")


def test_load_rejects_non_openapi(tmp_path: Path) -> None:
    p = tmp_path / "nope.yaml"
    p.write_text("just: a mapping\n")
    with pytest.raises(SpecError, match="openapi"):
        load_spec(p)


def test_load_rejects_swagger_2(tmp_path: Path) -> None:
    p = tmp_path / "swagger.yaml"
    p.write_text("openapi: 2.0\npaths:\n  /a: {}\n")
    with pytest.raises(SpecError, match="3.x"):
        load_spec(p)


def test_resolve_refs_inlines_components(example_spec) -> None:
    item = resolve_refs(example_spec["paths"]["/users/{id}"], example_spec)
    schema = item["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" not in schema
    assert schema["properties"]["id"]["type"] == "integer"


def test_resolve_refs_rejects_external() -> None:
    root = {"a": {"$ref": "other.yaml#/Foo"}}
    with pytest.raises(SpecError, match="external"):
        resolve_refs(root["a"], root)


def test_resolve_refs_rejects_recursive() -> None:
    root = {"Node": {"type": "object", "properties": {"child": {"$ref": "#/Node"}}}}
    with pytest.raises(SpecError, match="recursive"):
        resolve_refs(root["Node"], root)


def test_resolve_refs_missing_target() -> None:
    root = {"a": {"$ref": "#/does/not/exist"}}
    with pytest.raises(SpecError, match="not found"):
        resolve_refs(root["a"], root)


def test_nullable_becomes_null_type() -> None:
    converted = to_json_schema({"type": "string", "nullable": True})
    assert converted["type"] == ["string", "null"]
    assert "nullable" not in converted


def test_nullable_enum_gains_null_member() -> None:
    converted = to_json_schema({"type": "string", "enum": ["a", "b"], "nullable": True})
    assert None in converted["enum"]


def test_nullable_without_type_wraps_in_anyof() -> None:
    converted = to_json_schema({"nullable": True, "minLength": 1})
    assert {"type": "null"} in converted["anyOf"]


def test_nullable_does_not_leak_from_property_names() -> None:
    # A property literally named "nullable" must not trigger conversion.
    converted = to_json_schema({"type": "object", "properties": {"nullable": {"type": "boolean"}}})
    assert converted["type"] == "object"
    assert converted["properties"]["nullable"] == {"type": "boolean"}


def test_find_targets_all_methods(example_spec) -> None:
    targets = find_targets(example_spec, "/users/{id}")
    labels = {t.label for t in targets}
    assert labels == {
        "get_200_response",
        "get_404_response",
        "patch_request",
        "patch_200_response",
    }


def test_find_targets_method_filter(example_spec) -> None:
    targets = find_targets(example_spec, "/users/{id}", method="patch")
    assert {t.method for t in targets} == {"PATCH"}
    assert any(t.kind == "request" for t in targets)


def test_find_targets_unknown_endpoint(example_spec) -> None:
    with pytest.raises(SpecError, match="Available"):
        find_targets(example_spec, "/nope")


def test_find_targets_unknown_method(example_spec) -> None:
    with pytest.raises(SpecError, match="no DELETE operation"):
        find_targets(example_spec, "/users/{id}", method="delete")


def test_list_endpoints(example_spec) -> None:
    endpoints = dict(list_endpoints(example_spec))
    assert endpoints["/users/{id}"] == ["GET", "PATCH"]
    assert endpoints["/users"] == ["GET"]
