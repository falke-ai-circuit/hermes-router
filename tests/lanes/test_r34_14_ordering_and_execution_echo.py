"""R34-14 — ordering, orphaned advisories, empty persists, execution echo.

Built from two live sessions, both of which ate a real turn:

SESSION A  api_1792543426_8e2f94ef (one user message, 00:43:49)
  twelve `complexity_skip clear_simple` lines, 15 tool calls, work finished
  00:45:16; POST sweep_started 00:45:22; the persisted assistant row for that
  close was len=0; an advisory capped 3121->600 fired at 00:45:35 — THIRTEEN
  SECONDS AFTER the turn closed; a SECOND sweep_started at 00:46:22 with no
  user message between the two.

SESSION B  api_1792543869_964dbe2a (01:03:18-01:03:50)
  complexity_skip clear_simple AND risk_consult_fire r2 on the same turn (the
  risk leg did not honor the complexity mute); advisory capped 3800->600; the
  delivered body was a 5863-char restatement of the advisory.

Item 7 is the headline: when the echo guard fires AND the frame resolves to
`execution`, the turn must not close on the echo.
"""
import sqlite3

import pytest

import hermes_router as plugin
from hermes_router import advisory_consumption as ac
from hermes_router import config_access, echo_guard, risk
from hermes_router.core import canonical

SID = "api_1792543426_8e2f94ef"
ECHO_BODY = (
    "BRIEF — R34-14 (rev 2, item 7 leads). Max 8 bullets.\n"
    "1. SUCCESS SHAPE. A work turn that receives an advisory can never...\n"
    "2. MECHANISM CHOICE — prefer the hand-back...\n"
    "POVs.\nPractitioner: it works in the hand only if...\n"
    "Outsider: the agent answered a dispatch by summarizing...\n"
    "Skeptic: first question is whether the echo guard...\n"
    '{"povs":[{"role":"conservative","claim":"late-injection is unproven"}]}'
)
CLEAN_BODY = "14 passed. retry.py added; commit 3ac6505."


@pytest.fixture()
def logged(monkeypatch):
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: rows.append((e, dict(f))))
    monkeypatch.setattr(ac, "_STATE", {})
    monkeypatch.setattr(ac, "_ORDER", [])
    monkeypatch.setattr(ac, "_CLOSED", {})
    monkeypatch.setattr(ac, "_OPEN_SIG", {})
    monkeypatch.setattr(ac, "_TURN_NO", {})
    monkeypatch.setattr(ac, "_SWEEPS", {})
    monkeypatch.setattr(ac, "_ORPHANS", {})
    yield rows
    ac.clear_turn_ledger(SID)


def _events(rows, name):
    return [f for _, f in rows if f.get("event_detail") == name]


# ---------------------------------------------------------------- item 7
def test_execution_echo_blocks_the_close(monkeypatch):
    """A capped advisory that becomes the SUBJECT of an execution turn must
    not be the delivered answer — and with NO work signal (the guard checked)
    the hand-back is the correct, truthful outcome."""
    rows = []
    monkeypatch.setattr(echo_guard, "detect_envelope_echo_families",
                        lambda t: ["povs_array", "pov_triplet"])
    out = echo_guard.enforce_execution_echo(
        ECHO_BODY, SID, frame="execution",
        log_route=lambda e, **f: rows.append((e, dict(f))))
    assert out is not None
    assert out.action == "handback_failure"
    assert out.body == echo_guard.HAND_BACK_TEXT
    blocked = _events(rows, "advisory_echo_blocked")
    assert len(blocked) == 1
    assert blocked[0]["action"] == "handback_failure"
    assert blocked[0]["reason"] == "no_work_signal"
    assert blocked[0]["chars"] == len(ECHO_BODY)
    assert blocked[0]["frame"] == "execution"


def test_impulse_frame_strips_the_echo(monkeypatch):
    """CONTRACT CHANGE (R34-19f, 2026-10-21): a non-execution frame used to be
    log-only, which put the router's advisory tail in front of the user on
    turns that never touched a tool. Stripping is the delivery boundary; the
    impulse frame is still never handed back or withheld."""
    monkeypatch.setattr(echo_guard, "detect_envelope_echo_families",
                        lambda t: ["povs_array"])
    res = echo_guard.enforce_execution_echo(ECHO_BODY, SID, frame="impulse")
    assert res is not None
    assert res.action == "strip_keep_work"
    assert res.body.strip() and "nothing was executed" not in res.body


def test_clean_execution_body_is_untouched():
    assert echo_guard.enforce_execution_echo(
        CLEAN_BODY, SID, frame="execution") is None


def test_blocked_echo_is_not_reported_as_delivered(monkeypatch):
    """Block first, report second: a refused body must not emit
    `envelope_echo_delivered` — that row would claim we shipped the echo."""
    rows = []
    monkeypatch.setattr(echo_guard, "detect_envelope_echo_families",
                        lambda t: ["povs_array"])
    echo_guard.enforce_execution_echo(
        ECHO_BODY, SID, frame="execution",
        log_route=lambda e, **f: rows.append((e, dict(f))))
    assert _events(rows, "envelope_echo_delivered") == []


