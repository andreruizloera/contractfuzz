"""Tests for the response mocking helpers.

Both backends are installed in the dev environment, so the happy paths run
against the real ``responses`` and ``respx`` libraries with real ``requests``
and ``httpx`` clients. The "backend not installed" paths are exercised by
hiding the module in ``sys.modules``, which makes ``importlib`` raise the
same ``ImportError`` a user without the package gets.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import requests
from respx.models import AllMockedAssertionError

from contractfuzz.cases import Case
from contractfuzz.errors import ContractfuzzError, MockBackendError
from contractfuzz.mocking import mock_contract_response, resolve_backend, url_pattern

EXAMPLE_SPEC = Path(__file__).resolve().parent.parent / "examples" / "openapi.yaml"
SPEC = str(EXAMPLE_SPEC)

URL = "https://api.example.com/users/7"

PAYLOAD = {
    "id": 7,
    "displayName": "Ada",
    "email": "ada@example.com",
    "status": "active",
    "roles": [],
    "profileImage": None,
}


def make_case(**overrides: Any) -> Case:
    """A Case shaped like the ones the plugin builds, tweakable per test."""
    fields: dict[str, Any] = {
        "endpoint": "/users/{id}",
        "target": "GET 200 response",
        "method": "GET",
        "kind": "empty_array",
        "path": "$.roles",
        "description": "roles = []",
        "danger": 3,
        "status": "200",
        "media_type": "application/json",
    }
    fields.update(overrides)
    return Case(**fields)


def hide(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make importing ``names`` fail the way it does when they are not installed."""
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)


# ---------------------------------------------------------------------------
# The core: a real client gets the generated payload, with no server
# ---------------------------------------------------------------------------


def test_responses_backend_serves_the_variant_to_a_requests_client() -> None:
    with mock_contract_response(URL, PAYLOAD, make_case(), backend="responses") as mocked:
        response = requests.get(URL, timeout=5)
    assert response.status_code == 200
    assert response.json() == PAYLOAD
    assert response.headers["Content-Type"] == "application/json"
    assert mocked.backend == "responses"


def test_respx_backend_serves_the_variant_to_an_httpx_client() -> None:
    with mock_contract_response(URL, PAYLOAD, make_case(), backend="respx") as mocked:
        response = httpx.get(URL)
    assert response.status_code == 200
    assert response.json() == PAYLOAD
    assert response.headers["Content-Type"] == "application/json"
    assert mocked.backend == "respx"


def test_a_top_level_array_response_is_served_as_json() -> None:
    payload = [PAYLOAD, PAYLOAD]
    with mock_contract_response(URL, payload, make_case(), backend="respx"):
        assert httpx.get(URL).json() == payload


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_status_and_content_type_come_from_the_contract(backend: str) -> None:
    """A 404 variant is served as a 404, and the declared media type is kept."""
    case = make_case(
        target="GET 404 response",
        status="404",
        media_type="application/vnd.api+json",
        description='code = ""',
    )
    body = {"code": "", "message": "gone"}
    with mock_contract_response(URL, body, case, backend=backend) as mocked:
        response = requests.get(URL, timeout=5) if backend == "responses" else httpx.get(URL)
    assert mocked.status == 404
    assert response.status_code == 404
    assert response.headers["Content-Type"] == "application/vnd.api+json"
    assert response.json() == body


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_the_case_supplies_the_method(backend: str) -> None:
    case = make_case(target="PATCH 200 response", method="PATCH")
    with mock_contract_response(URL, PAYLOAD, case, backend=backend) as mocked:
        assert mocked.method == "PATCH"
        if backend == "responses":
            assert requests.patch(URL, timeout=5).status_code == 200
        else:
            assert httpx.patch(URL).status_code == 200


