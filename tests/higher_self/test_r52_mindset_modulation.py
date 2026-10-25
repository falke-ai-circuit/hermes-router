"""R52 — MINDSET MODULATION pins (T1-T8).

The measured basis (R47/R48, n=135 paired PRE/trace sessions) keeps exactly
two drives: scope (r=+0.291, p=0%) and rigour (r=+0.232, p=1%). resolve and
tempo do not predict behaviour and are dropped from PRE (T6).

Contracts pinned here:
  T1 unreadable trace -> None for that drive, and NO stance line from it
  T2 |delta| inside band -> zero lines (silence)
  T3 |delta| outside band -> exactly one line for that drive, <=110 chars
  T4 both out of band -> exactly two lines, total <=220 chars
  T5 exception inside mindset.py -> consult returns its advisory unchanged and
     `mindset_modulation_skipped` is logged
  T6 tempo/resolve are NOT emitted anywhere
  T7 the stance lines carry no task-specific advice (no file names, no commands)
  T8 the advisory body persisted to state.db does not contain the block

All pins are mocked: zero network, zero live LLM calls.
"""
import os
import re
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from hermes_router import anchor_chain, anchor_exec, mindset, router_core  # noqa: E402
from hermes_router import mindset_note  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import telemetry  # noqa: E402

SID = "s-r52-mindset"
ASK = "make the change"
ADVISORY = "PLAN\n1. read the target and widen to its callers\n2. verify each claim"


def _history(n=40):
    """Plain prior raw scores: 0..n-1 for both drives (>= MIN_HISTORY)."""
    return {"scope": [float(i) for i in range(n)],
            "rigour": [float(i) for i in range(n)]}


def _trace(n_tool=6, files=3, checks=2):
    """A readable synthetic (role, tool_name, content) trace."""
    rows = [("user", None, ASK)]
    for i in range(n_tool):
        if i < files:
            rows.append(("tool", "read_file", "opened pkg/mod_%d.py for review" % i))
        elif i < files + checks:
            rows.append(("tool", "terminal", "pytest -q -> 12 passed"))
        else:
            rows.append(("tool", "terminal", "git status --short -> clean"))
    rows.append(("assistant", None, "done"))
    return rows


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No live state.db history, no live pairing state."""
    state.clear_pre_advisory(SID)
    monkeypatch.setattr(anchor_exec, "_mindset_history", lambda *a, **k: _history())
    yield
    state.clear_pre_advisory(SID)


# ----------------------------------------------------------------- T1 --------

def test_t1_unreadable_trace_is_none_never_the_floor():
    got = mindset.read_trace_vectors([], _history())
    assert got == {"scope": None, "rigour": None}
    # a trace with no tool calls carries no signal at all
    got2 = mindset.read_trace_vectors([("user", None, ASK)], _history())
    assert got2["scope"] is None and got2["rigour"] is None
    # and nothing is emitted from an unreadable drive
    assert mindset.stance_lines({"scope": None, "rigour": None}) == []
    # None propagates through the delta when either side is unreadable
    d = mindset.delta_vector({"scope": 0.9, "rigour": 0.9},
                             {"scope": None, "rigour": 0.1})
    assert d["scope"] is None
    # ... and NO stance line is emitted from the unreadable drive
    lines = mindset.stance_lines(d)
    assert all("scope" not in ln.lower() for ln in lines)
    assert mindset.stance_lines({"scope": None, "rigour": None}) == []


def test_t1_short_history_is_none():
    """< 20 sessions of the profile's own history -> uncalibrated -> None."""
    for hist in (None, {}, {"scope": [1.0] * 5, "rigour": [1.0] * 5}):
        got = mindset.read_trace_vectors(_trace(), hist)
        assert got == {"scope": None, "rigour": None}
    # constant history cannot rank anything either (no variance -> unknown)
    flat = {"scope": [0.0] * 40, "rigour": [0.0] * 40}
    assert mindset.read_trace_vectors(_trace(), flat) == {"scope": None, "rigour": None}


# ----------------------------------------------------------------- T2 --------

def test_t2_inside_band_is_silent():
    assert mindset.stance_lines({"scope": 0.1, "rigour": -0.2}) == []
    assert mindset.stance_lines({"scope": 0.5, "rigour": -0.5}) == []  # band is exclusive
    assert mindset.stance_lines({}) == []


# ----------------------------------------------------------------- T3 --------

