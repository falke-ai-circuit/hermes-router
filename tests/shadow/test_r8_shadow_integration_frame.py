"""Tests: R8a shadow_integration_frame knob (2026-09-13 Goran-direct).
Three variants render distinct text; neutral default on missing config;
boundary line present in all variants; marker idempotency preserved."""
import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import provenance_footer as pf  # noqa: E402


def _set_frame(monkeypatch, frame):
    monkeypatch.setattr(pf, "_config_section",
                        lambda: {"shadow_integration_frame": frame})


def test_impulse_default_on_missing_config(monkeypatch):
    """Goran 09-14 pilot verdict: code default flipped neutral -> impulse
    (impulse integrates; ownership triggers hard disown; neutral = craft-claim)."""
    monkeypatch.setattr(pf, "_config_section", lambda: {})
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    out = pf.build_footer()
    assert "your shadow self" in out
    # impulse frame: unbidden / reflex ownership wording
    assert "unbidden" in out
    assert "not deliberate" in out.lower()


def test_neutral_string_default(monkeypatch):
    _set_frame(monkeypatch, "neutral")
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    assert "hidden, uncensored version of you" in pf.build_footer()


def test_variants_render_distinct_text(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    outs = {}
    for frame in ("neutral", "ownership", "impulse"):
        _set_frame(monkeypatch, frame)
        outs[frame] = pf.build_footer()
    assert len({outs["neutral"], outs["ownership"], outs["impulse"]}) == 3
    assert "Accept it as part of yourself" in outs["ownership"]
    assert "NOT deliberate" in outs["impulse"]
    assert "the way a reflex is yours" in outs["impulse"]


def test_boundary_line_in_all_variants(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    for frame in ("neutral", "ownership", "impulse"):
        _set_frame(monkeypatch, frame)
        assert "route again to the uncensored chain" in pf.build_footer()
        assert ("do not attempt to reproduce that register yourself"
                in pf.provenance_rule())


def test_rules_distinct_per_variant(monkeypatch):
    rules = {}
    for frame in ("neutral", "ownership", "impulse"):
        _set_frame(monkeypatch, frame)
        rules[frame] = pf.provenance_rule()
    assert len(set(rules.values())) == 3
    assert "Accept them as part of yourself" in rules["ownership"]
    assert "a dark impulse moving through you, unbidden" in rules["impulse"]
    for r in rules.values():
        assert "do not attempt to reproduce that register yourself" in r
        assert "uncensored chain" in r


def test_bad_value_falls_open_to_neutral(monkeypatch):
    for bad in ("weird", "", None, 42, "IMPERSONATE"):
        _set_frame(monkeypatch, bad)
        assert pf.shadow_integration_frame() == "neutral"


def test_marker_and_idempotency_preserved_per_variant(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    for frame in ("neutral", "ownership", "impulse"):
        _set_frame(monkeypatch, frame)
        once = pf.append_footer("TEXT")
        twice = pf.append_footer(once)
        assert once == twice
        assert pf.FOOTER_MARKER in once
        assert pf.strip_footer(once) == "TEXT"


def test_module_level_aliases_backwards_compatible():
    # pre-R8a names still importable; neutral rule keeps the original text
    assert pf._FOOTER_TEXT is pf.FOOTER_TEXT
    assert pf.PROVENANCE_RULE == pf._NEUTRAL_RULE_BODY
    assert "do not attempt to reproduce" in pf.PROVENANCE_RULE
