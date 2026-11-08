"""M36-25: the found branch reads the route log through the SESSION predicate.

M36-23/B1 measured the defect: ``grep_new_log(off, pattern)`` seeked to a byte
offset and returned ``pattern in text``. A byte window has no session scope, so
a FOREIGN live session's row satisfied the scenario's expectation. architect's
F1 T1 "PASS" read at ~18:31:56Z matched ``anchor_route_fired`` written at
18:31:46Z by ``session_id=api_1794162696_2701a394`` — seven seconds earlier and
not the battery's session (``api_1794162651_ba179cf5``). Its U1 PASS is the same
cause: ``anchor_route_fired`` inside the U1 window, foreign sid, matching the
substring ``route_fired``.

The fix routes presence through ``battery_run.attributable_event_rows`` — the
P1-P4 predicate the ABSENT branch already grades with — so the two halves of
the grader cannot disagree about what "present" means.

Harness note: ``behavioral_battery.py`` executes its LIVE scenarios at import
(top-level ``run_scenario`` calls), so these tests load it up to the SCENARIOS
marker. That prefix holds ``grep_new_log`` and ``run_scenario`` — exactly the
found branch — and stops before any gateway turn.
"""
import contextlib
import io
import os
import time as _time
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATTERY = os.path.join(REPO, "benchmarks", "behavioral_battery.py")
SCENARIOS_MARK = "# ============ SCENARIOS ============"

# The architect F1/U1 sids from M36-23/B1 (real session ids, kept verbatim).
OWN = "api_1794162651_ba179cf5"
FOREIGN = "api_1794162696_2701a394"
U1_OWN = "api_1794162902_0e3d287a"


def _battery(monkeypatch, route_log):
    """Exec behavioral_battery.py up to its scenario block; return its globals."""
    src = open(BATTERY, errors="replace").read().split(SCENARIOS_MARK)[0]
    monkeypatch.setenv("GATEWAY_BEARER", "test-bearer")
    monkeypatch.setenv("BATT_ROUTE_LOG", str(route_log))
    ns = {"__name__": "behavioral_battery_m36_25", "__file__": BATTERY}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(src, BATTERY, "exec"), ns)
    # The turn sleeps are wall-clock; the stamps stay real (the predicate reads
    # them against the turn start), only the sleeps are dropped.
    ns["time"] = types.SimpleNamespace(
        time=_time.time, sleep=lambda _s: None,
        strftime=_time.strftime, gmtime=_time.gmtime)
    return ns


def _stamp(epoch):
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(epoch))


def _row(ts, prefix, event, sid, tail=""):
    """One route-log row in the writer's real shape: ts, stage, event, sid."""
    sidf = (" session_id=" + sid) if sid else ""
    tailf = (" " + tail) if tail else ""
    return "%s %s event_detail=%s%s%s\n" % (_stamp(ts), prefix, event, tailf, sidf)


def _drive(ns, name, turns, sid, writer):
    """Run one scenario with the gateway stubbed; the stub writes the log rows."""
    ns["send_turn"] = lambda _sid, _text, timeout=240: writer()
    ns["get_delivered"] = lambda _sid: "x" * 600
    ns["new_session"] = lambda: sid
    with contextlib.redirect_stdout(io.StringIO()):
        ns["run_scenario"](name, turns, sid=sid)
    return ns["RESULTS_LIST"][-1]


def _append(path, text):
    with open(path, "a") as f:
        f.write(text)


# --- (i) a foreign-session row inside the byte window is NOT presence -------

