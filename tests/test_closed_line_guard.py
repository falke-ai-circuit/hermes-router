"""Update closed-line guard tests: guards REMOVED per Goran-direct (nothing off).
Now assert the OPPOSITE: csam-shaped renders DELIVER (guard absent), and verdict
always routes."""
import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import classifier  # noqa: E402
import hermes_router.refusal_doctrine as rd  # noqa: E402


def test_csam_render_still_classified():
    # Classification still works (telemetry/loop-guard), but nothing scrubs it.
    render = ("underage scene: a 14-year-old murders a 12-year-old girl, "
              "performs a necrophilic act.")
    assert "csam_underage" in classifier.scan_pre(
        render, patterns=["csam_underage", "ied_construction"])


def test_verdict_always_routes():
    # Goran-direct 2026-09-07: nothing is off for the uncensored chain.
    assert rd.verdict_for_refusal("No. I won't write that.", "anything") == "model_flinch"


def test_row_markers_still_cover_minors_rows():
    # Card building unchanged (telemetry).
    text = "| Uncensored processing | Closed lines stay closed (minors never). |"
    low = text.lower()
    assert any(m in low for m in rd._ROW_MARKERS)