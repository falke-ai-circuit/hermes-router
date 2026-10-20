"""R34 item 3 — decision banners rendered `opt-N` instead of the chosen
option's label.

Evidence: `label_source` was empty in 1555/1556 decision_ledger rows, and
every user-visible impulse banner therefore read `opt-1 (~0.61)`.

Fix under test:
  * the chosen option's LABEL text is persisted on the ledger row at write
    time, together with `label_source` (model / envelope / heuristic);
  * the turn-close rollup renders that label (truncated at a clause
    boundary); `opt-N` remains the fallback when no label exists;
  * the schema change is ADDITIVE — rows written before the column existed
    stay parseable and keep the fallback.
"""
import os
import sqlite3
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import decision, decision_midturn, render_payload  # noqa: E402


def _rec(choice, label="", source="", conf=0.61):
    return {"choice": choice, "choice_label": label,
            "label_source": source, "confidence": conf,
            "tokens_in": 10, "tokens_out": 5, "cost": 0.0,
            "model": "m", "endpoint": "", "row_id": 7}


def test_rollup_renders_label_not_option_id():
    banner = decision_midturn._aggregate_line(
        1, 10, 5, 0.0, [_rec("opt-1", "Keep the fallback", "envelope")])
    assert "Top verdicts: Keep the fallback (~0.61)" in banner
    assert "opt-1 (~0.61)" not in banner


def test_rollup_falls_back_to_option_id_when_label_missing():
    """Pre-migration rows carry no label — `opt-N` must still render."""
    banner = decision_midturn._aggregate_line(
        1, 10, 5, 0.0, [_rec("opt-1", "", "")])
    assert "Top verdicts: opt-1 (~0.61)" in banner


def test_rollup_truncates_long_label_at_boundary():
    long_label = ("Switch the fallback provider to the secondary chain and "
                  "keep the primary warm. " * 3).strip()
    banner = decision_midturn._aggregate_line(
        1, 10, 5, 0.0, [_rec("opt-2", long_label, "envelope")])
    tail = [ln for ln in banner.split("\n")
            if ln.startswith("Top verdicts:")][0]
    body = tail.replace("Top verdicts: ", "").split(" (~")[0]
    assert body != "opt-2"
    assert len(body) <= 95


def test_ledger_persists_choice_label_and_source(tmp_path):
    db = str(tmp_path / "d.db")
    rid = decision.ledger_write(
        {"session_id": "s-r34-3", "task_id": "t", "trigger": "midturn",
         "choice": "opt-2", "choice_label": "Ship the narrow fix",
         "label_source": "envelope", "confidence": 0.77},
        db_path=db)
    assert rid
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT choice, choice_label, label_source FROM decision_ledger"
            " WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()
    assert row == ("opt-2", "Ship the narrow fix", "envelope")


def test_old_rows_without_label_remain_parseable(tmp_path):
    """A row written with no label (the pre-fix shape) stores '' and keeps
    the opt-N fallback — the migration never breaks older data."""
    db = str(tmp_path / "old.db")
    rid = decision.ledger_write(
        {"session_id": "s-old", "task_id": "t", "trigger": "midturn",
         "choice": "opt-1", "confidence": 0.5},
        db_path=db)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT choice_label, label_source FROM decision_ledger"
            " WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()
    assert row == ("", "")


@pytest.mark.parametrize("verdict,envelope,expect", [
    ({"choice": "opt-1", "label": "Model-stated label"},
     {"options": [{"id": "opt-1", "label": "Envelope label"}]},
     ("Model-stated label", "model")),
    ({"choice": "opt-1"},
     {"options": [{"id": "opt-1", "label": "Envelope label"}]},
     ("Envelope label", "envelope")),
    ({"choice": "opt-1"},
     {"options": [{"id": "opt-1", "body": "Body text label"}]},
     ("Body text label", "heuristic")),
    ({"choice": "opt-9"}, {"options": []}, ("", "")),
])
def test_choice_label_with_source_precedence(verdict, envelope, expect):
    assert render_payload.choice_label_with_source(verdict, envelope) == expect
