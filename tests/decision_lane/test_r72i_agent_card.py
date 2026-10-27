"""R72i — the reading knows which role has to live with the step.

Without a card the decision model scored moves as if every agent were interchangeable, so
"read X first" was judged the same for a coder mid-repair and a reviewer mid-audit. The card
is prose from the profile's own IDENTITY, never its frontmatter.
"""
from __future__ import annotations

import os

from hermes_router import reflex_lane as rl


def test_state_carries_the_role_card_first():
    state = rl.build_state("fix the renderer", "patch", "ImportError: no name cache",
                           card="role: coder; repairs code, never ships unverified")
    head = state.splitlines()[0]
    assert "WHO THIS IS FOR" in head
    assert "role: coder" in state
    # still carries the evidence it always did
    assert "USER ASK" in state
    assert "RESULT OF THAT ACTION" in state


def test_state_omits_the_card_rather_than_inventing_one():
    state = rl.build_state("fix the renderer", "", "boom")
    assert "WHO THIS IS FOR" not in state


def test_card_is_prose_not_frontmatter(tmp_path, monkeypatch):
    ident = tmp_path / "IDENTITY.md"
    ident.write_text("---\nrole: coder\nreflect_count: 12\ntemplate_version: v1.1\n---\n\n"
                     "# Coder\nI repair code and never trust a claim I have not run.\n")
    monkeypatch.setenv("HERMES_PROFILE_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_PROFILE", "cardtest")
    monkeypatch.setattr(rl, "_CARD_CACHE", {})
    card = rl._agent_card()
    assert "I repair code" in card
    # metadata is not a role description
    assert "template_version" not in card
    assert "reflect_count" not in card


def test_bold_prose_is_a_card_not_noise(tmp_path, monkeypatch):
    """Most of the fleet writes IDENTITY prose as bold lines (**Coder - ...**). Treating "*" as
    markdown noise emptied the card for those profiles while plain-prose profiles looked fine,
    so the feature was silently absent exactly where it was needed."""
    ident = tmp_path / "IDENTITY.md"
    ident.write_text("---\nrole: coder\n---\n\n# IDENTITY.md - Coder\n\n"
                     "> **Load order on wake:** IDENTITY then SOUL\n\n"
                     "**Coder - clinical precision through a code-substrate lens.**\n"
                     "**Trait tags:** conservation-first\n")
    monkeypatch.setenv("HERMES_PROFILE_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_PROFILE", "boldcard")
    monkeypatch.setattr(rl, "_CARD_CACHE", {})
    card = rl._agent_card()
    assert "clinical precision" in card
    assert "conservation-first" in card
    # the blockquote preamble and headings stay out
    assert "Load order on wake" not in card


def test_missing_identity_yields_empty_card(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_PROFILE", "nocards")
    monkeypatch.setattr(rl, "_CARD_CACHE", {})
    assert rl._agent_card() == ""


def test_card_is_cached_per_profile_size_bound(tmp_path, monkeypatch):
    ident = tmp_path / "IDENTITY.md"
    ident.write_text("x " * 2000)
    monkeypatch.setenv("HERMES_PROFILE_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_PROFILE", "bigcard")
    monkeypatch.setattr(rl, "_CARD_CACHE", {})
    card = rl._agent_card()
    assert 0 < len(card) <= rl.CARD_CHARS
    assert os.path.isfile(str(ident))
