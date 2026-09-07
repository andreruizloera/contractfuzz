"""Tests for the pytest plugin, run through `pytester` (pytest inside pytest).

Each test writes a small suite the way a user would write one, runs it, and
asserts on the outcomes, the test ids, and the summary the plugin prints.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from contractfuzz.plugin import (
    _case_id,
    _generate,
    _generation_cache,
    _unique_ids,
    contract_variants,
)

EXAMPLE_SPEC = Path(__file__).resolve().parent.parent / "examples" / "openapi.yaml"
SPEC = str(EXAMPLE_SPEC)


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    """Run the inner suite.

    No `-p contractfuzz.plugin`: the inner run picks the plugin up from the
    `pytest11` entry point, which is exactly what a user who installs
    contractfuzz gets. Passing it explicitly would hide a broken entry
    point.
    """
    return pytester.runpytest(*args)


_VERBOSE_LINE = re.compile(r"::\w+\[(?P<id>[^\]]+)\] (PASSED|FAILED|SKIPPED|ERROR)")


def ids_from(result: pytest.RunResult) -> set[str]:
    """The parametrization ids pytest reported, pulled out of -v output."""
    return {m.group("id") for m in map(_VERBOSE_LINE.search, result.outlines) if m}


# ---------------------------------------------------------------------------
# The core: one test case per contract-valid payload
# ---------------------------------------------------------------------------


def test_every_variant_becomes_its_own_passing_test_case(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_tolerant_client(payload):
            # A client that assumes nothing beyond the contract.
            roles = payload.get("roles", [])
            assert isinstance(roles, list)
        """
    )
    result = run(pytester, "-v")
    # 22 variants for this target plus the baseline; asserted exactly so a
    # generation regression shows up here.
    result.assert_outcomes(passed=23)


def test_test_ids_name_the_mutation_that_produced_the_payload(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_anything(payload):
            pass
        """
    )
    ids = ids_from(run(pytester, "-v"))
    assert {
        "baseline",
        "roles_empty",  # roles = []
        "age_omitted",
        "profileimage_null",
        "profileimage_omitted",
        "age_150_maximum",
        "address_street_omitted",
        "status_inactive_enum_alternative",
    } <= ids


def test_case_id_says_what_happened_not_just_where() -> None:
    # The description alone is enough for most kinds, and the slug matches
    # the one `generate` uses for fixture filenames.
    assert _case_id({"kind": "omit_optional", "description": "age omitted"}) == "age_omitted"
    assert _case_id({"kind": "set_null", "description": "profileImage = null"}) == (
        "profileimage_null"
    )
    # These two are pure punctuation and would slug down to the bare field
    # name, so the kind is spelled out instead.
    assert _case_id({"kind": "empty_array", "description": "roles = []"}) == "roles_empty"
    assert _case_id({"kind": "empty_string", "description": 'displayName = ""'}) == (
        "displayname_empty"
    )


def test_unique_ids_disambiguates_repeats() -> None:
    assert _unique_ids(["roles", "age", "roles", "roles"]) == ["roles", "age", "roles_2", "roles_3"]
    assert _unique_ids([]) == []


def test_failing_case_is_reported_under_the_mutation_id(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_fragile(payload):
            payload["roles"][0]      # assumes roles is never empty
            payload["profileImage"].lower()   # assumes nullable field is a string
        """
    )
    result = run(pytester, "-v")
    failed = {
        m.group("id")
        for m in map(_VERBOSE_LINE.search, result.outlines)
        if m and m.group(2) == "FAILED"
    }
    assert failed == {"roles_empty", "profileimage_null", "profileimage_omitted"}
    assert "baseline" in ids_from(result)


def test_every_generated_payload_validates_against_the_contract(
    pytester: pytest.Pytester,
) -> None:
    """The core guarantee, enforced through the plugin's own code path."""
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants
        from contractfuzz.spec import find_targets, load_spec
        from contractfuzz.validation import validation_errors

        SCHEMA = next(
            t for t in find_targets(load_spec({SPEC!r}), "/users/{{id}}", "get")
            if t.status == "200"
        ).schema

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_valid(payload):
            assert validation_errors(SCHEMA, payload) == []
        """
    )
    run(pytester).assert_outcomes(passed=23)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_status_selects_one_response(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status=404)
        def test_error_shape(payload):
            assert set(payload) <= {{"code", "message", "x_contractfuzz_extra"}}
        """
    )
    # The 404 Error schema yields 3 variants plus the baseline. `status=404`
    # is passed as an int here on purpose: it is coerced.
    run(pytester).assert_outcomes(passed=4)


