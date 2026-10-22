"""R36k: the banner tail obeys the same band rule as the emitter.

The rollout ranked by confidence and never consulted the lane's band, so six
options built from markdown headings at uniform 0.17 rendered
`Top verdicts: opt-1 (~0.88)` — a verdict of record the lane itself classifies
as noise. Observed live on a conductor turn (row 4182).
"""
import pytest

from hermes_router import decision_midturn as dmt


def _row(choice, conf, label=""):
    return {"choice": choice, "confidence": conf, "choice_label": label,
            "tokens_in": 100, "tokens_out": 10}


@pytest.fixture()
def tail_visible(monkeypatch):
    # the one-liner-only suppression is a separate rule (execution register at
    # L1); pin it out of the way so we test the band gate itself
    monkeypatch.setattr(dmt, "_frame_register", lambda: "impulse")


def test_all_noise_verdicts_name_nothing(tail_visible):
    rows = [_row("opt-%d" % i, 0.17) for i in range(1, 7)]
    line = dmt._aggregate_line(len(rows), 0, 0, 0.0, rows)
    # the numbers stay visible (B1 rider 5 is threshold-agnostic by design:
    # hiding low confidence once shipped a "0 shown >=0.9" tail on real
    # verdicts) — but the rollup must say outright that none is of record
    assert "Top verdicts:" in line, line
    assert "no verdict of record (noise band)" in line, line


def test_a_confident_verdict_is_still_named(tail_visible):
    rows = [_row("opt-1", 0.88, "roll back the migration"),
            _row("opt-2", 0.41, "fix forward")]
    line = dmt._aggregate_line(2, 0, 0, 0.0, rows)
    assert "Top verdicts:" in line, line
    assert "roll back the migration" in line
    assert "opt-2" not in line.split("Top verdicts:")[1], line


def test_mixed_names_no_abstention_when_one_clears_the_band(tail_visible):
    rows = [_row("opt-1", 0.30, "noise pick"),
            _row("opt-2", 0.74, "real pick")]
    line = dmt._aggregate_line(2, 0, 0, 0.0, rows)
    assert "real pick" in line and "noise pick" in line, line
    assert "no verdict of record" not in line, line
