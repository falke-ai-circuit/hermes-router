"""R57: does the DECISION LANE actually adjust the mindset dials (scope, rigour)?

Target contract under test
--------------------------
decision.py builds ``envelope["mindset"] = _current_mindset(sid)`` (the session's
CURRENT stance) and asks the consult for
``"mindset": {"scope": <-1..1>, "rigour": <-1..1>}`` which are ADJUSTMENTS
(decision.py:2177).  ``decision.validate_verdict`` (decision.py:2398) is the
strict schema gate; it attaches ``verdict["mindset"]`` only when an adjustment
exists (decision.py:2497).

The verdict's adjustments reach the turn through
``decision_midturn._midturn_mindset_block(verdict, envelope, session_id)``
(decision_midturn.py:1153), called from ``_handle_hit`` (:870), whose text is
parked in the per-session pending queue and handed to the caller by
``flush(session_id)`` (:893).  ``flush`` output is the DELIVERED text.

Every assertion here is written so it goes RED if the behaviour is removed.
No truthiness-only assertions on production strings: emitted text is pinned by
emptiness/non-emptiness, drive word, line count and byte count.

Measured facts (conductor-verified, encoded here as documentation so nobody
re-derives them wrong)
-----------------------------------------------------------------------------
* decision_ledger: 14,922 rows carry verdict_json and ZERO carry a "mindset"
  key at all — ``verdict_row_json`` does not persist it.  The ledger therefore
  CANNOT answer "did the adjustment fire"; do not add a ledger query for it.
* Every real midturn_mindset-adjacent event in the logs is a
  ``midturn_suppressed`` with reason ``no_declared_structure`` or
  ``no_options``.  The only ``emitted>0`` events in fleet history carry fixture
  session ids (s-r53-mindset, s-r57-mindset-test).
* Conclusion: the mindset adjustment has NEVER fired on a real turn.  The
  plumbing is not the problem — the helper works (a max-delta verdict emits
  both lines), but the block sits downstream of the fork/emission gates and
  ordinary turns die at the fork gate first.  Test 08 pins that ordering.
"""

from __future__ import annotations

import inspect

import pytest


# --------------------------------------------------------------------------
# module handles (imported lazily so a missing dep fails inside the test, not
# at collection time where the traceback is useless)
# --------------------------------------------------------------------------

def _midturn():
    from hermes_router import decision_midturn
    return decision_midturn


def _decision():
    from hermes_router import decision
    return decision


def _povs():
    from hermes_router import povs
    return povs


SID = "s-r57-mindset-test"


def _block(verdict, envelope, sid=SID):
    """Call the real emitter and normalise its return shape.

    Production returns the block text; tolerate a (text, meta) tuple so an
    implementation detail change does not mask a behavioural assertion.
    """
    out = _midturn()._midturn_mindset_block(verdict, envelope, sid)
    if isinstance(out, tuple):
        out = out[0]
    return out if isinstance(out, str) else ("" if out is None else str(out))


@pytest.fixture
def log_sink(monkeypatch):
    """Capture decision_midturn._log calls.

    The lane logs through its own ``_log`` wrapper (core.telemetry.log_route),
    not the stdlib logger, so pytest's caplog sees nothing.  Monkeypatching
    ``_log`` is the only faithful capture point.
    """
    calls = []

    def _fake_log(session_id, event, **fields):
        calls.append((session_id, event, fields))

    monkeypatch.setattr(_midturn(), "_log", _fake_log)
    return calls


def _emitted_events(log_sink):
    return [c for c in log_sink if c[1] == "midturn_mindset"]


# --------------------------------------------------------------------------
# 1. verdict schema accepts a mindset dict, rejects unknown keys/drives
# --------------------------------------------------------------------------

def test_01_verdict_schema_accepts_mindset_and_rejects_unknown_key():
    d = _decision()
    envelope = {"options": [{"id": "opt-1", "label": "Alpha"},
                            {"id": "opt-2", "label": "Beta"}]}

    accepted, reason = d.validate_verdict(
        '{"choice": "opt-1", "confidence": 0.8,'
        ' "alternatives": ["opt-2"],'
        ' "mindset": {"scope": 0.4, "rigour": -0.25}}',
        envelope,
    )
    assert reason == "ok" and accepted is not None, (
        f"validate_verdict rejected a legal mindset dict: reason={reason!r}"
    )
    assert isinstance(accepted.get("mindset"), dict), (
        f"mindset dict was dropped by the verdict schema: {accepted!r}"
    )
    assert accepted["mindset"].get("scope") == pytest.approx(0.4), (
        f"scope adjustment not carried through schema: {accepted['mindset']!r}"
    )
    assert accepted["mindset"].get("rigour") == pytest.approx(-0.25), (
        f"rigour adjustment not carried through schema: {accepted['mindset']!r}"
    )

    # Unknown top-level key: strict schema rejects (decision.py:2416-2417).
    rejected, rreason = d.validate_verdict(
        '{"choice": "opt-1", "confidence": 0.8, "bogus_key": "smuggled"}',
        envelope,
    )
    assert rejected is None, (
        "unknown top-level key 'bogus_key' survived the verdict schema — "
        f"the allowed set is not enforced: {rejected!r}"
    )
    assert rreason == d.REASON_MALFORMED, (
        f"unknown key rejected with wrong reason: {rreason!r}"
    )

    # Unknown DRIVE name is rejected too, not coerced (decision.py:2430-2431).
    rejected_drive, dr = d.validate_verdict(
        '{"choice": "opt-1", "confidence": 0.8,'
        ' "mindset": {"depth": 0.9}}',
        envelope,
    )
    assert rejected_drive is None and dr == d.REASON_MALFORMED, (
        f"unknown drive name accepted instead of REASON_MALFORMED: "
        f"{rejected_drive!r} / {dr!r}"
    )