def test_kind_request_selects_the_request_body(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="patch", kind="request")
        def test_update_body(payload, contractfuzz_case):
            assert contractfuzz_case.method == "PATCH"
            # UserUpdate sets additionalProperties: false, so nothing extra.
            assert set(payload) <= {{"displayName", "age", "status"}}
        """
    )
    result = run(pytester)
    result.assert_outcomes(passed=8)


def test_several_targets_get_label_prefixed_ids(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", kind="any")
        def test_both_responses(payload):
            pass
        """
    )
    ids = ids_from(run(pytester, "-v"))
    assert {
        "get_200_response__baseline",
        "get_404_response__baseline",
        "get_200_response__roles_empty",
        "get_404_response__code_empty",
    } <= ids


def test_min_danger_drops_the_low_danger_variants(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants(
            {SPEC!r}, "/users/{{id}}", method="get", status="200",
            min_danger=3, include_baseline=False,
        )
        def test_only_dangerous(payload, contractfuzz_case):
            assert contractfuzz_case.danger >= 3
            assert contractfuzz_case.kind in {{"omit_optional", "set_null", "empty_array"}}
        """
    )
    result = run(pytester)
    result.assert_outcomes(passed=7)


def test_include_baseline_false_removes_the_happy_path_case(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants(
            {SPEC!r}, "/users/{{id}}", method="get", status="200", include_baseline=False
        )
        def test_no_baseline(payload, contractfuzz_case):
            assert not contractfuzz_case.is_baseline
        """
    )
    run(pytester).assert_outcomes(passed=22)


def test_argname_can_be_renamed(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants(
            {SPEC!r}, "/users/{{id}}", method="get", status="404", argname="body"
        )
        def test_named_body(body):
            assert "code" in body or "message" in body
        """
    )
    run(pytester).assert_outcomes(passed=4)


def test_relative_spec_path_resolves_next_to_the_test_file(pytester: pytest.Pytester) -> None:
    shutil.copy(EXAMPLE_SPEC, pytester.path / "openapi.yaml")
    sub = pytester.mkdir("suite")
    shutil.copy(EXAMPLE_SPEC, sub / "contract.yaml")
    (sub / "test_relative.py").write_text(
        "from contractfuzz.plugin import contract_variants\n"
        "\n"
        "\n"
        '@contract_variants("contract.yaml", "/users/{id}", method="get", status="404")\n'
        "def test_relative(payload):\n"
        "    assert isinstance(payload, dict)\n",
        encoding="utf-8",
    )
    # Run from the rootdir, not from suite/: resolution is relative to the
    # test file, so the working directory must not matter.
    run(pytester).assert_outcomes(passed=4)


def test_invalid_kind_and_min_danger_raise_immediately() -> None:
    with pytest.raises(ValueError, match="kind must be one of"):
        contract_variants(SPEC, "/users/{id}", kind="responses")
    with pytest.raises(ValueError, match="min_danger must be"):
        contract_variants(SPEC, "/users/{id}", min_danger=-1)


# ---------------------------------------------------------------------------
# --contractfuzz-danger: narrow a whole run without editing decorators
# ---------------------------------------------------------------------------


def danger_suite(pytester: pytest.Pytester) -> None:
    """A suite with one decorated test and one ordinary test."""
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_generated(payload):
            pass

        def test_ordinary():
            assert True
        """
    )


def test_danger_option_keeps_only_the_dangerous_cases(pytester: pytest.Pytester) -> None:
    danger_suite(pytester)
    # 23 generated cases: the baseline, 7 at danger 3, and 15 below it. The
    # ordinary test is untouched, which is why this is 9 and not 8.
    result = run(pytester, "--contractfuzz-danger=3", "-v")
    result.assert_outcomes(passed=9, deselected=15)
    ids = ids_from(result)
    assert {"baseline", "roles_empty", "age_omitted", "profileimage_null"} <= ids
    assert not {"email_empty", "age_150_maximum", "status_inactive_enum_alternative"} & ids


def test_without_the_option_nothing_is_deselected(pytester: pytest.Pytester) -> None:
    danger_suite(pytester)
    run(pytester).assert_outcomes(passed=24, deselected=0, failed=0)


