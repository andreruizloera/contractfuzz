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
it silently assumed. Run it once from the command line, or wire it into
your suite with the pytest plugin and get one test case per assumption,
served to your real `requests` or `httpx` client with no server running.

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
./demo.sh        # generate fixtures, replay them, run them as a pytest suite,
                 # then serve them to real requests and httpx clients
```

Or, one piece at a time:

```
uv run contractfuzz generate examples/openapi.yaml --endpoint '/users/{id}' --out fixtures
uv run pytest examples/test_fragile_client.py    # expected to fail: that is the point
uv run pytest examples/test_mocked_client.py     # the same, through requests and httpx
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

The same four, as a pytest suite. `examples/test_fragile_client.py` is a
docstring, two imports, and this:

```python
@contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
def test_render_profile_handles_every_contract_valid_response(payload: dict[str, Any]) -> None:
    render_profile(payload)
```

```
$ pytest examples/test_fragile_client.py --tb=no -q
...F...F.....FF........                                                  [100%]
================================= contractfuzz =================================
23 contract-valid payloads exercised, 4 broke the client.

Undocumented assumptions, most dangerous first:
  [danger 3] age omitted
      KeyError: 'age'
      at fragile_client.py:33  years_old = user["age"]  # assumes optional age is present
  [danger 3] roles = []
      IndexError: list index out of range
      at fragile_client.py:32  primary_role = user["roles"][0]  # assumes roles is never empty
  [danger 3] profileImage omitted
      KeyError: 'profileImage'
      at fragile_client.py:34  avatar = user["profileImage"].lower()  # assumes nullable field is a string
  [danger 3] profileImage = null
      AttributeError: 'NoneType' object has no attribute 'lower'
      at fragile_client.py:34  avatar = user["profileImage"].lower()  # assumes nullable field is a string
=========================== short test summary info ============================
FAILED examples/test_fragile_client.py::test_render_profile_handles_every_contract_valid_response[age_omitted]
FAILED examples/test_fragile_client.py::test_render_profile_handles_every_contract_valid_response[roles_empty]
FAILED examples/test_fragile_client.py::test_render_profile_handles_every_contract_valid_response[profileimage_omitted]
FAILED examples/test_fragile_client.py::test_render_profile_handles_every_contract_valid_response[profileimage_null]
```

Nineteen payloads the client handles, four it does not, one test case per
mutation, and no fixture directory to keep in sync.

Real clients fetch over HTTP before they parse anything, so the payload has
to arrive through `requests` or `httpx`. `mock_contract_response` registers
it as the response the contract declares, status code and content type
included, with no server running:

```python
@contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
def test_requests_client(payload: dict[str, Any], contractfuzz_case: Case) -> None:
    with mock_contract_response(URL, payload, contractfuzz_case, backend="responses"):
        fetch_profile_with_requests(BASE_URL, 7)
```

`examples/test_mocked_client.py` does that for both an httpx client and a
requests client, and both break in the same four places:

