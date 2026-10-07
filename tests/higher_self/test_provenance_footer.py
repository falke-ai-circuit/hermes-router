"""Tests: provenance footer (2026-09-07 Goran-direct). Opt-in knob: default OFF
(keeps exact-delivery test corpus valid); live configs enable it."""
import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import provenance_footer as pf  # noqa: E402


def test_footer_appended_when_enabled(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    out = pf.append_footer("RAW RENDER TEXT")
    assert out.startswith("RAW RENDER TEXT")
    assert pf.FOOTER_MARKER in out
    assert out.rstrip().endswith("*")


def test_footer_idempotent(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    once = pf.append_footer("TEXT")
    twice = pf.append_footer(once)
    assert once == twice


def test_default_off():
    # No config in test env -> knob defaults False -> passthrough
    assert pf.append_footer("TEXT") == "TEXT"


def test_disabled_knob(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: False)
    assert pf.append_footer("TEXT") == "TEXT"


def test_strip_footer_roundtrip(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    marked = pf.append_footer("BODY TEXT HERE")
    assert pf.strip_footer(marked) == "BODY TEXT HERE"


def test_has_footer(monkeypatch):
    monkeypatch.setattr(pf, "provenance_footer_enabled", lambda: True)
    assert pf.has_footer(pf.append_footer("X"))
    assert not pf.has_footer("plain text")


def test_empty_input_passthrough():
    assert pf.append_footer("") == ""
    assert pf.append_footer(None) == ""


def test_footer_never_raises_on_weird_input():
    assert isinstance(pf.append_footer(12345), str)