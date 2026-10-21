"""R34-18 — two residuals left by R34-17, both with live evidence.

Live specimen (session 20261021_074100_e9a8eb, 2026-10-21):

    07:41:17 PRE  anchor_banner_parked
    07:41:17 PRE  debug_banner_emitted lane=anchor est_cost=0.013634
    07:41:48 POST advisory_echo_stripped action=strip_keep_work chars_removed=0 \
                  chars=2208 families=povs_array frame=execution \
                  work_signal=terminal_run+write_tool tools=12 writes=3
    07:41:48 POST sweep_started response_chars=2208 turn_no=6
    07:41:48 POST banner_park_dropped reason=turn_closed_undelivered turn_no=6 \
                  edge=benign

RESIDUAL 1 — the strip was a NO-OP. `chars_removed=0` while a family was
detected, and the delivered body ended with the full fenced advisory block:

    ```json
    {"report": "...", "files": [...], "pytest": "...",
     "povs": [{"lens": "algorithms", "stance": "...", "argument": "..."}]}
    ```

The stripped shape (a bare object STARTING with an advisory key) was not the
shape the body carried (a fenced report object with `povs` as one sibling
key). Pinned here: that exact body loses the fence, keeps the report and the
pytest line, and `chars_removed` is non-zero whenever the event fires.

RESIDUAL 2 — the banner was dropped as `turn_closed_undelivered` on a turn
whose 2208-char body the user read. Delivery truth now comes from the body
landing (`sweep_started response_chars` / the delivering edge's text) plus the
park's ask identity, never from the turn counter alone. Pinning: strip ->
sweep(response_chars>0) attaches the banner (`banner_render_captured`) and
never emits `turn_closed_undelivered`; that reason may never appear alongside
a non-zero delivered char count.
"""
import pytest

import hermes_router as plugin
from hermes_router import (advisory_consumption as ac, config_access,
                           debug_banner, echo_guard, work_signal as ws)
from hermes_router.features.banners import lifecycle as bll

SID = "api_1792566000_e9a8eb"
ASK = "R34-18 build the strip shape fix and the banner delivery truth. anchor this"

REPORT_LINE = ("Built strip_shape.py (2048 bytes) and its tests in "
               "/opt/data/tmp/r34_18/task.")
PYTEST_LINE = "pytest: 14 passed in 0.31s."

# The live body verbatim in shape: human report FIRST, the advisory echoed as a
# fenced multi-key JSON block whose `povs` array is the LAST top-level key.
LIVE_BODY = (
    REPORT_LINE + "\n" + PYTEST_LINE + "\n\n"
    "The strip now understands a fenced block whose top level mixes the "
    "agent's own report with advisory keys.\n\n"
    "```json\n"
    "{\n"
    '  "report": "Built strip_shape.py and tests",\n'
    '  "files": ["strip_shape.py", "test_strip_shape.py"],\n'
    '  "pytest": "14 passed",\n'
    '  "povs": [\n'
    '    {"lens": "algorithms", "stance": "keep", "argument": "first-wins order"},\n'
    '    {"lens": "testing", "stance": "keep", "argument": "covers the fence"}\n'
    "  ]\n"
    "}\n"
    "```"
)

# The report exists ONLY inside the JSON: the fence is replaced by its `report`
# value, never left dangling.
JSON_ONLY_BODY = (
    "```json\n"
    '{"povs": [{"lens": "algorithms", "stance": "x", "argument": "y"}],\n'
    ' "report": "Rewrote the delivery seam and ran pytest: 7 passed."}\n'
    "```"
)


@pytest.fixture()
def logged(monkeypatch):
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: rows.append((e, dict(f))))
    monkeypatch.setattr(bll, "_log_route",
                        lambda e, **f: rows.append((e, dict(f))))
    monkeypatch.setattr(ac, "_STATE", {})
    monkeypatch.setattr(ac, "_ORDER", [])
    monkeypatch.setattr(ac, "_CLOSED", {})
    monkeypatch.setattr(ac, "_OPEN_SIG", {})
    monkeypatch.setattr(ac, "_TURN_NO", {})
    monkeypatch.setattr(ac, "_SWEEPS", {})
    monkeypatch.setattr(ac, "_ORPHANS", {})
    monkeypatch.setattr(ac, "_BODY_LANDED", {})
    monkeypatch.setattr(ac, "_TOOL_SEQ", 0)
    ws.reset()
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()
    yield rows
    ws.reset()
    ac.clear_turn_ledger(SID)


def _events(rows, name):
    return [f for _, f in rows if f.get("event_detail") == name]


def _write_payload(*names):
    return {"messages": [
        {"role": "user", "content": ASK},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": str(i), "type": "function",
                         "function": {"name": n, "arguments": "{}"}}
                        for i, n in enumerate(names)]},
    ]}


def _work_turn():
    ac.note_turn_open(SID, ASK)
    ws.note_terminal_activity(SID, "pytest -q /opt/data/tmp/r34_18")
    ws.note_request_activity(SID, _write_payload("write_file", "write_file"))