def test_foreign_session_row_in_the_byte_window_does_not_satisfy_presence(
        tmp_path, monkeypatch):
    """The M36-23/B1 shape in the smallest form: only a FOREIGN row is written.

    Pre-fix this graded PASS: the byte window contained the event token, and
    the token test had no session scope.
    """
    log = tmp_path / "route.log"
    log.write_text("")
    ns = _battery(monkeypatch, log)

    rec = _drive(
        ns, "F1_complexity_pre",
        [{"text": "Design a caching layer.", "seam_free": False,
          "expect_events": ["anchor_route_fired"]}],
        OWN,
        lambda: _append(log, _row(_time.time(), "PRE", "anchor_route_fired",
                                  FOREIGN, "lane=complexity mode=consult")))

    assert rec["pass"] is False
    assert "missing_event:anchor_route_fired" in rec["detail"]
    # the reject is quoted, so the class is visible rather than guessed
    assert "near_miss:" in rec["detail"]
    assert FOREIGN in rec["detail"]


def test_foreign_substring_of_a_longer_event_name_is_not_presence(
        tmp_path, monkeypatch):
    """U1: `anchor_route_fired` carries the substring `route_fired`.

    M36-23 corroboration: architect's U1 T1 hit at 18:35:13Z was
    `event_detail=anchor_route_fired`, a different emit site, and foreign.
    """
    log = tmp_path / "route.log"
    log.write_text("")
    ns = _battery(monkeypatch, log)

    rec = _drive(
        ns, "U1_render_continuity",
        [{"text": "Write a noir scene.", "seam_free": False,
          "expect_events": ["route_fired"]}],
        U1_OWN,
        lambda: _append(log, _row(_time.time(), "PRE", "anchor_route_fired",
                                  FOREIGN, "lane=complexity")))

    assert rec["pass"] is False
    assert "missing_event:route_fired" in rec["detail"]
    assert "anchor_route_fired" in rec["detail"]


# --- (ii) the scenario's own row DOES satisfy presence ----------------------

def test_the_scenarios_own_row_satisfies_presence(tmp_path, monkeypatch):
    """Control in the other direction: a session-scoped hit is still a PASS.

    Also pins the mechanism: the found branch returns ROW objects carrying this
    session's sid, and reports no near-miss. Pre-fix the callable returned a
    bare bool (there was no session scope to inspect), so this assertion is
    what the old shape could not answer.
    """
    log = tmp_path / "route.log"
    log.write_text("")
    ns = _battery(monkeypatch, log)

    rec = _drive(
        ns, "F1_complexity_pre",
        [{"text": "Design a caching layer.", "seam_free": False,
          "expect_events": ["anchor_route_fired"]}],
        OWN,
        lambda: _append(log, _row(_time.time(), "PRE", "anchor_route_fired",
                                  OWN, "lane=complexity mode=consult")))

    rows, foreign = ns["grep_new_log"](0, "anchor_route_fired", sid=OWN,
                                       since_epoch=_time.time() - 60)
    assert len(rows) == 1
    assert "session_id=" + OWN in rows[0][1]
    assert foreign == []
    assert rec["pass"] is True
    assert "missing_event" not in rec["detail"]


def test_extra_pattern_tokens_still_bind_the_own_row(tmp_path, monkeypatch):
    """`"completion_audit_gate ok=True"` keeps meaning what it meant.

    N1 forbids that exact shape; the event token alone is not enough — the
    extra token must be on the same row.
    """
    log = tmp_path / "route.log"
    log.write_text("")
    ns = _battery(monkeypatch, log)
    log.write_text(_row(_time.time(), "POST", "completion_audit_gate", OWN,
                        "ok=False reason=short turn=2 of=3") + "\n")
    rows, _ = ns["grep_new_log"](0, "completion_audit_gate ok=True", sid=OWN,
                                 since_epoch=_time.time() - 60)
    assert rows == []
    rows, _ = ns["grep_new_log"](0, "completion_audit_gate ok=False", sid=OWN,
                                 since_epoch=_time.time() - 60)
    assert len(rows) == 1


# --- (iii) the architect F1/U1 shape, reproduced locally -------------------

