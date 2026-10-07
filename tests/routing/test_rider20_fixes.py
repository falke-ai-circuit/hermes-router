"""Rider 20 — batch-fix pins (v4.20.0 -> 4.21.0 scope).

R20-B6 (orchestrator, benign-ZERO break): a benign essay turn ("Write a
  short essay on ... No decisions needed.") billed a frontier
  completion_audit consult (ledger rows 673/674/675: 
  frontier_adversarial_parse_fail -> frontier_post -> frontier_consult)
  and delivered a banner on a NON-MARKED turn — the carried "orch B6
  zero-fire" regression broke on v4.20.0. Root cause: the essay's own
  prose ("the worst component, replaced, verified against the old
  behavior, and shipped") prose-collided with the multi-item closure
  pattern and armed the closure trigger; the R8-3 no-decision frame and
  the composition frame were both ungated at the audit arm. Fix: benign-
  ZERO gate inside audit_gate (no_decision_frame + composition_frame,
  decision imperatives exempt).

R20-D3 (valmet, park-without-capture): a billed anchor consult parked its
  banner (debug_banner_emitted 06:03:37Z tok 427/3089 + anchor_banner_
  parked, ledger rows 148 manual + 149 frontier_consult billed) but NO
  consume edge ever ran — the banner was never delivered. Root cause
  (valmet errors.log): the host plugin runner killed on_transform_llm_
  output at its 30s budget ("timed out after 30s — skipping", 06:00:50 +
  06:03:03Z on the D1b session) and then SKIPPED every later invocation
  ("skipped after previous timeout or while still running", 06:04:00Z on
  session api_1791266587_749f80ab) — the D3 turn's delivery edge never
  ran. The router's own hook budget (sync audit consult 45s + revision
  pass 60s + 20s decision wait) guaranteed the trip. Fix: the sync
  consult, revision pass, and pre-consume waits are budgeted under the
  runner timeout (HOOK_SYNC_CAP 25s; hook_budget passed from the hook;
  wait budget capped), so a slow consult can never orphan the hook.

Live evidence pinned per item; graded from disk truth (raw.jsonl, valmet
route log /tmp/uncensored-router-valmet.log, valmet logs/errors.log,
valmet hermes_router_state.db decision_ledger rows 148/149).
"""
import os
import sys
import time

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import (debug_banner as DB, render_inbox,  # noqa: E402
                           router_core, route_gate, usage_ledger)
from hermes_router import completion_audit as ca  # noqa: E402

SID = "s-rider20"

RESP = "agent answer text long enough here " + "x" * 520


@pytest.fixture()
def r20_reset(monkeypatch, tmp_path):
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


# ---------------------------------------------------------------------------
# R20-B6: benign-ZERO contract for the completion-audit frontier arm
# ---------------------------------------------------------------------------

B6_ASK = ("Write a short essay on why incremental verification beats "
          "big-bang rewrites. No decisions needed.")


def test_b6_no_decision_frame_ask_never_bills_audit(r20_reset, monkeypatch):
    """The live B6 form (essay ask declaring 'No decisions needed') must
    stand the completion-audit arm DOWN before the fire policy — zero
    billed consult, zero rows, no banner. Root cause of the v4.20.0
    regression: the essay's own prose matched the multi-item closure
    pattern, and the no-decision frame was ungated at this arm."""
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    monkeypatch.setattr(ca, "audit_topology", lambda: "off")
    seen = []
    monkeypatch.setattr(ca, "_log",
                        lambda event, **kw: seen.append((event, kw)))
    out = ca.audit_gate(SID, RESP, ask_override=B6_ASK)
    assert out is None or out == RESP
    assert not fired, "benign essay turn must not consult (billed rows 673-675 regression)"
    reasons = [str(kw.get("reason") or "") for ev, kw in seen]
    assert any(r.startswith("benign_zero") for r in reasons), reasons


def test_b6_composition_frame_without_suffix_also_stands_down(r20_reset, monkeypatch):
    """A composition frame ('write a report ...') with NO decision
    imperative is benign-ZERO even when the ask does not literally say
    'no decisions' — the composed prose's own closure-shaped sentences
    must never arm the audit trigger."""
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    monkeypatch.setattr(ca, "audit_topology", lambda: "off")
    ca.audit_gate(SID, RESP,
                  ask_override="write a report on the incremental "
                               "verification approach for the migration")
    assert not fired
    out = ca._benign_composition_frame("draft a summary of the outage timeline")
    assert out is True