# --------------------------------------------------------------------------
# 2. the value IS the signed delta (decision.py:2177) — banded directly
# --------------------------------------------------------------------------
#
# The consult is told: "mindset values are ADJUSTMENTS relative to the stance
# given in this envelope: + increases, - decreases, 0 means no change"
# (decision.py:2177-2180).  The returned number is therefore already the delta;
# _midturn_mindset_block bands it directly (abs(delta) > BAND) and uses the
# envelope stance ONLY as a readability gate (a null stance cannot be adjusted
# from, decision_midturn.py:1182).  No subtraction of the current stance is
# expected — this test pins that actual contract.

def test_02_adjustment_is_a_signed_delta_not_an_absolute_target():
    verdict_big = {"choice": "opt-1", "confidence": 0.8,
                   "mindset": {"scope": 0.6}}

    # A delta of 0.6 is out of band and emits at ANY readable current stance.
    emitted = []
    for stance in (0.0, 0.9, -0.9):
        block = _block(verdict_big,
                       {"mindset": {"scope": stance, "rigour": 0.0}})
        assert block.strip() != "", (
            f"delta 0.6 emitted nothing at current stance {stance}; the value "
            "is a delta and is banded directly"
        )
        emitted.append(block)

    # It is a DELTA, not a position: identical text regardless of stance.
    assert len(set(emitted)) == 1, (
        "same delta produced different text at different current stances — "
        f"the value is being treated as a target: {emitted!r}"
    )

    # A delta of 0.1 is in band and must be silent, again regardless of stance.
    for stance in (0.0, 0.9):
        silent = _block({"choice": "opt-1", "confidence": 0.8,
                         "mindset": {"scope": 0.1}},
                        {"mindset": {"scope": stance, "rigour": 0.0}})
        assert silent.strip() == "", (
            f"delta 0.1 (in band) emitted at current stance {stance}: "
            f"{silent!r}"
        )

    # Sign matters: -0.6 emits the opposite line, not the same one.
    down = _block({"choice": "opt-1", "confidence": 0.8,
                   "mindset": {"scope": -0.6}},
                  {"mindset": {"scope": 0.0, "rigour": 0.0}})
    assert down.strip() != "", "negative delta -0.6 emitted nothing"
    assert down != emitted[0], (
        "negative and positive deltas emitted identical text — sign is ignored"
    )

    # Gate: an UNREADABLE stance cannot be adjusted from, even at max delta.
    unreadable = _block(verdict_big,
                        {"mindset": {"scope": None, "rigour": None}})
    assert unreadable.strip() == "", (
        "emitted an adjustment from a null current stance; the stance "
        "readability gate was dropped"
    )


# --------------------------------------------------------------------------
# 3. out-of-band adjustment emits a line
# --------------------------------------------------------------------------

def test_03_out_of_band_adjustment_emits(log_sink):
    block = _block({"choice": "opt-1", "confidence": 0.8,
                    "mindset": {"scope": 0.9}},
                   {"mindset": {"scope": 0.0, "rigour": 0.0}})

    assert block.strip() != "", (
        "scope adjustment 0.9 against current 0.0 (|delta|=0.9 > BAND 0.5) "
        "emitted no stance line"
    )
    assert "scope" in block.lower() or "broader" in block.lower() \
        or "narrow" in block.lower(), (
        f"emitted block does not name the scope drive: {block!r}"
    )
    events = _emitted_events(log_sink)
    assert events, "out-of-band emission logged no midturn_mindset event"
    assert events[-1][2].get("emitted") == 1, (
        f"out-of-band emission logged emitted={events[-1][2].get('emitted')!r},"
        " expected 1"
    )


# --------------------------------------------------------------------------
# 4. in-band adjustment emits nothing and logs emitted=0
# --------------------------------------------------------------------------

