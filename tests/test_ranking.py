from __future__ import annotations

from contractfuzz.generator import Variant
from contractfuzz.mutations import Mutation
from contractfuzz.ranking import dangerous_variants, render_summary


def _variant(kind: str, description: str) -> Variant:
    return Variant(Mutation(kind, ("x",), description, None), {"x": None})


def test_dangerous_variants_filters_and_sorts() -> None:
    variants = [
        _variant("enum_alternative", "low"),
        _variant("empty_string", "medium"),
        _variant("omit_optional", "high"),
        _variant("set_null", "high2"),
    ]
    ranked = dangerous_variants(variants)
    assert [v.mutation.description for v in ranked] == ["high", "high2", "medium"]


def test_dangerous_variants_threshold() -> None:
    variants = [_variant("enum_alternative", "low"), _variant("set_null", "high")]
    assert len(dangerous_variants(variants, threshold=1)) == 2
    assert len(dangerous_variants(variants, threshold=3)) == 1


def test_render_summary_numbers_dangerous_variants() -> None:
    variants = [
        _variant("omit_optional", "age omitted"),
        _variant("set_null", "profileImage = null"),
        _variant("empty_array", "roles = []"),
        _variant("empty_string", 'displayName = ""'),
        _variant("enum_alternative", "status = 'inactive'"),
    ]
    text = render_summary("GET 200 response", variants)
    assert "GET 200 response: 5 valid edge-case variants" in text
    assert "   1. age omitted" in text
    assert "   2. profileImage = null" in text
    assert "   3. roles = []" in text
    assert '   4. displayName = ""' in text
    assert "status" not in text  # danger 1 stays out of the headline list


def test_render_summary_truncates_long_lists() -> None:
    variants = [_variant("omit_optional", f"field{i} omitted") for i in range(20)]
    text = render_summary("GET 200 response", variants, limit=12)
    assert "... and 8 more" in text


def test_render_summary_without_dangerous_variants() -> None:
    text = render_summary("GET 200 response", [_variant("enum_alternative", "low")])
    assert "Potentially dangerous" not in text
