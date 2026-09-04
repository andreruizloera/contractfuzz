# contractfuzz

Find undocumented assumptions in API clients.

[![CI](https://github.com/andreruizloera/contractfuzz/actions/workflows/ci.yml/badge.svg)](https://github.com/andreruizloera/contractfuzz/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Your OpenAPI contract says `roles` is an array. It never says it is
non-empty. Your client does `user["roles"][0]` anyway, every happy-path
test passes, and it ships. contractfuzz reads the contract and generates
the payloads the server is *allowed* to send but never does in testing:
optional fields missing, nullables null, arrays empty, strings empty,
integers at their boundaries. Feed those to your client and find out what
it silently assumed.

**Core principle: every generated payload remains valid according to the
OpenAPI contract.** contractfuzz never produces malformed data. Each
variant is checked against the resolved schema with a real JSON Schema
validator (jsonschema) before it is emitted, and the test suite enforces
this for every mutation class. When contractfuzz breaks your client, the
bug is in the client, not in the data.

## Quickstart

```
git clone https://github.com/andreruizloera/contractfuzz
cd contractfuzz
uv sync
./demo.sh        # or: uv run contractfuzz generate examples/openapi.yaml --endpoint '/users/{id}' --out fixtures
```

## Example

Generating fixtures from the bundled example contract:

```
$ contractfuzz generate examples/openapi.yaml --endpoint '/users/{id}' --out fixtures
Generated 54 valid edge-case fixtures for /users/{id} -> fixtures/
Every fixture validates against the OpenAPI contract.

GET 200 response: 22 valid edge-case variants
  Potentially dangerous variants:
   1. age omitted
   2. roles = []
   3. profileImage omitted
   4. profileImage = null
   5. address omitted (optional object)
   6. address.street omitted
   7. address.postalCode omitted
   8. displayName = ""
   9. email = ""
  10. age = 150 (maximum)
  11. roles has 5 items (maxItems)
  12. profileImage = ""
      ... and 2 more (see manifest.json)

GET 404 response: 3 valid edge-case variants
  Potentially dangerous variants:
   1. code = ""
   2. message = ""

PATCH request body: 7 valid edge-case variants
  ...

Machine-readable mutation records: fixtures/manifest.json
```

Replaying those fixtures through `examples/fragile_client.py`, a client
that works perfectly on the happy path:

```
$ python examples/fragile_client.py fixtures
Replayed 22 contract-valid responses through the client.
CRASHES: 4 contract-valid payloads crashed the client:
  - age omitted
      KeyError: 'age'
      at line 33: years_old = user["age"]  # assumes optional age is present
  - roles = []
      IndexError: list index out of range
      at line 32: primary_role = user["roles"][0]  # assumes roles is never empty
  - profileImage omitted
      KeyError: 'profileImage'
      at line 34: avatar = user["profileImage"].lower()  # assumes nullable field is a string
  - profileImage = null
      AttributeError: 'NoneType' object has no attribute 'lower'
      at line 34: avatar = user["profileImage"].lower()  # assumes nullable field is a string
```

Four real bugs, found using only data the contract explicitly allows.
`demo.sh` runs this end to end.

## Why?

Contract testing tools check that servers conform to their spec. Almost
nothing checks the other direction: whether *clients* handle everything
the spec permits. In practice clients are tested against one or two
recorded responses and quietly inherit every convenient property those
responses happened to have. The gap between "what the contract promises"
and "what the client assumes" is where production incidents live: the
first user with zero roles, the first profile without an avatar, the day
a backend starts omitting a field it always used to send.

Fuzzers that throw garbage at parsers do not find these bugs, because the
garbage gets rejected at the parsing layer. contractfuzz stays inside the
contract on purpose, so every failure it finds is a legitimate bug that a
conforming server could trigger tomorrow.

## Installation

Requires Python 3.12+.

```
uv add contractfuzz      # inside a project
# or, from a clone:
uv sync
```

Plain pip works too: `pip install .` from a clone. Dependencies are just
`jsonschema` and `pyyaml`.

## Usage

### Generate fixtures

```
contractfuzz generate openapi.yaml --endpoint /users/{id} [--method get] [--out fixtures] [--responses-only]
```

Parses the spec, resolves internal `$ref`s, and writes one JSON file per
variant plus a `manifest.json` mapping each file to a machine-readable
mutation record:

```json
{
  "file": "get_200_response__007__roles.json",
  "mutation": {
    "kind": "empty_array",
    "path": "$.roles",
    "description": "roles = []",
    "danger": 3
  }
}
```

Mutation classes: `omit_optional`, `set_null`, `empty_array`,
`empty_string`, `boundary_min`, `boundary_max`, `boundary_items_min`,
`boundary_items_max`, `enum_alternative`, and `extra_property` (added only
when `additionalProperties` permits it). Danger scores rank how likely a
mutation is to break a naive client; the summary lists scores 2 and up.

### List endpoints

```
contractfuzz list openapi.yaml
```

### Mutation proxy

```
contractfuzz proxy openapi.yaml --upstream http://localhost:8080 [--port 8642]
```

Point your client at the proxy instead of the real server. JSON responses
that match a path in the spec and validate against it get one schema-valid
mutation applied to the real upstream body, rotating through the most
dangerous mutations first. Response headers document each change:

```
$ curl -si http://127.0.0.1:8642/users/7 | grep -i contractfuzz
X-Contractfuzz-Mutation: age omitted
X-Contractfuzz-Mutation-Path: $.age

$ curl -si http://127.0.0.1:8642/users/7 | grep -i contractfuzz
X-Contractfuzz-Mutation: roles = []
X-Contractfuzz-Mutation-Path: $.roles
```

Proxy subset in v0.1 (documented, tested): JSON bodies only, responses
are buffered (no streaming), hop-by-hop headers are dropped, and upstream
bodies that do not validate against the contract pass through untouched
with an `X-Contractfuzz` header explaining why.

## Architecture

```
spec.py        load YAML/JSON, inline internal $refs, convert OpenAPI 3.0
               nullable to JSON Schema "null" types, pick out the request
               and response schemas for an endpoint
baseline.py    build one fully-populated instance that satisfies a schema
mutations.py   walk schema and baseline together, emitting candidate
               mutations with JSONPath-style locations
validation.py  the enforcement point: jsonschema Draft 2020-12 validation
               of every candidate; invalid candidates are discarded
generator.py   orchestration, deduplication, fixture and manifest output
ranking.py     danger scoring and the CLI summary
proxy.py       the mutating HTTP proxy built on the same mutation engine
cli.py         argparse CLI: generate, list, proxy
```

The pipeline is deliberately one-directional: baseline, then mutations,
then validation, then output. Nothing reaches disk or the network without
passing the validator.

## Limitations

- `format` is treated as an annotation, matching JSON Schema and OpenAPI
  defaults. An empty string is a valid `format: email` value unless the
  schema also sets `minLength` or `pattern`. That is deliberate: it is
  exactly the kind of undocumented assumption this tool exists to surface.
- An absent `additionalProperties` permits extra properties, per JSON
  Schema semantics, so contractfuzz will exercise that.
- `oneOf`/`anyOf` generation uses the first branch; `allOf` uses a shallow
  merge that covers the common base-model-plus-extension pattern.
- External and recursive `$ref`s are rejected with a clean error.
- Strings constrained only by an unusual `pattern` may be unsupported
  (contractfuzz tries a handful of candidates, then says so honestly).
- The proxy handles the documented JSON subset above; it is not a general
  HTTP intermediary.

## Roadmap

See [ROADMAP.md](ROADMAP.md). Headlines: external and recursive `$ref`
support, full `oneOf`/`anyOf` branch coverage, pattern-aware string
synthesis, combined multi-field mutations, and a pytest plugin that turns
each fixture into a test case.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `uv run pytest` and
`uv run ruff check .` before sending a PR.

GitHub topics: `openapi`, `fuzzing`, `api-testing`, `property-testing`,
`developer-tools`.

## License

MIT, see [LICENSE](LICENSE).
