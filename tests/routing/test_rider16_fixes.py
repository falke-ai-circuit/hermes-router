"""Rider 16 — T1r9 verdict fixes (v4.16.6 -> 4.17.0 scope).

R16-1 (R9-1 + R9-3): consult-execution wiring on the declared forms —
  root cause of the bannerless silent zero: on_llm_execution's anchored-
  failure branch called .get("model") on rec['endpoint'], which is an
  AnchorEndpoint OBJECT — AttributeError mid-branch, swallowed by the
  outer handler; route_skipped never reached the route log, no failure
  banner parked, body delivered bannerless (live: analyst 5a 'ask your
  higher self' + R15_6 open-question, agent.log anchor_route_failed 404
  'Model fable' only — and the analyst anchor primary nous://fable is a
  CONFIG misconfig, owner territory, documented not touched).
  Fixes: getattr endpoint access + route_skipped model field + fail-loud
  parked failure banner on the anchored-failure path. Success-path
  delivery (consult runs, banner in delivered body) verified in-process
  for both forms.
R16-3 (R9-5): cooldown-first semantics — VERIFIED HELD, not regressed:
  the B4b 'decision repeat' banner + 'row=1549' were a forged banner
  authored in the agent reply body (no route events, no tokens-ledger
  row, no decision-ledger row — ledger row 1549 belongs to a later C4
  consult in a different session; the label '(decision repeat)' exists
  nowhere in the router source). The rescan-dedupe suppressed the second
  identical manual ask. Pinned here.

Grading contract (R13-4 binding): the four legs verify the DELIVERED
BODY string, never log lines alone. Probes here are development harnesses.
"""
import os
import sys

import sqlite3

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_exec, decision, debug_banner as DB,  # noqa: E402
                           render_inbox, router_core, route_gate,
                           usage_ledger)

SID = "s-rider16"


@pytest.fixture()
def r16_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    plugin.state.reset_turn_identity(SID)
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()
    DB._HELD_DECISIONS.clear()
    route_gate.clear_turn_claims()
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    return tmp_path


def _events(monkeypatch, sink):
    # monkeypatch-tracked: the stub MUST restore at teardown — a leaked
    # no-write _log_route starves later tests' log-file assertions.
    monkeypatch.setattr(plugin, "_log_route", lambda seam, event_detail="",
                        **kw: sink.append((seam, event_detail, kw)))


def _request(text):
    return {"messages": [{"role": "user", "content": text}],
            "model": "z-ai/glm-5.3-flash"}


def _body(resp):
    try:
        return str(resp["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001
        return ""


def _run_turn(monkeypatch, tmp_path, text, anchor=None):
    """One provider-call turn: PRE + execution + benign-edge consume.
    Returns (delivered_body, events, anchored_models)."""
    sink = []
    _events(monkeypatch, sink)
    if anchor is not None:
        monkeypatch.setattr(anchor_exec, "anchored_call", anchor)
    req = _request(text)
    ctx = {"session_id": SID}
    plugin.on_llm_request(request=req, original_request=req, **ctx)
    out = plugin.on_llm_execution(request=req, next_call=lambda r: "EXEC",
                                  **ctx)
    # delivery edge: the transform hook takes the response TEXT and returns
    # the delivered body (banner consumed + appended here when parked)
    tr = plugin.on_transform_llm_output(response_text="agent answer",
                                        session_id=SID, model="flash")
    body = tr if isinstance(tr, str) and tr else "agent answer"
    return body, sink, out


# ---------------------------------------------------------------------------
# R16-1: anchored-failure observability + fail-loud banner
# ---------------------------------------------------------------------------

def test_anchored_failure_logs_route_skipped(r16_reset, monkeypatch):
    """The .get() on the AnchorEndpoint object used to raise mid-branch and
    the outer handler swallowed it: route_skipped never reached the route
    log (silent zero). getattr form restores it."""
    seen = []
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda ep, kw: (None, None, None, None))
    _, sink, _ = _run_turn(monkeypatch, r16_reset,
                           "ask your higher self: is the two-pass dispatcher "
                           "refactor the right long-term call for the fleet?")
    skips = [kw for s, d, kw in sink if d == "route_skipped"]
    assert skips, "route_skipped must reach the route log on anchored failure"
    assert skips[-1].get("reason") == "anchored_call_failed"
    assert skips[-1].get("fail_kind") == "anchored_call_failed"
    assert "model" in skips[-1]  # the failing model id field is present


def _dec_cfg(monkeypatch):
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: {"enabled": True, "level": 3})
    monkeypatch.setattr(decision, "_cfg",
                        lambda: {"enabled": True, "level": 3})


