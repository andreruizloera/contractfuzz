from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from contractfuzz.proxy import _match_template, make_proxy_server
from contractfuzz.spec import find_targets
from contractfuzz.validation import validation_errors

VALID_USER = {
    "id": 7,
    "displayName": "Ada",
    "email": "ada@example.com",
    "status": "active",
    "roles": ["admin", "viewer"],
    "age": 36,
    "profileImage": "https://example.com/ada.png",
}


class _Upstream(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/users/bad"):
            body = json.dumps({"id": "not-an-integer"}).encode()
        elif self.path.startswith("/users/"):
            body = json.dumps(VALID_USER).encode()
        elif self.path == "/text":
            body = b"plain text"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        else:
            body = json.dumps({"unmatched": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def upstream() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def proxy(example_spec: dict[str, Any], upstream: str) -> Iterator[str]:
    server = make_proxy_server(example_spec, upstream, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, dict[str, str], bytes]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, dict(response.headers), response.read()


def test_match_template() -> None:
    assert _match_template("/users/{id}", "/users/7")
    assert not _match_template("/users/{id}", "/users/7/pets")
    assert not _match_template("/users/{id}", "/orders/7")
    assert _match_template("/users", "/users")


def test_proxy_mutates_matched_json_and_stays_valid(
    proxy: str, example_spec: dict[str, Any]
) -> None:
    target = next(
        t
        for t in find_targets(example_spec, "/users/{id}", "get")
        if t.kind == "response" and t.status == "200"
    )
    descriptions = set()
    saw_mutation_of_upstream = False
    for _ in range(8):
        status, headers, body = _get(f"{proxy}/users/7")
        assert status == 200
        data = json.loads(body)
        assert validation_errors(target.schema, data) == []
        assert "X-Contractfuzz-Mutation" in headers
        descriptions.add(headers["X-Contractfuzz-Mutation"])
        if data != VALID_USER:
            saw_mutation_of_upstream = True
    assert saw_mutation_of_upstream
    assert len(descriptions) >= 3  # mutations rotate across requests


def test_proxy_passes_through_contract_violating_upstream(proxy: str) -> None:
    status, headers, body = _get(f"{proxy}/users/bad")
    assert status == 200
    assert json.loads(body) == {"id": "not-an-integer"}
    assert headers.get("X-Contractfuzz") == "upstream-body-violates-contract, passed through"
    assert "X-Contractfuzz-Mutation" not in headers


def test_proxy_passes_through_unmatched_paths(proxy: str) -> None:
    status, headers, body = _get(f"{proxy}/unknown/route")
    assert status == 200
    assert json.loads(body) == {"unmatched": True}
    assert "X-Contractfuzz-Mutation" not in headers


def test_proxy_passes_through_non_json(proxy: str) -> None:
    status, headers, body = _get(f"{proxy}/text")
    assert status == 200
    assert body == b"plain text"
    assert "X-Contractfuzz-Mutation" not in headers


def test_proxy_reports_unreachable_upstream(example_spec: dict[str, Any]) -> None:
    server = make_proxy_server(example_spec, "http://127.0.0.1:1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/users/7"
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(url, timeout=10)
        assert excinfo.value.code == 502
    finally:
        server.shutdown()
        server.server_close()
