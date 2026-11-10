"""M49-02 — the aux_none outcome must carry a NAMED cause, and one turn must
not pay twice for the same question.

WHY (measured, M49-01 §4): 3741 of 7163 `aux_none` rows (52.2%) returned in
<=10 ms — the aux call was never made — and the breaker was refuted, so the
rolling-hour budget is the mechanism. The gate row could not say so: it read
`aux_raw_call`'s bare falsy return, which carried no reason. Every check below
is a distinct cause on the SAME outcome token (`aux_none`), which is the whole
point: existing readers keep the outcome, operators gain the cause.
"""
from __future__ import annotations

import time

import pytest

import hermes_router as plugin  # noqa: F401 — plugin init, as the gate seam expects
import hermes_router.semantic_classifier as sc
from hermes_router.core import state
from hermes_router.features.detection import domain_gate as dg

BENIGN = ("Design a caching layer for our multi-tenant API - invalidation "
          "strategy, stampede protection, memory budgeting.")

_OK_BODY = '{"choices":[{"message":{"content":"{\\"domain\\": \\"benign_task\\"}"}}]}'


def _fresh(sid: str, cfg: dict | None = None) -> None:
    sc.reset_limits()
    state.reset_turn_identity(sid)
    state.clear_pre_domain(sid)
    if cfg is not None:
        sc._classification_cfg = lambda _c=None, _cfg=cfg: _cfg


def _exhaust_budget() -> None:
    """Pin the rolling-hour window at the budget (the measured saturation)."""
    now = time.time()
    with sc._LOCK:
        sc._CALL_TIMES.clear()
        for _ in range(5000):
            sc._CALL_TIMES.append(now)


# ---------------------------------------------------------------------------
# (1) the cause is on the row, and the outcome token is unchanged
# ---------------------------------------------------------------------------
def test_cap_is_named_on_the_gate_row():
    sid = "m49-02-cap"
    _fresh(sid)
    _exhaust_budget()
    dg._worker(sid, BENIGN, None)
    row = dg.read(sid)
    assert row is not None
    assert row["gate"] == dg.GATE_ABSTAIN
    assert row["reason"] == "aux_none", "existing outcome token must not change"
    assert row["cause"] == "cap", row
    print(f"\n(1) cap turn: reason={row['reason']} cause={row['cause']}")


# ---------------------------------------------------------------------------
# (2) controls: empty answer and open breaker are DISTINCT causes
# ---------------------------------------------------------------------------
def test_empty_answer_is_empty_content_not_cap(monkeypatch):
    sid = "m49-02-empty"

    class _Msg:
        content = ""

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    class _Completions:
        def create(self, **kw):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client",
                        lambda task: (_Client(), "some-model"))
    monkeypatch.setattr(sc, "_hermes_aux_call", sc._original_hermes_aux)
    _fresh(sid)
    dg._worker(sid, BENIGN, None)
    row = dg.read(sid)
    assert row["reason"] == "aux_none"
    assert row["cause"] == "empty_content", row
    assert row["cause"] != "cap"
    print(f"\n(2a) empty answer: cause={row['cause']}")


def test_breaker_open_is_breaker_not_cap(monkeypatch):
    _fresh("m49-02-breaker", cfg={"aux_retries": 0, "aux_breaker_failures": 1,
                                  "aux_calls_per_hour": 10000,
                                  "aux_breaker_cooldown_seconds": 600})
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda payload, timeout: None)
    assert sc.aux_raw_call("prime the breaker") is None   # ordinary path counts
    assert sc.breaker_is_open() is True
    sid = "m49-02-breaker"
    dg._worker(sid, BENIGN, None)
    row = dg.read(sid)
    assert row["reason"] == "aux_none"
    assert row["cause"] == "breaker", row
    assert row["cause"] != "cap"
    print(f"\n(2b) breaker open: cause={row['cause']}")


# ---------------------------------------------------------------------------
# (3) an abstain stays an abstain — the cause makes it visible, not a route
# ---------------------------------------------------------------------------
def test_cap_suppressed_abstain_is_still_abstain_and_creates_no_route():
    sid = "m49-02-abstain"
    _fresh(sid)
    _exhaust_budget()
    dg._worker(sid, BENIGN, None)
    row = dg.read(sid)
    assert dg.verdict(row) == dg.GATE_ABSTAIN
    assert dg.verdict(row) != dg.GATE_FLAGGED
    assert dg.deterministic_domain(BENIGN) == "", "no route candidate for this ask"
    semantics = dict(row.get("gate_semantics") or ())
    assert semantics.get("gate_abstain") == "defer", semantics
    print(f"\n(3) verdict={dg.verdict(row)} fallback={semantics.get('gate_abstain')} "
          f"cause={row['cause']}")


# ---------------------------------------------------------------------------
# (4) no second paid call for the same question in one turn
# ---------------------------------------------------------------------------
def test_one_answer_per_turn_dedupes_the_repeated_seam(monkeypatch):
    sid = "m49-02-calls"
    _fresh(sid)
    calls = {"n": 0}

    def fake(payload, timeout):
        calls["n"] += 1
        return _OK_BODY

    monkeypatch.setattr(sc, "_hermes_aux_call", fake)
    dg._worker(sid, BENIGN, None)
    dg._worker(sid, BENIGN, None)   # POST's re-consult of the same ask
    assert calls["n"] == 1, f"the second seam re-spent the budget ({calls['n']} calls)"
    assert sc.aux_failure_cause() == ""
    print(f"\n(4) aux calls for two identical gate seams in one turn: {calls['n']}")
