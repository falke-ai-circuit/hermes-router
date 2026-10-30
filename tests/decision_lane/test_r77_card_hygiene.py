"""R77 — read-lane card hygiene (frontmatter, clause boundary, doctrine).

Measured: four profiles' read cards were 1274-1275 chars, one net character
apart, because the IDENTITY head kept its YAML frontmatter (metadata) and was
then cut mid-word at a fixed 1200 chars.
"""
from __future__ import annotations

from hermes_router import persona_card as pc


def test_frontmatter_is_not_persona():
    raw = ("---\nrole: coder\nlast_reflect: 2026-09-07\nreflect_count: 54\n"
           "template_version: v1.1\n---\n\n**Coder — precise.** Conservation first.\n")
    out = pc._strip_frontmatter(raw)
    assert "template_version" not in out and "reflect_count" not in out
    assert "Conservation first" in out


def test_truncation_lands_on_a_clause_never_mid_word():
    text = "First sentence here. Second sentence is the one that overflows the budget"
    out = pc._truncate_clause(text, 30)
    assert out.endswith("…")
    assert "Second sentence" not in out
    # never a mid-word cut: the last word of the kept text is whole
    assert out[:-2].rstrip()[-1:].isalnum() or out[:-2].rstrip()[-1:] in ".)"


def test_short_text_is_untouched_by_truncation():
    assert pc._truncate_clause("short prose", 100) == "short prose"


def test_doctrine_lift_is_role_specific_and_bounded():
    import tempfile, os
    with tempfile.TemporaryDirectory() as home:
        open(os.path.join(home, "SOUL.md"), "w").write(
            "# SOUL\n\n## Hard Rules\n\n| Rule | Detail |\n|---|---|\n"
            "| Understand first | Never edit a file you have not read. |\n"
            "| Verify | Ship only what you have run. |\n"
            "| Storage | Keep the cache warm. |\n"
            "| Notes | Assorted remarks. |\n\n## Other\n\nignored\n")
        out = pc._doctrine_constraints(home, 520, 3)
    assert out.startswith("- ")
    assert "Understand first" in out and "Verify" in out
    # three rows max: the unmarked trailing row loses the marker ranking
    assert out.count("\n- ") == 2
    assert "Notes" not in out


def test_read_ceiling_is_bounded():
    assert pc.PERSONA_READ_CARD_MAX == 1200
