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

The URL may keep the spec's path parameters. ``https://api.example.com/users/{id}``
answers ``/users/7`` and ``/users/8`` alike, so ``BASE_URL +
contractfuzz_case.endpoint`` is a valid first argument and the test does not
have to know which id the client builds.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from contractfuzz.cases import Case
from contractfuzz.errors import MockBackendError
from contractfuzz.spec import HTTP_METHODS

BACKENDS = ("respx", "responses")

_MOCKS = {"respx": "httpx clients", "responses": "requests clients"}

_METHODS = frozenset(m.upper() for m in HTTP_METHODS)

# A path parameter as an OpenAPI path writes it: one name between braces.
_PLACEHOLDER = re.compile(r"\{[^{}/?#]+\}")

# What a path parameter's value can be in a request URL: one path segment, or
# part of one. OpenAPI's default `simple` style never puts a raw `/` in it.
_SEGMENT = "[^/?#]+"


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


def url_pattern(url: str) -> re.Pattern[str] | None:
    """Compile a URL that keeps its path parameters, such as ``.../users/{id}``.

    Returns None when ``url`` has no ``{name}`` placeholder: a concrete URL is
    registered with the backend exactly as it is. Otherwise each placeholder
    matches one path segment or part of one, so ``/users/{id}`` matches
    ``/users/7`` and not ``/users/7/posts``, ``/users/`` or ``/v2/users/7``.
    Scheme and host compare case-insensitively, and a query string the client
    adds is accepted, which is what both backends already do for a concrete
    URL registered without one.

    The pattern is anchored at both ends because the backends apply it
    differently: responses calls ``match`` and respx calls ``search``, and an
    unanchored ``search`` would let a mocked route answer a longer path.

    Raises:
        MockBackendError: the template has no scheme and host, has a
            placeholder outside the path, or carries its own query string or
            fragment. Each of those would need a guess about what to match.
    """
    if not _PLACEHOLDER.search(url):
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise MockBackendError(
            f"{url!r} is a path template with no scheme and host; pass the full URL"
            " the client requests, such as 'https://api.example.com/users/{id}'"
        )
    if _PLACEHOLDER.search(parts.netloc):
        raise MockBackendError(
            f"{url!r} has a placeholder in the host; only path parameters are"
            " filled in, so write the host out"
        )
    if parts.query or parts.fragment:
        raise MockBackendError(
            f"{url!r} is a path template with a query string or fragment; a template"
            " already accepts any query string the client sends, so leave it off"
        )
    origin = re.escape(f"{parts.scheme}://{parts.netloc}")
    path = _SEGMENT.join(re.escape(piece) for piece in _PLACEHOLDER.split(parts.path))
    return re.compile(rf"\A(?i:{origin}){path}(?:\?.*)?\Z")


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
    url: str  # as passed, placeholders included
    status: int  # 200, 404, ... as declared by the contract
    content_type: str  # "application/json", "application/vnd.api+json", ...
    body: bytes  # the serialized variant
    url_pattern: re.Pattern[str] | None = None  # what a templated url compiled to


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
    method: str, url: str | re.Pattern[str], status: int, content_type: str, body: bytes
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
    method: str, url: str | re.Pattern[str], status: int, content_type: str, body: bytes
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
    """Answer requests to ``url`` with ``payload``, as the contract declares it.

    Args:
        url: the URL the client under test will request. Either concrete
            (``https://api.example.com/users/7``) or with the path parameters
            left as the spec writes them (``https://api.example.com/users/{id}``),
            which answers any value in each placeholder's place; see
            ``url_pattern`` for exactly what a template matches.
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
            describes a request body rather than a response, the contract
            declares a status code that is not a concrete number, or ``url``
            is a template that cannot be matched without guessing.
    """
    name = resolve_backend(backend)
    resolved_method = _resolve_method(case, method)
    resolved_status = _resolve_status(case, status)
    resolved_type = content_type or (case.media_type if case else None) or "application/json"
    pattern = url_pattern(url)
    body = json.dumps(payload).encode("utf-8")

    factory = _BACKEND_FACTORIES[name]
    registered = url if pattern is None else pattern
    with factory(resolved_method, registered, resolved_status, resolved_type, body) as (
        mock,
        call_count,
    ):
        yield MockedResponse(
            backend=name,
            mock=mock,
            method=resolved_method,
            url=url,
            status=resolved_status,
            content_type=resolved_type,
            body=body,
            url_pattern=pattern,
        )
        if assert_called and call_count() == 0:
            what = case.description if case else "the mocked payload"
            raise AssertionError(
                f"contractfuzz: the client never requested {resolved_method} {url},"
                f" so it was never given the variant ({what})."
                " Check the URL, or pass assert_called=False."
            )
