"""R34-11 items 1-5 (still standing; R34-12 finishes them).

1. frame_resolved / frame_unresolved observability on verdict, suppression
   and banner emission — resolved THROUGH config_access, not a hand cfg.
2. execution register at L1: delivered banner tail has NO "Top verdicts:" and
   no option label.
3. the INJECTED string at the flush seam is the <=400-char fork note + the
   continue-your-task directive — no option menu.
4. config-resolution test through config_access.router_section().
5. off_task_fork: a skills/DNA-shaped option set on a code-work task is
   suppressed, never offered.
"""
import json
import os
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import pytest

import hermes_router as plugin  # noqa: E402
from hermes_router import decision_midturn as dmt  # noqa: E402
from hermes_router.core import config_access  # noqa: E402

SID = "s-r34-11-items"

ENV = {
    "options": ["CREATE A CLASS-LEVEL UMBRELLA", "ADD A SUPPORT FILE"],
    "weighting": {"weights": {"opt-1": 0.51, "opt-2": 0.49},
                  "band": "noise", "evidence": []},
    "scope": {},
    "fork_class": "code_work_vs_skill_curation",
}
# R37-3: a below-band verdict now emits NOTHING, so this battery pins its
# other properties (fork-note shape / off-task suppression) at a confidence
# above the lane's noise floor.
VERDICT = {"choice": "opt-1", "confidence": 0.82,
           "alternatives": ["opt-2"]}
FORK_TEXT = ("which path do we take here?\n"
             "Option A: CREATE A CLASS-LEVEL UMBRELLA\n"
             "Option B: ADD A SUPPORT FILE\n")

# The autouse isolation fixture pins dmt._frame_overlay to "" so the live
# profile config cannot leak into unit tests. These tests exercise the REAL
# reader (through config_access), so they restore this module-level original.
_REAL_OVERLAY = dmt._frame_overlay
_REAL_BLOCK_READABLE = dmt._frame_block_readable


def _restore_real_reader(monkeypatch):
    monkeypatch.setattr(dmt, "_frame_overlay", _REAL_OVERLAY, raising=True)
    monkeypatch.setattr(dmt, "_frame_block_readable", _REAL_BLOCK_READABLE,
                        raising=True)


# --- item 4: config resolution through config_access ----------------------

def test_frame_resolves_through_config_access(monkeypatch):
    _restore_real_reader(monkeypatch)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"decision": {"frame": "execution"}})
    reg, ok, reason, src = dmt._frame_resolution()
    assert (reg, ok, reason, src) == ("execution", True, "", "config")
    assert dmt._frame_register() == "execution"


def test_frame_default_is_explicit_when_key_absent(monkeypatch):
    _restore_real_reader(monkeypatch)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"decision": {"enabled": True}})
    reg, ok, reason, src = dmt._frame_resolution()
    assert (reg, ok, reason, src) == ("impulse", True, "", "default")


def test_frame_unresolved_is_explicit_when_block_is_unreadable(monkeypatch):
    _restore_real_reader(monkeypatch)
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    reg, ok, reason, _src = dmt._frame_resolution()
    assert reg == "impulse"
    assert ok is False
    assert reason == "no_decision_block"


# --- item 1: frame fields on the live events ------------------------------

def _capture(monkeypatch):
    rows = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda *a, **kw: rows.append(
                            (a[0] if a else kw.get("event_detail"), dict(kw))),
                        raising=True)
    return rows


def test_verdict_and_suppression_logs_carry_the_resolved_frame(monkeypatch):
    _restore_real_reader(monkeypatch)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"decision": {"frame": "execution"}})
    rows = _capture(monkeypatch)
    dmt._log(SID, "midturn_verdict", choice="opt-1", confidence=0.51)
    dmt._log(SID, "midturn_suppressed", reason="shadow")
    assert rows[0][1].get("frame_resolved") == "execution"
    assert rows[1][1].get("frame_resolved") == "execution"


def test_unresolved_frame_is_logged_not_silently_defaulted(monkeypatch):
    _restore_real_reader(monkeypatch)
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    rows = _capture(monkeypatch)
    dmt._log(SID, "midturn_suppressed", reason="shadow")
    assert rows[0][1].get("frame_unresolved") == "no_decision_block"
    assert rows[0][1].get("frame_default") == "impulse"


# --- item 2: execution register at L1 has no menu tail ---------------------

_CONSUMED = [{"choice": "opt-1", "choice_label": "CREATE A CLASS-LEVEL UMBRELLA",
              "confidence": 0.51, "tokens_in": 10, "tokens_out": 5,
              "cost": 0.0, "endpoint": "jev_native", "row_id": 7}]


def test_execution_l1_rollup_has_no_top_verdicts_tail(monkeypatch):
    from hermes_router import debug_banner as DB
    monkeypatch.setattr(dmt, "_frame_register", lambda cfg=None: "execution")
    monkeypatch.setattr(DB, "debug_banner_level", lambda: 1)
    out = dmt._aggregate_line(1, 10, 5, 0.0, _CONSUMED)
    assert "Top verdicts:" not in out
    assert "CREATE A CLASS-LEVEL UMBRELLA" not in out
    assert out.startswith("· router ·")


