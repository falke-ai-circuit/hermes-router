"""M40-07 — one channel, one injection point.

The defect this file pins: a decision reading was appended INTO the last
``role="tool"`` message (``reflex_lane._inject_into_evidence``, reached when
``shape == "evidence"``). A tool result is the only thing a parent agent treats
as its child's ground truth, so a reading appended underneath an analyst-child
answer became part of the answer the parent reasoned from.

Three cases, one seam:

  A — the child answer survives BYTE-IDENTICAL, the reading still arrives (once,
      as a ``system`` message), and the refusal of ``shape: evidence`` is logged.
  B — the delivery boundary can strip the block whole (block-level, not
      line-wise) and withholds a body that is nothing but the block.
  C — no ``tool``/``user``/``assistant`` message is mutated anywhere in the run.
      This is the assertion that survives refactors of the block's text.
"""
from __future__ import annotations

from hermes_router import frames
from hermes_router import reflex_lane as rl
from hermes_router.gate import registration

CHILD = (
    "Analyst 3 timed out because the probe's own timeout (90s, /opt/data/probes/probe.yaml) "
    "is shorter than the Gateway round-trip on the shared queue: the child enqueued at "
    "14:02:11, the queue drained at 14:03:44, and the supervisor killed the run at 14:02:41 "
    "while the work was still parked. Two contributing facts from /opt/data/probes/supervisor.log: "
    "(1) the tasklane carried 7 in-flight items against a cap of 4, so the dispatch waited "
    "1m33s before it even started; (2) the probe's retry budget is 1, so the first slow "
    "drain became a hard failure rather than a retry. The fix is either a per-child timeout "
    "derived from queue depth, or a cap that refuses rather than queues. I checked the "
    "supervisor log for the last 24h: three other children parked over 90s and all three "
    "were killed the same way, so this is systemic, not a one-off — the queue drained at "
    "a rate of about one item per 13s while each child needed 90s of wall clock, which "
    "means a queue of 7 can never drain in time and the cap is the wrong instrument. "
    "Nothing else in the run was affected; the parent session is clean, the anchor chain "
    "is intact, and no other profile reported a similar stall in the same window. The "
    "recommended change is a per-child timeout computed as max(90s, queue_depth * 13s). "
    "Next: re-run the probe against session 42."
)

RULE_MARKER = "[PLATFORM RULE: decision_reading_integration_rule"
FENCE_HEAD = rl._FENCE_HEAD
FENCE_TAIL = rl._FENCE_TAIL
FAMILIES = ("BEGIN ROUTER READING", "END ROUTER READING", "DECISION READING",
            "[PLATFORM RULE:")

SPEAKING_ROLES = ("tool", "user", "assistant")


def _request():
    return {"messages": [
        {"role": "system", "content": "You are conductor. Route work; never execute."},
        {"role": "user", "content": "diagnose why analyst 3 timed out"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1",
            "function": {"name": "delegate_task",
                         "arguments": '{"profile":"analyst"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": CHILD},
    ]}


def _dist():
    """One parsed option set: p descending; the top option carries a dotted target
    (the window's supervisor.log), so `render` emits a `<- take this` line."""
    return {"answers": {"action": {"probabilities": {"a1": 0.74, "a2": 0.17}}},
            "scope": {"score": 1}, "rigour": {"score": 3}}


def _cfg(**kw):
    body = {"enabled": True, "every_call": True, "shape": "evidence",
            "register": "frame", "integrate": True}
    body.update(kw)
    return {"hermes_router": {"reflex_weights": body}}


def _events(monkeypatch):
    seen = []

    def _spy(sid, event, **fields):
        seen.append((event, fields))

    monkeypatch.setattr(rl, "_log", _spy)
    return seen


def test_case_a_child_answer_is_byte_identical_and_the_reading_lands_in_system(monkeypatch):
    events = _events(monkeypatch)
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist())
    rl.clear()
    request = _request()
    before = len(request["messages"])

    assert rl.run_on_request(request, "sess-m40-07", _cfg()) is True

    # 1. exact string equality — not containment, not startswith
    assert request["messages"][3]["content"] == CHILD

    # 2. no speaking channel carries any marker family
    for msg in request["messages"]:
        if msg.get("role") in ("tool", "assistant"):
            content = str(msg.get("content") or "")
            for fam in FAMILIES:
                assert fam not in content, "%s leaked into role=%s" % (
                    fam, msg.get("role"))

    # 3. the reading arrives exactly once, as `system`, fenced, under the rule marker
    added = request["messages"][before:]
    readings = [m for m in added if FENCE_HEAD in str(m.get("content") or "")]
    assert len(readings) == 1, "exactly one reading message"
    reading = readings[0]
    assert reading["role"] == "system"
    assert FENCE_TAIL in reading["content"]
    assert "[DECISION READING | DECISION-LANE" in reading["content"]
    assert any(RULE_MARKER in str(m.get("content") or "") for m in added
               if m.get("role") == "system"), "the integration contract shipped"
    assert "<- take this" in reading["content"], "the dotted target ranked top"

    # 4. after the evidence it was derived from, before generation
    assert request["messages"].index(reading) > 3

    # 5. the `evidence` shape is refused OUT LOUD, not silently rewritten
    refused = [f for e, f in events if e == "reflex_shape_evidence_refused"]
    assert refused and refused[0].get("using") == "system"


def test_case_b_strip_on_read_removes_the_block_whole(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist())
    rl.clear()
    request = _request()
    rl.run_on_request(request, "sess-m40-07-b", _cfg())
    block = [m["content"] for m in request["messages"]
             if FENCE_HEAD in str(m.get("content") or "")][-1]
    answer = "The probe timed out because the queue drained late."
    body = answer + "\n\n" + block

    stripped = frames.strip_platform_rule_blocks(body)
    assert stripped == answer, (
        "the block and its separating blank line are removed whole")
    assert frames.strip_platform_rule_blocks(stripped) == stripped, "idempotent"

    # the delivery boundary never ships a body that is nothing but the block
    delivered, outcome, _marker = registration._sanitize_delivery(block)
    assert delivered != block
    assert delivered == "" or "withheld" in str(outcome)


def test_case_c_no_speaking_channel_is_mutated_anywhere_in_the_run(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist())
    rl.clear()
    request = _request()
    snapshot = [repr(m) for m in request["messages"]
                if m.get("role") in SPEAKING_ROLES]

    rl.run_on_request(request, "sess-m40-07-c", _cfg())

    after = [repr(m) for m in request["messages"]
             if m.get("role") in SPEAKING_ROLES]
    assert after == snapshot, "tool/user/assistant bytes must be untouched"
