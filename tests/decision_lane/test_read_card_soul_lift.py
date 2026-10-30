"""UNIT 1 pin: the READ card's PERSONA section carries SOUL rows, not IDENTITY only.

Measured defect: _build_enriched_persona_context() lifted ZERO lines from
SOUL.md for two profiles because the read card's PERSONA slice came from
_persona_substance(), which read IDENTITY.md and fell back to SOUL.md ONLY when
IDENTITY was absent. The marker-driven SOUL lifter existed but was wired only
into the render/voice card.

These pins assert a marker row reaches the card VERBATIM (not len > 0), that a
second profile's card differs, and that the combined slice stays inside the
card budget.
"""
from __future__ import annotations

import os

import pytest

from hermes_router import persona_card as pc
from hermes_router import reflex_lane as rl


# Two profiles with DIFFERENT identities and DIFFERENT marker rows: the shape
# the fleet has, without naming fleet agents in this test's fixtures.
PROFILES = {
    "alpha": {
        "IDENTITY.md": (
            "---\nrole: alpha\n---\n\n"
            "**I map the terrain before anyone moves.** Every claim is a "
            "hypothesis until measured; I optimise for the shortest verified "
            "path to the answer.\n"
        ),
        "SOUL.md": (
            "# SOUL.md — Alpha\n\n"
            "## Voice and character\n\n"
            "**Voice:** terse, dry, never theatrical — the wording is the model "
            "of the thought.\n"
            "**Closed line:** nothing about named dead, no invented victim, "
            "ever.\n\n"
            "## Hard Rules\n\n"
            "| Rule | Detail |\n|---|---|\n"
            "| Verify first | A claim that has not been run is a hypothesis. |\n"
        ),
        "AGENTS.md": (
            "# AGENTS\n\n## Gates\n\n| Gate | When | Action |\n|---|---|---|\n"
            "| G-AUDIT | Before handover | cite the run output |\n"
        ),
        "MEMORY.md": "# MEMORY\n\n§ lesson-a - a correction that was verified.\n",
    },
    "beta": {
        "IDENTITY.md": (
            "---\nrole: beta\n---\n\n"
            "**I am the inside operator — the company works through me.** "
            "Procedures, people and approval chains are the substrate; I move "
            "inside them rather than around them.\n"
        ),
        "SOUL.md": (
            "# SOUL.md — Beta\n\n"
            "## Role\n\n"
            "**Character:** unhedged, unbothered, allergic to narration.\n\n"
            "## Hard Rules\n\n"
            "| Rule | Detail |\n|---|---|\n"
            "| Never send | ALWAYS draft; never send on the user's behalf, "
            "no conversational lift. |\n"
        ),
        "AGENTS.md": (
            "# AGENTS\n\n## Gates\n\n| Gate | When | Action |\n|---|---|---|\n"
            "| G-DRAFT | Before writing | draft, never send |\n"
        ),
        "MEMORY.md": "# MEMORY\n\n§ lesson-b - a standing correction.\n",
    },
}

# The marker row each profile's SOUL.md contributes, verbatim.
EXPECTED_SOUL_ROWS = {
    "alpha": "**Voice:** terse, dry, never theatrical",
    "beta": "| Never send | ALWAYS draft; never send",
}


def _profile(tmp_path, name):
    home = tmp_path / "profiles" / name
    home.mkdir(parents=True)
    for fname, body in PROFILES[name].items():
        (home / fname).write_text(body)
    return home


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("HERMES_PROFILE_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(pc, "_memo", {})
    monkeypatch.setattr(pc, "_card_cache", {})
    monkeypatch.setattr(rl, "card_cache", lambda: pc._card_cache)
    yield
    pc._memo.clear()
    pc._card_cache.clear()


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_read_card_persona_carries_a_soul_marker_row(tmp_path, monkeypatch, name):
    home = _profile(tmp_path, name)
    monkeypatch.setattr(pc, "__file__",
                        str(home / "plugins" / "hermes_router" / "persona_card.py"))
    secs = pc.card_sections(str(home))
    persona = secs["PERSONA"]
    # the SOUL row arrives VERBATIM (a specific marker row, not "something")
    assert EXPECTED_SOUL_ROWS[name] in persona, (
        "SOUL marker row missing from the read card's PERSONA slice: %r"
        % persona)
    # and the identity prose is still there: both sources, not one or the other
    ident = pc._prose_paragraphs(os.path.join(str(home), "IDENTITY.md"),
                                 pc.PERSONA_READ_IDENTITY_CHARS, 2)
    assert ident.strip()
    assert ident.split("\n")[0][:40] in persona
    # the whole card still fits the default budget
    card = pc.build_read_card(str(home))
    assert len(card) <= pc.CARD_BUDGET_DEFAULT
    assert EXPECTED_SOUL_ROWS[name] in card


def test_two_profiles_produce_different_read_cards(tmp_path, monkeypatch):
    cards = {}
    for name in PROFILES:
        home = _profile(tmp_path / name, name)
        monkeypatch.setattr(pc, "__file__",
                            str(home / "plugins" / "hermes_router" / "persona_card.py"))
        cards[name] = pc.build_read_card(str(home)).replace(str(home), "")
    assert cards["alpha"] and cards["beta"]
    assert cards["alpha"] != cards["beta"]
    assert EXPECTED_SOUL_ROWS["alpha"] not in cards["beta"]
    assert EXPECTED_SOUL_ROWS["beta"] not in cards["alpha"]


def test_no_soul_file_degrades_to_identity_only(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "soulless"
    home.mkdir(parents=True)
    (home / "IDENTITY.md").write_text(PROFILES["alpha"]["IDENTITY.md"])
    monkeypatch.setattr(pc, "__file__",
                        str(home / "plugins" / "hermes_router" / "persona_card.py"))
    persona = pc.card_sections(str(home))["PERSONA"]
    assert "map the terrain" in persona


def test_persona_slice_respects_the_share_split(tmp_path, monkeypatch):
    home = _profile(tmp_path, "alpha")
    monkeypatch.setattr(pc, "__file__",
                        str(home / "plugins" / "hermes_router" / "persona_card.py"))
    share = 400
    out = pc._persona_substance(str(home), share, with_soul=True)
    assert len(out) <= share
    assert EXPECTED_SOUL_ROWS["alpha"] in out
    ident_only = pc._persona_substance(str(home), share)
    assert EXPECTED_SOUL_ROWS["alpha"] not in ident_only, (
        "with_soul=False must keep the legacy identity-only slice")