def test_t3_one_line_only_for_the_drive_out_of_band():
    lines = mindset.stance_lines({"scope": 0.9, "rigour": 0.1})
    assert len(lines) == 1
    assert "scope" in lines[0].lower()
    assert len(lines[0]) <= 110
    lines = mindset.stance_lines({"scope": 0.1, "rigour": -0.9})
    assert len(lines) == 1
    assert "rigour" in lines[0].lower()
    assert len(lines[0]) <= 110


def test_t3_direction_selects_the_opposite_stance():
    wide = mindset.stance_lines({"scope": 0.9, "rigour": None})[0]
    narrow = mindset.stance_lines({"scope": -0.9, "rigour": None})[0]
    assert wide != narrow
    more = mindset.stance_lines({"scope": None, "rigour": 0.9})[0]
    less = mindset.stance_lines({"scope": None, "rigour": -0.9})[0]
    assert more != less


# ----------------------------------------------------------------- T4 --------

def test_t4_both_out_of_band_is_two_lines_within_budget():
    lines = mindset.stance_lines({"scope": 0.9, "rigour": -0.9})
    assert len(lines) == 2
    assert all(len(x) <= 110 for x in lines)
    assert sum(len(x) for x in lines) + 1 <= 220


# ----------------------------------------------------------------- T5 --------

def _pre_run(monkeypatch, content=ADVISORY, orientation=True):
    """Drive the PRE orientation branch; return (result, events)."""
    rec = {
        "task_id": "t-r52",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri("openrouter://test/model-a",
                                                  "primary"),
        "route_id": "r-r52",
        "orientation": orientation,
        "reason": "complexity_orientation",
    }
    events = []
    orig = telemetry.patchpoints.log_route_override
    telemetry.patchpoints.log_route_override = lambda event, **f: events.append(
        dict(f, _event=event))

    def _call(endpoint, api_kwargs):
        return (content, 0.0, 10, 5)

    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "anchored_call", _call)
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **k: None)
    api = {"messages": [{"role": "user", "content": ASK}]}
    try:
        result = anchor_exec.maybe_execute_anchored(SID, api)
    finally:
        telemetry.patchpoints.log_route_override = orig
    return result, events


def _skipped(events):
    return [e for e in events
            if str(e.get("event_detail") or "") == "mindset_modulation_skipped"]


def _emitted(events):
    return [e for e in events
            if str(e.get("event_detail") or "") == "mindset_modulation"]