def test_explicit_arguments_override_the_case() -> None:
    case = make_case()
    with mock_contract_response(
        URL,
        PAYLOAD,
        case,
        backend="respx",
        method="post",
        status=503,
        content_type="application/problem+json",
    ) as mocked:
        response = httpx.post(URL)
    assert (mocked.method, mocked.status) == ("POST", 503)
    assert response.status_code == 503
    assert response.headers["Content-Type"] == "application/problem+json"


def test_without_a_case_it_defaults_to_a_json_get_200() -> None:
    with mock_contract_response(URL, PAYLOAD, backend="respx") as mocked:
        response = httpx.get(URL)
    assert (mocked.method, mocked.status, mocked.content_type) == (
        "GET",
        200,
        "application/json",
    )
    assert response.status_code == 200


def test_the_backend_object_is_exposed_for_extra_routes() -> None:
    """Escape hatch: the underlying router is handed back, not hidden."""
    with mock_contract_response(URL, PAYLOAD, make_case(), backend="respx") as mocked:
        mocked.mock.get("https://api.example.com/health").respond(200, json={"ok": True})
        assert httpx.get("https://api.example.com/health").json() == {"ok": True}
        httpx.get(URL)


# ---------------------------------------------------------------------------
# A test that never called the client is not a passing test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_a_client_that_never_requests_fails_the_test(backend: str) -> None:
    with (
        pytest.raises(AssertionError, match="never requested GET"),
        mock_contract_response(URL, PAYLOAD, make_case(), backend=backend),
    ):
        pass


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_assert_called_false_allows_an_unused_mock(backend: str) -> None:
    with mock_contract_response(URL, PAYLOAD, make_case(), backend=backend, assert_called=False):
        pass


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_the_clients_own_exception_is_not_masked(backend: str) -> None:
    """The failure a test exists to see must not be replaced by bookkeeping."""
    with (
        pytest.raises(KeyError, match="age"),
        mock_contract_response(URL, PAYLOAD, make_case(), backend=backend),
    ):
        PAYLOAD["age"]  # noqa: B018  the client's own bug, raised before any request


# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------