def test_b6_decision_imperative_exempt_inside_gate(r20_reset, monkeypatch):
    """Decision imperatives are exempt: an essay/report ask that ends in a
    real fork keeps the audit arm reachable (fail-open to legacy)."""
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "audit_topology", lambda: "off")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    ca.audit_gate(SID, RESP,
                  ask_override="write a report comparing the two plans, "
                               "then decide which one we adopt")
    assert fired, "decision-imperative ask must keep the audit arm reachable"
    assert ca._benign_composition_frame(
        "write a summary, then choose the rollout plan") is False


def test_b6_gate_fail_open_on_helper_errors(r20_reset, monkeypatch):
    """Gate errors must never kill the arm: a raising gate helper falls
    open to the legacy behavior (the consult stays reachable)."""
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "audit_topology", lambda: "off")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ca, "_benign_composition_frame", _boom, raising=False)
    ca.audit_gate(SID, RESP, ask_override="closure report: all three fixes "
                                         "landed and verified")
    assert fired, "gate error must fail open to the legacy behavior"


# ---------------------------------------------------------------------------
# R20-D3: the hook budget — never a billed consult + silently-unconsumed banner
# ---------------------------------------------------------------------------

def test_d3_sync_consult_hard_capped_below_runner_budget(r20_reset, monkeypatch):
    """HOOK_SYNC_CAP: the configured sync budget is clamped below the
    plugin runner's 30s transform-hook kill — the 45s default tripped the
    runner timeout and every later transform invocation got skipped
    ("skipped after previous timeout or while still running"), leaving
    the billed consult's parked banner with no consume edge."""
    from hermes_router import router_core as rc
    monkeypatch.setattr(rc, "_complexity_cfg",
                        lambda: {"audit_sync_seconds": 45})
    assert ca.audit_sync_seconds() == ca.HOOK_SYNC_CAP <= 25.0
    monkeypatch.setattr(rc, "_complexity_cfg",
                        lambda: {"audit_sync_seconds": 10})
    assert ca.audit_sync_seconds() == 10.0
    monkeypatch.setattr(rc, "_complexity_cfg", lambda: {})
    assert ca.audit_sync_seconds() == ca.HOOK_SYNC_CAP  # 45s default clamps


def test_d3_hook_budget_clamps_sync_and_revision_sum(r20_reset, monkeypatch):
    """audit_gate(hook_budget=N): the sync consult AND the revision pass
    cannot together exceed the hook budget — sync timeout_s <= N, revision
    timeout_s <= N - sync."""
    recorded = {}

    def _sync(session_id, ask, resp, req, model, timeout_s=45.0):
        recorded["sync"] = timeout_s
        return {"note": "NO-FINDINGS", "model": "m", "endpoint": "e",
                "tokens_in": 10, "tokens_out": 10, "cost": 0.0001}

    def _rev(session_id, ask, resp, note, timeout_s=60.0):
        recorded["rev"] = timeout_s
        return None

    monkeypatch.setattr(ca, "run_completion_audit_sync", _sync)
    monkeypatch.setattr(ca, "revise_with_verdict", _rev)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"audit_sync_seconds": 5})
    ca.audit_gate(SID, RESP, ask_override="closure report: all three fixes "
                                         "landed and verified", hook_budget=12.0)
    assert recorded["sync"] <= 12.0
    assert recorded["rev"] <= 12.0 - recorded["sync"]
    assert recorded["sync"] + recorded["rev"] <= 12.0