# ---------------------------------------------------------------- item 1
def test_advisory_resolving_after_turn_close_is_discarded(logged):
    assert ac.note_advisory_injected(SID, "fix the defects", chars=600,
                                     envelope_chars=1951) is True
    ac.note_turn_closed(SID)
    assert ac.note_advisory_injected(SID, "fix the defects", chars=600) is False
    after = _events(logged, "advisory_after_turn_close")
    assert len(after) == 1
    assert after[0]["delta_s"] >= 0.0
    assert _events(logged, "advisory_orphaned_post_close")[0]["action"] == "discarded"


def test_same_user_text_does_not_reopen_a_closed_turn(logged):
    """The second sweep carries no new user message — it must not bless the
    late consult by 'opening a new turn'."""
    ac.note_turn_open(SID, "run R34-14")
    ac.note_turn_closed(SID)
    ac.note_turn_open(SID, "run R34-14")          # duplicate transform pass
    assert ac.note_advisory_injected(SID, "run R34-14") is False


def test_a_genuinely_new_ask_does_reopen(logged):
    ac.note_turn_open(SID, "run R34-14")
    ac.note_turn_closed(SID)
    ac.note_turn_open(SID, "now do something else")
    assert ac.note_advisory_injected(SID, "now do something else") is True


# ---------------------------------------------------------------- item 2
def test_two_sweeps_one_user_message_are_accounted(logged):
    ac.note_turn_open(SID, "run R34-14")
    n1, dup1 = ac.note_sweep(SID)
    n2, dup2 = ac.note_sweep(SID)
    assert n1 == n2
    assert (dup1, dup2) == (False, True)


# ---------------------------------------------------------------- item 3
def test_empty_body_never_overwrites_a_persisted_row(tmp_path, logged, monkeypatch):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
                 " role TEXT, content TEXT, api_content TEXT)")
    conn.execute("INSERT INTO messages (session_id, role, content)"
                 " VALUES (?, 'assistant', ?)", (SID, "the swept response"))
    conn.commit()
    conn.close()
    assert canonical._rewrite_once(str(db), SID, "the swept response", "", False) is False
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT content FROM messages WHERE session_id = ?",
                       (SID,)).fetchone()
    conn.close()
    assert row[0] == "the swept response", "the row was blanked"
    assert _events(logged, "persist_empty_body_refused")


# ---------------------------------------------------------------- item 4
def test_orphan_names_the_real_condition_not_no_tools(logged):
    ac.note_advisory_injected(SID, "fix the defects")
    ac.note_turn_closed(SID)
    ac.note_advisory_injected(SID, "fix the defects")   # post-close, discarded
    ac.close_turn(SID, log_route=lambda e, **f: logged.append((e, dict(f))))
    assert _events(logged, "advisory_consumed_turn") == []
    orphan = _events(logged, "advisory_orphaned_post_close")
    assert orphan and orphan[-1]["action"] == "orphaned_at_close"


# ---------------------------------------------------------------- item 8
def _cfg(monkeypatch, block):
    monkeypatch.setattr(config_access, "router_section", lambda: block)


def test_risk_inherits_the_complexity_mute(monkeypatch):
    """complexity.level: 1 with no explicit risk.enabled must NOT fire."""
    _cfg(monkeypatch, {"complexity": {"level": 1}})
    assert risk.risk_enabled() is False


def test_explicit_risk_enabled_still_wins(monkeypatch):
    _cfg(monkeypatch, {"complexity": {"level": 0}, "risk": {"enabled": True}})
    assert risk.risk_enabled() is True
    _cfg(monkeypatch, {"complexity": {"level": 3}, "risk": {"enabled": False}})
    assert risk.risk_enabled() is False


def test_auto_consult_tier_keeps_risk_on(monkeypatch):
    _cfg(monkeypatch, {"complexity": {"level": 3}})
    assert risk.risk_enabled() is True


# ---------------------------------------------------------------- session A shape
def test_live_shape_one_message_15_tools_sweep_late_consult_second_sweep(logged):
    """The whole A timeline: the late consult is orphaned, the second sweep is
    accounted as a duplicate, and the 15-tool turn is never accused."""
    ac.note_turn_open(SID, "R34-14 dispatch")           # one user message
    for _ in range(15):
        ac.note_tool_activity(SID)                      # 15 tool calls
    _n1, dup1 = ac.note_sweep(SID)                      # 00:45:22 sweep
    assert dup1 is False
    ac.note_turn_closed(SID)                            # turn CLOSED
    assert ac.note_advisory_injected(SID, "R34-14 dispatch", chars=600) is False
    _n2, dup2 = ac.note_sweep(SID)                      # 00:46:22 sweep
    assert dup2 is True
    ac.close_turn(SID, log_route=lambda e, **f: logged.append((e, dict(f))))
    assert _events(logged, "advisory_consumed_turn") == []
    assert _events(logged, "advisory_after_turn_close")[0]["delta_s"] >= 0.0
