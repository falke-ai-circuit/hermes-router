"""R59k: the decision lane gets ONE firing point -- midturn -- and it lands
before the call.

Ordered by Goran: "all decision must be only midturn not pre or post".

What must stay true:
  * automatic PRE-time detection stands down,
  * the POST turn-close scan stands down,
  * an explicitly asked decision (on-demand) still consults -- it is a request,
    not a firing point, and gating it would silently break `decide this:`,
  * the knob reverts to legacy without a code change, and
  * midturn itself is untouched by this gate.
"""
import json
import sys

import pytest

sys.path.insert(0, "/opt/data/plugins")
from hermes_router import decision  # noqa: E402

SID = "s-r59k"
TASK = "t-r59k"
ASK = "which one should we pick: redis or memcached? weigh the tradeoffs"

LOGGED = []


def _log(event, **fields):
    LOGGED.append((event, dict(fields)))


def _cfg(**over):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "backend": "jev", "rescan_dedupe": False,
                "seam": "midturn_prellm"})
    cfg.update(over)
    return cfg


def _suppressed():
    return [f for e, f in LOGGED
            if e == "decision_suppressed" and f.get("reason") == "seam_midturn_only"]


@pytest.fixture(autouse=True)
def _reset():
    LOGGED.clear()
    decision.reset_state() if hasattr(decision, "reset_state") else None
    yield
    LOGGED.clear()


def test_code_default_is_legacy_so_the_contract_tests_hold():
    """The seam change is a config decision, not a code default."""
    cfg = dict(decision.DEFAULTS)
    assert decision.seam_mode(cfg) == "legacy"
    # and the explicit opt-in is what the fleet config will carry
    assert decision.seam_mode(_cfg()) == "midturn_prellm"


def test_unrecognised_seam_value_falls_back_to_legacy_not_silently():
    assert decision.seam_mode(_cfg(seam="legacy")) == "legacy"
    assert decision.seam_mode(_cfg(seam="whatever")) == "legacy"


def test_pre_trigger_stands_down(monkeypatch):
    monkeypatch.setattr(decision, "_cfg", lambda: _cfg())

    def _no_backend(*a, **k):
        raise AssertionError("PRE-time detection must not consult under midturn_prellm")

    monkeypatch.setattr(decision, "_http_post_json", _no_backend, raising=False)
    decision.handle_decision_v3(session_id=SID, task_id=TASK, task_text=ASK,
                               log_route=_log, trigger="pre")
    assert _suppressed(), "the stand-down must be logged, not silent"
    assert _suppressed()[0].get("trigger") == "pre"


def test_post_scan_stands_down(monkeypatch):
    monkeypatch.setattr(decision, "_cfg", lambda: _cfg())
    decision.post_fork_scan(SID, ASK + "\nOption 1: redis, because latency.\n"
                                      "Option 2: memcached, because simplicity.",
                            log_route=_log)
    assert _suppressed() and _suppressed()[0].get("trigger") == "post"


def test_on_demand_is_not_a_firing_point_and_survives(monkeypatch):
    """Gating this would silently break every explicit 'decide this:' ask."""
    monkeypatch.setattr(decision, "_cfg", lambda: _cfg())
    decision.handle_decision_v3(session_id=SID, task_id=TASK, task_text=ASK,
                               log_route=_log, trigger="on_demand")
    assert not _suppressed(), "on-demand must not be gated by the seam mode"


def test_legacy_mode_restores_pre(monkeypatch):
    monkeypatch.setattr(decision, "_cfg", lambda: _cfg(seam="legacy"))
    decision.handle_decision_v3(session_id=SID, task_id=TASK, task_text=ASK,
                               log_route=_log, trigger="pre")
    assert not _suppressed()


def test_midturn_is_not_gated_here(monkeypatch):
    monkeypatch.setattr(decision, "_cfg", lambda: _cfg())
    assert decision._seam_allows("midturn") is True
    assert decision._seam_allows("pre") is False
    assert decision._seam_allows("post") is False
