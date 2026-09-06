"""The contractfuzz pytest plugin pointed at the intentionally fragile client.

THIS FILE IS EXPECTED TO FAIL. `render_profile` breaks on four payloads the
OpenAPI contract explicitly allows, which is the whole point of the demo. It
lives in examples/ rather than tests/ so the repo's own suite stays green;
demo.sh runs it and checks that it does fail.

Run it yourself from the repo root:

    uv run pytest examples/test_fragile_client.py
"""

from __future__ import annotations

from typing import Any

from fragile_client import render_profile

from contractfuzz.plugin import contract_variants


@contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
def test_render_profile_handles_every_contract_valid_response(payload: dict[str, Any]) -> None:
    render_profile(payload)
