"""R78-U — decision-lane supersession ordering + bounded prose admission.

Three defects, one shape: a lane whose silence cannot be attributed.

1. The decision lane stood down UNCONDITIONALLY while the reflex lane was
   merely ARMED (`legacy_lane_superseded`), so detection never ran and empty
   options/envelope fields were consequences, not extraction failures. The
   stand-down is now per-turn: the reflex lane must be able to build a board
   from THIS turn's own text.
2. The shape gate rejected plain-prose "X or Y" wholesale. A BOUNDED prose
   fork is now admitted; the bound is proved here against a three-option ask,
   a multi-sentence ask, and a non-option prose block.
3. The reflex lane returned silently on 3 of 4 probe sessions with its reason
   only in memory. Every decline now emits one `reflex_held reason=<token>`.
"""
import sys

sys.path.insert(0, "/opt/data/plugins")

import pytest  # noqa: E402

from hermes_router import decision as _dec  # noqa: E402
from hermes_router import decision_midturn as dm  # noqa: E402
from hermes_router import reflex_lane as rl  # noqa: E402
from hermes_router import reflex_weights as _rw  # noqa: E402

REFLEX_ARMED = {"hermes_router": {"reflex_weights": {"enabled": True}}}

# A plain two-option prose ask that is NOT the admitted shape: two sentences,
# so the bound refuses it (same class as the probe specimen).
PLAIN_PROSE_ASK = ("The lane went silent again on the probe run. "
                   "Should we keep the midturn seam or remove it?")
# Two rival paths in one tool result: the reflex lane CAN build a board here.
COVERABLE_TEXT = ("read /opt/data/app/utils_v1.py and /opt/data/app/utils_v2.py\n"
                  "app.py imports utils_v2 at line 12, utils_v1 still shipped\n")


def _wire(monkeypatch, mode="on"):
    events = []
    monkeypatch.setattr(dm, "_log",
                        lambda sid, name, **kw: events.append((name, kw)))
    monkeypatch.setattr(dm, "_mode", lambda cfg: mode)
    monkeypatch.setattr(dm, "_cfg", lambda: dict(_dec.DEFAULTS))
    # The reflex lane is ARMED in these tests (conftest isolates the real cfg).
    monkeypatch.setattr(dm, "_superseded_by_reflex", lambda cfg=None: True)
    dm._SUPERSEDED_LOGGED.clear()
    dm._COVERAGE_LOGGED.clear()
    return events


# ---------------------------------------------------------------------------
# The bound: exactly what is admitted, and what it rejects
# ---------------------------------------------------------------------------

def test_bounded_two_option_prose_ask_is_admitted():
    for ask in ("Should we ship the envelope now or hold the release?",
                "Do we keep the midturn seam, or remove it?",
                "Postgres or SQLite?"):
        assert _dec.prose_fork_shape(ask) == "bounded", ask
        assert _dec.has_bounded_prose_fork(ask) is True
        sides = _dec.bounded_prose_options(ask)
        assert len(sides) == 2, (ask, sides)


def test_bound_rejects_a_three_option_ask():
    for ask in ("Should we ship today, tomorrow, or next week?",
                "Do we pick redis or memcached or a file cache?"):
        assert _dec.prose_fork_shape(ask) == "unbounded", ask
        assert _dec.has_bounded_prose_fork(ask) is False


def test_bound_rejects_a_multi_sentence_ask():
    assert _dec.prose_fork_shape(PLAIN_PROSE_ASK) == "unbounded"
    assert _dec.has_bounded_prose_fork(PLAIN_PROSE_ASK) is False


def test_bound_rejects_a_non_option_prose_block():
    for block in ("deploy finished, see log for the stdout or stderr redirect;"
                  " nothing to decide here",
                  "if not ok or not retry: return False",
                  "should we keep this or drop it\nand then run the suite"):
        assert _dec.has_bounded_prose_fork(block) is False, block


# ---------------------------------------------------------------------------
# ACCEPTANCE — attributable silence, not unattributable stand-down
# ---------------------------------------------------------------------------

def test_plain_prose_ask_logs_no_options_and_never_the_bare_stand_down(monkeypatch):
    """The exact acceptance: `midturn_suppressed reason=no_options` and NOT
    `legacy_lane_superseded`, with the reflex lane armed for the session."""
    events = _wire(monkeypatch)
    dm.on_terminal_output("r78u-accept", "terminal", PLAIN_PROSE_ASK)

    names = [n for n, _ in events]
    suppressed = [kw for n, kw in events if n == "midturn_suppressed"]
    assert suppressed, names
    assert any(kw.get("reason") == "no_options" for kw in suppressed), suppressed
    assert "legacy_lane_superseded" not in names, names


