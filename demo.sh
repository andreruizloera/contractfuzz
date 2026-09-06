#!/usr/bin/env bash
# contractfuzz demo: generate contract-valid edge cases from the example
# OpenAPI spec, then replay them through an intentionally fragile client
# and watch it break on payloads the contract explicitly allows. Step 3 does
# the same thing as a pytest suite, through the plugin.
set -euo pipefail
cd "$(dirname "$0")"

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

RUN="uv run"
if ! command -v uv >/dev/null 2>&1; then
    RUN="python3"
    export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
    CONTRACTFUZZ=(python3 -m contractfuzz)
    PYTEST=(python3 -m pytest)
else
    CONTRACTFUZZ=(uv run contractfuzz)
    PYTEST=(uv run pytest)
fi

echo "== 1. Generate contract-valid edge-case fixtures =="
"${CONTRACTFUZZ[@]}" generate examples/openapi.yaml --endpoint '/users/{id}' --out "$OUT"

echo "== 2. Replay them through the fragile example client =="
if $RUN examples/fragile_client.py "$OUT"; then
    echo "demo unexpectedly found no crashes" >&2
    exit 1
fi

echo
echo "== 3. The same payloads as a pytest suite, via the plugin =="
# examples/test_fragile_client.py is expected to fail: the client is fragile
# on purpose. A green run here would mean the plugin generated nothing.
if $RUN pytest examples/test_fragile_client.py -q; then
    echo "demo unexpectedly found no failing cases" >&2
    exit 1
fi

echo
echo "== Result: contractfuzz found real bugs using only contract-valid data =="