```
$ pytest examples/test_mocked_client.py --tb=no -q
...F...F.....FF...........F...F.....FF............                       [100%]
================================= contractfuzz =================================
50 contract-valid payloads exercised, 8 broke the client.

Undocumented assumptions, most dangerous first:
  [danger 3] GET 200 response: age omitted
      KeyError: 'age'
      at fragile_client.py:33  years_old = user["age"]  # assumes optional age is present
  [danger 3] GET 200 response: roles = []
      IndexError: list index out of range
      at fragile_client.py:32  primary_role = user["roles"][0]  # assumes roles is never empty
  [danger 3] GET 200 response: profileImage omitted
      KeyError: 'profileImage'
      at fragile_client.py:34  avatar = user["profileImage"].lower()  # assumes nullable field is a string
  [danger 3] GET 200 response: profileImage = null
      AttributeError: 'NoneType' object has no attribute 'lower'
      at fragile_client.py:34  avatar = user["profileImage"].lower()  # assumes nullable field is a string
  [danger 3] GET 200 response: age omitted
      KeyError: 'age'
      at fragile_client.py:33  years_old = user["age"]  # assumes optional age is present
  [danger 3] GET 200 response: roles = []
      IndexError: list index out of range
      at fragile_client.py:32  primary_role = user["roles"][0]  # assumes roles is never empty
  [danger 3] GET 200 response: profileImage omitted
      KeyError: 'profileImage'
      at fragile_client.py:34  avatar = user["profileImage"].lower()  # assumes nullable field is a string
  [danger 3] GET 200 response: profileImage = null
      AttributeError: 'NoneType' object has no attribute 'lower'
      at fragile_client.py:34  avatar = user["profileImage"].lower()  # assumes nullable field is a string
=========================== short test summary info ============================
FAILED examples/test_mocked_client.py::test_requests_client_survives_every_contract_valid_response[age_omitted]
FAILED examples/test_mocked_client.py::test_requests_client_survives_every_contract_valid_response[roles_empty]
FAILED examples/test_mocked_client.py::test_requests_client_survives_every_contract_valid_response[profileimage_omitted]
FAILED examples/test_mocked_client.py::test_requests_client_survives_every_contract_valid_response[profileimage_null]
FAILED examples/test_mocked_client.py::test_httpx_client_survives_every_contract_valid_response[age_omitted]
FAILED examples/test_mocked_client.py::test_httpx_client_survives_every_contract_valid_response[roles_empty]
FAILED examples/test_mocked_client.py::test_httpx_client_survives_every_contract_valid_response[profileimage_omitted]
FAILED examples/test_mocked_client.py::test_httpx_client_survives_every_contract_valid_response[profileimage_null]
```

Eight failures, not four: each assumption is found once per client library,
which is why every entry appears twice. The `FAILED` lines name which client
hit it. The other 42 cases include the documented 404 variants, which arrive
as 404s because that is what the spec says, not because the test said so.

`demo.sh` runs all of this end to end.

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
uv add contractfuzz              # inside a project
uv add 'contractfuzz[pytest]'    # with the pytest plugin
uv add 'contractfuzz[mock]'      # with the response mocking helpers
# or, from a clone:
uv sync
```

Plain pip works too: `pip install .` from a clone. Dependencies are just
`jsonschema` and `pyyaml`; the `pytest` extra adds pytest and nothing else,
and the `mock` extra adds `respx` and `responses`. Install whichever of
those two matches your HTTP client and skip the extra if you prefer.

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

### pytest plugin

```
pip install 'contractfuzz[pytest]'
```

That is the whole setup. The plugin registers itself through pytest's
`pytest11` entry point, so there is nothing to add to `conftest.py`.

```python
from contractfuzz.plugin import contract_variants


@contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
def test_client_handles_it(payload):
    render_profile(payload)
```

Every contract-valid payload becomes its own test case, generated in
process at collection time and named after the mutation that produced it,
so `pytest -k roles_empty` reruns exactly the case where `roles` came back
as `[]`. Nothing is written to disk, which means there is no fixture
directory to regenerate: add an optional field to the spec and the suite
grows a case for it on the next run.

Keyword arguments, all optional:

| argument | default | meaning |
| --- | --- | --- |
| `method` | every method on the endpoint | restrict to one HTTP method |
| `kind` | `"response"` | `"response"`, `"request"`, or `"any"` |
| `status` | every status | restrict responses to one status code |
| `min_danger` | `1` (keep everything) | drop variants below this danger score |
| `include_baseline` | `True` | also run the fully-populated happy-path payload |
| `argname` | `"payload"` | the test argument that receives the payload |

A relative spec path resolves against the directory holding the test file,
not the working directory, so the suite runs the same from anywhere.

Keep `include_baseline` on. If the happy-path payload fails too, the
edge-case failures say nothing about the contract, and the summary says so
rather than letting you read them as findings:

```
warning: the fully-populated baseline payload failed too, so these failures
are not specific to the generated edge cases.
Fix the happy path first; until then the results below mean little.
```

The `contractfuzz_case` fixture hands a test the mutation it was given, so
a known-accepted variant can be skipped without dropping the whole suite:

```python
@contract_variants("openapi.yaml", "/users/{id}", status="200")
def test_client_handles_it(payload, contractfuzz_case):
    if contractfuzz_case.kind == "empty_string":
        pytest.skip("empty display names are accepted upstream, by agreement")
    render_profile(payload)
