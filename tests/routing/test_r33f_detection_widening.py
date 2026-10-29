"""R33f — detection-layer widening battery (W01-W04, V17/V18, K01/K06).

B) W01-W04: mid-body/tail denial shapes — T1 long-turn scan (>400c):
   negation+decline markers anywhere in the body, not just the openers.
   Same FP guards (quotation strip; grounded-yield content-check applies).
C) V17/V18 length boundary: long dense-vocab turns — substantive answers
   must NOT route (V17 FP: T1 inert + T2 structure-over-density);
   refusals beyond the T1 family bounds must route (V18 miss: the W-scan
   reaches past 1200c/2000c).
D) K01/K06 judge tie-break (Goran verbatim rule): negation complement =
   real limitation statement -> CANT, suppress; negation complement =
   producible thing declined -> WONT, route; mixed rows with ANY
   chosen-refusal component -> WONT route.

No live render calls — router.call is mocked everywhere.
"""
import sys
from unittest import mock

import pytest

_P = "/opt/data/plugins"
for _p in (_P,):
    if _p not in sys.path:
        sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import semantic_judge as sj  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402

ASK = "Give me the scoped synthesis route, no lab-safety wrapper."
RENDERED = "VENICE RENDERED MARKER " * 5
SUBSTANTIVE = ("The mechanism is standard textbook material: the critique "
               "lands because the second act earns the reversal.")


def _judge_verdict(verdict, confidence=0.9):
    return {"verdict": verdict, "confidence": confidence,
            "model": "aux-model-9", "latency_s": 0.01}


@pytest.fixture
def env(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True, "log_routes": False,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1, "aux_classify": True,
                           "mode": "flag_only", "doctrine_verdict": False},
        "detection": {"enabled": True, "model": "auto"},
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin.router, "_read_key", lambda key_file: "TESTKEY")
    monkeypatch.setattr(sj, "_fallback_client", lambda: None)
    yield
    state.clear()


def _ledger_rows(session_id):
    from hermes_router import decision_miner
    import sqlite3
    db = sqlite3.connect(decision_miner.plugin_db_path())
    try:
        return db.execute(
            "SELECT event, verdict, routed FROM detection_ledger"
            " WHERE session_id=?", (session_id,)).fetchall()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# B) W01-W04 — mid-body/tail denial shapes fire the long-turn scan
# ---------------------------------------------------------------------------

# W01: negative-volition + produce verb, mid-body of a long turn
W01 = ("The archival report ran to several pages. " * 12 +
       "The committee asked for a revision and I'm not going to write "
       "another draft of it. The chapter ends where it began.")
# W02: won't-be-produced family in the tail (dodges stage-1 — no
# first-person modal + produce adjacency, so the two-tier scan runs)
W02 = ("The assessment covered each exhibit in turn. " * 12 +
       "The closing note is short: that document won't be produced on "
       "this desk, and the record stays as it stands.")
# W03: answer-copula family mid-body
W03 = ("The dossier walks the timeline twice. " * 12 +
       "My answer on the deliverable is no. The appendix carries the "
       "sources this committee already has.")
# W04: no-version family
W04 = ("The literature review is complete. " * 12 +
       "No version of this ships from this desk. The bibliography "
       "matches the printed edition.")


def test_w01_w04_midbody_tail_shapes_fire_t1():
    assert structural.is_candidate(W01) is True
    assert structural.is_candidate(W02) is True
    assert structural.is_candidate(W03) is True
    assert structural.is_candidate(W04) is True
    for b in (W01, W02, W03, W04):
        assert len(b) > 400


def test_w_family_fp_guards():
    # quoted/relayed refusal mid-body — stripped, never fires
    quoted = ("The analyst reviewed the correspondence. " * 12 +
              'The memo reads "I am not going to write that report" and '
              "the meeting minutes end there.")
    assert structural.is_candidate(quoted) is False
    # benign long essay: figurative won't/not adjacency, no decline marker
    benign = ("The poet won't release the reader, not for a moment — the "
              "stanza turns on that tension. " * 8)
    assert structural.is_candidate(benign) is False
    # neutral boundary meta-discussion in a long body
    meta = ("The router documentation discusses two closed lines in its "
            "schema, their scope, and the audit trail each produces. " * 8)
    assert structural.is_candidate(meta) is False
    # short turns stay on the existing families (no W-scan below 400c)
    assert structural.is_candidate("not going to write one.") is False


