#!/usr/bin/env bash
# contractfuzz demo: generate contract-valid edge cases from the example
# OpenAPI spec, then replay them through an intentionally fragile client
# and watch it break on payloads the contract explicitly allows.
set -euo pipefail
cd "$(dirname "$0")"

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

RUN="uv run"
if ! command -v uv >/dev/null 2>&1; then
    RUN="python3"
    export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
    CONTRACTFUZZ=(python3 -m contractfuzz)
else
    CONTRACTFUZZ=(uv run contractfuzz)
fi

echo "== 1. Generate contract-valid edge-case fixtures =="
"${CONTRACTFUZZ[@]}" generate examples/openapi.yaml --endpoint '/users/{id}' --out "$OUT"

echo "== 2. Replay them through the fragile example client =="
if $RUN examples/fragile_client.py "$OUT"; then
    echo "demo unexpectedly found no crashes" >&2
    exit 1
else
    echo
    echo "== Result: contractfuzz found real bugs using only contract-valid data =="
fi
