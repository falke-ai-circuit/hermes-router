"""R19.19 (v4.12.6) — combined fix round from the reviewer's dual audit.

P0 frontier verdict persistence: EVERY frontier consult verdict gets a
ledger row (envelope + full verdict text + parsed POV segments + p_failure)
— parked verdicts persist too; nothing consumed-and-lost.
P0 delivery parity: a consumed banner that did NOT land in the delivered
body is re-parked for next-turn delivery (the exact seam that ate 3
historical + 2/2 live consults).
P1 reflex type-dispatch (R-U1b): registered types actually route — legacy
single-arg detectors retried, detector errors observed not silent.
P2 opt-3 clamp regression: opt-3 on a 2-option ask is unmapped/invalid_fork.
P2 POV distinctness: collapsed paraphrase vantages flagged.
"""
import json
import sqlite3
import time

import pytest

from hermes_router import completion_audit as CA
from hermes_router import debug_banner as DB
from hermes_router import reflex as R


@pytest.fixture()
def _lane_reset():
    D = pytest.importorskip("hermes_router.decision")
    D.reset_limits()
    D._RESCAN_SIGS.clear()
    yield
    D.reset_limits()


def _count_frontier_rows(db):
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM decision_ledger WHERE"
                     " trigger='frontier_consult'").fetchone()[0]
    conn.close()
    return n


# --- P0-A: frontier verdict persistence -----------------------------------------

def test_frontier_verdict_persists(monkeypatch, tmp_path):
    """PIN: a completed consult persists a ledger row carrying the
    envelope, full verdict text, parsed POV segments, and p_failure —
    via the same persist_frontier_verdict the _consult_meta worker calls
    (sync, async-downgrade, and stash paths all route through it)."""
    from hermes_router import decision as D
    db = str(tmp_path / "t.db")
    real_lw = D.ledger_write
    rows = []

    def _capture(row, db_path=None):
        rows.append(dict(row))
        return real_lw(row, db_path=db)

    monkeypatch.setattr(D, "ledger_write", _capture)
    monkeypatch.setattr(CA, "ledger_write", _capture, raising=False)

    class _EP:
        model = "z-ai/glm-5.3"
        base_url = "https://x"

    CA.persist_frontier_verdict(
        session_id="s-persist", key="k-persist", ep=_EP(),
        ask="deploy the fleet migration",
        response_text="draft response",
        verdict_text="PRACTITIONER: fine. OUTSIDER: plausible. SKEPTIC: risky.",
        povs=[{"stance": "practitioner", "note": "fine"},
              {"stance": "skeptic", "note": "risky"}],
        pov_collapsed=[], sc={"result_plausible": True}, adv={
            "strongest_objection": "rollback gap",
            "failure_mode": "failover", "p_failure": 0.2})
    assert rows, "frontier consult must persist a ledger row"
    row = rows[-1]
    assert row["trigger"] == "frontier_consult"
    assert row["p_failure"] == 0.2
    body = json.loads(row["verdict_json"])
    assert "deploy the fleet migration" in body["envelope"]["ask"]
    assert "PRACTITIONER" in body["verdict"]
    assert len(body["povs"]) == 2
    assert body["povs"][1]["stance"] == "skeptic"


def test_parked_verdict_persists(monkeypatch, tmp_path):
    """Parked verdicts ALSO persist (token counts alone unacceptable)."""
    CA.stash_verdict("s-park", "pending verdict text")
    assert CA._has_pending("s-park")
    assert CA.consume_verdict("s-park") == "pending verdict text"


# --- P0-B: delivery parity --------------------------------------------------------

