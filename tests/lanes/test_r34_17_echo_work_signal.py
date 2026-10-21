"""R34-17 — the execution-echo guard must not fire on a turn that did work.

LIVE SPECIMEN (session 20261021_070153_f41a2f, 2026-10-21):

    07:02:12 PRE anchor_advisory_capped from=2918 to=600 reason=l1_bounded_advisory
    07:02:12 PRE anchor_route_fired lane=complexity mode=consult
    07:02:21 PRE claim_standdown claim_lane=higher-pre (x5)
    07:02:37 POST advisory_echo_blocked action=handback_failure chars=1003 \
                  families=povs_array frame=execution
    07:02:57 POST banner_render_captured edge=capture_fallback

    DELIVERED BODY (168 chars): "[router] turn ended on the router's own
    advisory echo, not work product. ... nothing was executed. ..."
    ON DISK, SAME TURN: dedupe.py (1849 b), test_dedupe.py (1471 b),
    pytest -> 5 passed.

The guard detected a real echo, threw the WORK away, and told the user a
falsehood about the filesystem. R34-17: the decision keys on the TURN'S WORK
SIGNAL, never on body shape alone —

  * work turn  -> strip the echoed advisory, KEEP the report
                 (`advisory_echo_stripped action=strip_keep_work`)
  * no work    -> hand back (`advisory_echo_blocked action=handback_failure
                 reason=no_work_signal`), the only path allowed to say
                 "nothing was executed"
  * work turn where the strip cannot yield a report -> withhold the body with
                 a notice that claims NOTHING about execution
                 (`advisory_echo_withheld action=withhold_echo reason=strip_failed`)

Plus item 3: `anchor_banner_consume parked=True` must never appear on a turn
with no park.
"""
import pytest

import hermes_router as plugin
from hermes_router import (advisory_consumption as ac, config_access,
                           debug_banner, echo_guard, work_signal as ws)
from hermes_router.features.banners import lifecycle as bll

SID = "api_1792551733_f41a2f"
ASK = ("R34-17 build dedupe.py + tests in /opt/data/tmp/ab_frontier/task. "
       "anchor this")

REPORT = (
    "Built dedupe.py (1849 bytes) and test_dedupe.py (1471 bytes) in "
    "/opt/data/tmp/ab_frontier/task.\n"
    "pytest: 5 passed in 0.02s.\n"
    "Dedupe keeps first occurrence order; tests cover str, int and nested keys."
)
ECHO_TAIL = (
    "\n\nPOVs.\n"
    "Practitioner: the strip must run before the block decision.\n"
    "Outsider: a guard that lies about the filesystem is a broken harness.\n"
    "Skeptic: is any terminal run too coarse a work signal?\n"
    '{"povs":[{"role":"practitioner","claim":"strip-first"}],'
    '"synthesis":"gate on work signal first"}'
)
WORK_BODY = REPORT + ECHO_TAIL
ECHO_ONLY = (
    "POVs.\n"
    "Practitioner: strip-first.\n"
    "Outsider: truth in the notice.\n"
    '{"povs":[{"role":"skeptic","claim":"coarse signal"}],'
    '"synthesis":"gate on work signal first"}'
)


@pytest.fixture()
def logged(monkeypatch):
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: rows.append((e, dict(f))))
    monkeypatch.setattr(bll, "_log_route",
                        lambda e, **f: rows.append((e, dict(f))))
    for mod in (ac,):
        monkeypatch.setattr(mod, "_STATE", {})
        monkeypatch.setattr(mod, "_ORDER", [])
        monkeypatch.setattr(mod, "_CLOSED", {})
        monkeypatch.setattr(mod, "_OPEN_SIG", {})
        monkeypatch.setattr(mod, "_TURN_NO", {})
        monkeypatch.setattr(mod, "_SWEEPS", {})
        monkeypatch.setattr(mod, "_ORPHANS", {})
        monkeypatch.setattr(mod, "_TOOL_SEQ", 0)
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


# ------------------------------------------------------- item 1: the predicate
def test_live_turn_has_work_signal(logged):
    """The exact live shape: one ask, one anchor consult, advisory capped,
    a terminal ran and two files were written."""
    ac.note_turn_open(SID, ASK)
    assert ac.note_advisory_injected(SID, ASK, chars=600,
                                     envelope_chars=1951) is True
    ws.note_terminal_activity(SID, "pytest -q /opt/data/tmp/ab_frontier/task")
    ws.note_request_activity(SID, _write_payload("write_file", "write_file"))
    facts = ws.work_facts(SID)
    assert facts["has_work"] is True
    assert facts["terminal"] == 1
    assert facts["writes"] == 2
    assert facts["predicate"] == "terminal_run+write_tool"


