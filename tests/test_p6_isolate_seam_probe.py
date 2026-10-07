"""P6 — seam liveness probe + isolate()/record_swallow telemetry.

Pins:
- seam_probe: on_* entries fire counters; first turn-identity advance runs
  the dead-on-arrival check (zero-fire seams emit `seam_dead_on_arrival`);
  no extra middleware is registered (host chain untouched).
- record_swallow / isolate: router.swallow warning + per-gate counter,
  surfaced via router_status (swallow_counts + seam_fires).
"""
import pytest

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(autouse=True)
def _probe_state():
    from hermes_router.core import telemetry as T

    T._SEAM_FIRES.clear()
    T._SEAM_REGISTERED = False
    yield
    T._SEAM_FIRES.clear()
    T._SEAM_REGISTERED = False


def test_seam_probe_fires_and_dead_on_arrival(monkeypatch, caplog):
    from hermes_router.core import telemetry as T
    from hermes_router.core import state

    T.seam_probe_register(None)
    T.seam_probe_fire("llm_request")
    T.seam_probe_fire("llm_execution")
    T.seam_probe_fire("transform_llm_output")
    rows = []
    monkeypatch.setattr(T, "_tlm_log",
                        lambda ev, **f: rows.append((ev, f)))
    with caplog.at_level("WARNING", logger="router.swallow"):
        state.record_last_seen("s-seam", "hello")
    assert T.seam_probe_maybe_advance is not None
    # transform_tool_result is dead-from-birth (no on_* entry) — the probe
    # must name it, not bury it.
    assert "transform_tool_result" in T.seam_fires()
    assert any("seam_dead_on_arrival" in r.message
               for r in caplog.records), caplog.records
    # one-shot: a second advance does nothing
    n = len(rows)
    state.record_last_seen("s-seam", "again")
    assert len(rows) == n


def test_record_swallow_counter_and_warning(caplog):
    from hermes_router.core import telemetry as T

    before = T.swallow_count().get("g.test", 0)
    with caplog.at_level("WARNING", logger="router.swallow"):
        T.record_swallow("g.test", ValueError("boom"), extra=1)
    assert T.swallow_count()["g.test"] == before + 1
    assert any("router.swallow" in r.message and "g.test" in r.getMessage()
               for r in caplog.records), caplog.records


def test_isolate_boundaries_preserved():
    from hermes_router.core import telemetry as T

    assert T.isolate("g.i", lambda: 5) == 5
    assert T.isolate("g.i", lambda: 1 / 0) is None          # on_fail=pass
    with pytest.raises(ZeroDivisionError):
        T.isolate("g.i", lambda: 1 / 0, on_fail="raise")
    assert T.isolate("g.i", lambda: 1 / 0, on_fail="default:7") == 7


def test_router_status_exposes_swallow_and_seam_counts():
    import json as _json

    from hermes_router.api import router_tools

    payload = _json.loads(router_tools.router_status())
    assert "swallow_counts" in payload
    assert "seam_fires" in payload
