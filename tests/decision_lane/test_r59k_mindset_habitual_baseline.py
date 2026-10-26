"""R59k -- the midturn mindset channel must not go silent just because the fork
lands early in the session.

A fork normally fires on the FIRST tool result, when the session trace carries no
signal. `_current_mindset` then returned null for both drives, the envelope told the
model the stance was unknown, and `_midturn_mindset_block` discarded whatever
adjustment came back (`reason=scores_but_stance_unreadable`). Four of eight live cells
in the M2 run showed exactly that.

The fix mirrors PRE's R59i: fall back to the profile's HABITUAL stance when the drive
has enough history to define one, and keep null (honest silence) when it does not.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _stub(monkeypatch, rows, hist, vec):
    from hermes_router import decision, mindset

    monkeypatch.setattr(decision, "_session_trace_rows", lambda sid, **kw: rows)
    monkeypatch.setattr(decision, "_note_unreadable", lambda *a, **k: None)

    class _Ax:
        @staticmethod
        def _mindset_history():
            return hist

    monkeypatch.setitem(sys.modules, "hermes_router.anchor_exec", _Ax) if False else None
    monkeypatch.setattr("hermes_router.anchor_exec._mindset_history", lambda: hist,
                        raising=False)
    monkeypatch.setattr(mindset, "read_trace_vectors", lambda trace, h: vec)


def test_unreadable_drive_falls_back_to_habitual_when_history_exists(monkeypatch):
    from hermes_router import decision, mindset

    hist = {"scope": [0.1] * 40, "rigour": [0.2] * 40}
    _stub(monkeypatch, [("assistant", None, "x")], hist,
          {"scope": None, "rigour": None})
    monkeypatch.setattr(mindset, "_history_values",
                        lambda h, d: h.get(d, []), raising=False)
    out = decision._current_mindset("api_test")
    assert out["scope"] == 0.0, out
    assert out["rigour"] == 0.0, out


def test_unreadable_drive_stays_null_without_history(monkeypatch):
    from hermes_router import decision, mindset

    _stub(monkeypatch, [("assistant", None, "x")], {"scope": [], "rigour": []},
          {"scope": None, "rigour": None})
    monkeypatch.setattr(mindset, "_history_values", lambda h, d: [], raising=False)
    out = decision._current_mindset("api_test")
    assert out["scope"] is None and out["rigour"] is None, out


def test_readable_session_stance_is_not_overwritten(monkeypatch):
    from hermes_router import decision, mindset

    hist = {"scope": [0.1] * 40, "rigour": [0.2] * 40}
    _stub(monkeypatch, [("assistant", None, "x")], hist,
          {"scope": 0.62, "rigour": -0.31})
    monkeypatch.setattr(mindset, "_history_values", lambda h, d: h.get(d, []), raising=False)
    out = decision._current_mindset("api_test")
    assert out["scope"] == 0.62 and out["rigour"] == -0.31, out


def test_no_rows_returns_blank_and_does_not_invent(monkeypatch):
    from hermes_router import decision, mindset

    _stub(monkeypatch, [], {"scope": [0.1] * 40}, {"scope": None, "rigour": None})
    monkeypatch.setattr(mindset, "_history_values",
                        lambda h, d: h.get(d, []), raising=False)
    out = decision._current_mindset("api_test")
    assert out["scope"] is None and out["rigour"] is None, out


def test_emission_gate_now_passes_with_habitual_stance(monkeypatch):
    """The point of the fix: an adjustment from the model is no longer discarded."""
    from hermes_router import decision_midturn as dm

    note = dm._midturn_mindset_block({"choice": "opt-1", "mindset": {"scope": 0.9}},
                                     {"mindset": {"scope": 0.0, "rigour": 0.0}})
    assert note, "an out-of-band adjustment against a habitual stance must render"
    assert "sch" in note or "scope" in note.lower()
