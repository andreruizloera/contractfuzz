# Roadmap

Honest future work. None of this is implemented yet.

## Schema coverage

- External `$ref` resolution (other files, URLs) with a fetch allowlist.
- Recursive `$ref`s via bounded-depth unrolling instead of rejection.
- Full `oneOf`/`anyOf` coverage: generate a baseline and variants per
  branch, not just the first one.
- Deep `allOf` conflict resolution beyond the current shallow merge.
- Pattern-aware string synthesis (generate strings from regexes) so
  `pattern`-constrained fields get boundary variants too.
- `prefixItems`, `contains`, `if`/`then`/`else`, and `not` support.
- Mutations for every array element, not just the first.
- Number boundary variants for `exclusiveMinimum`/`exclusiveMaximum`
  expressed as floats, plus maxLength-sized string variants.

## Generation modes

- Combined mutations: apply several dangerous mutations at once (all
  optionals omitted, everything nullable set to null) for worst-case
  payloads.
- Seeded generation from a recorded real response instead of a synthetic
  baseline, so fixtures look like production data.
- Danger-score tuning via a config file, including per-field overrides.

## Integrations

The pytest plugin (`contract_variants`), the `--contractfuzz-danger`
option, and the response mocking helpers for `respx` and `responses`
(`mock_contract_response`) all SHIPPED; see the README. What is still
future work around them:

- Reuse a pinned fixture directory instead of generating in-process, for
  suites that want byte-identical payloads across runs.
- Mocking helpers for the other common backends: `aioresponses`,
  `pytest-httpserver`, and `requests-mock`.
- URL templating in the mocking helpers, so `/users/{id}` can be registered
  once instead of naming the concrete URL per test.
- JS/TS fixture output with type stubs for frontend test suites.

## Proxy

- Request-body mutation for testing servers, not just clients (with an
  explicit opt-in flag, since that fuzzes the upstream).
- Mutation scheduling: per-route selection, deterministic seeds, and a
  control endpoint to pick the next mutation.
- HTTPS termination and HTTP/2.
- Streaming and chunked responses.
