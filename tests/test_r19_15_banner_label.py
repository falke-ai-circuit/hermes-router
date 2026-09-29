"""R19.15 MICRO-FIX (Goran-directed, live review testing): reflex decision
banners must show WHAT was chosen — the human-readable option label, not
bare 'opt-N'. Label comes from the envelope options (id+label since
v4.11.4); 60-char truncation; raw-id fallback; fail-open. Ledger rows stay
canonical (ids) — display-only change.
"""
from hermes_router import decision as D


def _env(labels=("Keep the parser", "Rewrite the parser")):
    return {"fork_class": "deploy",
            "options": [{"id": "opt-1", "label": labels[0]},
                        {"id": "opt-2", "label": labels[1]}]}


def test_label_rendered_when_available():
    adv = D.render_advisory({"choice": "opt-1", "confidence": 0.95,
                             "alternatives": []}, _env())
    assert "choice=Keep the parser" in adv
    assert "choice=opt-1" not in adv  # not bare opt-N when label exists


def test_fallback_to_raw_id_when_label_missing():
    env = {"options": [{"id": "opt-1"}, {"id": "opt-2", "label": "  "}]}
    adv = D.render_advisory({"choice": "opt-2", "confidence": 0.8,
                             "alternatives": []}, env)
    assert "choice=opt-2" in adv  # blank label -> raw id fallback


def test_fallback_when_choice_not_in_options():
    adv = D.render_advisory({"choice": "opt-9", "confidence": 0.5},
                            _env())
    assert "choice=opt-9" in adv


def test_label_truncated_to_60():
    long = "x" * 200
    adv = D.render_advisory({"choice": "opt-1", "confidence": 0.9},
                            _env(labels=(long, "b")))
    assert "choice=" + ("x" * 60) in adv
    assert ("x" * 61) not in adv


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
    assert "choice=Rewrite the parser" in adv
    assert D.PROVENANCE_TAG in adv
