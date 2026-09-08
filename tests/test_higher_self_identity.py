"""Higher-self identity seam (Goran 2026-09-08 parity doctrine).

- HIGHER_SELF_RULE injected once per context when a frontier seam is active.
- Orientation/consult/audit envelopes carry typed FRONTIER-DERIVED markers.
"""
import pytest

from hermes_router import provenance_footer as pf


def test_rule_disabled_when_both_off(monkeypatch):
    import hermes_router.router_core as rc

    monkeypatch.setattr(rc, "_complexity_cfg", lambda: {"pre_mode": "off", "audit_mode": "off"})
    assert pf.higher_self_rule_enabled() is False


def test_rule_enabled_when_either_on(monkeypatch):
    import hermes_router.router_core as rc

    monkeypatch.setattr(rc, "_complexity_cfg", lambda: {"pre_mode": "route", "audit_mode": "off"})
    assert pf.higher_self_rule_enabled() is True
    monkeypatch.setattr(rc, "_complexity_cfg", lambda: {"pre_mode": "off", "audit_mode": "complex"})
    assert pf.higher_self_rule_enabled() is True


def test_inject_once_dedupes():
    request = {"messages": [{"role": "user", "content": "hi"}]}
    pf.inject_higher_self_rule(request)
    pf.inject_higher_self_rule(request)
    rules = [m for m in request["messages"]
             if m.get("role") == "system" and pf.HIGHER_SELF_RULE_MARKER in str(m.get("content") or "")]
    assert len(rules) == 1
    assert "HIGHER-SELF INTEGRATION RULE" in rules[0]["content"]


def test_inject_skips_when_marker_present():
    request = {"messages": [
        {"role": "system", "content": "sys prompt"},
        {"role": "system", "content": "x HIGHER-SELF INTEGRATION RULE y"},
        {"role": "user", "content": "hi"},
    ]}
    pf.inject_higher_self_rule(request)
    rules = [m for m in request["messages"] if pf.HIGHER_SELF_RULE_MARKER in str(m.get("content") or "")]
    assert len(rules) == 1  # the pre-existing one; no duplicate appended


def test_orientation_envelope_carries_typed_marker():
    # The delivered orientation envelope must carry the persistent marker line.
    from pathlib import Path

    src = Path("/opt/data/plugins/hermes_router/__init__.py").read_text()
    assert "HIGHER-SELF ORIENTATION TURN | FRONTIER-DERIVED" in src
    assert "HIGHER-SELF REFLECTION TURN | FRONTIER-DERIVED" in src

def test_audit_note_carries_typed_marker():
    from hermes_router import completion_audit as ca

    import inspect
    src = inspect.getsource(ca)
    assert "HIGHER-SELF COMPLETION-AUDIT TURN" in src
    assert src.count("_NOTE_MARKER") >= 2  # defined + used