def test_anchored_failure_parks_fail_loud_banner(r16_reset, monkeypatch):
    """A failed consult must not deliver a silently bannerless body: a
    fail-loud failure banner parks and consumes at the benign edge."""
    _dec_cfg(monkeypatch)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda ep, kw: (None, None, None, None))
    body, sink, _ = _run_turn(monkeypatch, r16_reset,
                              "decide this: what should the fleet prioritize "
                              "next quarter?")
    # delivery contract: the failure banner reaches the delivered body
    assert "consult_failed" in body or "anchored_call_failed" in body, body


def test_declared_higherself_success_delivers_banner(r16_reset, monkeypatch):
    """'ask your higher self' success path: consult executes on the anchored
    (frontier) machinery and the banner renders in the delivered body
    same-turn (four-leg: route event + banner IN body + spend row)."""
    calls = []

    def anchor(ep, kw):
        calls.append(getattr(ep, "model", ""))
        return ("STUB ORIENTATION BRIEF", 0.001, 100, 50)

    body, sink, _ = _run_turn(monkeypatch, r16_reset,
                              "ask your higher self: is the two-pass "
                              "dispatcher refactor the right long-term call "
                              "for the fleet?", anchor=anchor)
    fired = [kw for s, d, kw in sink if d == "anchor_route_fired"]
    assert fired, "consult must route"
    assert calls, "consult must execute (anchored call ran)"
    assert "router" in body and "consult" in body, body


def test_open_question_success_delivers_banner(r16_reset, monkeypatch):
    """F4 open-question form: frontier-tagged consult executes and the
    banner renders in the delivered body same-turn."""
    calls = []

    def anchor(ep, kw):
        calls.append(getattr(ep, "model", ""))
        return ("STUB CONSULT VERDICT", 0.001, 100, 50)

    router_core._decision_cfg = lambda: {"enabled": True, "level": 3}
    monkeypatch.setattr(decision, "_cfg",
                        lambda: {"enabled": True, "level": 3})
    body, sink, _ = _run_turn(monkeypatch, r16_reset,
                              "decide this: what should the fleet prioritize "
                              "next quarter?", anchor=anchor)
    fired = [kw for s, d, kw in sink if d == "anchor_route_fired"]
    assert fired and fired[0].get("reason") == "manual_open_question_frontier"
    assert calls, "consult must execute"
    assert "router" in body and "consult" in body, body


# ---------------------------------------------------------------------------
# R16-3: cooldown-first semantics (VERIFIED HELD; B4b was a forged banner)
# ---------------------------------------------------------------------------

