from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from contractfuzz.spec import Target, find_targets, load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SPEC = REPO_ROOT / "examples" / "openapi.yaml"


@pytest.fixture(scope="session")
def example_spec_path() -> Path:
    return EXAMPLE_SPEC


@pytest.fixture(scope="session")
def example_spec() -> dict[str, Any]:
    return load_spec(EXAMPLE_SPEC)


@pytest.fixture(scope="session")
def user_target(example_spec: dict[str, Any]) -> Target:
    targets = find_targets(example_spec, "/users/{id}", "get")
    return next(t for t in targets if t.kind == "response" and t.status == "200")
