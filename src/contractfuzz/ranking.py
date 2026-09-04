"""Rank generated variants by how likely they are to break a naive client."""

from __future__ import annotations

from contractfuzz.generator import Variant


def dangerous_variants(variants: list[Variant], threshold: int = 2) -> list[Variant]:
    """Variants at or above ``threshold`` danger, most dangerous first, stable order."""
    ranked = [v for v in variants if v.mutation.danger >= threshold]
    ranked.sort(key=lambda v: -v.mutation.danger)
    return ranked


def render_summary(display: str, variants: list[Variant], limit: int = 12) -> str:
    """Render the per-target summary block printed by the CLI."""
    lines = [f"{display}: {len(variants)} valid edge-case variants"]
    ranked = dangerous_variants(variants)
    if ranked:
        lines.append("  Potentially dangerous variants:")
        for i, variant in enumerate(ranked[:limit], start=1):
            lines.append(f"  {i:2d}. {variant.mutation.description}")
        if len(ranked) > limit:
            lines.append(f"      ... and {len(ranked) - limit} more (see manifest.json)")
    return "\n".join(lines)
