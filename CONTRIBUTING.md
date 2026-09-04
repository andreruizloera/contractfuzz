# Contributing

Bug reports, mutation ideas, and specs that break contractfuzz are all
welcome. A failing spec attached to an issue is the single most useful
thing you can send.

## Setup

```
git clone https://github.com/andreruizloera/contractfuzz
cd contractfuzz
uv sync
uv run pytest
```

## Before you send a PR

1. `uv run ruff format .` and `uv run ruff check .` pass.
2. `uv run pytest` passes.
3. New mutation classes come with three things: the mutation itself, a
   test showing it fires when the schema permits it, and a test showing
   it does not fire when the schema forbids it.
4. The invariant is non-negotiable: every emitted variant must validate
   against the resolved schema. If your change can produce an invalid
   candidate, the validator will discard it, but prefer not proposing it
   at all; `rejected == 0` for the example spec is asserted in the tests.

## Design notes

- Keep the pipeline one-directional: baseline, mutations, validation,
  output. Do not add side channels around the validator.
- Expected failures raise `ContractfuzzError` subclasses with messages a
  person can act on; the CLI turns them into clean nonzero exits.
- No new runtime dependencies without prior discussion in an issue.
