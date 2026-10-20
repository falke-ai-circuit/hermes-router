"""R34 item 5 — a zero-content frontier consult was invisible AND still billed.

Live: `anchor_route_failed reason=empty_response model=... finish=stop
tool_calls=False reasoning_chars=0` — 50 lifetime events (orchestrator 27,
architect 5). The provider call costs input tokens and yields nothing; the
turn shows no degradation and the ledger counts nothing at all.

Fix under test: None/empty consult results go through the same visible
failure path as a dropped consult —
  * an observable ledger row (outcome=failed, reason=empty_response), so a
    zero-content consult is never counted as a completed consult;
  * a parked failure banner the next delivery edge reports.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_chain, anchor_exec, debug_banner,  # noqa: E402
                           decision, router_core)

SID = "s-r34-item5"


def _run_empty_consult(monkeypatch, content):
    rec = {
        "task_id": "t-r34-5",
        "lane": "complexity",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri(
            "openrouter://test/model-a", "primary"),
        "route_id": "r-r34-5",
        "session_id": SID,
    }
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async",
                        lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda endpoint, api_kwargs: (content, None, None, None))
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()

    rows = []
    monkeypatch.setattr(decision, "ledger_write",
                        lambda row, db_path="": rows.append(dict(row)) or 1)
    out = anchor_exec.maybe_execute_anchored(SID, {"messages": []})
    return out, rows, rec


def test_none_consult_writes_failed_ledger_row(monkeypatch):
    out, rows, rec = _run_empty_consult(monkeypatch, None)
    assert out is None
    assert rows, "an empty consult must leave an observable ledger row"
    row = rows[0]
    assert row.get("outcome") == "failed"
    assert row.get("fail_open_reason") == "empty_response"
    assert row.get("session_id") == SID
    assert row.get("task_id") == rec["task_id"]
    assert row.get("model") == rec["endpoint"].model


def test_empty_string_consult_counts_as_empty(monkeypatch):
    """`content=""` is the same failure class as None."""
    out, rows, _ = _run_empty_consult(monkeypatch, "   \n")
    assert out is None
    assert rows and rows[0].get("outcome") == "failed"


def test_zero_content_consult_never_counts_as_completed(monkeypatch):
    _, rows, _ = _run_empty_consult(monkeypatch, None)
    assert not [r for r in rows if r.get("outcome") == "completed"]


def test_empty_consult_parks_a_visible_failure_banner(monkeypatch):
    _run_empty_consult(monkeypatch, None)
    parked = debug_banner._ANCHOR_BANNERS.get(SID) or ""
    assert "consult failed" in parked
    assert "empty response" in parked
    assert debug_banner.BANNER_HEAD in parked


def test_failure_banner_reaches_a_delivered_body(monkeypatch):
    """The parked state is not the acceptance — the DELIVERED body is."""
    _run_empty_consult(monkeypatch, None)
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    delivered = debug_banner.consume_parked_banner(SID)
    assert "consult failed" in (delivered or "")