def test_armed_reflex_that_cannot_cover_the_turn_says_why_it_stayed_live(monkeypatch):
    events = _wire(monkeypatch)
    dm.on_terminal_output("r78u-active", "terminal", PLAIN_PROSE_ASK)

    active = [kw for n, kw in events if n == "legacy_lane_active"]
    assert active, [n for n, _ in events]
    assert active[0]["reason"] == "reflex_cannot_cover_turn"
    assert active[0]["why"], active[0]


def test_armed_reflex_covers_a_board_bearing_turn_and_says_so(monkeypatch):
    events = _wire(monkeypatch)
    dm.on_terminal_output("r78u-cover", "terminal", COVERABLE_TEXT)

    superseded = [kw for n, kw in events if n == "legacy_lane_superseded"]
    assert superseded, [n for n, _ in events]
    assert superseded[0]["reason"] == "reflex_covers_turn"
    assert superseded[0]["covers"].startswith("reflex_board="), superseded[0]
    # and the lane did not run detection on a turn the reflex covers
    assert not [n for n, _ in events if n == "midturn_suppressed"]


def test_stand_down_is_per_turn_not_per_session(monkeypatch):
    """One coverable turn must not license standing down for later turns of the
    same session — the old per-session dedupe hid every later turn."""
    events = _wire(monkeypatch)
    dm.on_terminal_output("r78u-perturn", "terminal", COVERABLE_TEXT)
    dm.on_terminal_output("r78u-perturn", "terminal", PLAIN_PROSE_ASK)

    names = [n for n, _ in events]
    assert "legacy_lane_superseded" in names
    assert any(kw.get("reason") == "no_options"
               for n, kw in events if n == "midturn_suppressed"), names


def test_bounded_prose_ask_passes_the_shape_gate(monkeypatch):
    """Admission end to end: the bounded ask reaches _handle_hit (shadow logs
    it) instead of dying on the declared-structure gate."""
    events = _wire(monkeypatch, mode="shadow")
    dm.on_terminal_output("r78u-bounded", "terminal",
                          "Should we ship the envelope now or hold the release?")

    suppressed = [kw for n, kw in events if n == "midturn_suppressed"]
    assert suppressed, [n for n, _ in events]
    reasons = {kw.get("reason") for kw in suppressed}
    assert reasons == {"shadow"}, reasons


# ---------------------------------------------------------------------------
# Defect 2 — the reflex lane names every silence
# ---------------------------------------------------------------------------

def _reflex_events(monkeypatch):
    events = []
    monkeypatch.setattr(rl, "_log",
                        lambda sid, name, **kw: events.append((name, kw)))
    monkeypatch.setattr(_rw, "_note", lambda **kw: None)
    return events


def test_reflex_lane_attributes_a_thin_request(monkeypatch):
    events = _reflex_events(monkeypatch)
    rl.clear()
    ok = rl.run_on_request({"messages": [{"role": "user", "content": "hi"}]},
                           session_id="r78u-reflex-thin", cfg=REFLEX_ARMED)
    assert ok is False
    held = [kw for n, kw in events if n == "reflex_held"]
    assert held and held[-1]["reason"] == "no_work_in_request", held


def test_reflex_lane_attributes_a_thin_tool_result(monkeypatch):
    events = _reflex_events(monkeypatch)
    rl.clear()
    block = rl.fire("r78u-reflex-fire", "terminal", "short output", REFLEX_ARMED)
    assert block == ""
    held = [kw for n, kw in events if n == "reflex_held"]
    assert held and held[-1]["reason"] == "result_too_thin", held


def test_reflex_lane_attributes_a_missing_candidate_set(monkeypatch):
    events = _reflex_events(monkeypatch)
    rl.clear()
    block = rl.fire("r78u-reflex-nocand", "terminal",
                    "a" * 80 + " nothing nameable in this result at all", REFLEX_ARMED)
    assert block == ""
    held = [kw for n, kw in events if n == "reflex_held"]
    assert held and held[-1]["reason"] in ("no_candidates", "transport_error"), held


def test_reflex_lane_attributes_a_disabled_lane(monkeypatch):
    events = _reflex_events(monkeypatch)
    rl.clear()
    ok = rl.run_on_request({"messages": [{"role": "user", "content": "x" * 200}]},
                           session_id="r78u-reflex-off", cfg={"hermes_router": {}})
    assert ok is False
    held = [kw for n, kw in events if n == "reflex_held"]
    assert held and held[-1]["reason"] == "lane_disabled", held
