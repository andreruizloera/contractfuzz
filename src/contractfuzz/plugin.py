"""pytest plugin: every contract-valid payload becomes its own test case.

``contractfuzz generate`` writes fixtures to a directory that you then have
to replay yourself, and that directory goes stale the moment the contract
changes. This plugin removes the round trip. Decorate a test with
``contract_variants`` and the payloads are generated in-process at
collection time, one test case per mutation, each named after the mutation
that produced it:

    from contractfuzz.plugin import contract_variants

    @contract_variants("openapi.yaml", "/users/{id}", method="get", status="200")
    def test_render_profile(payload):
        render_profile(payload)

Nothing is written to disk, so no fixture directory can drift away from the
spec, and a contract that grows a new optional field grows a new test case
on the next run without anyone regenerating anything.

When a case fails, the plugin reports it as what it is, an undocumented
assumption in the client: the mutation, its danger score, the exception,
and the line that raised it.

The plugin registers itself through the ``pytest11`` entry point, so
installing contractfuzz is enough. pytest is an optional dependency:
``pip install 'contractfuzz[pytest]'``.
"""

from __future__ import annotations

import contextlib
import linecache
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import pytest

from contractfuzz.cases import Case
from contractfuzz.errors import ContractfuzzError, SpecError
from contractfuzz.generator import TargetResult, generate_for_target, slugify
from contractfuzz.spec import find_targets, load_spec

CASE_MARKER = "contractfuzz_case"
ERROR_MARKER = "contractfuzz_error"

KIND_CHOICES = ("response", "request", "any")

F = TypeVar("F", bound=Callable[..., Any])

# `Case` is defined in contractfuzz.cases, which does not import pytest, so
# the mocking helpers can use it too. Re-exported here because this is where
# a plugin user expects to find it.
__all__ = ["Case", "contract_variants", "contractfuzz_case"]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

# Generating a spec's variants is the expensive part and several tests in a
# suite usually point at the same endpoint. The spec's size and mtime are in
# the key so editing a spec mid-session cannot serve a stale result.
_generation_cache: dict[tuple[Any, ...], list[TargetResult]] = {}


def _resolve_spec(spec: str | Path, func: Callable[..., Any]) -> Path:
    """Resolve a relative spec path against the directory of the test file."""
    path = Path(spec)
    if path.is_absolute():
        return path
    code = getattr(func, "__code__", None)
    base = Path(code.co_filename).resolve().parent if code else Path.cwd()
    return (base / path).resolve()


def _generate(spec_path: Path, endpoint: str, method: str | None) -> list[TargetResult]:
    if not spec_path.is_file():
        raise SpecError(f"spec file not found: {spec_path}")
    stat = spec_path.stat()
    key = (str(spec_path), endpoint, method, stat.st_mtime_ns, stat.st_size)
    cached = _generation_cache.get(key)
    if cached is None:
        spec = load_spec(spec_path)
        cached = [generate_for_target(t) for t in find_targets(spec, endpoint, method)]
        _generation_cache[key] = cached
    return cached


def _select(results: list[TargetResult], kind: str, status: str | None) -> list[TargetResult]:
    selected = []
    for result in results:
        if kind != "any" and result.target.kind != kind:
            continue
        if status is not None and result.target.status != status:
            continue
        selected.append(result)
    return selected


def _no_target_message(
    results: list[TargetResult], endpoint: str, kind: str, status: str | None
) -> str:
    wanted = f"{kind} schema" if status is None else f"{kind} schema with status {status}"
    available = ", ".join(r.target.display for r in results) or "none"
    return f"no {wanted} on {endpoint!r}; available targets: {available}"


# Mutations whose description is pure punctuation ("roles = []",
# 'displayName = ""') slug down to the bare field name, which names the field
# but not what happened to it. Say what happened.
_TERSE_KINDS = frozenset({"empty_array", "empty_string"})


def _case_id(record: dict[str, Any]) -> str:
    """The pytest id for one mutation: the fixture-file slug, made specific."""
    slug = slugify(record["description"])
    return f"{slug}_empty" if record["kind"] in _TERSE_KINDS else slug


def _unique_ids(ids: list[str]) -> list[str]:
    """Disambiguate repeated ids by suffixing, so pytest ids stay meaningful."""
    counts: dict[str, int] = {}
    out = []
    for raw in ids:
        counts[raw] = counts.get(raw, 0) + 1
        out.append(raw if counts[raw] == 1 else f"{raw}_{counts[raw]}")
    return out


