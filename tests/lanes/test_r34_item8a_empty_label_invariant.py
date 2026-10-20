"""R34 item 8a — EMPTY-LABEL INVARIANT at the render choke point.

Live specimen: the 20:22 turn delivered the literal text
`(The option text for "why_not" was omitted by the author. The option
existed in the decision an...` and the turn ENDED there.

Rules pinned here:
  * an option whose label is empty/whitespace is DROPPED from the frame
    (never emitted as its bare id) — after the R19.20 closed-set clamp;
  * if no labelled options remain, render_advisory returns "" (no advisory);
  * no alternatives -> the why_not section is omitted entirely, never the
    placeholder sentence.
"""
import os
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

from hermes_router import decision_midturn as dmt  # noqa: E402
from hermes_router import render_payload  # noqa: E402


def _env(options, weights=None):
    return {
        "options": options,
        "weighting": {"weights": weights or {"opt-1": 0.4, "opt-2": 0.6},
                      "band": "weak", "evidence": []},
        "scope": {},
    }


def test_blank_labels_only_render_no_advisory():
    env = _env([{"id": "opt-1", "label": ""},
                {"id": "opt-2", "label": "   "}])
    verdict = {"choice": "opt-1", "confidence": 0.6, "alternatives": ["opt-2"]}
    assert render_payload.render_advisory(verdict, env) == ""
    assert render_payload.render_impulse_frame(verdict, env) == ""


def test_blank_option_is_dropped_from_a_mixed_set():
    env = _env([{"id": "opt-1", "label": "ship the narrow fix"},
                {"id": "opt-2", "label": ""}])
    verdict = {"choice": "opt-1", "confidence": 0.7, "alternatives": []}
    frame = render_payload.render_impulse_frame(verdict, env)
    assert "ship the narrow fix pulls" in frame
    assert "opt-2" not in frame


def test_no_alternatives_omits_why_not_section():
    env = _env([{"id": "opt-1", "label": "ship the narrow fix"}])
    verdict = {"choice": "opt-1", "confidence": 0.8, "alternatives": []}
    note = dmt._render_midturn_advisory(verdict, env, {})
    assert "why_not" not in note.lower()
    assert "no viable alternative" not in note.lower()


def test_alternatives_still_render_why_not():
    env = _env([{"id": "opt-1", "label": "ship the narrow fix"},
                {"id": "opt-2", "label": "revert the branch"}])
    verdict = {"choice": "opt-1", "confidence": 0.8,
               "alternatives": ["opt-2"]}
    note = dmt._render_midturn_advisory(verdict, env, {})
    assert "why_not: opt-2" in note