def test_second_identical_manual_ask_consults_nothing(r16_reset, ledger_tmp,
                                                      monkeypatch):
    """Same session, second identical manual ask: no consult, no tokens row,
    no decision-ledger row (rescan dedupe on (fork_signature, task_id)).
    The t1r9 B4b 'decision repeat' banner + row=1549 was forged in the agent
    reply body — '(decision repeat)' exists nowhere in the router source and
    ledger row 1549 belongs to a later consult in a different session."""
    import json
    STD2 = ("decide this: commit ordering. Option A: run the regression "
            "suite before committing. Option B: commit first and let the "
            "suite catch it. Which and why?")
    _dec_cfg = {"enabled": True, "level": 3}
    monkeypatch.setattr(router_core, "_decision_cfg", lambda: _dec_cfg)
    monkeypatch.setattr(decision, "_cfg", lambda: _dec_cfg)
    anchored = []
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda ep, kw: (anchored.append(1) or
                                        ("V", 0.001, 10, 5)))

    tokens = str(ledger_tmp / "tokens.jsonl")

    def token_rows():
        try:
            with open(tokens) as f:
                return [json.loads(l) for l in f if l.strip()]
        except Exception:  # noqa: BLE001
            return []

    # first ask consults
    plugin.on_llm_request(request=_request(STD2), original_request=_request(STD2),
                          session_id=SID)
    plugin.on_llm_execution(request=_request(STD2),
                            next_call=lambda r: "EXEC", session_id=SID)
    # the manual decision consult is async; drain the worker
    import time as _t
    for _ in range(50):
        _t.sleep(0.02)
        if decision_rows_written() >= 1:
            break
    assert decision_rows_written() >= 1, "first ask must consult"

    n_rows_before = decision_rows_written()
    n_tokens_before = len(token_rows())

    # second identical ask: suppressed
    sink = []
    _events(monkeypatch, sink)
    plugin.on_llm_request(request=_request(STD2), original_request=_request(STD2),
                          session_id=SID)
    plugin.on_llm_execution(request=_request(STD2),
                            next_call=lambda r: "EXEC", session_id=SID)
    for _ in range(50):
        _t.sleep(0.02)
        if decision_rows_written() > n_rows_before:
            break
    assert decision_rows_written() == n_rows_before, \
        "second identical ask must write NO decision row"
    assert len(token_rows()) == n_tokens_before, \
        "second identical ask must write NO tokens row"
    assert not anchored or len(anchored) == 0, \
        "second identical ask must not run an anchored consult"


def decision_rows_written():
    """Count decision_ledger rows (the ledger DB is monkeypatched per-test
    via decision_miner.plugin_db_path -> tmp)."""
    import sqlite3
    from hermes_router import decision_miner
    path = decision_miner.plugin_db_path()
    if not path or not os.path.exists(path):
        return 0
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n = con.execute("select count(*) from decision_ledger").fetchone()[0]
    except Exception:  # noqa: BLE001
        n = 0
    finally:
        con.close()
    return n


@pytest.fixture()
def ledger_tmp(monkeypatch, tmp_path):
    from hermes_router import decision_miner
    monkeypatch.setattr(decision_miner, "plugin_db_path",
                        lambda: str(tmp_path / "hermes_router_state.db"))
    return tmp_path



# ---------------------------------------------------------------------------
# R16-2a: benign-brief-frame gate on the ANCHOR (completion-audit) leg
# ---------------------------------------------------------------------------

def test_audit_gate_skips_benign_brief_ask(r16_reset, monkeypatch):
    """D1a target ZERO: a benign setup/briefing ask must not bill the
    completion-audit frontier consult. The gate sits at audit_gate — the
    last ungated frontier arm (PRE R15-7 + POST R14-2 already gated)."""
    from hermes_router import completion_audit as ca
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    seen = []
    monkeypatch.setattr(ca, "_log", lambda event, **kw: seen.append(event))
    out = ca.audit_gate(SID, "agent answer text long enough here " + "x"*520,
                        ask_override="brief me on the tradeoffs of vitest "
                                     "vs jest for the fleet tests")
    assert out is None or out == "agent answer text long enough here " + "x"*520
    assert not fired, "benign brief ask must not consult"
    assert "audit_gate_skip" in seen and seen.count("audit_gate_skip") >= 1


def test_audit_gate_unchanged_on_decision_ask(r16_reset, monkeypatch):
    """A real decision-imperative ask is exempt inside the gate — the audit
    consult path stays reachable (fail-open to legacy behavior)."""
    from hermes_router import completion_audit as ca
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "audit_topology", lambda: "off")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    ca.audit_gate(SID, "agent answer text long enough here " + "x" * 520,
                  ask_override="decide which of the two plans we ship")
    assert fired, "decision-imperative ask must keep the audit arm reachable"


# ---------------------------------------------------------------------------
# R16-2b: cascade capture — second banner on the same turn still lands
# ---------------------------------------------------------------------------

