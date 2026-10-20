"""R34 item 1 — the dead retry arm on the anchored-consult failure path.

Live proof (analyst route log 2026-10-20T18:56:22Z):
    anchor_execution_exception fail_kind=name 'ep' is not defined
    route_skipped lane=complexity reason=anchored_call_failed

Root cause: ``maybe_execute_anchored`` (def at anchor_exec.py:651) schedules
the R16-4 recovery worker with ``ep`` — a name that exists only inside
``retry_anchored_async`` — while the local endpoint variable in that
function is ``endpoint``. Every consult that returned None (timeout / empty
body) therefore raised NameError inside the worker-scheduling call; the
outer handler swallowed it as anchor_execution_exception and the consult
was dropped silently. The pre-existing tests patch the SYNC wrapper
(``anchor_exec.retry_anchored_async``) and never exercise this call site,
which is why the dead arm shipped.

Grading contract: drive the REAL maybe_execute_anchored, patch the worker at
its CALL-SITE name (``_retry_anchored_async``), and assert both the schedule
(with the endpoint) and the absence of an exception event.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import anchor_chain, anchor_exec, router_core  # noqa: E402

SID = "s-r34-item1"


def _stage_rec(monkeypatch):
    rec = {
        "task_id": "t-r34-1",
        "lane": "complexity",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri(
            "openrouter://test/model-a", "primary"),
        "route_id": "r-r34-1",
        "session_id": SID,
    }
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff",
                        lambda *a, **k: None)
    return rec


def test_none_consult_schedules_retry_with_endpoint_and_logs_no_exception(
        monkeypatch):
    rec = _stage_rec(monkeypatch)
    events = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: events.append((e, dict(f))))

    scheduled = []

    def _fake_retry(session_id, rec_, api_kwargs, ep, *, base_timeout=300):
        scheduled.append({"session_id": session_id, "rec": rec_, "ep": ep,
                          "base_timeout": base_timeout})

    # CALL-SITE name — patching the sync wrapper (retry_anchored_async) never
    # reaches this arm.
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async", _fake_retry)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda endpoint, api_kwargs: (None, 0.0, None, None))

    outcome = anchor_exec.maybe_execute_anchored(SID, {"messages": []})

    assert outcome is None, "failed consult must still fail open (pass-through)"
    assert scheduled, "R16-4 recovery worker was NOT scheduled"
    assert scheduled[0]["ep"] is rec["endpoint"], (
        "retry worker must receive the endpoint (ep is undefined here)")
    assert scheduled[0]["session_id"] == SID
    assert scheduled[0]["base_timeout"] == 300

    fails = [f for f in events
             if f[1].get("event_detail") == "anchor_execution_exception"]
    assert not fails, "no exception may be logged on the None-consult arm: %s" % (
        fails,)


def test_retry_arm_names_resolve_in_source():
    """Belt-and-braces guard against the same name drift returning: the
    scheduling call inside maybe_execute_anchored must not reference `ep`.

    Reads the MODULE FILE, not inspect.getsource() of the live object — the
    suite stub-patches anchor_exec.maybe_execute_anchored in other tests, and
    getsource() then returns the stub's body."""
    src = open(anchor_exec.__file__, "r", encoding="utf-8").read()
    start = src.index("def maybe_execute_anchored(")
    try:
        end = src.index("\ndef ", start + 10)
    except ValueError:
        end = len(src)
    body = src[start:end]
    assert "_retry_anchored_async(session_id, rec, api_kwargs, endpoint," in body
    assert "api_kwargs, ep" not in body
