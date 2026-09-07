"""The same fragile logic, behind two real HTTP clients.

``fragile_client.render_profile`` is handed a dict. Real code fetches that
dict over HTTP first, and that is the layer where a fixture directory stops
being enough: you need the payload to come back through ``requests`` or
``httpx``, with the status code and content type the contract declares.

These two functions are ordinary clients. Nothing about them knows it is
being tested. ``examples/test_mocked_client.py`` points contractfuzz's
mocking helpers at them, which is the whole setup.
"""

from __future__ import annotations

import httpx
import requests
from fragile_client import render_profile

TIMEOUT = 5.0


def fetch_profile_with_requests(base_url: str, user_id: int) -> str:
    """Fetch one user with requests and render them."""
    response = requests.get(f"{base_url}/users/{user_id}", timeout=TIMEOUT)
    response.raise_for_status()
    return render_profile(response.json())


def fetch_profile_with_httpx(base_url: str, user_id: int) -> str:
    """Fetch one user with httpx and render them."""
    response = httpx.get(f"{base_url}/users/{user_id}", timeout=TIMEOUT)
    response.raise_for_status()
    return render_profile(response.json())
