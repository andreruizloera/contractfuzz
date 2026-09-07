"""What one generated test case is: a payload's provenance.

This lives apart from ``plugin.py`` because two consumers need it and only
one of them needs pytest. The plugin hands a ``Case`` to tests through the
``contractfuzz_case`` fixture; the mocking helpers read the same object to
decide what status code and content type the mocked response should carry.
Keeping the dataclass here means ``contractfuzz.mocking`` works in a project
that never installs pytest.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    """One payload's provenance: which target it came from, and what was done to it."""

    endpoint: str  # "/users/{id}"
    target: str  # "GET 200 response"
    method: str  # "GET"
    kind: str  # mutation kind, or "baseline"
    path: str  # JSONPath-style location, "$" for the baseline
    description: str  # "roles = []"
    danger: int  # 3 = very likely to break a naive client, 0 for the baseline
    status: str | None = None  # response status as written in the spec; None for request bodies
    media_type: str = "application/json"  # the content type the contract declares

    @property
    def is_baseline(self) -> bool:
        return self.kind == "baseline"