# ---------------------------------------------------------- residual 1: strip
def test_live_body_loses_the_fence_and_keeps_the_report(logged):
    """The exact live shape: fenced multi-key JSON, report first, povs last."""
    _work_turn()
    res = echo_guard.enforce_execution_echo(
        LIVE_BODY, SID, frame="execution",
        log_route=lambda e, **f: logged.append((e, dict(f))))
    assert res is not None and res.action == "strip_keep_work"
    body = res.body
    # (a) the fence and every advisory key are gone — nothing dangling
    assert "```" not in body
    assert '"povs"' not in body
    assert "povs" not in body.lower()
    assert "lens" not in body.lower()
    assert "stance" not in body.lower()
    assert "argument" not in body.lower()
    # (b) the human report and the pytest line survive
    assert "strip_shape.py (2048 bytes)" in body
    assert "pytest: 14 passed in 0.31s." in body
    assert body.startswith("Built strip_shape.py")
    # (c) the event may not claim a strip it did not perform
    stripped = _events(logged, "advisory_echo_stripped")
    assert len(stripped) == 1
    assert stripped[0]["chars_removed"] > 0
    assert "povs_array" in stripped[0]["families"]
    assert _events(logged, "advisory_echo_withheld") == []


def test_report_only_inside_the_json_is_rendered_plain(logged):
    """No prose above the fence -> render the JSON's `report` value in place
    of the block, never ship a bare fence."""
    _work_turn()
    out, removed = echo_guard.strip_advisory_echo(JSON_ONLY_BODY)
    assert removed > 0
    assert "```" not in out
    assert '"povs"' not in out
    assert "Rewrote the delivery seam and ran pytest: 7 passed." in out
    assert out.strip().startswith("Rewrote the delivery seam")


def test_strip_never_fires_with_zero_removed(logged):
    """The invariant: `advisory_echo_stripped` with a detected family implies
    chars_removed > 0. A body the strip cannot reduce is withheld, not
    reported as stripped."""
    _work_turn()
    # An advisory-only body with no path to a report -> withhold path.
    echo_only = '```json\n{"povs": [{"lens": "x", "stance": "y"}]}\n```'
    res = echo_guard.enforce_execution_echo(
        echo_only, SID, frame="execution",
        log_route=lambda e, **f: logged.append((e, dict(f))))
    assert res is not None
    for row in _events(logged, "advisory_echo_stripped"):
        assert row["chars_removed"] > 0


def test_balanced_fenced_code_in_a_clean_report_is_left_alone(logged):
    """A genuine code sample in the report is NOT an advisory block."""
    ac.note_turn_open(SID, "a plain ask")
    body = "Fixed it.\n\n```python\nprint(1)\n```\n\npytest: 3 passed"
    out, removed = echo_guard.strip_advisory_echo(body)
    assert "```python" in out and removed == 0


# ------------------------------------------- residual 2: delivery truth
def test_strip_then_sweep_attaches_the_banner(logged, monkeypatch):
    """STOPPING POINT: park -> (counter drifts on a duplicate pass over the
    SAME ask) -> strip -> sweep response_chars>0 -> benign delivery. The banner
    MUST attach (`banner_render_captured`) and the turn must NOT be declared
    undelivered."""
    from hermes_router import (canonical, completion_audit, decision_midturn,
                               render_inbox, route_gate, router_core, state,
                               usage_ledger)

    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    monkeypatch.setattr(decision_midturn, "_frame_register",
                        lambda: "execution")
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(_tmp_jsonl(monkeypatch)))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(_tmp_jsonl(monkeypatch)))
    monkeypatch.setattr(canonical, "rewrite_persisted_turn",
                        lambda *a, **k: True)

    _work_turn()
    banner = debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model="m", endpoint="h",
        tokens_in=10, tokens_out=5, est_cost=0.013634, latency_s=1.0,
        task_id="t1", session_id=SID)
    debug_banner.park_anchor_banner(SID, banner)
    _rec = debug_banner.park_record(SID)
    assert _rec.get("turn_no") == 1
    assert _rec.get("ask_sig"), "the park must carry the ask identity"

    # The live drift: the turn counter advanced while the ask identity is
    # unchanged (a duplicate transform pass over the SAME message is what the
    # ledger does — the counter is bookkeeping, not delivery truth).
    monkeypatch.setitem(ac._TURN_NO, ac._key(SID), 2)
    assert ac.turn_no(SID) == 2
    assert ac.open_signal(SID) == _rec["ask_sig"]
    # The work belongs to the pass that is delivering (the live turn ran 12
    # tools / 3 writes / 3 terminal runs before its final transform).
    ws.note_terminal_activity(SID, "pytest -q")
    ws.note_request_activity(SID, _write_payload("write_file", "write_file"))

    out = plugin.on_transform_llm_output(
        response_text=LIVE_BODY, session_id=SID, model="m")
    assert out, "the seam returned nothing"

    ev = [f for _, f in logged]
    names = [f.get("event_detail") for f in ev]
    # (a) the strip really happened on this body
    assert _events(logged, "advisory_echo_stripped")[-1]["chars_removed"] > 0
    # (b) the banner is in the delivered body, exactly once
    assert "· router ·" in out
    assert out.count("· router ·") == 1
    # (c) captured on the benign edge, never dropped as undelivered
    captured = _events(logged, "banner_render_captured")
    assert captured and captured[-1].get("edge") == "benign"
    dropped = _events(logged, "banner_park_dropped")
    assert [f.get("reason") for f in dropped] == []
    assert "turn_closed_undelivered" not in names
    # (d) no turn_closed_undelivered alongside a delivered body — ever
    for f in ev:
        if f.get("event_detail") in ("banner_park_dropped",):
            assert not (f.get("reason") == "turn_closed_undelivered"
                        and int(f.get("body_chars") or 0) > 0)


