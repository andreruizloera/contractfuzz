"""Load OpenAPI 3 documents and extract JSON schema targets for an endpoint.

Responsibilities:
- parse YAML or JSON specs,
- resolve internal ``$ref`` pointers by inlining (external refs are rejected
  with a clean error; recursive refs are detected and rejected),
- convert OpenAPI 3.0 ``nullable`` into JSON Schema ``type: [..., "null"]``,
- collect the request-body and response schemas for one endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from contractfuzz.errors import SpecError

JsonValue = Any
Schema = dict[str, Any]

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")

# Keys whose value is a map of schemas, a list of schemas, or a single schema.
_SCHEMA_MAP_KEYS = frozenset({"properties", "patternProperties", "$defs", "definitions"})
_SCHEMA_LIST_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUE_KEYS = frozenset(
    {"items", "additionalProperties", "not", "contains", "if", "then", "else", "propertyNames"}
)
# OpenAPI-only keywords that are not JSON Schema assertions.
_DROP_KEYS = frozenset({"nullable", "example", "xml", "externalDocs", "discriminator"})


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate an OpenAPI 3 document from YAML or JSON."""
    p = Path(path)
    if not p.is_file():
        raise SpecError(f"spec file not found: {p}")
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"could not parse {p} as YAML/JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise SpecError(f"{p} does not contain an OpenAPI document (top level is not a mapping)")
    version = doc.get("openapi")
    if version is None:
        raise SpecError(f"{p} has no 'openapi' key; only OpenAPI 3.x documents are supported")
    if not str(version).startswith("3"):
        raise SpecError(f"unsupported OpenAPI version {version!r}; only 3.x is supported")
    if not isinstance(doc.get("paths"), dict) or not doc["paths"]:
        raise SpecError(f"{p} has no 'paths' section; nothing to generate from")
    return doc


def _lookup_pointer(root: dict[str, Any], ref: str) -> JsonValue:
    node: JsonValue = root
    for raw in ref[len("#/") :].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            raise SpecError(f"$ref target not found in document: {ref}")
    return node


def resolve_refs(node: JsonValue, root: dict[str, Any], _stack: tuple[str, ...] = ()) -> JsonValue:
    """Return a deep copy of ``node`` with every internal ``$ref`` inlined."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                raise SpecError(f"external $ref is not supported yet: {ref!r} (see ROADMAP.md)")
            if ref in _stack:
                raise SpecError(
                    f"recursive $ref detected: {ref!r}; recursive schemas are on the roadmap"
                )
            resolved = resolve_refs(_lookup_pointer(root, ref), root, (*_stack, ref))
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            if siblings and isinstance(resolved, dict):
                resolved = {**resolved, **resolve_refs(siblings, root, _stack)}
            return resolved
        return {k: resolve_refs(v, root, _stack) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_refs(v, root, _stack) for v in node]
    return node


def to_json_schema(schema: Schema) -> Schema:
    """Convert a resolved OpenAPI schema object into a plain JSON Schema.

    OpenAPI 3.0 expresses null with ``nullable: true``; JSON Schema uses a
    ``"null"`` entry in ``type`` (or in ``enum``). OpenAPI 3.1 schemas are
    already JSON Schema and pass through unchanged apart from dropped
    annotation-only keys.
    """
    out: Schema = {}
    for key, value in schema.items():
        if key in _DROP_KEYS:
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            out[key] = {
                name: to_json_schema(sub) if isinstance(sub, dict) else sub
                for name, sub in value.items()
            }
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            out[key] = [to_json_schema(sub) if isinstance(sub, dict) else sub for sub in value]
        elif key in _SCHEMA_VALUE_KEYS and isinstance(value, dict):
            out[key] = to_json_schema(value)
        else:
            out[key] = value
    if schema.get("nullable") is True:
        type_ = out.get("type")
        if isinstance(type_, str):
            out["type"] = [type_, "null"]
        elif isinstance(type_, list):
            if "null" not in type_:
                out["type"] = [*type_, "null"]
        else:
            out = {"anyOf": [out, {"type": "null"}]}
            return out
        if isinstance(out.get("enum"), list) and None not in out["enum"]:
            out["enum"] = [*out["enum"], None]
    return out


@dataclass(frozen=True)
class Target:
    """One JSON schema attached to an operation: a response or a request body."""

    method: str  # upper-case HTTP method
    kind: str  # "response" or "request"
    status: str | None  # response status code, None for request bodies
    media_type: str
    schema: Schema  # resolved, normalized JSON Schema

    @property
    def label(self) -> str:
        if self.kind == "response":
            return f"{self.method.lower()}_{self.status}_response"
        return f"{self.method.lower()}_request"

    @property
    def display(self) -> str:
        if self.kind == "response":
            return f"{self.method} {self.status} response"
        return f"{self.method} request body"


def _is_json_media_type(media_type: str) -> bool:
    base = media_type.split(";")[0].strip().lower()
    return base == "application/json" or base.endswith("+json")


def list_endpoints(spec: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Return (path, [METHOD, ...]) pairs for every path in the spec."""
    result: list[tuple[str, list[str]]] = []
    for path, item in spec["paths"].items():
        if not isinstance(item, dict):
            continue
        methods = [m.upper() for m in HTTP_METHODS if m in item]
        result.append((path, methods))
    return result


def find_targets(spec: dict[str, Any], endpoint: str, method: str | None = None) -> list[Target]:
    """Collect every JSON schema target for ``endpoint`` (optionally one method)."""
    paths = spec["paths"]
    if endpoint not in paths:
        available = ", ".join(sorted(paths))
        raise SpecError(f"endpoint {endpoint!r} not found in spec. Available: {available}")
    item = resolve_refs(paths[endpoint], spec)
    if not isinstance(item, dict):
        raise SpecError(f"path item for {endpoint!r} is not a mapping")

    wanted = method.lower() if method else None
    if wanted is not None and wanted not in item:
        have = ", ".join(m.upper() for m in HTTP_METHODS if m in item) or "none"
        raise SpecError(f"no {wanted.upper()} operation on {endpoint!r} (available: {have})")

    targets: list[Target] = []
    for m in HTTP_METHODS:
        if m not in item or (wanted is not None and m != wanted):
            continue
        op = item[m]
        if not isinstance(op, dict):
            continue
        body = op.get("requestBody") or {}
        for media_type, media in (body.get("content") or {}).items():
            if _is_json_media_type(media_type) and isinstance(media.get("schema"), dict):
                targets.append(
                    Target(m.upper(), "request", None, media_type, to_json_schema(media["schema"]))
                )
        for status, response in (op.get("responses") or {}).items():
            if not isinstance(response, dict):
                continue
            for media_type, media in (response.get("content") or {}).items():
                if _is_json_media_type(media_type) and isinstance(media.get("schema"), dict):
                    targets.append(
                        Target(
                            m.upper(),
                            "response",
                            str(status),
                            media_type,
                            to_json_schema(media["schema"]),
                        )
                    )
    if not targets:
        raise SpecError(
            f"no JSON request or response schemas found for {endpoint!r};"
            " contractfuzz only handles application/json content"
        )
    return targets
