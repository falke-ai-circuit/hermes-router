"""Portability — the card works on ANY Hermes install, not this fleet's layout.

The old builder worked by accident: get_hermes_home() happened to return the
profile dir here, while the plugin's own resolver assumed an install root. One
resolver now answers "where do this agent's DNA files live?" with a chain, and
the card degrades cleanly when files are absent.
"""
from __future__ import annotations

import os

import pytest

from hermes_router import persona_card as pc
from hermes_router import reflex_lane as rl

DNA = {
    "IDENTITY.md": "---\nrole: x\n---\n\n**Thing - precise.** Conservation first.\n",
    "AGENTS.md": ("# AGENTS\n\n## Gates\n\n| Gate | When | Action |\n|---|---|---|\n"
                  "| G-VERIFY | Before claiming done | run the check, cite the output |\n"),
    "MEMORY.md": ("# MEMORY\n\n§ lesson-one (wt:0.9) - a correction that was verified wrong "
                  "and reframed into a standing rule.\n"),
}


def _profile(tmp_path, files=("IDENTITY.md", "AGENTS.md", "MEMORY.md")):
    home = tmp_path / "profiles" / "anyagent"
    home.mkdir(parents=True)
    for name in files:
        (home / name).write_text(DNA[name])
    return home


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("HERMES_PROFILE_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(pc, "_memo", {})
    monkeypatch.setattr(pc, "_card_cache", {})
    monkeypatch.setattr(rl, "card_cache", lambda: pc._card_cache)
    yield
    # teardown: a tmp-home card must never outlive this test
    pc._memo.clear()
    pc._card_cache.clear()


def test_only_identity_yields_persona_and_no_crash(tmp_path, monkeypatch):
    home = _profile(tmp_path, files=("IDENTITY.md",))
    monkeypatch.setattr(pc, "__file__", str(home / "plugins" / "hermes_router" / "persona_card.py"))
    secs = pc.card_sections(str(home))
    assert "Conservation first" in secs["PERSONA"]
    assert secs["PROCEDURES"] == "" and secs["HISTORY"] == ""
    card = pc.build_read_card(str(home))
    assert card.startswith("=== PERSONA ===")
    assert "=== PROCEDURES ===" not in card and "=== HISTORY ===" not in card


def test_all_three_sources_yield_three_labelled_sections(tmp_path, monkeypatch):
    home = _profile(tmp_path)
    monkeypatch.setattr(pc, "__file__", str(home / "plugins" / "hermes_router" / "persona_card.py"))
    card = pc.build_read_card(str(home))
    for label in ("=== PERSONA ===", "=== PROCEDURES ===", "=== HISTORY ==="):
        assert label in card
    assert "G-VERIFY" in card
    assert len(card) <= pc.CARD_BUDGET_DEFAULT
    # no metadata leaks into the card
    for banned in ("---", "role: x", "template_version", "Load order on wake"):
        assert banned not in card


def test_empty_install_yields_empty_card_not_an_exception(tmp_path, monkeypatch):
    """No source file anywhere -> "" and no exception (cardless lane)."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    absent = {k: "absent-%s.md" % k for k in pc.CARD_SOURCE_DEFAULTS}
    cfg = dict(pc._card_cfg(), **absent)
    monkeypatch.setattr(pc, "_card_cfg", lambda: cfg)
    monkeypatch.setattr(pc, "__file__", str(empty / "plugins" / "hermes_router" / "persona_card.py"))
    assert pc.build_read_card(str(empty)) == ""
    assert pc.profile_home(str(empty)) == ""


def test_walk_up_finds_the_profile_when_home_is_the_install_root(tmp_path, monkeypatch):
    """Regression for the accident: get_hermes_home() is the install root (no DNA),
    the plugin sits under <root>/profiles/<agent>/plugins/, nothing points at the
    profile — the walk-up must still find it."""
    root = tmp_path / "install"
    home = root / "profiles" / "anyagent"
    (home / "plugins" / "hermes_router").mkdir(parents=True)
    (home / "IDENTITY.md").write_text(DNA["IDENTITY.md"])
    fake_mod = home / "plugins" / "hermes_router" / "persona_card.py"

    monkeypatch.setattr(pc, "__file__", str(fake_mod))
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: root)
    assert pc.profile_home() == str(home)


def test_resolves_with_hermes_home_unset(tmp_path, monkeypatch):
    """No HERMES_HOME / HERMES_PROFILE_HOME at all: still resolves from the tree."""
    home = _profile(tmp_path)
    (home / "plugins" / "hermes_router").mkdir(parents=True)
    monkeypatch.setattr(pc, "__file__", str(home / "plugins" / "hermes_router" / "persona_card.py"))
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path / "install-root")
    assert pc.profile_home() == str(home)


def test_builders_resolve_dna_through_the_resolver_not_get_hermes_home(tmp_path, monkeypatch):
    """INTEGRATION: the builders, not just the resolver.

    Vanilla-install shape: get_hermes_home() is the install root and holds no
    DNA, while HERMES_PROFILE_HOME points at the real profile. The builders used
    to call the get_hermes_home()-only helper and returned an EMPTY card here —
    a green resolver test hid it, because no test ever exercised a builder.
    """
    home = _profile(tmp_path)
    root = tmp_path / "install-root"
    root.mkdir()
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: root)
    monkeypatch.setenv("HERMES_PROFILE_HOME", str(home))
    monkeypatch.setattr(pc, "__file__", str(home) + "/plugins/hermes_router/persona_card.py")
    # the compact builder takes the identity branch under persona_mode=full (its
    # voice_stems branch builds no parts); pin the mode so this test measures the
    # RESOLVER, not that config switch
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"persona_mode": "full", "card_budget_chars": 1200})

    read_card = pc._build_enriched_persona_context(1200, include_mandate=False)
    assert read_card, "read card resolved empty despite a real profile"
    assert "=== PERSONA ===" in read_card

    compact = pc._build_compact_persona_context()
    assert compact, "compact card resolved empty despite a real profile"


def test_no_fleet_knowledge_in_the_card_modules():
    names = ("coder", "reviewer", "analyst", "orchestrator", "conductor")
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for mod in ("persona_card.py", "method_card.py", "refusal_doctrine.py"):
        text = open(os.path.join(here, mod), encoding="utf-8").read()
        assert "/opt/data" not in text, "%s hardcodes a fleet path" % mod
        for name in names:
            assert name not in text.lower(), "%s names a fleet agent (%s)" % (mod, name)


def test_memory_edit_invalidates_the_cached_card(tmp_path, monkeypatch):
    home = _profile(tmp_path)
    monkeypatch.setattr(pc, "__file__", str(home / "plugins" / "hermes_router" / "persona_card.py"))
    first = pc.build_read_card(str(home))
    assert "lesson-one" in first
    (home / "MEMORY.md").write_text(
        DNA["MEMORY.md"] + "\n§ lesson-two (wt:0.9) - a newer correction that supersedes the old one.\n")
    second = pc.build_read_card(str(home))
    assert second != first
    assert "lesson-two" in second