def test_ordering_strip_sweep_then_delivery_attaches(logged, monkeypatch):
    """PARSE-TIME ORDERING PIN: strip -> sweep(response_chars>0) -> benign
    delivery. The banner attaches; `turn_closed_undelivered` never appears."""
    from hermes_router.features.detection import semantic_judge as sj

    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    _work_turn()
    banner = debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model="m", endpoint="h",
        tokens_in=10, tokens_out=5, est_cost=0.013634, latency_s=1.0,
        task_id="t1", session_id=SID)
    debug_banner.park_anchor_banner(SID, banner)
    # Counter drifts, ask identity does not (the live shape).
    monkeypatch.setitem(ac._TURN_NO, ac._key(SID), 2)
    assert ac.turn_no(SID) == 2
    assert ac.open_signal(SID) == debug_banner.park_record(SID)["ask_sig"]
    # work happened in the delivering pass (turn-scoped counters)
    ws.note_terminal_activity(SID, "pytest -q")
    ws.note_request_activity(SID, _write_payload("write_file", "write_file"))

    res = echo_guard.enforce_execution_echo(
        LIVE_BODY, SID, frame="execution",
        log_route=lambda e, **f: logged.append((e, dict(f))))
    assert res is not None and res.action == "strip_keep_work"
    assert res.chars_removed > 0

    # The POST sweep lands the body-landed evidence (same response_chars).
    sj.post_detection_scan(
        SID, res.body, log_route=lambda e, **f: logged.append((e, dict(f))),
        cfg={"enabled": True})

    out = bll.LIFECYCLE.deliver(SID, res.body, "anchor", "benign",
                                log_edge="benign")
    names = [f.get("event_detail") for _, f in logged]
    # ordering: strip, then sweep, then the banner decision
    assert names.index("advisory_echo_stripped") < names.index("sweep_started")
    sweep = _events(logged, "sweep_started")[-1]
    assert sweep["response_chars"] > 0
    # the delivered body carries the banner exactly once
    assert "· router ·" in out and out.count("· router ·") == 1
    assert "turn_closed_undelivered" not in names
    assert _events(logged, "banner_park_dropped") == []


def test_undelivered_is_only_sayable_with_zero_body(logged):
    """The drop reason names the evidence: a superseded ask with a body in
    hand is `ask_superseded`; `turn_closed_undelivered` needs body_chars=0."""
    ac.note_turn_open(SID, "first ask")
    banner = debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model="m", endpoint="h",
        tokens_in=1, tokens_out=1, est_cost=0.01, latency_s=1.0,
        task_id="t1", session_id=SID)
    debug_banner.park_anchor_banner(SID, banner)

    ac.note_turn_open(SID, "a different ask")      # turn 2, ask moved on
    out = bll.LIFECYCLE.deliver(SID, "next turn body", "anchor", "benign",
                                log_edge="benign")
    dropped = _events(logged, "banner_park_dropped")
    assert dropped and dropped[-1]["reason"] == "ask_superseded"
    assert dropped[-1]["body_chars"] > 0
    assert dropped[-1]["row_present"] is False
    assert "· router ·" not in (out or "")

    # Same shape but NOTHING landed: now (and only now) "undelivered" is true.
    ac.note_turn_open(SID, "third ask")            # turn 3
    debug_banner.park_anchor_banner(SID, banner, task_id="t2")
    # simulate the drift with the SAME ask, then deliver an empty body
    ac.note_turn_open(SID, "third ask")
    out2 = bll.LIFECYCLE.deliver(SID, "", "anchor", "benign", log_edge="benign")
    dropped2 = _events(logged, "banner_park_dropped")[-1]
    assert dropped2["reason"] == "turn_closed_undelivered"
    assert dropped2["body_chars"] == 0
    assert dropped2["row_present"] is False
    assert out2 == ""


def _tmp_jsonl(monkeypatch):
    import os
    import tempfile
    d = tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None)
    return os.path.join(d, "x.jsonl")
