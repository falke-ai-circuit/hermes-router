"""R34 item 8b — per-profile frame register `decision.frame`.

`impulse` (default) keeps today's advisory envelope BYTE-IDENTICAL.
`execution` renders a FORK NOTE only: <=400 chars, the fork + the recorded
choice + ONE instruction to continue the in-progress task and not answer with
the note or restate it. No option menu, no why_not, no invitation to choose.
"""
import os
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import pytest  # noqa: E402

from hermes_router import decision_midturn as dmt  # noqa: E402

ENV = {
    "options": [{"id": "opt-1", "label": "ship the narrow fix"},
                {"id": "opt-2", "label": "revert the branch"}],
    "weighting": {"weights": {"opt-1": 0.7, "opt-2": 0.3},
                  "band": "strong", "evidence": []},
    "scope": {},
    "fork_class": "delivery_vs_revert",
}
VERDICT = {"choice": "opt-1", "confidence": 0.82,
           "alternatives": ["opt-2"]}


def _render(monkeypatch, register):
    cfg = {"frame": register} if register else {}
    monkeypatch.setattr(dmt, "_cfg", lambda: dict(cfg))
    return dmt._render_midturn_advisory(VERDICT, ENV, {})


def test_impulse_register_is_unchanged(monkeypatch):
    out = _render(monkeypatch, None)          # absent -> impulse
    assert out.startswith(dmt.ADVISORY_HEADER)
    assert "the fork surfaces as:" in out
    assert "ship the narrow fix pulls" in out
    assert "why_not: opt-2" in out
    # explicit 'impulse' is the same bytes
    assert _render(monkeypatch, "impulse") == out


def test_execution_register_is_a_bounded_fork_note(monkeypatch):
    out = _render(monkeypatch, "execution")
    assert out.startswith(dmt.FORK_NOTE_HEAD)
    assert len(out) <= dmt.FORK_NOTE_MAX_CHARS
    assert dmt.FORK_NOTE_DIRECTIVE in out
    assert "ship the narrow fix" in out          # the recorded choice
    assert "delivery_vs_revert" in out           # the fork
    # no menu, no why_not, no invitation to choose
    assert "pulls" not in out
    assert "why_not" not in out.lower()
    assert "opt-2" not in out
    assert "choose" not in out.lower()


def test_execution_register_note_cannot_exceed_cap(monkeypatch):
    env = dict(ENV, fork_class="x" * 900)
    monkeypatch.setattr(dmt, "_cfg", lambda: {"frame": "execution"})
    out = dmt._render_midturn_advisory(VERDICT, env, {})
    assert len(out) <= dmt.FORK_NOTE_MAX_CHARS


def test_unknown_register_falls_back_to_impulse(monkeypatch):
    out = _render(monkeypatch, "shouty")
    assert out.startswith(dmt.ADVISORY_HEADER)
