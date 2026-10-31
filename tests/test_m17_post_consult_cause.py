"""R78U M17 — the POST consult must produce text when it RUNS, and must NAME
why when it does not.

Live (conductor pass 3, post-deploy): 5 orchestrator fires, all
`audit_gate_returned returned=none chars=0`; the only cause token on any row
was the generic `post_consult_failed`. The real blocker was the daily cap:
`est_cost` prices the FULL max_tokens allowance, so a consult near the cap was
refused for a worst-case price — 27 orchestrator + 4 valmet rows of
`cap_blocked`, zero consults. That is a fifth cause class, and it was silent.

Covered here:
  Unit 1: headroom that CAN buy a verdict resizes the allowance and the
          consult RUNS (returns the no-findings RESULT, not None).
  Unit 2: headroom that CANNOT is a named `cap_exhausted` with numbers.
  Unit 3: the terminal `audit_gate_returned` cause carries the real class.
"""
from types import SimpleNamespace

import pytest

from hermes_router import anchor_chain
from hermes_router import anchor_exec as ax
from hermes_router import completion_audit as ca
from hermes_router import router_core

PRICING = {"frontier-x": {"input_per_1m": 3.0, "output_per_1m": 75.0}}
RESP = "Done. " + "The change is in place and the build is green. " * 20
ASK = "fix the audit lane and report the evidence"


class _Chain:
    def __init__(self, cap=2.0):
        self.daily_cap_usd = cap
        self.pricing = PRICING
        self.endpoint = SimpleNamespace(
            model="frontier-x", base_url="https://frontier.invalid",
            api_key="k", provider="x")

    def endpoint_for(self, name):
        return self.endpoint


def _wire(monkeypatch, spend):
    events = []
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, kw)))
    monkeypatch.setattr(ca, "_log_pair", lambda stage, ok, **kw: events.append(
        ("higher_self_pair", dict(kw, stage=stage, paired=ok))))
    monkeypatch.setattr(anchor_chain, "load_anchor_chain", lambda: _Chain())
    monkeypatch.setattr(anchor_chain, "today_spend", lambda: spend)
    monkeypatch.setattr(anchor_exec_mod(), "last_failure", lambda: ("", {}))
    monkeypatch.setattr(ca, "persist_frontier_verdict", lambda **k: 7)
    monkeypatch.setattr(ca, "stash_verdict", lambda *a, **k: None)
    return events


def anchor_exec_mod():
    return ax


def test_cap_headroom_that_fits_runs_the_consult(monkeypatch):
    """Spend 1.90 of a 2.00 cap: headroom ~$0.10 buys a verdict, so it RUNS."""
    calls = []

    def _call(ep, kwargs, timeout=0):
        calls.append(kwargs)
        return "NO-FINDINGS", 0.01, 12, 40

    monkeypatch.setattr(ax, "anchored_call", _call)
    # headroom $0.07 < the $0.094 worst-case estimate -> resized, then runs
    events = _wire(monkeypatch, spend=1.93)
    meta = ca._consult_meta("s-m17-fit", ASK, RESP, None, "frontier-x", "k1",
                            socket_timeout=24)
    assert calls, "a consult that fits the remaining cap must actually run"
    assert 0 < calls[0]["max_tokens"] <= 1200, calls[0]["max_tokens"]
    assert isinstance(meta, dict), "a completed no-findings consult is a RESULT"
    assert meta.get("verdict") == "no-findings"
    assert any(e == "completion_audit_cap_resized" for e, _ in events), events


def test_cap_headroom_below_the_floor_names_cap_exhausted(monkeypatch):
    """Spend 1.99 of a 2.00 cap: cannot buy a verdict -> NAMED, not silent."""
    monkeypatch.setattr(ax, "anchored_call",
                        lambda *a, **k: pytest.fail("must not call the provider"))
    events = _wire(monkeypatch, spend=1.99)
    diag = {}
    meta = ca._consult_meta("s-m17-nohead", ASK, RESP, None, "frontier-x", "k2",
                            socket_timeout=24, diag=diag)
    assert meta is None
    assert diag.get("reason") == "cap_exhausted", diag
    assert "headroom_usd=" in str(diag.get("detail") or "")
    rows = [kw for e, kw in events
            if e == "completion_audit_skipped" and kw.get("reason") == "cap_exhausted"]
    assert rows, events
    assert "cap_usd=" in str(rows[0].get("detail") or "")


def test_audit_gate_terminal_row_carries_the_real_cause(monkeypatch):
    """`returned=none` is never causeless: the gate records WHY it stood down."""
    events = []
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, kw)))
    monkeypatch.setattr(ca, "_log_pair", lambda stage, ok, **kw: events.append(
        ("higher_self_pair", dict(kw, stage=stage, paired=ok))))
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    monkeypatch.setattr(ca, "audit_sync_seconds", lambda: 24)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "is_closure_response", lambda a, r: False,
                        raising=False)
    monkeypatch.setattr(ca, "stash_verdict", lambda *a, **k: None)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)

    def _sync(*a, **k):
        k["diag"]["reason"] = "cap_exhausted"
        k["diag"]["detail"] = "cap_usd=2.0 spend_usd=1.99"
        return None

    monkeypatch.setattr(ca, "run_completion_audit_sync", _sync)
    ca._set_gate_cause("")
    out = ca.audit_gate("s-m17-cause", RESP, ask_override=ASK,
                        context={"request": {"messages": [
                            {"role": "tool", "content": "ok"}] * 60}})
    assert out is None
    assert ca.gate_cause() == "cap_exhausted", ca.gate_cause()
    empty = [kw for e, kw in events if e == "completion_audit_consult_empty"]
    assert empty and empty[0].get("cause") == "cap_exhausted", events


def test_a_skip_sets_the_cause_too(monkeypatch):
    """The ordinary skip path (short unpaired reply) also names its cause."""
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: None)
    monkeypatch.setattr(ca, "_log_pair", lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    ca._set_gate_cause("")
    assert ca.audit_gate("s-m17-skip", "ok", ask_override=ASK) is None
    assert ca.gate_cause() == "response_too_short_unpaired", ca.gate_cause()


def test_max_tokens_for_headroom_math():
    ep = SimpleNamespace(model="frontier-x")
    # $0.10 headroom, 1325 input tokens at $3/1M = $0.003975
    fit = anchor_chain.max_tokens_for_headroom(ep, 1325, 0.10, PRICING)
    assert 1270 <= fit <= 1290, fit
    assert anchor_chain.max_tokens_for_headroom(ep, 1325, 0.001, PRICING) == 0