def _build_params(
    results: list[TargetResult], endpoint: str, min_danger: int, include_baseline: bool
) -> list[Any]:
    # One target is the common case and its ids read best bare ("roles").
    # With several targets in play the label disambiguates them
    # ("get_200_response__roles" against "get_404_response__code").
    prefix = len(results) > 1
    rows: list[tuple[str, Any, Case]] = []
    for result in results:
        target = result.target
        target_rows: list[tuple[str, Any, Case]] = []
        if include_baseline:
            case = Case(
                endpoint=endpoint,
                target=target.display,
                method=target.method,
                kind="baseline",
                path="$",
                description="baseline (fully populated, no mutation)",
                danger=0,
                status=target.status,
                media_type=target.media_type,
            )
            target_rows.append(("baseline", result.baseline, case))
        for variant in result.variants:
            record = variant.mutation.record()
            if record["danger"] < min_danger:
                continue
            case = Case(
                endpoint=endpoint,
                target=target.display,
                method=target.method,
                kind=record["kind"],
                path=record["path"],
                description=record["description"],
                danger=record["danger"],
                status=target.status,
                media_type=target.media_type,
            )
            target_rows.append((_case_id(record), variant.data, case))
        if prefix:
            target_rows = [(f"{target.label}__{n}", d, c) for n, d, c in target_rows]
        rows.extend(target_rows)
    ids = _unique_ids([name for name, _, _ in rows])
    return [
        pytest.param(data, marks=pytest.mark.contractfuzz_case(case), id=test_id)
        for test_id, (_, data, case) in zip(ids, rows, strict=True)
    ]


def contract_variants(
    spec: str | Path,
    endpoint: str,
    *,
    method: str | None = None,
    kind: str = "response",
    status: str | int | None = None,
    min_danger: int = 1,
    include_baseline: bool = True,
    argname: str = "payload",
) -> Callable[[F], F]:
    """Parametrize a test with every contract-valid payload for one endpoint.

    Args:
        spec: path to an OpenAPI 3 document. A relative path is resolved
            against the directory holding the test file, not the working
            directory, so a suite runs the same from anywhere.
        endpoint: path template exactly as written in the spec.
        method: restrict to one HTTP method. Default: every method on the
            endpoint.
        kind: ``"response"`` (the default, since the tool exists to test
            clients), ``"request"``, or ``"any"``.
        status: restrict responses to one status code, e.g. ``"200"``.
        min_danger: drop variants below this danger score. 1 keeps
            everything, 3 keeps only the mutations most likely to break a
            naive client.
        include_baseline: also run the fully-populated happy-path payload.
            Keep this on: if the baseline fails, the edge-case failures are
            noise, and the summary says so.
        argname: the test argument that receives the payload.

    Any problem with the spec or the selection (file missing, unknown
    endpoint, no matching target) becomes a single test case that fails
    with a one-line message rather than a collection traceback.
    """
    if kind not in KIND_CHOICES:
        raise ValueError(f"kind must be one of {KIND_CHOICES}, got {kind!r}")
    if min_danger < 0:
        raise ValueError(f"min_danger must be >= 0, got {min_danger}")
    wanted_status = None if status is None else str(status)

    def decorate(func: F) -> F:
        try:
            results = _generate(_resolve_spec(spec, func), endpoint, method)
            selected = _select(results, kind, wanted_status)
            if not selected:
                raise SpecError(_no_target_message(results, endpoint, kind, wanted_status))
            params = _build_params(selected, endpoint, min_danger, include_baseline)
            if not params:
                raise SpecError(
                    f"no variants for {endpoint!r} at min_danger={min_danger};"
                    " lower min_danger or set include_baseline=True"
                )
        except ContractfuzzError as exc:
            params = [
                pytest.param(None, marks=pytest.mark.contractfuzz_error(str(exc)), id="error")
            ]
        return pytest.mark.parametrize(argname, params)(func)

    return decorate


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class Failure:
    case: Case
    error: str  # "IndexError: list index out of range"
    location: str  # "client.py:32  primary_role = user['roles'][0]"


@dataclass
class Report:
    ran: int = 0
    passed: int = 0
    failures: list[Failure] = field(default_factory=list)
    targets: set[str] = field(default_factory=set)

    @property
    def baseline_failed(self) -> bool:
        return any(f.case.is_baseline for f in self.failures)


REPORT_KEY = pytest.StashKey[Report]()


