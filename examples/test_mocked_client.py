"""Contract variants served to real HTTP clients, with no server running.

THIS FILE IS EXPECTED TO FAIL. The clients in ``mocked_client.py`` inherit
the four undocumented assumptions of ``render_profile``, and both of them
break on the same four contract-valid payloads, which is the point of the
demo. It lives in examples/ rather than tests/ so the repo's own suite stays
green; demo.sh runs it and checks that it does fail.

Run it yourself from the repo root:

    uv run pytest examples/test_mocked_client.py
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import requests
from mocked_client import fetch_profile_with_httpx, fetch_profile_with_requests

from contractfuzz.cases import Case
from contractfuzz.mocking import mock_contract_response
from contractfuzz.plugin import contract_variants

BASE_URL = "https://api.example.com"
URL = f"{BASE_URL}/users/7"


@contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
def test_requests_client_survives_every_contract_valid_response(
    payload: dict[str, Any], contractfuzz_case: Case
) -> None:
    with mock_contract_response(URL, payload, contractfuzz_case, backend="responses"):
        fetch_profile_with_requests(BASE_URL, 7)


@contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
def test_httpx_client_survives_every_contract_valid_response(
    payload: dict[str, Any], contractfuzz_case: Case
) -> None:
    with mock_contract_response(URL, payload, contractfuzz_case, backend="respx"):
        fetch_profile_with_httpx(BASE_URL, 7)


# The status code comes from the contract, not from a hardcoded 200: these
# variants are attached to the 404 response, so both clients see a 404 and
# raise. Nothing here says "404" except the spec.
@contract_variants("openapi.yaml", "/users/{id}", method="get", status="404")
def test_both_clients_raise_on_the_documented_404(
    payload: dict[str, Any], contractfuzz_case: Case
) -> None:
    with (
        mock_contract_response(URL, payload, contractfuzz_case, backend="responses"),
        pytest.raises(requests.HTTPError),
    ):
        fetch_profile_with_requests(BASE_URL, 7)
    with (
        mock_contract_response(URL, payload, contractfuzz_case, backend="respx"),
        pytest.raises(httpx.HTTPStatusError),
    ):
        fetch_profile_with_httpx(BASE_URL, 7)
