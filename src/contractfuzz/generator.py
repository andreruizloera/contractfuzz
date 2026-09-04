"""Orchestrate baseline generation, mutation, validation, and fixture output.

Core principle: the generated data must remain valid according to the
OpenAPI contract. Every candidate mutation is validated against the resolved
schema with jsonschema before it is emitted; anything that fails validation
is discarded (and counted, so tests can assert the pipeline never produces
rejects for supported schemas).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contractfuzz.baseline import build_baseline
from contractfuzz.errors import UnsupportedSchemaError
from contractfuzz.mutations import Mutation, collect_mutations
from contractfuzz.spec import Schema, Target
from contractfuzz.validation import make_validator, validation_errors


@dataclass(frozen=True)
class Variant:
    """A schema-valid instance plus the machine-readable mutation that made it."""

    mutation: Mutation
    data: Any


@dataclass(frozen=True)
class TargetResult:
    target: Target
    baseline: Any
    variants: list[Variant]
    rejected: int  # candidates dropped because they failed contract validation


def generate_variants(schema: Schema) -> tuple[Any, list[Variant], int]:
    """Generate ``(baseline, variants, rejected_count)`` for one schema."""
    baseline = build_baseline(schema)
    errors = validation_errors(schema, baseline)
    if errors:
        raise UnsupportedSchemaError(
            "generated baseline does not satisfy the schema: " + "; ".join(errors[:3])
        )
    validator = make_validator(schema)

    variants: list[Variant] = []
    rejected = 0
    seen: set[str] = {json.dumps(baseline, sort_keys=True)}
    for mutation in collect_mutations(schema, baseline):
        data = mutation.apply(baseline)
        if not validator.is_valid(data):
            rejected += 1
            continue
        key = json.dumps(data, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        variants.append(Variant(mutation, data))
    return baseline, variants, rejected


def generate_for_target(target: Target) -> TargetResult:
    baseline, variants, rejected = generate_variants(target.schema)
    return TargetResult(target, baseline, variants, rejected)


def _slug(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return slug[:limit] or "variant"


def write_fixtures(
    out_dir: Path, spec_path: str, endpoint: str, results: list[TargetResult]
) -> dict[str, Any]:
    """Write per-variant JSON fixtures plus a manifest; return the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "tool": "contractfuzz",
        "spec": spec_path,
        "endpoint": endpoint,
        "targets": [],
    }
    for result in results:
        label = result.target.label
        baseline_file = f"{label}__baseline.json"
        (out_dir / baseline_file).write_text(
            json.dumps(result.baseline, indent=2) + "\n", encoding="utf-8"
        )
        entry: dict[str, Any] = {
            "method": result.target.method,
            "kind": result.target.kind,
            "status": result.target.status,
            "media_type": result.target.media_type,
            "baseline": baseline_file,
            "variants": [],
        }
        for i, variant in enumerate(result.variants, start=1):
            name = f"{label}__{i:03d}__{_slug(variant.mutation.description)}.json"
            (out_dir / name).write_text(json.dumps(variant.data, indent=2) + "\n", encoding="utf-8")
            entry["variants"].append({"file": name, "mutation": variant.mutation.record()})
        manifest["targets"].append(entry)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
