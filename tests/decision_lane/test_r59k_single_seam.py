"""R76 (2026-10-30): the decision lane has ONE firing point -- midturn,
pre-LLM -- and there is no seam switch left to re-enable the others.

The old contract (R59k) was a config knob: `seam: midturn_prellm` switched
PRE detection and the POST turn-close scan off while `legacy` switched them
back on. A single typo in a profile config restored three firing points, and
the knob itself was invisible in the code's behaviour (coder ran
`midturn: off` while the lane delivered 1,454 readings). The switch is gone:

  * `seam_mode` / `_seam_allows` do not exist,
  * the POST turn-close scan (`post_fork_scan`) does not exist,
  * the PRE-time automatic detection does not fire (a heuristic fork arriving
    at the v3 entry stands down),
  * an explicitly requested decision still consults -- it is a request, not a
    firing point, and gating it would silently break every `decide this:` ask,
  * no `decision_suppressed reason=seam_midturn_only` event can be emitted,
    because nothing is suppressed by a seam any more.
"""
import sys

import pytest

sys.path.insert(0, "/opt/data/plugins")
from hermes_router import decision  # noqa: E402

SID = "s-r76-seam"
TASK = "t-r76-seam"
ASK = "decide this: redis or memcached? weigh the tradeoffs"
FORK_ASK = ("which one should we pick: redis or memcached? weigh the "
            "tradeoffs and pick one because latency matters")

LOGGED = []


def _log(event, **fields):
    LOGGED.append((event, dict(fields)))


def _cfg(**over):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "backend": "jev", "rescan_dedupe": False})
    cfg.update(over)
    return cfg


def _seam_events():
    return [f for e, f in LOGGED
            if e == "decision_suppressed" and f.get("reason") == "seam_midturn_only"]


@pytest.fixture(autouse=True)
def _reset():
    LOGGED.clear()
    yield
    LOGGED.clear()


def test_no_seam_switch_exists():
    """The mode dispatch is deleted, not defaulted."""
    assert not hasattr(decision, "seam_mode")
    assert not hasattr(decision, "_seam_allows")


def test_post_turn_close_scan_is_deleted():
    """The POST scan was the third firing point; it must not be callable."""
    assert not hasattr(decision, "post_fork_scan")
    assert not hasattr(decision, "post_audit_v3")


def test_config_carries_no_seam_or_midturn_key():
    """A knob that switches nothing is a lie with a maintenance cost."""
    assert "seam" not in decision.DEFAULTS
    assert "midturn" not in decision.DEFAULTS


def test_explicit_request_still_consults(monkeypatch):
    """On-demand is a request, not a firing point: it must survive."""
    monkeypatch.setattr(decision, "_cfg", lambda: _cfg())
    calls = []
    monkeypatch.setattr(decision, "_invoke",
                        lambda *a, **k: calls.append(a))
    decision.handle_decision_v3(session_id=SID, task_id=TASK, task_text=ASK,
                                log_route=_log, trigger="manual")
    assert calls, "an explicit decide line must still reach the consult path"
    assert not _seam_events()


def test_heuristic_hit_through_a_request_is_not_a_pre_fire(monkeypatch):
    """A structural fork arriving through an explicit request still consults,
    but it is labelled as the REQUEST it came through: no row may claim a PRE
    fork, because the PRE detection path no longer exists."""
    monkeypatch.setattr(decision, "_cfg", lambda: _cfg())
    calls = []
    monkeypatch.setattr(decision, "_invoke",
                        lambda *a, **k: calls.append(a))
    decision.handle_decision_v3(session_id=SID, task_id=TASK,
                                task_text=FORK_ASK, log_route=_log,
                                trigger="on_demand")
    assert calls, "an explicitly requested consult must still fire"
    assert calls[0][3] == "manual", "and it must not be labelled 'pre'"
    assert not _seam_events()
