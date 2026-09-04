"""The demo scenario: contract-valid fixtures crash the fragile example client."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from contractfuzz.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT = REPO_ROOT / "examples" / "fragile_client.py"


def _generate(tmp_path: Path, spec: Path) -> Path:
    out = tmp_path / "fixtures"
    assert main(["generate", str(spec), "--endpoint", "/users/{id}", "--out", str(out)]) == 0
    return out


def test_fragile_client_crashes_on_contract_valid_fixtures(
    tmp_path: Path, example_spec_path: Path
) -> None:
    out = _generate(tmp_path, example_spec_path)
    result = subprocess.run(
        [sys.executable, str(CLIENT), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "CRASHES:" in result.stdout
    assert "roles = []" in result.stdout
    assert "IndexError" in result.stdout
    assert "age omitted" in result.stdout
    assert "KeyError" in result.stdout
    assert "profileImage = null" in result.stdout
    assert "AttributeError" in result.stdout


def test_fragile_client_survives_the_baseline(tmp_path: Path, example_spec_path: Path) -> None:
    # The happy-path payload works, which is exactly why these bugs ship.
    import json

    sys.path.insert(0, str(CLIENT.parent))
    try:
        from fragile_client import render_profile
    finally:
        sys.path.pop(0)
    out = _generate(tmp_path, example_spec_path)
    baseline = json.loads((out / "get_200_response__baseline.json").read_text())
    assert render_profile(baseline)


def test_fragile_client_needs_a_manifest(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(CLIENT), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "manifest.json" in result.stderr