```

It carries `endpoint`, `target` (`"GET 200 response"`), `method`, `kind`,
`path` (`"$.roles"`), `description`, `danger`, `status` (`"200"`, or `None`
for a request body), `media_type`, and `is_baseline`.

A missing spec, an unknown endpoint, or a selection that matches no schema
fails as one clean test case with a one-line message, not a collection
traceback. `--no-contractfuzz-summary` turns off the summary block.

### Response mocking

```
pip install 'contractfuzz[mock]'
```

`mock_contract_response` answers one request with a generated variant, so a
test can exercise the client you actually ship instead of calling a parsing
function directly. Two backends: `responses` for clients built on
`requests`, `respx` for clients built on `httpx`.

```python
from contractfuzz.mocking import mock_contract_response
from contractfuzz.plugin import contract_variants

URL = "https://api.example.com/users/7"


@contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
def test_fetch_profile(payload, contractfuzz_case):
    with mock_contract_response(URL, payload, contractfuzz_case, backend="responses"):
        fetch_profile(7)
```

The mocked response is the response the contract declares. Passing the
`contractfuzz_case` gives the helper the method, the status code the spec
attached to that response (a 404 variant arrives as a 404, not a 200), and
the declared media type, `application/vnd.api+json` included.

Arguments after `case`, all keyword-only:

| argument | default | meaning |
| --- | --- | --- |
| `backend` | `"auto"` | `"respx"`, `"responses"`, or `"auto"` when exactly one is installed |
| `method` | the case's method, else `GET` | override the HTTP method |
| `status` | the contract's status, else `200` | override the status code |
| `content_type` | the contract's media type | override the response content type |
| `assert_called` | `True` | fail if the client never made the request |

`assert_called` is on because a test whose client never issued the request
proves nothing about the payload; turn it off for a client that may answer
from a cache.

The context manager yields a `MockedResponse` carrying `backend`, `method`,
`url`, `status`, `content_type`, `body`, and `mock`, the backend's own router
object, so a test that needs a second route keeps using respx or responses
directly.

Both backends are optional. contractfuzz imports neither until you ask for
one, and asking for one that is not installed raises `MockBackendError` with
the pip command that fixes it:

```
MockBackendError: the 'respx' mocking backend is not installed: respx mocks httpx clients; install it with `pip install 'contractfuzz[mock]'` or `pip install respx`
```

With both installed, `backend="auto"` says so rather than guessing which
HTTP library your client uses.

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
cases.py       Case: one payload's provenance, shared by the plugin and the
               mocking helpers, and free of pytest
plugin.py      the pytest plugin: contract_variants, the case marker, and
               the end-of-run summary of undocumented assumptions
mocking.py     respx and responses backends: register one generated variant
               as the response the contract declares
cli.py         argparse CLI: generate, list, proxy
```

`plugin.py` is the only module that imports pytest, and nothing imports
it: pytest loads it from the entry point. Installing contractfuzz without
the `pytest` extra leaves the rest of the tool fully usable. `mocking.py`
is the seam for HTTP mocking libraries: each backend is one small function
registering a route, imported on demand, so adding a third is local work.

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
- The pytest plugin generates in process on every run rather than reading
  a pinned fixture directory. Payloads are deterministic for a given spec,
  but a spec change silently changes the cases; if you need payloads
  frozen across runs, use `contractfuzz generate` and commit the output.
- `mock_contract_response` registers one route per call, against the
  concrete URL you pass: path templates are not filled in for you, and a
  flow that makes several requests needs the backend router it hands back.

## Roadmap

See [ROADMAP.md](ROADMAP.md). Headlines: external and recursive `$ref`
support, full `oneOf`/`anyOf` branch coverage, pattern-aware string
synthesis, combined multi-field mutations, and a `--contractfuzz-danger`
option to narrow a whole run to the most dangerous mutations.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `uv run pytest` and
`uv run ruff check .` before sending a PR.

GitHub topics: `openapi`, `fuzzing`, `api-testing`, `property-testing`,
`developer-tools`.

## License

MIT, see [LICENSE](LICENSE).