def test_the_baseline_survives_any_threshold(pytester: pytest.Pytester) -> None:
    """The control case runs whatever the danger setting: it is what makes
    the other failures readable."""
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_generated(payload, contractfuzz_case):
            assert contractfuzz_case.is_baseline
        """
    )
    result = run(pytester, "--contractfuzz-danger=99", "-v")
    result.assert_outcomes(passed=1, deselected=22)
    assert "baseline" in ids_from(result)


def test_the_option_raises_the_floor_but_cannot_lower_it(pytester: pytest.Pytester) -> None:
    """A decorator that already asked for danger 3 does not get cases back."""
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants(
            {SPEC!r}, "/users/{{id}}", method="get", status="200",
            min_danger=3, include_baseline=False,
        )
        def test_generated(payload):
            pass
        """
    )
    run(pytester, "--contractfuzz-danger=1").assert_outcomes(passed=7, deselected=0)


def test_the_summary_counts_only_what_ran(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_generated(payload):
            payload["roles"][0]
        """
    )
    result = run(pytester, "--contractfuzz-danger=3")
    result.stdout.fnmatch_lines(["*8 contract-valid payloads exercised, 1 broke the client.*"])


def test_a_negative_threshold_is_a_clean_usage_error(pytester: pytest.Pytester) -> None:
    danger_suite(pytester)
    result = run(pytester, "--contractfuzz-danger=-1")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--contractfuzz-danger must be 0 or more, got -1*"])
    assert "Traceback" not in result.stderr.str()


def test_a_non_numeric_threshold_is_a_clean_usage_error(pytester: pytest.Pytester) -> None:
    danger_suite(pytester)
    result = run(pytester, "--contractfuzz-danger=high")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*invalid int value: 'high'*"])


# ---------------------------------------------------------------------------
# Clean failures instead of collection tracebacks
# ---------------------------------------------------------------------------


def test_missing_spec_fails_with_one_clean_line(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        from contractfuzz.plugin import contract_variants

        @contract_variants("/no/such/spec.yaml", "/users/{id}")
        def test_never_runs(payload):
            raise AssertionError("the test body must not run")
        """
    )
    result = run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*contractfuzz: spec file not found: /no/such/spec.yaml*"])
    assert "Traceback" not in result.stdout.str()


def test_unknown_endpoint_fails_cleanly_and_lists_what_exists(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/nope")
        def test_never_runs(payload):
            raise AssertionError("the test body must not run")
        """
    )
    result = run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*contractfuzz: endpoint '/nope' not found*Available:*"])


def test_no_matching_target_fails_cleanly_and_names_the_alternatives(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", kind="request")
        def test_never_runs(payload):
            raise AssertionError("the test body must not run")
        """
    )
    result = run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*contractfuzz: no request schema on '/users/{id}'*GET 200 response*"]
    )


def test_min_danger_above_every_variant_fails_cleanly(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants(
            {SPEC!r}, "/users/{{id}}", method="get", status="404",
            min_danger=9, include_baseline=False,
        )
        def test_never_runs(payload):
            raise AssertionError("the test body must not run")
        """
    )
    result = run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*contractfuzz: no variants*min_danger=9*"])


def test_contractfuzz_case_fixture_outside_a_generated_test_fails_cleanly(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        def test_borrowed(contractfuzz_case):
            pass
        """
    )
    result = run(pytester)
    # A missing fixture prerequisite is a setup error, which is what pytest
    # calls this. What matters is that the message is one clean line.
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*only available on tests parametrized by contract_variants*"])
    assert "Traceback" not in result.stdout.str()


# ---------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------


def test_summary_reports_each_undocumented_assumption(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="200")
        def test_fragile(payload):
            primary_role = payload["roles"][0]
        """
    )
    out = run(pytester).stdout.str()
    assert "23 contract-valid payloads exercised, 1 broke the client." in out
    assert "Undocumented assumptions, most dangerous first:" in out
    assert "[danger 3] roles = []" in out
    assert "IndexError: list index out of range" in out
    # The reported location is the client line that raised, source included.
    assert 'primary_role = payload["roles"][0]' in out
    assert "at test_summary_reports_each_undocumented_assumption.py:" in out


def test_summary_says_so_when_everything_is_handled(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="404")
        def test_tolerant(payload):
            pass
        """
    )
    result = run(pytester)
    result.stdout.fnmatch_lines(["4 contract-valid payloads exercised, all handled."])