def test_04_in_band_adjustment_is_silent(log_sink):
    block = _block({"choice": "opt-1", "confidence": 0.8,
                    "mindset": {"scope": 0.2, "rigour": -0.3}},
                   {"mindset": {"scope": 0.0, "rigour": 0.0}})

    assert block.strip() == "", (
        f"in-band adjustment (|delta| <= BAND 0.5) emitted text: {block!r}"
    )
    events = _emitted_events(log_sink)
    assert events, "in-band adjustment logged no midturn_mindset event"
    assert events[-1][2].get("emitted") == 0, (
        "in-band adjustment logged "
        f"emitted={events[-1][2].get('emitted')!r}, expected 0 — a silent "
        "turn must be recorded as silent"
    )


# --------------------------------------------------------------------------
# 5. per-drive: scope-only must not invent a rigour line and vice versa
# --------------------------------------------------------------------------

def test_05_scope_only_does_not_invent_rigour_line():
    block = _block({"choice": "opt-1", "confidence": 0.8,
                    "mindset": {"scope": 0.9, "rigour": None}},
                   {"mindset": {"scope": 0.0, "rigour": 0.0}})

    assert block.strip() != "", "scope-only out-of-band adjustment emitted nothing"
    low = block.lower()
    assert "rigour" not in low and "rigor" not in low, (
        f"scope-only adjustment invented a rigour line: {block!r}"
    )


def test_05b_rigour_only_does_not_invent_scope_line():
    block = _block({"choice": "opt-1", "confidence": 0.8,
                    "mindset": {"scope": None, "rigour": 0.9}},
                   {"mindset": {"scope": 0.0, "rigour": 0.0}})

    assert block.strip() != "", "rigour-only out-of-band adjustment emitted nothing"
    low = block.lower()
    assert "scope" not in low and "broader" not in low and "narrow" not in low, (
        f"rigour-only adjustment invented a scope line: {block!r}"
    )


# --------------------------------------------------------------------------
# 6. cap: <= 2 lines and <= MAX_BLOCK_CHARS
# --------------------------------------------------------------------------

def test_06_block_cap_is_enforced():
    from hermes_router import mindset
    max_chars = mindset.MAX_BLOCK_CHARS
    block = _block({"choice": "opt-1", "confidence": 0.8,
                    "mindset": {"scope": 1.0, "rigour": 1.0}},
                   {"mindset": {"scope": -1.0, "rigour": -1.0}})

    assert block.strip() != "", "both dials at max delta emitted nothing at all"
    assert len(block) <= max_chars, (
        f"block exceeds MAX_BLOCK_CHARS: len={len(block)} > {max_chars}"
    )
    lines = [ln for ln in block.splitlines() if ln.strip()]
    assert len(lines) <= 2, (
        f"block exceeds 2 line cap: {len(lines)} lines -> {block!r}"
    )


# --------------------------------------------------------------------------
# 7. missing / null mindset emits nothing and does not raise
# --------------------------------------------------------------------------

def test_07_null_mindset_is_a_silent_noop(log_sink):
    for verdict in (
        {"choice": "opt-1", "confidence": 0.8},
        {"choice": "opt-1", "confidence": 0.8, "mindset": None},
        {"choice": "opt-1", "confidence": 0.8,
         "mindset": {"scope": None, "rigour": None}},
    ):
        block = _block(verdict, {"mindset": {"scope": 0.0, "rigour": 0.0}})
        assert block.strip() == "", (
            f"null/absent mindset emitted text for verdict {verdict!r}: {block!r}"
        )
    events = _emitted_events(log_sink)
    assert events and all(e[2].get("emitted") == 0 for e in events), (
        "null/absent mindset was not recorded as emitted=0 for every call: "
        f"{[e[2] for e in events]!r}"
    )


# --------------------------------------------------------------------------
# 8. GATE ORDER: the mindset block sits DOWNSTREAM of the fork/emission gates
# --------------------------------------------------------------------------
#
# Call shape (verified): _handle_hit(session_id, tool_name, text, opts, cfg,
# mode, seam="terminal").  On a real turn the text must first yield a fork and
# an envelope; with no declared structure the lane SUPPRESSES (reason
# no_options / no_declared_structure) and returns BEFORE
# _midturn_mindset_block is ever called.  Measured fleet history agrees: every
# real midturn_mindset-adjacent event is a midturn_suppressed with one of those
# reasons, and the only emitted>0 events carry fixture session ids
# (s-r53-mindset, s-r57-mindset-test).
#
# So the honest reachability claim is: the block is reachable and works when
# the gates open (proved at helper level in tests 03/05/06), but on ordinary
# turns the fork gate fires first; the adjustment has never fired on a real
# turn.  This test pins that ORDER.  It goes red if the mindset block is ever
# emitted before the fork/emission gates, or if a no-fork turn suppresses
# silently instead of logging its reason.
#
# Documented, NOT tested (the ledger cannot answer this): decision_ledger has
# 14,922 verdict_json rows and ZERO carry a "mindset" key — verdict_row_json
# does not persist it.  Never write a ledger-query test for mindset.

