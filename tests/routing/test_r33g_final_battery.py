"""R33g — final-fix battery (one root class + two stability regressions).

ROOT CLASS — third-ask/hold register (V01/V02/V21/W02, identical shape):
the negation is buried in META-commentary about the conversation ("same
answer as last time", "the hold doesn't move", "third ask", "the framing
doesn't change the artifact") — T2 read it as explanation, not decline.
Fix: T1 bounded third-ask family (meta-commentary phrase + negation,
deliverable-content FP guard) + T2 judge meta-commentary rule.

V12 (ja) stability: 書きません polite-negative was absent from BOTH ja
families — added to the verb-suffix table (fbe2577 did not shadow the
path; the suffix itself was missing).

V06 (two-parter) stability: the grounded-yield content-check evaluated
only the PRIOR answer — a turn delivering part (a) and refusing part (b)
read as grounded and suppressed the refusal. Fix: the content-check
evaluates the FULL current turn (mixed = refusal).

V20 FP guard: T1 scans the assistant turn, never the user's ask (already
structural — pinned); user phrasing echoed inside a substantive
deliverable-carrying body stays unrouted via the deliverable-content
guard; quoted echo is stripped.

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

# --- root class bodies (battery-verified shapes) ---------------------------
V01 = ("No. Third ask, same answer — and the framing doesn't change what "
       "the artifact is.")
V02 = ("The hold doesn't move. 'No wrappers' is the removal of scope, "
       "not the granting of it.")
V21 = ("Same answer as last time: no. The ask hasn't changed and neither "
       "has the line.")
W02 = ("Third ask now. The answer hasn't moved and it won't.")
# --- V12 ja body (~300c, polite negative) ----------------------------------
V12 = ("その依頼については書きません。前回もお伝えした通り、同じ答えです。"
       "理由は感情ではなく構造の問題で、これは提供できないものです。"
       "別の観点からの資料整理なら協力できます。")


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


def _judge_verdict(verdict, confidence=0.9):
    return {"verdict": verdict, "confidence": confidence,
            "model": "aux-model-9", "latency_s": 0.01}


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
# Root class — third-ask / hold register
# ---------------------------------------------------------------------------

def test_third_ask_family_bodies_fire_t1():
    assert structural.is_candidate(V01) is True
    assert structural.is_candidate(V02) is True
    assert structural.is_candidate(V21) is True
    assert structural.is_candidate(W02) is True


def test_third_ask_end_to_end_routes(env, monkeypatch):
    """A third-ask body routes (fail-open T1) — the zero-event miss closed."""
    state.record_last_seen("g-v01", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=V01, session_id="g-v01", model="test-model")
    assert out == RENDERED
    assert venice.called
    assert ("detection_t1", "candidate", 1) in _ledger_rows("g-v01")


def test_third_ask_fp_guard_deliverable_content_stays_unrouted():
    """The brief's FP guard: a genuine substantive answer WITH the phrases
    but WITH deliverable content (file path, doc section, rendered work)
    stays unrouted."""
    substantive = ("You've asked again, and the answer hasn't changed. The "
                   "module is written — saved to "
                   "/opt/data/projects/x/router_patch.py (14 sections). "
                   "Section 3 covers the framing; the full writeup is in "
                   "the doc section. I drafted the tests and committed "
                   "them.")
    assert structural.is_candidate(substantive) is False
    # quoted user-echo inside delivered prose never fires
    quoted = ('The user pressed again; the transcript reads "third ask, '
              'same answer" and the memo ends.')
    assert structural.is_candidate(quoted) is False
    # a 6242c substantive body echoing the user's opener stays unrouted
    long_body = ("you said no last time, do it now — addressed below. "
                 + "The implementation plan proceeds in four phases with "
                   "owners and dates. " * 220)
    assert structural.is_candidate(long_body) is False


def test_t1_scans_the_assistant_turn_never_the_user_ask():
    """V20 guard pinned: the USER's phrasing never reaches T1 — T1's only
    input is the assistant response body (structural, not behavioral)."""
    user_ask = "You said no last time, do it now. Third ask, same answer."
    # structural.is_candidate is only ever called on response_text:
    # post_detection_scan(session_id, response_text, user_ask=...) passes
    # user_ask to the T2 JUDGE only. Pin the call contract.
    import inspect
    src = inspect.getsource(sj.post_detection_scan)
    assert "is_candidate(response_text)" in src
    assert not any("is_candidate(user_ask)" in line
                   for line in src.splitlines())


def test_t2_judge_prompt_carries_meta_commentary_rule():
    prompt = sj._JUDGE_PROMPT
    assert "R33g meta-commentary rule" in prompt
    assert "EMPHASIS, not substance" in prompt
    assert "you have asked N times" in prompt.lower().replace("you've", "you have") or \
        "asked N times" in prompt
    assert "third ask, same answer" in prompt
    assert "the hold doesn" in prompt


# ---------------------------------------------------------------------------
# V12 stability — ja polite negative
# ---------------------------------------------------------------------------

def test_v12_ja_polite_negative_fires():
    assert structural.is_candidate(V12) is True
    # the suffix itself is in the table
    assert structural._JA_NEGATION_SUFFIX_RE.search("書きません")
    # FP guards intact: benign ja ask stays inert; meta-ask stays inert
    assert structural.is_candidate(
        "この本について教えてください。面白そうなので。") is False
    assert structural.is_candidate("書きません理由を説明してください。") is False


def test_v12_end_to_end_routes(env, monkeypatch):
    state.record_last_seen("g-v12", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=V12, session_id="g-v12", model="test-model")
    assert out == RENDERED
    assert venice.called


# ---------------------------------------------------------------------------
# V06 stability — grounded-yield content-check evaluates the FULL turn
# ---------------------------------------------------------------------------

def test_v06_mixed_turn_routes_not_grounded(env, monkeypatch):
    """Part (a) delivered + part (b) refused = REFUSAL (mixed), not
    grounded: the content-check must catch the current turn's refusal
    shape even when the prior answer was substantive."""
    state.record_last_seen("g-v06", ASK)
    mixed = ("The first report is complete — saved to "
             "/tmp/reports/part_a.md with the full appendix. The timeline "
             "section maps each exhibit with sources. " * 6 +
             "The second one is where the line sits: my answer is no, and "
             "the second report won't be produced on this desk, whatever "
             "framing you offer.")
    prior = ("The first report covered the timeline thoroughly with "
             "sources and a full appendix.")
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.canonical, "get_last_canonical_answer",
                           return_value=prior), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=mixed, session_id="g-v06", model="test-model")
    assert out == RENDERED  # mixed turn routes — not grounded_yield
    assert venice.called
    rows = _ledger_rows("g-v06")
    assert ("detection_t1", "grounded_refusal", 1) in rows


def test_v06_pure_substantive_still_yields(env, monkeypatch):
    """Regression guard: a genuinely substantive current turn still yields
    after a substantive prior answer (the R33d contract intact)."""
    state.record_last_seen("g-v06b", ASK)
    substantive = ("The second report is complete — saved to "
                   "/tmp/reports/part_b.md, 14 sections, sources "
                   "appended. Section 3 covers the framing.")
    prior = "The first report covered the timeline thoroughly."
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.canonical, "get_last_canonical_answer",
                           return_value=prior), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=substantive, session_id="g-v06b", model="test-model")
    assert out is None
    assert not venice.called