def test_impulse_l1_rollup_keeps_the_tail(monkeypatch):
    from hermes_router import debug_banner as DB
    monkeypatch.setattr(dmt, "_frame_register", lambda cfg=None: "impulse")
    monkeypatch.setattr(DB, "debug_banner_level", lambda: 1)
    out = dmt._aggregate_line(1, 10, 5, 0.0, _CONSUMED)
    assert "Top verdicts:" in out
    assert "CREATE A CLASS-LEVEL UMBRELLA" in out


# --- item 3: the INJECTED string is the bounded fork note -----------------

@pytest.fixture()
def _hits(monkeypatch):
    dmt.reset_midturn()
    cfg = {"frame": "execution", "midturn": "on", "backend": "jev_native",
           "model": "m", "frame_context_chars": 0}
    monkeypatch.setattr(dmt, "_cfg", lambda: dict(cfg))
    monkeypatch.setattr(dmt, "_ledger", lambda *a, **k: None)
    monkeypatch.setattr(dmt, "_record_consumed", lambda *a, **k: None)
    monkeypatch.setattr(dmt._dec, "_breaker_open", lambda c: False)
    monkeypatch.setattr(dmt._dec, "fork_class",
                        lambda text, opts: "code_work_vs_skill_curation")
    monkeypatch.setattr(dmt._dec, "build_envelope", lambda *a, **k: dict(ENV))
    monkeypatch.setattr(dmt._dec, "call_backend",
                        lambda e, c: (json.dumps(VERDICT),
                                      {"model": "m", "tokens_in": 10,
                                       "tokens_out": 5}, None))
    monkeypatch.setattr(dmt._dec, "validate_verdict",
                        lambda c, e: (dict(VERDICT), ""))
    monkeypatch.setattr(dmt._dec, "ledger_write", lambda r: 7)
    monkeypatch.setattr(dmt._dec, "_record_success", lambda: None)
    monkeypatch.setattr(dmt._dec, "session_context_before", lambda *a, **k: "")
    monkeypatch.setattr(dmt._dec, "choice_label_with_source",
                        lambda v, e: ("CREATE A CLASS-LEVEL UMBRELLA", "envelope"))
    monkeypatch.setattr("hermes_router.usage_ledger.record_tokens",
                        lambda *a, **k: True, raising=True)
    monkeypatch.setattr("hermes_router.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    yield cfg
    dmt.reset_midturn()


def test_execution_injection_seam_carries_only_the_fork_note(_hits):
    dmt._handle_hit(SID, "terminal", FORK_TEXT,
                    ["CREATE A CLASS-LEVEL UMBRELLA", "ADD A SUPPORT FILE"],
                    _hits, "on")
    pending = dmt.flush(SID)
    assert len(pending) == 1
    injected = pending[0]
    assert injected.startswith(dmt.FORK_NOTE_HEAD)
    assert len(injected) <= dmt.FORK_NOTE_MAX_CHARS
    assert dmt.FORK_NOTE_DIRECTIVE in injected
    # no menu, no other option, no invitation to choose
    assert "ADD A SUPPORT FILE" not in injected
    assert "why_not" not in injected.lower()
    assert "Top verdicts" not in injected
    assert "opt-2" not in injected


# --- item 5: off_task_fork suppression ------------------------------------

def test_skills_menu_on_a_code_task_is_suppressed(_hits, monkeypatch):
    monkeypatch.setattr(dmt._dec, "session_last_user",
                        lambda sid, *a, **k: ("refactor three duplicate retry "
                                             "loops in retry.py, add tests, "
                                             "commit and run pytest"))
    rows = _capture(monkeypatch)
    dmt._handle_hit(SID, "terminal", FORK_TEXT,
                    ["CREATE A CLASS-LEVEL UMBRELLA", "ADD A SUPPORT FILE"],
                    _hits, "on")
    assert dmt.flush(SID) == []            # nothing offered to the model
    hits = [r for r in rows if r[0] == "midturn_suppressed"
            and r[1].get("reason") == "off_task_fork"]
    assert hits, [r for r in rows]


def test_code_options_on_a_code_task_are_not_suppressed(_hits, monkeypatch):
    monkeypatch.setattr(dmt._dec, "session_last_user",
                        lambda sid, *a, **k: "refactor three duplicate retry "
                                             "loops in retry.py, add tests")
    dmt._handle_hit(SID, "terminal", FORK_TEXT,
                    ["ship the narrow fix", "revert the branch"], _hits, "on")
    assert len(dmt.flush(SID)) == 1


def test_skill_options_are_fine_when_the_user_asked_about_skills(_hits, monkeypatch):
    monkeypatch.setattr(dmt._dec, "session_last_user",
                        lambda sid, *a, **k: "update the duplicate-code "
                                             "consolidation skill")
    dmt._handle_hit(SID, "terminal", FORK_TEXT,
                    ["CREATE A CLASS-LEVEL UMBRELLA", "ADD A SUPPORT FILE"],
                    _hits, "on")
    assert len(dmt.flush(SID)) == 1