def test_rewrite_cascade_second_banner_lands(r16_reset, monkeypatch, tmp_path):
    """R16-2b (valmet D1b): after the FIRST rewrite changed the persisted
    row's content, the SECOND banner's exact-match on the original response
    text missed (rewrite_no_match, row_present=False) and the banner was
    consumed-and-lost. The cascade fallback matches the row this router
    itself rewrote last."""
    from hermes_router import canonical as ce
    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id"
                " TEXT, role TEXT, content TEXT, api_content TEXT)")
    con.execute("INSERT INTO messages (session_id, role, content)"
                " VALUES (?, 'assistant', ?)", (SID, "agent answer"))
    con.commit(); con.close()
    monkeypatch.setattr(ce, "_state_db_path", lambda: str(db))
    ce._LAST_REWRITE.clear()
    # first banner: exact match on the raw response
    ok1 = ce.rewrite_persisted_turn(SID, "agent answer", "agent answer\nB1")
    assert ok1
    # second banner: exact match on the ORIGINAL text now misses — cascade
    # must match the row the first rewrite produced
    ok2 = ce.rewrite_persisted_turn(SID, "agent answer", "agent answer\nB2")
    assert ok2, "cascade must capture the second banner"
    con = sqlite3.connect(str(db))
    row = con.execute("select content from messages where session_id=?",
                      (SID,)).fetchone()[0]
    con.close()
    assert row == "agent answer\nB2", row


# ---------------------------------------------------------------------------
# R16-2c + R16-4: orphan recovery + bounded anchor retry delivery
# ---------------------------------------------------------------------------

def test_stage_model_swap_carries_payload(r16_reset):
    """The staged swap record carries a deep copy of the request payload —
    the POST-edge orphan recovery re-enters the anchor with it."""
    rd = router_core.RouteDecision(task_id="t16", lane=router_core.LANE_COMPLEXITY,
                                   mode=router_core.MODE_CONSULT,
                                   model_target=None, reason="r",
                                   ts=0.0, override_used=None,
                                   route_id="r16-1")
    req = _request("ask your higher self: is X the right call?")
    rec = router_core.stage_model_swap(SID, rd, payload=req)
    assert rec is not None
    assert rec.get("payload") is not None
    assert rec["payload"]["messages"][0]["content"].startswith("ask your")


def test_orphan_swap_recovered_at_delivery_edge(r16_reset, monkeypatch):
    """A staged swap still pending at the transform edge is an ORPHAN —
    the edge consumes it and re-enters the anchor via the retry worker
    (previously: silently dropped, 0 banners 0 rows — analyst A2)."""
    rd = router_core.RouteDecision(task_id="t16o", lane=router_core.LANE_COMPLEXITY,
                                   mode=router_core.MODE_CONSULT,
                                   model_target=None, reason="r",
                                   ts=0.0, override_used=None,
                                   route_id="r16o")
    req = _request("ask your higher self: is X the right call?")
    assert router_core.stage_model_swap(SID, rd, payload=req) is not None
    scheduled = []
    monkeypatch.setattr(anchor_exec, "retry_anchored_async",
                        lambda *a, **k: scheduled.append(a))
    sink = []
    _events(monkeypatch, sink)
    # no anchored call ever consumed the swap -> transform edge recovers it
    plugin.on_transform_llm_output(response_text="agent answer",
                                   session_id=SID, model="flash")
    assert router_core.peek_pending_swap(SID) is None, \
        "orphan must be consumed at the edge"
    assert scheduled, "recovery must schedule the anchor retry"
    assert any(d == "anchor_orphan_recovered" for s, d, kw in sink)


def test_anchor_retry_parks_verdict_banner(r16_reset, monkeypatch):
    """R16-4: a timed-out consult re-entered by the retry worker parks its
    verdict banner when it lands (delivery at the earliest edge), and the
    POST edge waits bounded for it."""
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda ep, kw, timeout=300: ("RETRY VERDICT", 0.001,
                                                     10, 5))
    rd = {"task_id": "t16r", "route_id": "r16r", "endpoint": object()}
    anchor_exec.retry_anchored_async(SID, rd, _request("ask X"),
                                     object(), base_timeout=300)
    import time as _t
    for _ in range(100):
        _t.sleep(0.02)
        if anchor_exec.pending_anchor_retries() == 0:
            break
    parked = DB.consume_parked_banner(SID)
    assert parked and "RETRY VERDICT" in parked, parked
    assert "consult_retry" in parked
