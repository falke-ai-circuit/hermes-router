"""R34 item 8e — LANE HEALTH: both lanes must be measurably worth running.

For each lane (complexity/frontier-anchor and decision/midturn) the four
properties that make a lane worth having:
  1. it FIRES on a qualifying turn (it actually runs);
  2. it produces NON-EMPTY advisory content;
  3. it never replaces the work product on an execution turn;
  4. it is OBSERVABLE (a ledger row / banner exists to reconcile against).
"""
import json
import os
import sqlite3
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import pytest  # noqa: E402

from hermes_router import (anchor_chain, anchor_exec, decision,  # noqa: E402
                           decision_midturn as dmt, router_core)

SID = "s-r34-8e"
TOOL_FORK = ("harness verdict — which one should we pick?\n"
             "a) redis\nb) memcached\nc) sqlite — weigh the tradeoffs")


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": sqlite3.connect(path))
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()
    yield path
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM decision_ledger ORDER BY id")]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# LANE 1 — complexity / frontier-anchor
# ---------------------------------------------------------------------------

def test_complexity_lane_fires_advisory_observable_never_replaces(ledger,
                                                                  monkeypatch):
    rec = {
        "task_id": "t-8e", "lane": "complexity",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri(
            "openrouter://test/model-a", "primary"),
        "route_id": "r-8e", "session_id": SID, "orientation": True,
    }
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async", lambda *a, **k: None)
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **k: None)
    content = "Prior art: keep the retry bounded and the banner parked. " * 40
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda endpoint, api_kwargs: (content, 0.0, 10, 5))

    # (1) FIRES on a qualifying turn
    out = anchor_exec.maybe_execute_anchored(SID, {"messages": [
        {"role": "user", "content": "Implement and commit the seven items."}]})
    assert out and out[0] == "done", "complexity lane did not fire"

    env = out[1]
    # (2) NON-EMPTY advisory
    assert str(env.get("answer") or "").strip()
    assert str(env.get("instruction") or "").strip()
    # (3) never replaces the work product: framed advisory-only, bounded
    assert env.get("advisory_only") is True
    assert len(str(env.get("answer") or "")) <= 600
    # (4) OBSERVABLE: a frontier-consult ledger row exists
    rows = [r for r in _rows(ledger) if r.get("fork_class") == "frontier_consult"
            or r.get("trigger") == "frontier_consult"]
    assert rows, "complexity lane left no reconcilable ledger row"


# ---------------------------------------------------------------------------
# LANE 2 — decision / midturn
# ---------------------------------------------------------------------------

def test_decision_lane_fires_advisory_observable_never_replaces(ledger,
                                                                monkeypatch):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on"})
    monkeypatch.setattr(dmt, "_cfg", lambda: dict(cfg))
    monkeypatch.setattr("hermes_router._log_route", lambda e, **f: None,
                        raising=False)
    monkeypatch.setattr("hermes_router.core.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)

    fired = []

    def _backend(envelope, cfg):
        fired.append(1)
        meta = {"model": "m", "endpoint": "typesafe", "tokens_in": 10,
                "tokens_out": 5, "latency_s": 0.1}
        return json.dumps({"choice": "opt-1", "confidence": 0.8,
                           "alternatives": []}), meta, "ok"

    monkeypatch.setattr(decision, "call_backend", _backend)

    # (1) FIRES on a qualifying turn (a declared fork in tool output)
    dmt.on_tool_result(SID, "terminal", TOOL_FORK)
    assert fired, "decision lane did not consult on a declared fork"

    # (2) NON-EMPTY advisory
    pending = dmt.flush(SID)
    assert pending and str(pending[0]).strip()
    # (3) never replaces the work product: the note is advisory context,
    #     never a body substitute
    note = str(pending[0])
    assert "ROUTER" in note.upper()
    assert note.strip() != TOOL_FORK
    # (4) OBSERVABLE: a decision-ledger row exists for the verdict
    rows = [r for r in _rows(ledger) if r.get("choice") == "opt-1"]
    assert rows, "decision lane left no reconcilable ledger row"


def test_decision_lane_execution_register_is_advisory_not_a_menu(ledger,
                                                                 monkeypatch):
    """Goran runs coder on `decision.frame=execution`: the note must be a
    fork note that tells the agent to continue, with no menu to answer."""
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on", "frame": "execution"})
    monkeypatch.setattr(dmt, "_cfg", lambda: dict(cfg))
    monkeypatch.setattr("hermes_router._log_route", lambda e, **f: None,
                        raising=False)
    monkeypatch.setattr("hermes_router.core.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr(decision, "call_backend",
                        lambda envelope, cfg: (json.dumps(
                            {"choice": "opt-1", "confidence": 0.8,
                             "alternatives": ["opt-2"]}),
                            {"model": "m", "endpoint": "typesafe",
                             "tokens_in": 10, "tokens_out": 5}, "ok"))
    dmt.on_tool_result(SID, "terminal", TOOL_FORK)
    pending = dmt.flush(SID)
    note = str(pending[0])
    assert dmt.FORK_NOTE_DIRECTIVE in note
    assert len(note) <= dmt.FORK_NOTE_MAX_CHARS
    assert "pulls" not in note