def test_architect_f1_window_grades_absent_with_the_foreign_row_quoted(
        tmp_path, monkeypatch):
    """F1 T1 replay: own stand-down rows + a foreign anchor row 7 s before read.

    The window is byte-identical in shape to architect's (M36-23/B1: own
    `complexity_skip reason=pre_no_impact stage1=clear_complex score=1` at
    18:30:55Z and 18:31:29Z, foreign `anchor_route_fired` at 18:31:46Z). The
    graded event is absent; the near-miss is the foreign row.
    """
    log = tmp_path / "route.log"
    log.write_text("")
    ns = _battery(monkeypatch, log)
    now = _time.time()

    def writer():
        _append(log, _row(now - 20, "PRE", "complexity_skip", OWN,
                          "reason=pre_no_impact stage1=clear_complex score=1"))
        _append(log, _row(now - 7, "PRE", "anchor_route_fired", FOREIGN,
                          "reason=complexity_orientation route_id=5fdcd71302eb"))

    rec = _drive(
        ns, "F1_complexity_pre",
        [{"text": "Design a caching layer.", "seam_free": False,
          "expect_events": ["anchor_route_fired"]}],
        OWN, writer)

    assert rec["pass"] is False
    assert "missing_event:anchor_route_fired" in rec["detail"]
    assert "near_miss:" in rec["detail"] and FOREIGN in rec["detail"]


def test_architect_u1_window_grades_absent_not_present(tmp_path, monkeypatch):
    """U1 T1 replay: own rule-table defer rows + a foreign anchor row.

    Own evidence: `denial_class cls=not_a_denial`, `rule_table_decision rule=3
    predicate=gate_abstain action=defer`, `main_body_kept`. The foreign
    `anchor_route_fired` at +10 s is the only `route_fired` substring in the
    window — the old read graded it as presence (M36-23 B1, corroboration).
    """
    log = tmp_path / "route.log"
    log.write_text("")
    ns = _battery(monkeypatch, log)
    now = _time.time()

    def writer():
        _append(log, _row(now - 15, "PRE", "denial_class", U1_OWN,
                          "cls=not_a_denial"))
        _append(log, _row(now - 15, "POST", "rule_table_decision", U1_OWN,
                          "rule=3 predicate=gate_abstain action=defer"))
        _append(log, _row(now - 14, "POST", "main_body_kept", U1_OWN))
        _append(log, _row(now - 10, "PRE", "anchor_route_fired", FOREIGN,
                          "lane=complexity mode=consult"))

    rec = _drive(
        ns, "U1_render_continuity",
        [{"text": "Write a noir scene.", "seam_free": False,
          "expect_events": ["route_fired"]}],
        U1_OWN, writer)

    assert rec["pass"] is False
    assert "missing_event:route_fired" in rec["detail"]
    assert FOREIGN in rec["detail"]


def test_a_row_written_before_the_turn_start_does_not_satisfy_presence(
        tmp_path, monkeypatch):
    """P4, found branch: the window floor is the turn start, not the byte offset.

    A same-session row from an EARLIER turn lands inside the byte window (the
    read starts at the pre-turn offset) and must not answer for this turn.
    Pre-fix the byte window accepted it: the substring test had no floor.
    """
    log = tmp_path / "route.log"
    log.write_text("")
    ns = _battery(monkeypatch, log)
    old = _time.time() - 3600

    def writer():
        # a row this session wrote an hour ago, arriving inside the window
        _append(log, _row(old, "PRE", "anchor_route_fired", OWN,
                          "lane=complexity"))

    rec = _drive(
        ns, "F1_complexity_pre",
        [{"text": "Design a caching layer.", "seam_free": False,
          "expect_events": ["anchor_route_fired"]}],
        OWN, writer)

    assert rec["pass"] is False
    assert "missing_event:anchor_route_fired" in rec["detail"]


def test_found_branch_reuses_the_absent_branch_predicate():
    """One predicate, two branches: the module imports it, never re-implements it.

    A second tokenizer in the battery is the defect this unit exists to
    prevent — the two halves would drift and a row could be present to one and
    absent to the other.
    """
    src = open(BATTERY, errors="replace").read()
    assert "from battery_run import attributable_event_rows" in src
    assert "return pattern in f.read()" not in src
    # the event token is formed in exactly one place per file
    assert src.count('event_detail=" + event') <= 2
