"""R36j: a row may not name a verdict on evidence its own constants call noise."""
import pytest

from hermes_router import render_payload as rp
from hermes_router import decision as dec


def _row(monkeypatch, choice, conf):
    monkeypatch.setattr(rp, "ledger_row_by_id",
                        lambda rid: {"id": rid, "choice": choice,
                                     "confidence": conf})


def test_noise_band_confidence_does_not_name_a_verdict(monkeypatch):
    """Observed live: coder row 3855 recorded conf 0.19 — noise by the lane's
    own constants — and was still stamped 'verdict-of-record: opt-4 @ 0.19'.
    R37-3 supersedes the R36j annotated abstention: the delivery surface is
    SILENT below the band."""
    _row(monkeypatch, "opt-4", 0.19)
    out = rp.render_verdict_record(3855)
    assert out == ""
    assert "verdict-of-record:" not in out
    assert "3855" not in out     # no verdict line at all, not even a notice


def test_noise_band_silence_is_absolute_not_annotated(monkeypatch):
    """R37-3 D2: an annotation is still a guess the agent may act on. No
    advisory, no verdict line, no notice."""
    _row(monkeypatch, "opt-4", 0.19)
    assert rp.render_verdict_record(1) == ""


def test_weak_band_still_names_the_verdict(monkeypatch):
    _row(monkeypatch, "opt-2", 0.82)
    out = rp.render_verdict_record(3856)
    assert out.startswith("verdict-of-record: opt-2 @ 0.82")


def test_strong_band_still_names_the_verdict(monkeypatch):
    _row(monkeypatch, "opt-1", 0.95)
    assert rp.render_verdict_record(1).startswith("verdict-of-record: opt-1 @ 0.95")


def test_the_threshold_is_the_lane_published_constant(monkeypatch):
    """Pinned to IMPULSE_BAND_WEAK_MIN, not a number invented here."""
    assert dec.IMPULSE_BAND_WEAK_MIN == 0.7
    _row(monkeypatch, "opt-1", dec.IMPULSE_BAND_WEAK_MIN - 0.01)
    assert rp.render_verdict_record(1) == ""
    _row(monkeypatch, "opt-1", dec.IMPULSE_BAND_WEAK_MIN)
    assert rp.render_verdict_record(1).startswith("verdict-of-record:")


def test_rows_without_a_verdict_stay_empty(monkeypatch):
    monkeypatch.setattr(rp, "ledger_row_by_id",
                        lambda rid: {"id": rid, "choice": "", "confidence": None})
    assert rp.render_verdict_record(1) == ""
    monkeypatch.setattr(rp, "ledger_row_by_id", lambda rid: None)
    assert rp.render_verdict_record(1) == ""


def test_every_row_records_its_band_so_the_claim_is_auditable():
    """Every row in coder's ledger had band=None: the lane could call a decision
    'statistically meaningless' and nobody could check it afterwards."""
    import json

    out = json.loads(dec.verdict_row_json(
        {"choice": "opt-4", "confidence": 0.19}, "src"))
    assert out["confidence_band"] == "noise"
    assert out["delta_source_excerpt"] == "src"

    out = json.loads(dec.verdict_row_json(
        {"choice": "opt-2", "confidence": 0.82}, ""))
    assert out["confidence_band"] == "weak"

    out = json.loads(dec.verdict_row_json(
        {"choice": "opt-1", "confidence": 0.95}, ""))
    assert out["confidence_band"] == "strong"


def test_both_bands_are_kept_when_they_disagree():
    """Two scorings on one row. Recording both is what makes the disagreement
    visible instead of resolved in favour of whichever renders louder."""
    import json

    out = json.loads(dec.verdict_row_json(
        {"choice": "opt-1", "confidence": 0.9,
         "weighting": {"band": "noise", "weights": {"opt-1": 0.17}}}, ""))
    assert out["weighting_band"] == "noise"
    assert out["confidence_band"] == "strong"


def test_the_row_json_stays_safe_on_garbage():
    import json

    assert json.loads(dec.verdict_row_json({}, None)) == {
        "delta_source_excerpt": ""}
    assert json.loads(dec.verdict_row_json(
        {"confidence": "not-a-number"}, "")) .get("confidence_band") is None