def test_mocking_imports_with_neither_backend_installed() -> None:
    """contractfuzz must import and run with no mocking backend present."""
    code = (
        "import sys, contractfuzz.mocking as m;"
        " assert 'respx' not in sys.modules, 'respx imported eagerly';"
        " assert 'responses' not in sys.modules, 'responses imported eagerly';"
        " print(m.BACKENDS)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "('respx', 'responses')"


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_a_missing_backend_names_the_pip_command(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    hide(monkeypatch, backend)
    with (
        pytest.raises(MockBackendError) as excinfo,
        mock_contract_response(URL, PAYLOAD, make_case(), backend=backend),
    ):
        pass
    message = str(excinfo.value)
    assert f"the {backend!r} mocking backend is not installed" in message
    assert "pip install 'contractfuzz[mock]'" in message
    assert f"pip install {backend}" in message
    assert isinstance(excinfo.value, ContractfuzzError)


def test_auto_picks_the_only_installed_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    hide(monkeypatch, "respx")
    assert resolve_backend("auto") == "responses"
    with mock_contract_response(URL, PAYLOAD, make_case()) as mocked:
        requests.get(URL, timeout=5)
    assert mocked.backend == "responses"


def test_auto_refuses_to_guess_when_both_are_installed() -> None:
    with pytest.raises(MockBackendError, match="are both installed"):
        resolve_backend("auto")


def test_auto_says_what_to_install_when_neither_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hide(monkeypatch, "respx", "responses")
    with pytest.raises(MockBackendError) as excinfo:
        resolve_backend("auto")
    assert "no response mocking backend is installed" in str(excinfo.value)
    assert "contractfuzz[mock]" in str(excinfo.value)


def test_an_unknown_backend_name_lists_the_real_ones() -> None:
    with pytest.raises(MockBackendError, match="unknown mocking backend 'httpretty'"):
        resolve_backend("httpretty")


# ---------------------------------------------------------------------------
# Cases that cannot be mocked as a response
# ---------------------------------------------------------------------------


def test_a_request_body_case_is_refused_with_an_actionable_message() -> None:
    case = make_case(target="PATCH request body", method="PATCH", status=None)
    with (
        pytest.raises(MockBackendError, match="is a request body, not a response"),
        mock_contract_response(URL, PAYLOAD, case, backend="respx"),
    ):
        pass


def test_a_status_range_asks_for_a_concrete_code() -> None:
    case = make_case(target="GET 2XX response", status="2XX")
    with (
        pytest.raises(MockBackendError, match="not a concrete status code"),
        mock_contract_response(URL, PAYLOAD, case, backend="respx"),
    ):
        pass


def test_an_unknown_method_is_refused() -> None:
    with (
        pytest.raises(MockBackendError, match="unknown HTTP method 'FETCH'"),
        mock_contract_response(URL, PAYLOAD, backend="respx", method="fetch"),
    ):
        pass


# ---------------------------------------------------------------------------
# Composition with the pytest plugin
# ---------------------------------------------------------------------------


def test_generated_variants_reach_a_real_client_through_the_plugin(
    pytester: pytest.Pytester,
) -> None:
    """The point of the feature: contract variants over real HTTP, no server."""
    pytester.makepyfile(
        f"""
        import requests

        from contractfuzz.mocking import mock_contract_response
        from contractfuzz.plugin import contract_variants

        URL = "https://api.example.com/users/7"

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_client(payload, contractfuzz_case):
            with mock_contract_response(URL, payload, contractfuzz_case, backend="responses"):
                user = requests.get(URL, timeout=5).json()
                user["roles"][0]              # assumes roles is never empty
                user["profileImage"].lower()  # assumes nullable field is a string
        """
    )
    result = pytester.runpytest("-v")
    failed = {
        line.split("[", 1)[1].split("]", 1)[0]
        for line in result.outlines
        if "FAILED" in line and "[" in line
    }
    assert failed == {"roles_empty", "profileimage_null", "profileimage_omitted"}
    result.stdout.fnmatch_lines(["*23 contract-valid payloads exercised, 3 broke the client.*"])


def test_the_404_variants_reach_the_client_as_404s(pytester: pytest.Pytester) -> None:
    """The mocked status code is the one the contract declares for that target."""
    pytester.makepyfile(
        f"""
        import httpx

        from contractfuzz.mocking import mock_contract_response
        from contractfuzz.plugin import contract_variants

        URL = "https://api.example.com/users/7"

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="404")
        def test_client(payload, contractfuzz_case):
            with mock_contract_response(URL, payload, contractfuzz_case, backend="respx"):
                assert httpx.get(URL).status_code == 404
        """
    )
    pytester.runpytest().assert_outcomes(passed=4)


# ---------------------------------------------------------------------------
# Path templates: one registration answers every id the client builds
# ---------------------------------------------------------------------------

TEMPLATE = "https://api.example.com/users/{id}"


def fetch(backend: str, url: str) -> requests.Response | httpx.Response:
    """GET ``url`` with the client library that ``backend`` mocks."""
    return requests.get(url, timeout=5) if backend == "responses" else httpx.get(url)


def test_a_concrete_url_is_registered_as_it_is() -> None:
    """The control: no placeholder, no pattern, the same route as before."""
    assert url_pattern(URL) is None
    with mock_contract_response(URL, PAYLOAD, make_case(), backend="respx") as mocked:
        httpx.get(URL)
    assert mocked.url_pattern is None


def test_a_template_matches_any_value_in_its_placeholder() -> None:
    pattern = url_pattern(TEMPLATE)
    assert pattern is not None
    for requested in (
        "https://api.example.com/users/7",
        "https://api.example.com/users/ada%20lovelace",
        "https://api.example.com/users/7?expand=roles",  # a concrete URL accepts a query too
        "HTTPS://API.EXAMPLE.COM/users/7",  # scheme and host are case-insensitive
    ):
        assert pattern.match(requested), requested


def test_a_template_does_not_match_a_different_route() -> None:
    """Anchored at both ends, since respx applies it with search() and responses with match()."""
    pattern = url_pattern(TEMPLATE)
    assert pattern is not None
    for requested in (
        "https://api.example.com/users/",  # an empty value is not a value
        "https://api.example.com/users",
        "https://api.example.com/users/7/posts",  # a value is at most one segment
        "https://api.example.com/v2/users/7",  # what an unanchored search() would accept
        "https://apixexample.com/users/7",  # the dot in the host is literal
        "https://api.example.com.evil.test/users/7",
        "http://api.example.com/users/7",
    ):
        assert not pattern.search(requested), requested


def test_several_placeholders_and_one_inside_a_segment() -> None:
    pattern = url_pattern("https://api.example.com/orgs/{org}/files/{name}.json")
    assert pattern is not None
    assert pattern.match("https://api.example.com/orgs/acme/files/report.v2.json")
    assert not pattern.match("https://api.example.com/orgs/acme/files/report.yaml")
    assert not pattern.match("https://api.example.com/orgs/acme/team/files/report.json")


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_one_templated_registration_answers_every_id(backend: str) -> None:
    """The feature: the test does not have to know which id the client builds."""
    with mock_contract_response(TEMPLATE, PAYLOAD, make_case(), backend=backend) as mocked:
        first = fetch(backend, "https://api.example.com/users/7")
        second = fetch(backend, "https://api.example.com/users/8?expand=roles")
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json() == PAYLOAD
    assert mocked.url == TEMPLATE
    assert mocked.url_pattern is not None


@pytest.mark.parametrize("backend", ["responses", "respx"])
def test_a_template_widens_the_value_not_the_route(backend: str) -> None:
    """A longer path gets the backend's own not-mocked error, as a wrong concrete URL does."""
    refused = requests.ConnectionError if backend == "responses" else AllMockedAssertionError
    with (
        pytest.raises(refused),
        mock_contract_response(TEMPLATE, PAYLOAD, make_case(), backend=backend),
    ):
        fetch(backend, "https://api.example.com/users/7/posts")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("/users/{id}", "no scheme and host"),
        ("https://{tenant}.example.com/users/{id}", "placeholder in the host"),
        ("https://api.example.com/users/{id}?expand=roles", "query string or fragment"),
    ],
)
def test_a_template_that_would_need_a_guess_is_refused(url: str, reason: str) -> None:
    with (
        pytest.raises(MockBackendError, match=reason),
        mock_contract_response(url, PAYLOAD, make_case(), backend="respx"),
    ):
        pass


def test_the_specs_own_path_template_is_a_valid_url(pytester: pytest.Pytester) -> None:
    """BASE_URL + contractfuzz_case.endpoint, and the client asks for an id no test names."""
    pytester.makepyfile(
        f"""
        import httpx

        from contractfuzz.mocking import mock_contract_response
        from contractfuzz.plugin import contract_variants

        BASE_URL = "https://api.example.com"

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_client(payload, contractfuzz_case):
            url = BASE_URL + contractfuzz_case.endpoint
            with mock_contract_response(url, payload, contractfuzz_case, backend="respx"):
                user = httpx.get(BASE_URL + "/users/42").json()
                user["roles"][0]              # assumes roles is never empty
                user["profileImage"].lower()  # assumes nullable field is a string
        """
    )
    result = pytester.runpytest("-v")
    failed = {
        line.split("[", 1)[1].split("]", 1)[0]
        for line in result.outlines
        if "FAILED" in line and "[" in line
    }
    assert failed == {"roles_empty", "profileimage_null", "profileimage_omitted"}
    result.stdout.fnmatch_lines(["*23 contract-valid payloads exercised, 3 broke the client.*"])