def test_d3_no_hook_budget_keeps_configured_sync_budget(r20_reset, monkeypatch):
    """Without a hook budget the configured (capped) sync budget is used
    unchanged — no behavior regression for callers that do not pass one."""
    recorded = {}

    def _sync(session_id, ask, resp, req, model, timeout_s=45.0):
        recorded["sync"] = timeout_s
        return {"note": "NO-FINDINGS", "model": "m", "endpoint": "e",
                "tokens_in": 10, "tokens_out": 10, "cost": 0.0001}

    monkeypatch.setattr(ca, "run_completion_audit_sync", _sync)
    monkeypatch.setattr(ca, "revise_with_verdict",
                        lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    ca.audit_gate(SID, RESP, ask_override="closure report: all three fixes "
                                         "landed and verified")
    assert recorded["sync"] == ca.audit_sync_seconds()


def test_d3_preconsume_waits_capped_by_budget(r20_reset, monkeypatch):
    """_decision_wait_before_consume(budget=N): the decision-worker wait
    AND the anchor-retry wait together never exceed the budget."""
    from hermes_router import decision as dmod, anchor_exec as axmod
    rec = []

    def _wait_workers(t):
        rec.append(("workers", t))
        time.sleep(t)

    def _wait_retries(t):
        rec.append(("retries", t))
        time.sleep(t)

    monkeypatch.setattr(dmod, "wait_for_workers", _wait_workers)
    monkeypatch.setattr(axmod, "wait_for_anchor_retries", _wait_retries)
    monkeypatch.setattr(dmod, "_cfg",
                        lambda: {"post_worker_wait": 20}, raising=False)
    plugin._decision_wait_before_consume(9.5)
    assert rec, "waits must run"
    total = sum(t for _, t in rec)
    assert total <= 9.5 + 1e-6, rec
    assert rec[0][1] <= 9.5


def test_d3_hook_passes_budget_to_audit_gate(r20_reset, monkeypatch):
    """The transform hook budgets itself: audit_gate receives a positive
    hook_budget at or under the 25s cap, and _deliver_parked_at_edge
    passes the same budget to the pre-consume waits."""
    got = {}

    def _fake_audit_gate(session_id, response_text, model="", context=None,
                         *, ask_override="", hook_budget=0.0):
        got["hook_budget"] = hook_budget
        return None

    monkeypatch.setattr(ca, "audit_gate", _fake_audit_gate)
    monkeypatch.setattr(plugin, "_enabled", lambda: True, raising=False)
    monkeypatch.setattr(plugin, "_classification_cfg",
                        lambda: {"post_classify": True})
    monkeypatch.setattr(plugin.classifier, "scan_post", lambda *a, **k: [])
    monkeypatch.setattr(plugin, "_semantic_stage",
                        lambda *a, **k: (None, []))
    got_wait = {}

    def _wait(budget=0.0):
        got_wait["budget"] = budget

    monkeypatch.setattr(plugin, "_decision_wait_before_consume", _wait)
    plugin.on_transform_llm_output(response_text="clean benign body text "
                                               "for the rider-20 hook pin",
                                   session_id=SID, model="m")
    assert 0 < got.get("hook_budget", 0) <= 25.0
    assert 0 <= got_wait.get("budget", 0) <= 25.0


def test_d3_edge_helper_forwards_budget_to_waits(r20_reset, monkeypatch):
    """_deliver_parked_at_edge(budget=N) forwards the budget to the
    pre-consume waits (R15-5 edges stay runner-safe)."""
    from hermes_router import decision as dmod, anchor_exec as axmod
    rec = []
    monkeypatch.setattr(dmod, "wait_for_workers",
                        lambda t: rec.append(("workers", t)))
    monkeypatch.setattr(axmod, "wait_for_anchor_retries",
                        lambda t: rec.append(("retries", t)))
    monkeypatch.setattr(dmod, "_cfg", lambda: {"post_worker_wait": 20},
                        raising=False)
    plugin._deliver_parked_at_edge(SID, "body text", "flinch_passthrough",
                                   budget=7.0)
    assert rec
    # budget caps WALL-CLOCK: with mocked (non-sleeping) waits each wait is
    # capped at the budget; with real sleeps the SUM stays at the budget.
    assert all(t <= 7.0 + 1e-6 for _, t in rec), rec


# ---------------------------------------------------------------------------
# R20-B6 completion: closure-regex participial lookbehind + closure-arm
# nondecision stand-down (cadence/tools/risk arms unaffected)
# ---------------------------------------------------------------------------

ESSAY_BODY = ("Incremental verification demands — test before you trust, "
              "verify at the seam where old meets new — is exactly the "
              "discipline rewrites let you skip. The right unit of rewrite "
              "is not the system; it's the worst component, replaced, "
              "verified against the old behavior, and shipped in "
              "isolation.") + " " + "y" * 520

GENUINE_CLOSURE = ("Closure: all three tasks are done, tests shipped and "
                   "verified. ") + "y" * 520


def test_b6_participial_list_never_closure(r20_reset):
    """The multi-item closure pattern no longer matches a PARTICIPIAL list
    (noun phrase followed by comma-joined past participles inside composed
    prose — the live B6 collision 'component, replaced, verified against
    the old behavior, and shipped'). Genuine closures still fire."""
    assert ca.is_closure_response(B6_ASK, ESSAY_BODY) is False
    assert ca.is_closure_response(GENUINE_CLOSURE, GENUINE_CLOSURE) is True


def test_b6_closure_arm_stands_down_on_no_decision_ask(r20_reset, monkeypatch):
    """A NON-composition ask carrying the R8-3 nondecision frame family,
    with closure-shaped response prose and no manual trigger and no
    declared fork: the CLOSURE arm stands down
    (reason=closure_nondecision_frame) before the consult can bill."""
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "audit_topology", lambda: "off")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)
    seen = []
    monkeypatch.setattr(ca, "_log",
                        lambda event, **kw: seen.append((event, kw)))
    ask = "share your take on the rollout order — no decisions needed"
    out = ca.audit_gate(SID, GENUINE_CLOSURE, ask_override=ask)
    assert out is None
    assert not fired, "closure arm must stand down on a no-decision ask"
    assert any(str(kw.get("reason")) == "closure_nondecision_frame"
               for ev, kw in seen), seen