def test_summary_warns_when_the_baseline_fails_too(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="404")
        def test_broken_test(payload):
            raise RuntimeError("the test itself is wrong")
        """
    )
    result = run(pytester)
    result.stdout.fnmatch_lines(
        [
            "*4 contract-valid payloads exercised, 4 broke the client.*",
            "*warning: the fully-populated baseline payload failed too*",
            "*Fix the happy path first*",
        ]
    )


def test_summary_names_the_target_when_several_are_in_play(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", kind="any")
        def test_needs_roles(payload):
            assert "roles" in payload
        """
    )
    out = run(pytester).stdout.str()
    assert "[danger 0] GET 404 response: baseline" in out
    assert "GET 200 response:" not in out  # that target passed, so it is not listed


def test_skipped_cases_are_not_counted(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        import pytest
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="404")
        def test_skips_the_empties(payload, contractfuzz_case):
            if contractfuzz_case.kind == "empty_string":
                pytest.skip("empty strings are accepted here by design")
        """
    )
    result = run(pytester)
    result.assert_outcomes(passed=2, skipped=2)
    result.stdout.fnmatch_lines(["2 contract-valid payloads exercised, all handled."])


def test_summary_can_be_suppressed(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="404")
        def test_tolerant(payload):
            pass
        """
    )
    result = run(pytester, "--no-contractfuzz-summary")
    result.assert_outcomes(passed=4)
    assert "contract-valid payloads exercised" not in result.stdout.str()


def test_plugin_is_silent_for_suites_that_do_not_use_it(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_unrelated():
            assert True
        """
    )
    result = run(pytester)
    result.assert_outcomes(passed=1)
    out = result.stdout.str()
    # The plugins banner names every installed plugin; what must not appear
    # is a summary section for a suite that generated nothing.
    assert "contract-valid payloads exercised" not in out
    assert "Undocumented assumptions" not in out


# ---------------------------------------------------------------------------
# Installation path
# ---------------------------------------------------------------------------


def test_entry_point_autoloads_the_plugin_in_a_fresh_process(
    pytester: pytest.Pytester,
) -> None:
    """A user who installs contractfuzz gets the plugin with no config at all."""
    pytester.makepyfile(
        f"""
        from contractfuzz.plugin import contract_variants

        @contract_variants({SPEC!r}, "/users/{{id}}", method="get", status="404")
        def test_autoloaded(payload):
            pass
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["4 contract-valid payloads exercised, all handled."])


def test_generation_is_cached_across_decorated_tests(pytester: pytest.Pytester) -> None:
    """Two tests on the same endpoint must not pay for generation twice."""
    spec_copy = pytester.path / "openapi.yaml"
    shutil.copy(EXAMPLE_SPEC, spec_copy)
    assert spec_copy.is_file()
    pytester.makepyfile(
        """
        from contractfuzz.plugin import _generation_cache, contract_variants

        BEFORE = len(_generation_cache)

        @contract_variants("openapi.yaml", "/users/{id}", method="get", status="404")
        def test_first(payload):
            pass

        @contract_variants("openapi.yaml", "/users/{id}", method="get", status="404")
        def test_second(payload):
            pass

        def test_only_one_generation_happened():
            assert len(_generation_cache) == BEFORE + 1
        """
    )
    run(pytester).assert_outcomes(passed=9)


def test_editing_the_spec_invalidates_the_cache(tmp_path: Path) -> None:
    """A cache keyed only on the path would serve stale variants after an edit."""
    spec = tmp_path / "openapi.yaml"
    shutil.copy(EXAMPLE_SPEC, spec)

    before = _generate(spec, "/users/{id}", "get")[0].variants
    assert any("5 items" in v.mutation.description for v in before)
    cached = len(_generation_cache)

    spec.write_text(
        spec.read_text(encoding="utf-8").replace("maxItems: 5", "maxItems: 4"), encoding="utf-8"
    )
    # Force a distinct mtime rather than relying on filesystem clock
    # resolution, which is coarser than this test on some filesystems.
    stat = spec.stat()
    os.utime(spec, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    after = _generate(spec, "/users/{id}", "get")[0].variants
    assert any("4 items" in v.mutation.description for v in after)
    assert not any("5 items" in v.mutation.description for v in after)
    assert len(_generation_cache) == cached + 1
