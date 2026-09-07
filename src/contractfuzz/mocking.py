"""Serve a generated variant to a real HTTP client, with no live server.

``contract_variants`` hands a test a contract-valid payload. Getting that
payload into the client under test used to be the user's problem: wire up an
HTTP mock, remember which status code the contract declares, remember the
content type, and repeat it in every suite. This module does that part, so a
test body is a client call and nothing else:

    from contractfuzz.mocking import mock_contract_response
    from contractfuzz.plugin import contract_variants

    @contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
    def test_fetch_profile(payload, contractfuzz_case):
        with mock_contract_response(URL, payload, contractfuzz_case):
            fetch_profile(7)

Two backends, both optional dependencies: ``responses`` mocks clients built
on ``requests``, ``respx`` mocks clients built on ``httpx``. contractfuzz
imports neither at import time; asking for one that is not installed raises
``MockBackendError`` with the pip command that fixes it.

The mocked response reflects what the contract declares. Given a ``Case``,
the status code is the one the spec attached to that response (404 for an
error variant, not a blanket 200) and the content type is the media type the
spec named, including ``application/*+json`` variants.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from contractfuzz.cases import Case
from contractfuzz.errors import MockBackendError
from contractfuzz.spec import HTTP_METHODS

BACKENDS = ("respx", "responses")

_MOCKS = {"respx": "httpx clients", "responses": "requests clients"}

_METHODS = frozenset(m.upper() for m in HTTP_METHODS)


def _install_hint(name: str) -> str:
    return (
        f"{name} mocks {_MOCKS[name]}; install it with"
        f" `pip install 'contractfuzz[mock]'` or `pip install {name}`"
    )


def _load(name: str) -> ModuleType:
    """Import a backend, or fail with the command that installs it."""
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        hint = _install_hint(name) if name in _MOCKS else f"install it with `pip install {name}`"
        raise MockBackendError(f"the {name!r} mocking backend is not installed: {hint}") from exc


def _is_installed(name: str) -> bool:
    try:
        importlib.import_module(name)
    except ImportError:
        return False
    return True


def resolve_backend(backend: str = "auto") -> str:
    """Return the backend to use, or explain why there is not exactly one.

    ``"auto"`` picks the single installed backend. With both installed there
    is no way to guess which HTTP library the client under test uses, so the
    caller is asked to say.
    """
    if backend in BACKENDS:
        _load(backend)  # fail here, with the install hint, not at registration time
        return backend
    if backend != "auto":
        raise MockBackendError(
            f"unknown mocking backend {backend!r};"
            f" use one of {', '.join(repr(b) for b in BACKENDS)} or 'auto'"
        )
    installed = [name for name in BACKENDS if _is_installed(name)]
    if not installed:
        raise MockBackendError(
            "no response mocking backend is installed:"
            " `pip install 'contractfuzz[mock]'`, or install just the one you need"
            " (respx for httpx clients, responses for requests clients)"
        )
    if len(installed) > 1:
        both = " and ".join(installed)
        raise MockBackendError(
            f"{both} are both installed, so backend='auto' cannot tell which one you"
            " want; pass backend='respx' for an httpx client or backend='responses'"
            " for a requests client"
        )
    return installed[0]


@dataclass(frozen=True)
class MockedResponse:
    """The response that was registered, and the backend object that holds it.

    ``mock`` is the backend's own handle (a ``respx`` router or a
    ``responses.RequestsMock``), so a test that needs more than one route can
    keep using the library directly instead of working around this helper.
    """

    backend: str  # "respx" or "responses"
    mock: Any  # respx.MockRouter or responses.RequestsMock
    method: str  # "GET"
    url: str
    status: int  # 200, 404, ... as declared by the contract
    content_type: str  # "application/json", "application/vnd.api+json", ...
    body: bytes  # the serialized variant


def _resolve_status(case: Case | None, status: int | str | None) -> int:
    if status is None:
        if case is None:
            return 200
        if case.status is None:
            raise MockBackendError(
                f"{case.target} is a request body, not a response, so there is nothing"
                " to mock; generate response variants with kind='response' (the default)"
            )
        status = case.status
    try:
        return int(status)
    except (TypeError, ValueError) as exc:
        raise MockBackendError(
            f"the contract declares this response as {status!r}, which is not a concrete"
            " status code; pass status=<int> to say what the server would send"
        ) from exc


def _resolve_method(case: Case | None, method: str | None) -> str:
    resolved = (method or (case.method if case else None) or "GET").upper()
    if resolved not in _METHODS:
        raise MockBackendError(
            f"unknown HTTP method {resolved!r}; expected one of {', '.join(sorted(_METHODS))}"
        )
    return resolved


@contextmanager
def _respx_mock(
    method: str, url: str, status: int, content_type: str, body: bytes
) -> Iterator[tuple[Any, Callable[[], int]]]:
    respx = _load("respx")
    httpx = _load("httpx")
    # assert_all_called stays off: this helper does its own check so both
    # backends fail the same way, with the same message.
    with respx.mock(assert_all_called=False) as router:
        route = router.request(method, url).mock(
            return_value=httpx.Response(
                status, content=body, headers={"Content-Type": content_type}
            )
        )
        yield router, lambda: route.call_count


@contextmanager
def _responses_mock(
    method: str, url: str, status: int, content_type: str, body: bytes
) -> Iterator[tuple[Any, Callable[[], int]]]:
    responses = _load("responses")
    # RequestsMock asserts on __exit__ even when the block raised, which would
    # hide the client's own exception, the one the test exists to see. Drive
    # start/stop directly instead and keep the assertion here.
    mock = responses.RequestsMock(assert_all_requests_are_fired=False)
    mock.start()
    try:
        registered = mock.add(
            method=method, url=url, body=body, status=status, content_type=content_type
        )
        yield mock, lambda: registered.call_count
    finally:
        mock.stop(allow_assert=False)
        mock.reset()


_BACKEND_FACTORIES = {"respx": _respx_mock, "responses": _responses_mock}


@contextmanager
def mock_contract_response(
    url: str,
    payload: Any,
    case: Case | None = None,
    *,
    backend: str = "auto",
    method: str | None = None,
    status: int | str | None = None,
    content_type: str | None = None,
    assert_called: bool = True,
) -> Iterator[MockedResponse]:
    """Answer one request to ``url`` with ``payload``, as the contract declares it.

    Args:
        url: the URL the client under test will request. Path templates are
            not filled in for you: pass the concrete URL your client builds.
        payload: the generated variant, serialized to JSON as the body.
        case: the ``Case`` for this payload, from the ``contractfuzz_case``
            fixture. It supplies the method, the status code the contract
            attached to this response, and the declared media type.
        backend: ``"respx"`` (httpx clients), ``"responses"`` (requests
            clients), or ``"auto"`` when exactly one of them is installed.
        method: override the HTTP method. Default: the case's method, or GET.
        status: override the status code. Default: the status the contract
            declares for this response, or 200 without a case.
        content_type: override the response content type. Default: the media
            type the contract declares, or ``application/json``.
        assert_called: fail if the client never made the request. A test that
            silently skips the HTTP call proves nothing about the payload, so
            this is on by default; turn it off for a client that may
            legitimately answer from a cache.

    Raises:
        MockBackendError: the backend is missing or ambiguous, the case
            describes a request body rather than a response, or the contract
            declares a status code that is not a concrete number.
    """
    name = resolve_backend(backend)
    resolved_method = _resolve_method(case, method)
    resolved_status = _resolve_status(case, status)
    resolved_type = content_type or (case.media_type if case else None) or "application/json"
    body = json.dumps(payload).encode("utf-8")

    factory = _BACKEND_FACTORIES[name]
    with factory(resolved_method, url, resolved_status, resolved_type, body) as (mock, call_count):
        yield MockedResponse(
            backend=name,
            mock=mock,
            method=resolved_method,
            url=url,
            status=resolved_status,
            content_type=resolved_type,
            body=body,
        )
        if assert_called and call_count() == 0:
            what = case.description if case else "the mocked payload"
            raise AssertionError(
                f"contractfuzz: the client never requested {resolved_method} {url},"
                f" so it was never given the variant ({what})."
                " Check the URL, or pass assert_called=False."
            )
