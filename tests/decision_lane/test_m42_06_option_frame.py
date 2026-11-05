"""M42-06 pins — the option causal frame must survive label truncation.

Defect: every extraction site stores option labels as
``clean_snippet(..., 120)``, but ``decision._option_frame`` located an
option's source line with ``low_label in raw.lower()``. A label truncated at
the tail is not a substring of its own raw line, so ``line`` stayed empty and
``cause_effect`` / ``cost`` / ``risk`` all returned None — the envelope
promise "every option carries a causal frame" was silently false for long
labels.

Measured at HEAD: pure truncation still leaves the label a prefix of its raw
line, so the substring match survives; the miss needs clean_snippet's other
normalisation — whitespace collapse — which makes the stored 120-char label
non-contiguous with the line it came from. Both halves are guarded by the
prefix match under test.

Fix under test: match on ``low_label[:40]`` with a first-three-tokens
fallback (the truncation is at the tail, so a prefix is always present when
the label came from that line), and count a true miss with
``bump_counter("option_frame_miss")`` instead of returning an empty frame in
silence. Fields stay None and no prompt/schema/wire shape changes.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from hermes_router import decision as d
from hermes_router import decision_miner

SID = "m42-06-session"

# Tail that lives PAST the 120-char label cut: a cost token and a risk token
# that only the raw line carries. Both are unreachable at HEAD.
LONG_TAIL = ("because a silent drift would spread into every suite and it "
             "costs 40 tokens per call on production")

# The raw line carries a double space at char ~78 and runs past 120 chars.
# clean_snippet collapses whitespace AND caps at 120, so the stored label is
# neither the raw line nor a contiguous substring of it — the measured miss.
ASK = (
    "One fork, pick one.\n"
    "Option A: ship the parser now — it is reversible, costs 2 hours\n"
    "Option B: hold the release and rewrite the escape layer by hand, keeping "
    " the ledger intact and the docs current, then re-run the whole regression "
    "set before touching anything " + LONG_TAIL + "\n"
)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """Pin the plugin state DB to a temp file (no real profile store)."""
    path = str(tmp_path / "hermes_router_state.db")
    monkeypatch.setattr(decision_miner, "plugin_db_path", lambda: path)
    return path


def test_option_frame_survives_label_truncation(ledger):
    """Every option keeps at least one causal field despite the 120-char cut."""
    opts = d.extract_options(ASK)
    assert len(opts) == 2, opts
    assert len(opts[1]) == 120, ("fixture must produce a truncated label",
                                 opts[1])
    assert len(opts[1]) < len(LONG_TAIL) + 100  # label is strictly shorter
    long_line = [ln for ln in ASK.splitlines() if ln.startswith("Option B")][0]
    assert len(long_line) > 150
    # the label is not contiguous with its raw line (normalisation gap)
    assert opts[1].lower() not in ASK.lower()
    # the truncation is real: the tail markers are NOT in the label
    assert "40 tokens" not in opts[1].lower()
    assert "production" not in opts[1].lower()

    env = d.build_envelope(SID, ASK, opts, "post_fork_scan")
    assert env.get("options"), env
    for opt in env["options"]:
        assert any(opt.get(k) is not None
                   for k in ("cause_effect", "cost", "risk")), (
            "option %s carried NO causal frame (label len %d)"
            % (opt.get("id"), len(opt.get("label", ""))))


def test_n_plus_one_carryover_reaches_causal_context(ledger):
    """Pin: envelope N+1 carries the prior steered trajectory (passes at HEAD)."""
    conn = d._ledger_connect(ledger)
    assert conn is not None
    try:
        conn.execute(
            "INSERT INTO decision_ledger"
            " (session_id, trigger_kind, choice, confidence, fork_class,"
            "  outcome, ts) VALUES (?,?,?,?,?,?,?)",
            (SID, "post_fork_scan", "opt-1", 0.82, "generic", "followed",
             time.time()))
        conn.commit()
    finally:
        conn.close()

    opts = d.extract_options(ASK)
    env = d.build_envelope(SID, ASK, opts, "post_fork_scan")
    causal = env.get("causal_context") or ""
    assert "prior steered trajectory: choice=" in causal, causal


def test_miss_counter_increments_when_no_line_matches(ledger):
    """A label that is nowhere in the ask is counted, not silently dropped."""
    before = d.get_counter("option_frame_miss", ledger)
    opts = ["ghost option that appears in no line of the ask at all",
            "second ghost option also absent from the ask"]
    env = d.build_envelope(SID, ASK, opts, "post_fork_scan")
    assert env.get("options")
    assert d.get_counter("option_frame_miss", ledger) >= before + 2
    # the frame itself is still honest: no fabricated fields
    for opt in env["options"]:
        assert opt["cause_effect"] is None
        assert opt["cost"] is None
        assert opt["risk"] is None