def test_read_only_traffic_is_not_work(logged):
    """The skeptic's hole: `read_file`/`search` must NOT make a turn a work
    turn — a turn that only looked is still a no-work turn."""
    ac.note_turn_open(SID, ASK)
    ws.note_request_activity(
        SID, _write_payload("read_file", "search_files", "web_search"))
    facts = ws.work_facts(SID)
    assert facts["has_work"] is False
    assert facts["predicate"] == "no_tool_activity"
    assert facts["tools"] == 3          # tracked, reported, not qualifying


def test_counters_are_per_turn(logged):
    ac.note_turn_open(SID, ASK)
    ws.note_terminal_activity(SID, "pytest")
    assert ws.work_facts(SID)["has_work"] is True
    ac.note_turn_open(SID, "a brand new ask")
    assert ws.work_facts(SID)["has_work"] is False


# ------------------------------------------- item 1: strip keeps the work
def test_live_shape_strips_the_echo_and_keeps_the_report(logged):
    ac.note_turn_open(SID, ASK)
    ac.note_advisory_injected(SID, ASK, chars=600)
    ws.note_terminal_activity(SID, "pytest -q")
    ws.note_request_activity(SID, _write_payload("write_file", "write_file"))
    res = echo_guard.enforce_execution_echo(
        WORK_BODY, SID, frame="execution",
        log_route=lambda e, **f: logged.append((e, dict(f))))
    assert res is not None
    assert res.action == "strip_keep_work"
    # (a) the delivered body LEADS with the work report
    assert res.body.startswith("Built dedupe.py")
    assert "5 passed" in res.body
    # (b) the echo is gone
    assert '"povs"' not in res.body
    assert "POVs." not in res.body
    assert "Practitioner:" not in res.body
    assert "synthesis" not in res.body.lower()
    # (c) the guard never claims the work did not happen
    assert "nothing was executed" not in res.body.lower()
    stripped = _events(logged, "advisory_echo_stripped")
    assert len(stripped) == 1
    assert stripped[0]["action"] == "strip_keep_work"
    assert stripped[0]["chars_removed"] > 0
    assert "povs_array" in stripped[0]["families"]
    assert stripped[0]["work_signal"] == "terminal_run+write_tool"
    assert _events(logged, "advisory_echo_blocked") == []


def test_strip_removes_the_router_banner_tail_only(logged):
    """A report that quotes the router tail loses the tail, keeps the report."""
    ac.note_turn_open(SID, ASK)
    ws.note_terminal_activity(SID, "make test")
    body = (REPORT + "\n\n"
            "\u00b7 router \u00b7 higher-self (frontier) | consult | model @ host "
            "| tok 2128/1506 | $0.019316 | row=3527 \u00b7")
    res = echo_guard.enforce_execution_echo(body, SID, frame="execution")
    # The tail alone is a "router_tail_no_work" family only when the body has
    # no work markers; with a report present it is not an echo at all.
    assert res is None


# --------------------------------------- item 2: handback only on no work
def test_no_work_signal_still_hands_back(logged):
    ac.note_turn_open(SID, ASK)
    ac.note_advisory_injected(SID, ASK, chars=600)
    res = echo_guard.enforce_execution_echo(
        ECHO_ONLY, SID, frame="execution",
        log_route=lambda e, **f: logged.append((e, dict(f))))
    assert res is not None
    assert res.action == "handback_failure"
    assert res.body == echo_guard.HAND_BACK_TEXT
    assert "nothing was executed" in res.body.lower()
    blocked = _events(logged, "advisory_echo_blocked")
    assert len(blocked) == 1
    assert blocked[0]["action"] == "handback_failure"
    assert blocked[0]["reason"] == "no_work_signal"
    assert blocked[0]["work_signal"] == "no_tool_activity"


def test_read_only_turn_is_handed_back_not_blocked_as_work(logged):
    """Only looking at files is not work: the handback path stays reachable."""
    ac.note_turn_open(SID, ASK)
    ws.note_request_activity(SID, _write_payload("read_file"))
    res = echo_guard.enforce_execution_echo(ECHO_ONLY, SID, frame="execution")
    assert res is not None and res.action == "handback_failure"


def test_unstrippable_work_turn_withholds_without_lying(logged):
    """Work happened but the strip yields no report -> the notice must not
    claim anything about execution (it points at the log instead)."""
    ac.note_turn_open(SID, ASK)
    ws.note_terminal_activity(SID, "pytest")
    body = ECHO_ONLY          # nothing but echo
    res = echo_guard.enforce_execution_echo(
        body, SID, frame="execution",
        log_route=lambda e, **f: logged.append((e, dict(f))))
    assert res is not None and res.action == "withhold_echo"
    assert res.body == echo_guard.WITHHELD_TEXT
    assert "nothing was executed" not in res.body.lower()
    withheld = _events(logged, "advisory_echo_withheld")
    assert withheld and withheld[0]["reason"] == "strip_failed"
    assert withheld[0]["action"] == "withhold_echo"


