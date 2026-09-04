"""An intentionally fragile API client, used as the contractfuzz demo target.

``render_profile`` is the kind of code that passes every happy-path test:
it works perfectly on the fully-populated example response. But it bakes in
assumptions the OpenAPI contract never made:

- it assumes ``roles`` is non-empty (``roles[0]``), but the contract has no
  ``minItems``;
- it assumes ``age`` is always present, but ``age`` is optional;
- it assumes ``profileImage`` is a string, but the contract says nullable.

Run it against a directory of contractfuzz fixtures:

    python examples/fragile_client.py <fixtures-dir>

It replays every generated GET /users/{id} 200 response through the client
and reports which contract-valid payloads crash it. Exits 1 when any fixture
crashes the client (the demo's expected outcome).
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any


def render_profile(user: dict[str, Any]) -> str:
    """Naive rendering logic with undocumented assumptions baked in."""
    primary_role = user["roles"][0]  # assumes roles is never empty
    years_old = user["age"]  # assumes optional age is present
    avatar = user["profileImage"].lower()  # assumes nullable field is a string
    name = user["displayName"]
    return f"{name} ({years_old}) role={primary_role} avatar={avatar}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python examples/fragile_client.py <fixtures-dir>", file=sys.stderr)
        return 2
    fixtures_dir = Path(argv[1])
    manifest_path = fixtures_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"error: no manifest.json in {fixtures_dir}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    crashes: list[tuple[str, str, str]] = []
    replayed = 0
    for target in manifest["targets"]:
        if not (target["method"] == "GET" and target.get("status") == "200"):
            continue
        for entry in target["variants"]:
            payload = json.loads((fixtures_dir / entry["file"]).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            replayed += 1
            try:
                render_profile(payload)
            except Exception as exc:
                frame = traceback.extract_tb(exc.__traceback__)[-1]
                crashes.append(
                    (
                        entry["mutation"]["description"],
                        f"{type(exc).__name__}: {exc}",
                        f"line {frame.lineno}: {frame.line}",
                    )
                )

    print(f"Replayed {replayed} contract-valid responses through the client.")
    if not crashes:
        print("No crashes. The client survived every contract-valid payload.")
        return 0
    print(f"CRASHES: {len(crashes)} contract-valid payloads crashed the client:")
    for description, error, location in crashes:
        print(f"  - {description}")
        print(f"      {error}")
        print(f"      at {location}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