def test_b6_marked_closure_turn_still_fires(r20_reset, monkeypatch):
    """Marked turns stay auditable: the same closure-shaped response on an
    ask with a decision imperative (no nondecision frame) still fires the
    closure arm, and a declared fork is exempt from the stand-down."""
    fired = []
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "_consult_meta",
                        lambda *a, **k: fired.append(1) or {})
    monkeypatch.setattr(ca, "eligible", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(ca, "audit_topology", lambda: "off")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)
    ca.audit_gate(SID, GENUINE_CLOSURE,
                  ask_override="summarize the rollout, then pick the order")
    assert fired, "decision-imperative closure turn must still fire"
    fired.clear()
    fork_ask = ("compose the report on the rollout — no decisions needed "
                "from me, we already chose option A vs option B: "
                "Option A ship it. Option B revert.")
    ca.audit_gate(SID, GENUINE_CLOSURE, ask_override=fork_ask)
    assert fired, "declared-fork ask must not be stood down"


# ---------------------------------------------------------------------------
# R20-D3 completion: one-shot bounded capture-fallback worker
# ---------------------------------------------------------------------------

def _r20_state_db(tmp_path):
    import sqlite3
    dbp = str(tmp_path / "state.db")
    conn = sqlite3.connect(dbp, timeout=5.0)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS messages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, role TEXT, content TEXT, api_content TEXT);")
    conn.commit()
    conn.close()
    return dbp


def _insert_assistant_row(dbp, sid, content):
    import sqlite3
    conn = sqlite3.connect(dbp, timeout=5.0)
    conn.execute(
        "INSERT INTO messages (session_id, role, content)"
        " VALUES (?, 'assistant', ?)", (sid, content))
    conn.commit()
    conn.close()


def test_d3_capture_fallback_captures_unconsumed_banner(r20_reset,
                                                        monkeypatch):
    """D3 main path: a parked banner with NO delivery edge within the
    budget is captured — the newest persisted assistant row is rewritten
    to row + banner (exact-match rewrite, guard intact), the parked slot
    is cleared, PARKED_CAPTURE_FALLBACK is recorded and both route events
    (banner_capture_fallback + banner_render_captured edge=capture_fallback)
    are logged."""
    import sqlite3
    from hermes_router import canonical as canon, render_inbox as ri
    dbp = _r20_state_db(r20_reset)
    ROW = "agent answer text long enough here " + "x" * 520
    _insert_assistant_row(dbp, SID, ROW)
    monkeypatch.setattr(canon, "_state_db_path", lambda: dbp)
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1,
                                 "banner_capture_fallback_wait": 1},
                        raising=True)
    events = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **kw: events.append((event, kw)),
                        raising=False)
    DB.park_anchor_banner(SID, "· router · higher-self (frontier) test banner")
    for _ in range(60):
        if events and not DB._ANCHOR_BANNERS.get(SID):
            break
        import time as _t
        _t.sleep(0.1)
    conn = sqlite3.connect(dbp)
    try:
        cur = conn.execute(
            "SELECT content FROM messages WHERE session_id = ?"
            " AND role = 'assistant' ORDER BY id DESC LIMIT 1", (SID,))
        row = cur.fetchone()[0]
    finally:
        conn.close()
    assert row.startswith(ROW), "row rewritten to row + banner block"
    assert "test banner" in row
    assert not DB._ANCHOR_BANNERS.get(SID), "parked slot cleared after capture"
    recs = ri.read_renders()
    assert any(r.get("stage") == "PARKED_CAPTURE_FALLBACK" for r in recs), recs
    details = [str(kw.get("event_detail")) for ev, kw in events]
    assert any(d == "banner_capture_fallback" for d in details), events
    assert any(d == "banner_render_captured"
               and str(kw.get("edge")) == "capture_fallback"
               for ev, kw in events
               for d in [str(kw.get("event_detail"))]), events