def test_non_execution_frame_strips_the_echo_but_never_hands_back():
    """CONTRACT CHANGE (R34-19f, 2026-10-21).

    Was: a non-execution frame was log-only, body untouched. That shipped the
    router's own advisory to the user on every plain question — the fleet sweep
    logged `envelope_echo_delivered` on 10/10 agents while the answer's tail
    carried `{"adversarial": …, "povs": […]}` and, on reviewer, the whole
    higher-self envelope including "SEAM INSTRUCTION: … now PROCEED".

    Now: a non-execution frame is STRIPPED (delivery boundary) but is never
    handed back and never withheld (those are execution-frame semantics). A
    clean body is still returned untouched, which is what "log-only" really
    meant.
    """
    ac.note_turn_open(SID, ASK)
    ws.note_terminal_activity(SID, "pytest")
    res = echo_guard.enforce_execution_echo(WORK_BODY, SID, frame="impulse")
    assert res is not None
    assert res.action == "strip_keep_work"
    assert "nothing was executed" not in res.body
    # a body with nothing to strip stays untouched
    assert echo_guard.enforce_execution_echo(
        "Just a plain answer with no advisory tail.", SID,
        frame="impulse") is None


def test_seam_keeps_the_banner_on_a_stripped_work_turn(logged, monkeypatch):
    """STOPPING-POINT ACCEPTANCE: on a work turn the echo is stripped at the
    POST seam, the report survives, the parked banner still attaches, and the
    delivered body never says "nothing was executed"."""
    from hermes_router import completion_audit, decision_midturn, render_inbox
    from hermes_router import route_gate, router_core, state, usage_ledger

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
                        lambda: str(_tmp_inbox(monkeypatch)))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(_tmp_inbox(monkeypatch)))

    ac.note_turn_open(SID, ASK)
    ws.note_terminal_activity(SID, "pytest -q")
    ws.note_request_activity(SID, _write_payload("write_file"))
    banner = debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model="m", endpoint="h",
        tokens_in=10, tokens_out=5, est_cost=0.01, latency_s=1.0,
        task_id="t1", session_id=SID)
    debug_banner.park_anchor_banner(SID, banner)

    out = plugin.on_transform_llm_output(
        response_text=WORK_BODY, session_id=SID, model="m")
    assert out, "the seam returned nothing"
    assert out.startswith("Built dedupe.py")            # (a) report leads
    assert "5 passed" in out
    assert "· router ·" in out                           # (c) banner present
    assert "nothing was executed" not in out.lower()     # (d)
    assert '"povs"' not in out
    assert _events(logged, "advisory_echo_stripped")


def _tmp_inbox(monkeypatch):
    import tempfile
    import os
    d = tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None)
    return os.path.join(d, "x.jsonl")


# ------------------------------------------- item 3: parked must be true
def test_consume_on_a_skipped_turn_reports_no_park(logged):
    """Session 20261021_065449_25ae5a shape: the anchor lane SKIPPED, nothing
    was parked, no banner in the body — the consume event must say so."""
    ac.note_turn_open(SID, "clear simple ask")
    out = bll.LIFECYCLE.deliver(SID, "plain answer", "anchor", "benign",
                                log_edge="benign")
    assert out == "plain answer"
    crec = _events(logged, "anchor_banner_consume")
    assert len(crec) == 1
    assert crec[0]["parked"] is False
    assert crec[0]["delivered"] is False
    assert crec[0]["reason"] == "no_park_this_turn"


def test_parked_true_requires_a_park_this_turn(logged, monkeypatch):
    """`parked=True` is derived from the park semaphore record, so it can
    never appear on a turn where nothing was parked."""
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    ac.note_turn_open(SID, ASK)
    banner = debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model="m", endpoint="h",
        tokens_in=1, tokens_out=1, est_cost=0.01, latency_s=1.0,
        task_id="t1", session_id=SID)
    debug_banner.park_anchor_banner(SID, banner)
    assert debug_banner.park_record(SID), "a park must register a record"
    out = bll.LIFECYCLE.deliver(SID, "work report", "anchor", "benign",
                                log_edge="benign")
    crec = _events(logged, "anchor_banner_consume")
    assert len(crec) == 1
    assert crec[0]["parked"] is True
    assert crec[0]["delivered"] is True
    assert crec[0]["reason"] == "delivered"
    assert "work report" in out

    # Next turn, nothing parked -> parked=False, never True.
    ac.note_turn_open(SID, "another ask entirely")
    bll.LIFECYCLE.deliver(SID, "second answer", "anchor", "benign",
                          log_edge="benign")
    last = _events(logged, "anchor_banner_consume")[-1]
    assert last["parked"] is False
    assert last["reason"] == "no_park_this_turn"