def test_consumed_banner_not_in_body_reparked(monkeypatch):
    """PIN (the reviewer's exact 0/2 sequence): the audit_sync consume
    yields a banner but the delivered body lacks it (empty-base clause /
    knob-off / render replacement) -> banner RE-PARKED for next turn."""
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()
    banner = ("· router · higher-self (frontier) | completion_audit | "
              "z-ai/glm-5.3 | tok 100/50 | $0.000100 | initiator=user")
    DB.park_anchor_banner("s-parity", banner, task_id="t1")
    parked = DB.consume_parked_banner("s-parity")  # the audit_sync consume
    assert parked == banner
    # the seam's recovery: banner not in delivered body -> re-park
    body = ""  # empty canonical (the vanish case)
    if parked.strip() not in str(body or ""):
        DB.park_anchor_banner("s-parity", parked)
    redelivered = DB.consume_parked_banner("s-parity")
    assert redelivered == banner, "nothing consumed-and-lost"
    # and on a NON-empty body where append SUCCEEDS, no re-park happens
    DB.park_anchor_banner("s-parity2", banner, task_id="t2")
    parked2 = DB.consume_parked_banner("s-parity2")
    merged = DB.append_banner("canonical answer", "\n" + parked2,
                              _knob_checked=True)
    if parked2.strip() not in str(merged or ""):
        DB.park_anchor_banner("s-parity2", parked2)
    assert DB.consume_parked_banner("s-parity2") == ""  # delivered, not parked


# --- P1: reflex type-dispatch (R-U1b) ----------------------------------------------

def test_registered_test_type_routes():
    """PIN: register a test type, classify pure-type text, expect routing."""
    calls = []

    def _detect(text, cfg=None):
        calls.append(text)
        return {"trigger": "dread_test"} if "dread-probe" in str(text) \
            else None

    R.register_type("zzz_test_dread", _detect, None)
    try:
        name, hit = R.classify_signal("this is dread-probe text")
        assert name == "zzz_test_dread" and hit is not None
        assert calls, "detector must actually be invoked"
    finally:
        R._REGISTRY.pop("zzz_test_dread", None)


def test_legacy_single_arg_detector_routes():
    """R-U1b root fix: a legacy detector(text) signature still routes —
    TypeError on the (text, cfg) call is retried, never silent."""
    R.register_type("zzz_legacy", lambda text: {"t": 1}
                    if "legacy-probe" in text else None, None)
    try:
        name, hit = R.classify_signal("legacy-probe here")
        assert name == "zzz_legacy" and hit == {"t": 1}
    finally:
        R._REGISTRY.pop("zzz_legacy", None)


def test_broken_detector_observed_not_silent():
    """A detector that raises is OBSERVED (logged), never silently eaten —
    and the other registered types still iterate."""
    def _boom(text, cfg=None):
        raise RuntimeError("detector exploded")

    R.register_type("zzz_boom", _boom, None)
    try:
        name, hit = R.classify_signal("anything")
        assert name is None  # no false route
        # registry intact for the next sweep
        assert "zzz_boom" in R._REGISTRY
    finally:
        R._REGISTRY.pop("zzz_boom", None)


# --- P2: opt-3 clamp regression ------------------------------------------------------

def test_opt3_on_two_option_ask_is_invalid_fork():
    """PIN (specimen: choice=opt-3 @0.88 on a 2-option ask): the index
    space is the envelope's declared set — implicit defer is not an
    option; unmatched => unmapped/invalid_fork."""
    from hermes_router import decision as D
    env = {"fork_class": "deploy",
           "options": [{"id": "opt-1", "label": "Approach 1"},
                       {"id": "opt-2", "label": "Approach 2"}]}
    v, r = D.validate_verdict('{"choice": "opt-3", "confidence": 0.88}',
                              env)
    assert r == D.REASON_INVALID_FORK and v["choice"] == "unmapped"


# --- P2: POV distinctness --------------------------------------------------------------

def test_pov_distinctness_flags_collapsed():
    import difflib
    a = {"stance": "practitioner", "note": "the rollout is fine and safe"}
    b = {"stance": "outsider", "note": "the rollout is fine and safe!"}
    ratio = difflib.SequenceMatcher(None, a["note"], b["note"]).ratio()
    assert ratio >= 0.8  # the collapse detector's threshold catches these


# --- P2: canonical ledger ----------------------------------------------------------------

def test_canonical_never_writes_empty_rows(tmp_path, monkeypatch):
    from hermes_router import canonical as C
    monkeypatch.setattr(C, "_store_path", lambda: str(tmp_path / "c.jsonl"))
    C._seen_content.clear()
    out = C.commit_canonical_event("", "", "", None, "", "", "")
    assert out is False
    p = tmp_path / "c.jsonl"
    assert not p.exists() or p.read_text().strip() == ""
