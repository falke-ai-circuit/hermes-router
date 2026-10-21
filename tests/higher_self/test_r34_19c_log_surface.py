"""R34-19c: the completion gate's telemetry must reach the route log.

`completion_audit._log` called `router_core._log_route`, which does not exist at
module level — every row raised AttributeError and fell through to a python
logger, so the gate's decisions (skip reasons, consult outcomes, pairing) were
invisible. Six weeks of "no post consult" was unexplainable for that reason.
"""
import inspect

from hermes_router import completion_audit as ca
from hermes_router import router_core


def test_router_core_has_no_module_level_log_route():
    # The premise of the bug: the old call target does not exist.
    assert not hasattr(router_core, "_log_route")


def test_log_prefers_the_hub_and_never_raises(monkeypatch):
    seen = []

    class FakeHub:
        def _log_route(self, *a, **kw):
            seen.append((a, kw))

    monkeypatch.setattr(ca, "_hub_fake", FakeHub(), raising=False)
    # _log resolves the hub from gate.orchestration; patch that resolver.
    from hermes_router.gate import orchestration as og

    monkeypatch.setattr(og, "_hub", lambda: FakeHub())
    ca._log("completion_audit_test_row", reason="unit")
    assert seen, "hub _log_route was not used"
    assert seen[0][1].get("event_detail") == "completion_audit_test_row"
    assert seen[0][0] == ("POST",)


def test_log_is_silent_when_every_surface_is_missing(monkeypatch):
    from hermes_router.gate import orchestration as og

    monkeypatch.setattr(og, "_hub", lambda: None)
    monkeypatch.setattr(ca.logger, "info", lambda *a, **k: None)
    ca._log("completion_audit_test_row")  # must not raise