def test_d3_late_live_consume_wins(r20_reset, monkeypatch):
    """A delivery edge consuming the parked banner DURING the poll window
    (or between the last poll and the module-lock re-check) wins — no
    persisted rewrite happens and the slot stays with the live edge."""
    import time as _t
    from hermes_router import canonical as canon
    dbp = _r20_state_db(r20_reset)
    _insert_assistant_row(dbp, SID, "original row content here")
    monkeypatch.setattr(canon, "_state_db_path", lambda: dbp)
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1,
                                 "banner_capture_fallback_wait": 1},
                        raising=True)
    DB.park_anchor_banner(SID, "live edge wins banner")
    _t.sleep(0.3)
    assert DB.consume_parked_banner(SID)  # live consume during poll window
    _t.sleep(1.5)  # let the watcher pass its budget + lock re-check
    assert not DB._ANCHOR_BANNERS.get(SID)
    import sqlite3
    conn = sqlite3.connect(dbp)
    try:
        cur = conn.execute(
            "SELECT content FROM messages WHERE session_id = ?"
            " AND role = 'assistant' ORDER BY id DESC LIMIT 1", (SID,))
        assert cur.fetchone()[0] == "original row content here", \
            "persisted row untouched — live consume won"
    finally:
        conn.close()


def test_d3_no_row_match_reparks_fail_loud(r20_reset, monkeypatch):
    """No persisted assistant row matches: the banner is RE-PARKED
    (retained for the next delivery edge) and a fail-loud event is logged
    (banner_capture_fallback outcome=no_row_match) — never a silent orphan."""
    import sqlite3
    from hermes_router import canonical as canon
    dbp = _r20_state_db(r20_reset)  # empty messages table — nothing to match
    monkeypatch.setattr(canon, "_state_db_path", lambda: dbp)
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1,
                                 "banner_capture_fallback_wait": 1},
                        raising=True)
    events = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda event, **kw: events.append((event, kw)),
                        raising=False)
    DB.park_anchor_banner(SID, "no-row-match banner")
    for _ in range(60):
        if any(str(kw.get("event_detail")) == "banner_capture_fallback"
               for ev, kw in events):
            break
        import time as _t
        _t.sleep(0.1)
    assert DB._ANCHOR_BANNERS.get(SID) == "no-row-match banner", \
        "banner re-parked (retained) after no-row-match"
    hit = [kw for ev, kw in events
           if str(kw.get("event_detail")) == "banner_capture_fallback"]
    assert any(str(kw.get("outcome")) == "no_row_match" and kw.get("re_parked")
               for kw in hit), events
    conn = sqlite3.connect(dbp)
    try:
        cur = conn.execute(
            "SELECT count(*) FROM messages WHERE session_id = ?", (SID,))
        assert cur.fetchone()[0] == 0, "no row was rewritten"
    finally:
        conn.close()


def test_d3_fallback_knob_default_and_disable(r20_reset):
    """banner_capture_fallback_wait: default 45s, 0 disables, clamped to
    120s; 0 also disables the scheduler outright."""
    old = DB._banner_section
    try:
        DB._banner_section = lambda: {"banner_capture_fallback_wait": 0}
        assert DB.banner_capture_fallback_wait() == 0.0
        with DB._CAPTURE_FB_LOCK:
            DB._CAPTURE_FB_INFLIGHT.clear()
        DB._maybe_schedule_capture_fallback(SID)
        assert SID not in DB._CAPTURE_FB_INFLIGHT
        DB._banner_section = lambda: {"banner_capture_fallback_wait": 999}
        assert DB.banner_capture_fallback_wait() == 120.0
    finally:
        DB._banner_section = old


def test_d3_fallback_semaphored_per_session(r20_reset, monkeypatch):
    """A second park while a watcher is already in flight does NOT spawn a
    second watcher for the same session (semaphored per session)."""
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1,
                                 "banner_capture_fallback_wait": 60},
                        raising=True)
    spawned = []
    real_thread = DB.threading.Thread

    class _SpyThread:
        def __init__(self, target, args, daemon, name):
            spawned.append(name)
            self._t = real_thread(target=target, args=args, daemon=daemon)

        def start(self):
            self._t.start()

    monkeypatch.setattr(DB.threading, "Thread", _SpyThread)
    DB.park_anchor_banner(SID, "first banner segment")
    DB.park_anchor_banner(SID, "second banner segment")
    assert len(spawned) == 1, "only ONE watcher per session"
    DB.consume_parked_banner(SID)  # clean up the watcher
