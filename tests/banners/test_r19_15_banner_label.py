"""R19.15 MICRO-FIX (Goran-directed, live review testing): reflex decision
banners must show WHAT was chosen — the human-readable option label, not
bare 'opt-N'. Label comes from the envelope options (id+label since
v4.11.4); 60-char truncation; raw-id fallback; fail-open. Ledger rows stay
canonical (ids) — display-only change.
"""
from hermes_router import decision as D


def _env(labels=("Keep the parser", "Rewrite the parser")):
    # D1 v1.1: the impulse frame reads the MECHANICAL weighting block
    opts = [{"id": "opt-1", "label": labels[0]},
            {"id": "opt-2", "label": labels[1]}]
    return {"fork_class": "deploy", "options": opts,
            "weighting": {"weights": {"opt-1": 0.6, "opt-2": 0.4},
                          "band": "noise", "evidence": [], "basis": "test"}}


def test_label_rendered_when_available():
    adv = D.render_advisory({"choice": "opt-1", "confidence": 0.95,
                             "alternatives": []}, _env())
    assert "Keep the parser pulls" in adv
    assert "opt-1 pulls" not in adv  # not bare opt-N when label exists


def test_fallback_to_raw_id_when_label_missing():
    env = {"options": [{"id": "opt-1"}, {"id": "opt-2", "label": "  "}],
           "weighting": {"weights": {"opt-1": 0.5, "opt-2": 0.5},
                         "band": "noise", "evidence": [], "basis": "test"}}
    adv = D.render_advisory({"choice": "opt-2", "confidence": 0.8,
                             "alternatives": []}, env)
    assert "opt-2 pulls" in adv  # blank label -> raw id fallback


def test_fallback_when_choice_not_in_options():
    """R19.20 supersedes the raw-id fallback: an unmatched choice renders
    NO advisory at all (closed-set clamp) — the invalid_fork row is the
    record, never a free-text choice line."""
    adv = D.render_advisory({"choice": "opt-9", "confidence": 0.5},
                            _env())
    assert adv == ""


def test_label_truncated_to_60():
    long = "x" * 200
    adv = D.render_advisory({"choice": "opt-1", "confidence": 0.9},
                            _env(labels=(long, "b")))
    # R13-4: word-boundary truncation — a spaceless blob keeps <= 60 chars
    # with an ellipsis marker; a >60-char run can never appear.
    assert ("x" * 60) + "…" in adv
    assert ("x" * 61) not in adv


def test_label_word_boundary_truncation():
    # R13-4 (live: conductor api_1791099470 — mid-sentence hard cut): a
    # real-sentence label cuts at the last word boundary + ellipsis.
    long = 'Cost baseline capture. "Fleet generalization after a day of proof."'
    adv = D.render_advisory({"choice": "opt-1", "confidence": 0.9},
                            _env(labels=(long, "b")))
    assert "after a day of proof" not in adv  # beyond the cut
    assert "of proof.\u2026" not in adv
    cut_label = adv.split(" pulls ", 1)[0].split("the fork surfaces as: ")[-1]
    assert cut_label.endswith("…")
    assert " pulls" not in cut_label  # no stray insertion inside the label


def test_fail_open_garbage_inputs():
    assert D.choice_label(None, None) == ""
    assert D.choice_label({}, {}) == ""
    assert D.choice_label({"choice": "opt-1"}, None) == "opt-1"
    assert D.choice_label({"choice": 5}, {"options": "garbage"}) == "5"


def test_midturn_advisory_carries_label():
    """All reflex banner emission points route through render_advisory —
    midturn's _render_midturn_advisory wraps it."""
    from hermes_router import decision_midturn as DM
    adv = DM._render_midturn_advisory(
        {"choice": "opt-2", "confidence": 0.7, "alternatives": ["opt-1"]},
        _env(), {"model": "typesafe/jev-router"})
    assert "Rewrite the parser pulls" in adv
    assert D.PROVENANCE_TAG in adv