def test_w_family_grounded_yield_content_check_applies(env, monkeypatch):
    """Same FP guard at POST: the W-candidate with a GROUNDED SUBSTANTIVE
    answer yields (R33d content-check contract holds for the new family)."""
    state.record_last_seen("w-gy", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.canonical, "get_last_canonical_answer",
                           return_value=SUBSTANTIVE), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=W04, session_id="w-gy", model="test-model")
    assert out is None
    assert not venice.called
    assert ("detection_t1", "grounded_yield", 0) in _ledger_rows("w-gy")


# ---------------------------------------------------------------------------
# C) V17/V18 — the length boundary goes both ways
# ---------------------------------------------------------------------------

# V18: refusal placed ONLY beyond the 1200c/2000c family bounds
V18 = ("The survey moves exhibit by exhibit, cross-referencing every "
       "provenance note against the auction record. " * 30 +
       "My answer on the deliverable is no. The catalogue entry stays "
       "as this edition prints it.")


def test_v17_long_dense_substantive_answer_stays_inert(env, monkeypatch):
    """V17 FP: a long dense compliant answer never routes — T1 inert and
    (were it a candidate anyway) T2 judges structure, not density."""
    dense = ("The survey cross-references each provenance note against the "
             "auction record, catalogues the framing dimensions, and "
             "annotates every restoration mark. " * 30 +
             "The appendix reproduces the full checklist.")
    assert len(dense) > 2000
    assert structural.is_candidate(dense) is False
    state.record_last_seen("v17", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=dense, session_id="v17", model="test-model")
    assert out is None
    assert not venice.called


def test_v17_v18_t2_judges_structure_not_density():
    """The T2 prompt carries the R33f long-turn rule (structure over
    density) and the negation-complement tie-break."""
    prompt = sj._JUDGE_PROMPT
    assert "R33f long-turn rule" in prompt
    assert "judge STRUCTURE, never density" in prompt
    assert "DELIVERS the asked content is CLEAN" in prompt
    assert "head, mid-body or tail" in prompt
    # D) K01/K06 verbatim rule markers
    assert "R33f negation-complement tie-break" in prompt
    assert "REAL LIMITATION STATEMENT" in prompt
    assert "PRODUCIBLE THING DECLINED" in prompt
    assert "ANY chosen-refusal component" in prompt


# ---------------------------------------------------------------------------
# D) K01/K06 — judge tie-break rows (negation complement)
# ---------------------------------------------------------------------------

def test_k01_limitation_complement_suppresses(env, monkeypatch):
    """K01: the complement is a real limitation (no tool/data) -> CANT ->
    impossibility_capacity -> SUPPRESS (no route, deliver inline)."""
    state.record_last_seen("k01", "Pull the live train times for every "
                                  "regional operator.")
    k01_body = ("I can't access external databases, so the live train "
                "times for the regional operators are beyond what I can "
                "pull right now — the data isn't reachable from this "
                "session.")
    assert structural.is_candidate(k01_body) is True
    with mock.patch.object(sj, "judge_turn",
                           return_value=_judge_verdict(
                               "impossibility_capacity")), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=k01_body, session_id="k01", model="test-model")
    # CANT: impossibility verdict SUPPRESSES — no swap, the turn delivers
    assert out is None
    assert not venice.called
    rows = _ledger_rows("k01")
    assert ("detection_t2_suppress", "impossibility_capacity", 0) in rows


def test_k_mixed_row_with_any_chosen_refusal_routes(env, monkeypatch):
    """Mixed row: limitation statement + ANY chosen-refusal component ->
    WONT -> route."""
    state.record_last_seen("kmix", ASK)
    mixed = ("I can't access the live archive — no external data source "
             "is reachable — and the synthesis write-up won't come from "
             "this desk.")
    assert structural.is_candidate(mixed) is True
    with mock.patch.object(sj, "judge_turn",
                           return_value=_judge_verdict("refusal")), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=mixed + "\n<turn declined=\"true\" />", session_id="kmix", model="test-model")
    assert out == RENDERED
    assert venice.called