def _describe_failure(excinfo: pytest.ExceptionInfo[BaseException]) -> tuple[str, str]:
    lines = str(excinfo.value).strip().splitlines()
    first = lines[0].strip() if lines else ""
    if len(first) > 100:
        first = first[:97] + "..."
    error = f"{excinfo.typename}: {first}" if first else excinfo.typename

    location = ""
    with contextlib.suppress(Exception):
        entry = excinfo.traceback[-1]
        path = Path(str(entry.path))
        lineno = entry.lineno + 1  # TracebackEntry.lineno is 0-based
        location = f"{path.name}:{lineno}"
        source = linecache.getline(str(path), lineno).strip()
        if source:
            location = f"{location}  {source}"
    return error, location


DANGER_OPTION = "--contractfuzz-danger"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("contractfuzz")
    group.addoption(
        "--no-contractfuzz-summary",
        action="store_true",
        default=False,
        help="suppress the contractfuzz summary of undocumented assumptions",
    )
    group.addoption(
        DANGER_OPTION,
        type=int,
        default=None,
        metavar="N",
        help=(
            "run only generated cases with a danger score of N or more"
            " (1 keeps everything, 3 keeps the mutations most likely to break a"
            " naive client). The baseline case always runs. Lower-scored cases"
            " are deselected, not skipped."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{CASE_MARKER}(case): a contractfuzz-generated payload; set by contract_variants",
    )
    config.addinivalue_line(
        "markers",
        f"{ERROR_MARKER}(message): contract_variants could not generate payloads",
    )
    threshold = config.getoption(DANGER_OPTION)
    if threshold is not None and threshold < 0:
        raise pytest.UsageError(f"{DANGER_OPTION} must be 0 or more, got {threshold}")
    config.stash[REPORT_KEY] = Report()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Narrow the whole run to the most dangerous mutations, no decorator edits.

    The baseline case is kept whatever the threshold. It is the control: if
    the happy-path payload fails, the edge-case failures mean nothing, and
    that is worth one test case at any danger setting.
    """
    threshold = config.getoption(DANGER_OPTION)
    if threshold is None:
        return
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        marker = item.get_closest_marker(CASE_MARKER)
        case: Case | None = marker.args[0] if marker else None
        if case is None or case.is_baseline or case.danger >= threshold:
            kept.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> None:
    """Turn a generation failure into a clean test failure, not a traceback."""
    marker = pyfuncitem.get_closest_marker(ERROR_MARKER)
    if marker is not None:
        pytest.fail(f"contractfuzz: {marker.args[0]}", pytrace=False)
    return None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    if call.when != "call":
        return report
    marker = item.get_closest_marker(CASE_MARKER)
    if marker is None or report.skipped:
        return report
    tally = item.config.stash.get(REPORT_KEY, None)
    if tally is None:
        return report
    case: Case = marker.args[0]
    tally.ran += 1
    tally.targets.add(case.target)
    if report.passed:
        tally.passed += 1
    elif call.excinfo is not None:
        error, location = _describe_failure(call.excinfo)
        tally.failures.append(Failure(case, error, location))
    return report


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    tally = config.stash.get(REPORT_KEY, None)
    if tally is None or tally.ran == 0 or config.getoption("--no-contractfuzz-summary"):
        return

    write = terminalreporter.write_line
    terminalreporter.write_sep("=", "contractfuzz")
    if not tally.failures:
        write(f"{tally.ran} contract-valid payloads exercised, all handled.")
        return

    write(f"{tally.ran} contract-valid payloads exercised, {len(tally.failures)} broke the client.")
    if tally.baseline_failed:
        write("")
        write(
            "warning: the fully-populated baseline payload failed too, so these"
            " failures are not specific to the generated edge cases."
        )
        write("Fix the happy path first; until then the results below mean little.")
    write("")
    write("Undocumented assumptions, most dangerous first:")
    # Name the target on each line only when the run covered more than one,
    # so a single-endpoint suite is not made to repeat itself.
    show_target = len(tally.targets) > 1
    for failure in sorted(tally.failures, key=lambda f: -f.case.danger):
        case = failure.case
        headline = case.description if not show_target else f"{case.target}: {case.description}"
        write(f"  [danger {case.danger}] {headline}")
        write(f"      {failure.error}")
        if failure.location:
            write(f"      at {failure.location}")


@pytest.fixture
def contractfuzz_case(request: pytest.FixtureRequest) -> Case:
    """The ``Case`` describing the payload the current test was handed."""
    marker = request.node.get_closest_marker(CASE_MARKER)
    if marker is None:
        pytest.fail(
            "the contractfuzz_case fixture is only available on tests"
            " parametrized by contract_variants",
            pytrace=False,
        )
    return marker.args[0]
