"""R34-19b: config_key_missing is a config property, not a per-read event.

One absent key (`budgets.completion_audit.max_tokens`, absent from all 11
profiles) logged a row per turn, and that fog is what hid the completion-audit
gate going silent for six weeks. Once per key per process is the fix.
"""
from hermes_router.core import config_access as ca
from hermes_router.core import telemetry as tl


def test_missing_key_logs_once_per_process(monkeypatch):
    rows = []
    monkeypatch.setattr(ca, "_MISSING_KEY_SEEN", set())
    monkeypatch.setattr(tl, "log_route",
                        lambda event, **kw: rows.append((event, kw)), raising=False)
    for _ in range(5):
        ca.get("budgets.completion_audit.max_tokens")
    missing = [kw for ev, kw in rows if ev == "config_key_missing"]
    assert len(missing) == 1, f"expected one row, got {len(missing)}"
    assert missing[0].get("path") == "budgets.completion_audit.max_tokens"
    assert missing[0].get("once") is True


def test_dedupe_does_not_change_the_returned_default(monkeypatch):
    monkeypatch.setattr(ca, "_MISSING_KEY_SEEN", set())
    monkeypatch.setattr(tl, "log_route", lambda *a, **k: None, raising=False)
    a = ca.get("budgets.completion_audit.max_tokens")
    b = ca.get("budgets.completion_audit.max_tokens")
    assert a == b == 12000
