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

The pytest plugin's tests live in `tests/test_plugin.py` and run through
`pytester`, which runs pytest inside pytest. They deliberately do not pass
`-p contractfuzz.plugin` to the inner run: the plugin has to load from its
`pytest11` entry point, the way it does for a user who just installs it.
If you change the plugin, run `./demo.sh`, whose steps 3 and 4 must still
fail (the example client is fragile on purpose).

The mocking helpers' tests live in `tests/test_mocking.py` and run against
the real `respx` and `responses` libraries, which `uv sync` installs. The
"backend not installed" paths are covered by hiding the module in
`sys.modules`, so please do not test them by uninstalling anything. A new
backend needs three things: the registration function, a test that a real
client gets the payload through it, and a test that asking for it without
the library installed names the pip command.

## Design notes

- Keep the pipeline one-directional: baseline, mutations, validation,
  output. Do not add side channels around the validator.
- Expected failures raise `ContractfuzzError` subclasses with messages a
  person can act on; the CLI turns them into clean nonzero exits, and the
  plugin turns them into one-line test failures rather than collection
  tracebacks.
- No new runtime dependencies without prior discussion in an issue. pytest
  is an optional extra (`contractfuzz[pytest]`) and nothing outside
  `plugin.py` may import it. The mocking backends are the same deal
  (`contractfuzz[mock]`): `mocking.py` imports them inside the function
  that needs them, never at module scope, and CI has a job that installs
  contractfuzz with no extras to keep that true.