def test_t5_exception_inside_mindset_leaves_the_consult_unchanged(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("mindset exploded")

    # R58k: the delivery path renders through mindset_note, so the
    # failure-isolation contract has to be probed there; monkeypatching
    # a function that is no longer called proves nothing.
    monkeypatch.setattr(mindset_note, "note", _boom)
    # R53: the modulation path only runs when a structured target exists
    _used_zero(monkeypatch)
    result, events = _pre_run(
        monkeypatch,
        content=ADVISORY + '\nMINDSET_TARGET {"scope": 0.9, "rigour": 0.9}')
    assert result is not None and result[0] == "done"
    env = result[1]
    assert mindset_note.NOTE_HEADER not in str(env.get("answer") or "")
    assert str(env.get("answer") or "").startswith(ADVISORY[:20])
    sk = _skipped(events)
    assert len(sk) == 1
    assert sk[0].get("reason") == "RuntimeError"


# ----------------------------------------------------------------- T6 --------

def test_t6_tempo_and_resolve_are_never_emitted():
    assert mindset.DRIVES == ("scope", "rigour")
    lines = mindset.stance_lines({"scope": 0.9, "rigour": 0.9,
                                  "resolve": 0.9, "tempo": -0.9})
    assert len(lines) == 2
    for ln in lines:
        assert "tempo" not in ln.lower()
        assert "resolve" not in ln.lower()


# ----------------------------------------------------------------- T7 --------

def test_t7_stance_lines_carry_no_task_specific_advice():
    file_ext = re.compile(r"\.(py|md|go|ts|js|sh|yaml|yml|json)\b", re.I)
    command = re.compile(r"\b(git|pytest|sed|grep|awk|curl|npm|go test|"
                         r"rm|mv|cp|chmod|systemctl)\b", re.I)
    for s in (-1, 1):
        for r in (-1, 1):
            lines = mindset.stance_lines({"scope": float(s), "rigour": float(r)})
            assert len(lines) == 2
            for ln in lines:
                assert not file_ext.search(ln), ln
                assert not command.search(ln), ln
                assert "/" not in ln, ln


# ----------------------------------------------------------------- T8 --------

def _used_zero(monkeypatch):
    """A readable trace scoring 0/0, so the calibrated stance saturates low."""
    monkeypatch.setattr(mindset, "raw_trace_scores",
                        lambda *a, **k: ({"scope": 0.0, "rigour": 0.0},
                                         {"scope": True, "rigour": True}))


def test_t8_persisted_body_does_not_contain_the_block(monkeypatch):
    """The delivered envelope carries the block; state.db's stored PRE text does not."""
    _used_zero(monkeypatch)
    content = ADVISORY + '\nMINDSET_TARGET {"scope": 0.9, "rigour": 0.9}'
    result, events = _pre_run(monkeypatch, content=content)
    assert result is not None and result[0] == "done"
    env = result[1]
    answer = str(env.get("answer") or "")
    assert mindset_note.NOTE_HEADER in answer
    assert answer.count(mindset_note.NOTE_HEADER) == 1
    stored = state.pre_advisory(SID)
    assert stored is not None
    assert mindset_note.NOTE_HEADER not in str(stored.get("text") or "")
    assert str(stored.get("text") or "") == ADVISORY


def test_t8_silence_appends_nothing_at_all(monkeypatch):
    """In band -> no header, no blank spacer, no event with emitted>0."""
    _used_zero(monkeypatch)
    # target == the agent's current calibrated stance => delta 0 => silence
    content = ADVISORY + '\nMINDSET_TARGET {"scope": -1.0, "rigour": -1.0}'
    result, events = _pre_run(monkeypatch, content=content)
    env = result[1]
    assert str(env.get("answer") or "") == ADVISORY
    em = _emitted(events)
    assert len(em) == 1
    assert int(em[0].get("emitted") or 0) == 0


# ----------------------------------------------------------------- R53 -------
# T7/T8/T9 (R53): the target is a STRUCTURED block, never prose scored by
# regex. The R52 keyword counter is deleted; a text proxy wearing the
# validated r/p figures is worse than no modulation.

def test_r53_t7_target_absent_means_no_modulation_and_is_logged(monkeypatch):
    _used_zero(monkeypatch)
    result, events = _pre_run(monkeypatch, content=ADVISORY)
    env = result[1]
    assert str(env.get("answer") or "") == ADVISORY
    assert env.get("mindset_modulation") is None
    missed = [e for e in events
              if str(e.get("event_detail") or "") == "mindset_target_missing"]
    assert len(missed) == 1
    assert _emitted(events) == []


def test_r53_t8_target_is_parsed_and_the_line_never_survives(monkeypatch):
    _used_zero(monkeypatch)
    block = 'MINDSET_TARGET {"scope": 0.9, "rigour": -0.9}'
    result, events = _pre_run(monkeypatch,
                              content="PLAN\n1. do it\n" + block + "\n")
    env = result[1]
    answer = str(env.get("answer") or "")
    assert "MINDSET_TARGET" not in answer
    assert mindset_note.NOTE_HEADER in answer
    stored = state.pre_advisory(SID)
    assert "MINDSET_TARGET" not in str(stored.get("text") or "")
    assert str(stored.get("text") or "").strip() == "PLAN\n1. do it"
    # the parsed vector is what fed the delta: +scope widening, -rigour
    assert any(e.get("event_detail") == "mindset_modulation" for e in events)


def test_r53_t8_unparseable_target_is_a_miss_not_a_crash(monkeypatch):
    _used_zero(monkeypatch)
    bad = 'MINDSET_TARGET {"scope": 3.4, "rigour": 0.2}'
    result, events = _pre_run(monkeypatch, content=ADVISORY + "\n" + bad)
    env = result[1]
    assert str(env.get("answer") or "") == ADVISORY
    assert len([e for e in events if e.get("event_detail")
                == "mindset_target_missing"]) == 1


def test_r53_t9_projection_vector_is_dict_only_no_text_path():
    import inspect as _inspect

    assert not hasattr(mindset, "raw_projection_scores")
    src = _inspect.getsource(mindset.projection_vector)
    for banned in ("re.", "findall", "search(", "_PATH_RE", "_CHECK_RE"):
        assert banned not in src, banned
    got = mindset.projection_vector({"scope": 0.5, "rigour": -0.5}, _history())
    assert got["scope"] is not None and got["rigour"] is not None
    # structured floats ONLY: prose, missing and out-of-range are UNKNOWN
    assert mindset.projection_vector("widen this", _history()) == {
        "scope": None, "rigour": None}
    assert mindset.projection_vector({"scope": 0.4}, _history())["rigour"] is None
    assert mindset.projection_vector({"scope": 1.4, "rigour": 0.0},
                                     _history())["scope"] is None