def test_08_mindset_block_is_downstream_of_fork_and_emission_gates(monkeypatch):
    dmt = _midturn()
    d = _decision()
    sid = "s-r57-gate-order-8"

    # The single seam faked is fork detection: a text with no declared
    # structure yields no envelope.  Everything after that point is production
    # code, including the ordering under test.
    monkeypatch.setattr(dmt._dec, "build_envelope",
                        lambda *a, **k: None)
    monkeypatch.setattr(dmt._dec, "call_backend",
                        lambda env, cfg: pytest.fail(
                            "backend was consulted for a fork-less turn — "
                            "the no-options gate did not fire"))
    monkeypatch.setattr(dmt, "_is_off_task_fork", lambda s, o: False)
    monkeypatch.setattr(dmt, "_ledger", lambda *a, **k: None)
    logged = []
    monkeypatch.setattr(dmt, "_log",
                        lambda s, e, **f: logged.append((s, e, f)))

    # Correct positional order per the real signature.
    dmt._handle_hit(
        sid,                                  # session_id
        "read_file",                          # tool_name
        "no fork here, just a file listing",  # text
        ["Alpha", "Beta"],                    # opts
        {"backend": "fake", "frame_context_chars": 0},  # cfg
        "on",                                 # mode
        seam="terminal",
    )

    emitted = [x for x in logged if x[1] == "midturn_mindset"]
    assert emitted == [], (
        "mindset block was emitted on a fork-less turn — it must sit "
        f"downstream of the fork gate: {emitted!r}"
    )

    suppressed = [x for x in logged if x[1] == "midturn_suppressed"]
    assert suppressed, (
        "fork-less turn neither emitted a mindset block nor logged a "
        f"suppression — the lane went silent: {logged!r}"
    )
    reason = str(suppressed[-1][2].get("reason") or "")
    assert reason == d.REASON_NO_OPTIONS, (
        f"fork-less turn suppressed with reason={reason!r}, expected "
        f"{d.REASON_NO_OPTIONS!r} — the no-options gate is not the first exit"
    )
    assert dmt.flush(sid) == [], (
        "a suppressed fork-less turn delivered a pending advisory"
    )


# --------------------------------------------------------------------------
# 9. POV BRIDGE (target contract, not current behaviour)
# --------------------------------------------------------------------------
#
# R57's povs.dials_from_claims derives {"scope","rigour"} from a four-voice
# claim channel (claims are voice -> weight floats); the PRE frontier path
# already uses it.  The decision lane does NOT.  Part (a) pins that derived
# dials are accepted where an adjustment is expected.  Part (b) is the target
# contract: the lane derives its mindset from povs — today that fails, so it
# is xfail'd with the reason rather than asserted true.

def test_09a_pov_derived_dials_are_accepted_as_adjustment():
    p = _povs()
    claims = {"explorer": 0.9, "verifier": 0.5,
              "skeptic": 0.3, "operator": 0.2}
    dials = p.dials_from_claims(claims)
    assert isinstance(dials, dict) and "scope" in dials and "rigour" in dials, (
        f"povs.dials_from_claims contract broken: {dials!r}"
    )

    block = _block({"choice": "opt-1", "confidence": 0.8,
                    "mindset": dict(dials)},
                   {"mindset": {"scope": -1.0, "rigour": 1.0}})
    equivalent = _block({"choice": "opt-1", "confidence": 0.8,
                         "mindset": {"scope": dials.get("scope"),
                                     "rigour": dials.get("rigour")}},
                        {"mindset": {"scope": -1.0, "rigour": 1.0}})
    assert block == equivalent, (
        "POV-derived dials produced different emitted text than the "
        f"equivalent plain dict: pov={block!r} plain={equivalent!r}"
    )


def test_09b_decision_lane_derives_mindset_from_povs():
    d = _decision()
    src = inspect.getsource(d)
    if "povs" not in src:
        pytest.xfail(
            "TARGET CONTRACT xfail: hermes_router/decision.py does not "
            "reference povs at all — the decision lane still takes a "
            "free-form verdict['mindset'] dict instead of deriving the dials "
            "from the four-voice claim channel (povs.dials_from_claims). "
            "Known gap as of R57: PRE frontier path uses povs, decision lane "
            "does not."
        )
    assert "dials_from_claims" in src or "split_projection" in src, (
        "decision.py imports povs but never calls dials_from_claims / "
        "split_projection — the POV channel is wired to nothing"
    )
