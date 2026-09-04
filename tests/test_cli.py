from __future__ import annotations

import json
from pathlib import Path

import pytest

from contractfuzz.cli import main


def test_generate_writes_fixtures_and_manifest(
    tmp_path: Path, example_spec_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "fixtures"
    code = main(
        ["generate", str(example_spec_path), "--endpoint", "/users/{id}", "--out", str(out)]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "Generated" in captured.out
    assert "valid edge-case fixtures" in captured.out
    assert "Potentially dangerous variants:" in captured.out
    assert "roles = []" in captured.out

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["endpoint"] == "/users/{id}"
    files = {f.name for f in out.iterdir()}
    for target in manifest["targets"]:
        assert target["baseline"] in files
        for entry in target["variants"]:
            assert entry["file"] in files
            record = entry["mutation"]
            assert set(record) == {"kind", "path", "description", "danger"}


def test_generate_fixture_count_matches_summary(
    tmp_path: Path, example_spec_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "fixtures"
    main(["generate", str(example_spec_path), "--endpoint", "/users/{id}", "--out", str(out)])
    captured = capsys.readouterr()
    total = int(captured.out.split("Generated ")[1].split(" ")[0])
    manifest = json.loads((out / "manifest.json").read_text())
    assert total == sum(len(t["variants"]) for t in manifest["targets"])


def test_generate_method_and_responses_only(
    tmp_path: Path, example_spec_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "fixtures"
    code = main(
        [
            "generate",
            str(example_spec_path),
            "--endpoint",
            "/users/{id}",
            "--method",
            "patch",
            "--responses-only",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert [t["kind"] for t in manifest["targets"]] == ["response"]
    assert [t["method"] for t in manifest["targets"]] == ["PATCH"]


def test_generate_unknown_endpoint_fails_cleanly(
    example_spec_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["generate", str(example_spec_path), "--endpoint", "/nope"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error:")
    assert "Available" in captured.err


def test_generate_missing_spec_fails_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["generate", "/no/such/spec.yaml", "--endpoint", "/users"])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_list_command(example_spec_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["list", str(example_spec_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "/users/{id}  [GET, PATCH]" in out


def test_proxy_rejects_bad_upstream(
    example_spec_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["proxy", str(example_spec_path), "--upstream", "not-a-url"])
    assert code == 2
    assert "upstream" in capsys.readouterr().err
