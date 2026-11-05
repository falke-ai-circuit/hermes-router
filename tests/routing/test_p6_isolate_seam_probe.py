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
    """M45-16: the judgement runs at the FIRST TURN END, and watches the seams the plugin
    actually fires. Judged at the first turn-identity advance (inside the first turn's request
    path), every end-of-turn seam read 0 before it had a chance to fire — dead by construction,
    on every process."""
    from hermes_router.core import telemetry as T

    T.seam_probe_register(None)
    rows = []
    monkeypatch.setattr(T, "_tlm_log", lambda ev, **f: rows.append((ev, f)))

    # Watched names == fired names: transform_tool_result was never an on_* entry.
    assert "transform_terminal_output" in T._TURN_SEAMS
    assert "transform_tool_result" not in T._TURN_SEAMS

    # A full turn fires all four seams -> nothing is dead.
    for seam in T._TURN_SEAMS:
        T.seam_probe_fire(seam)
    with caplog.at_level("WARNING", logger="router.swallow"):
        T.seam_probe_maybe_advance()
    assert not [r for r in caplog.records if "seam_dead_on_arrival" in r.message]
    assert rows == []


def test_seam_probe_still_names_a_seam_that_did_not_fire(monkeypatch, caplog):
    """The probe must keep its teeth: on a turn that DID use a tool, a tool seam that never
    fired is still named."""
    from hermes_router.core import telemetry as T

    T.seam_probe_register(None)
    rows = []
    monkeypatch.setattr(T, "_tlm_log", lambda ev, **f: rows.append((ev, f)))
    T.seam_probe_fire("llm_request")
    T.seam_probe_fire("transform_llm_output")
    T.seam_probe_fire("llm_execution")          # the tool ran; the tool seams are judged
    with caplog.at_level("WARNING", logger="router.swallow"):
        T.seam_probe_maybe_advance()
    warned = {r.getMessage() for r in caplog.records if "seam_dead_on_arrival" in r.message}
    assert warned == {"seam_dead_on_arrival seam=transform_terminal_output"}
    # one-shot: a second advance does nothing
    n = len(rows)
    T.seam_probe_maybe_advance()
    assert len(rows) == n


def test_tool_seams_are_not_judged_on_a_text_only_turn(monkeypatch, caplog):
    """M45-17 F1: a turn that reached the provider and made no tool call must not have its
    tool seams reported dead — they had no cause to fire."""
    from hermes_router.core import telemetry as T

    T.seam_probe_register(None)
    rows = []
    monkeypatch.setattr(T, "_tlm_log", lambda ev, **f: rows.append((ev, f)))
    T.seam_probe_fire("llm_request")
    T.seam_probe_fire("transform_llm_output")
    with caplog.at_level("WARNING", logger="router.swallow"):
        T.seam_probe_maybe_advance()
    assert not [r for r in caplog.records if "seam_dead_on_arrival" in r.message]
    assert rows == []


class _Ctx:
    def __init__(self):
        self.hooks, self.mw = {}, {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_middleware(self, name, fn):
        self.mw[name] = fn

    def __getattr__(self, _n):
        return lambda *a, **k: None


def _registered_ctx():
    from hermes_router.gate import registration as R

    ctx = _Ctx()
    R.register(ctx)
    return ctx


def test_every_watched_seam_is_a_seam_the_plugin_registers():
    """M45-17: watched names must be registered names. transform_tool_result is not a host
    hook on 0.21.4 and is never registered; watching it reported a phantom seam dead on
    every profile."""
    from hermes_router.core import telemetry as T

    ctx = _registered_ctx()
    registered = set(ctx.hooks) | set(ctx.mw)
    assert set(T._TURN_SEAMS) <= registered, sorted(set(T._TURN_SEAMS) - registered)
    assert "transform_tool_result" not in registered


def test_closed_line_turn_still_counts_the_turn_end_seam(monkeypatch):
    """M45-17: the host invokes the REGISTERED wrapper; a HOLD_CLOSED ask returns before the
    orchestrator, so the turn-end seam counter must be taken in the wrapper."""
    from hermes_router.core import telemetry as T
    from hermes_router.gate import registration as R

    T.seam_probe_register(None)
    monkeypatch.setattr(R, "_closed_line_hit", lambda kw: ("closed",))
    ctx = _registered_ctx()
    ctx.hooks["transform_llm_output"](response_text="body", session_id="s1")
    assert T.seam_fires().get("transform_llm_output", 0) == 1


def test_seam_probe_judges_nothing_when_no_request_was_made():
    """A process that never reached a provider cannot indict the seams downstream of it."""
    from hermes_router.core import telemetry as T

    T.seam_probe_register(None)
    assert T.seam_dead_on_arrival_check() == {s: 0 for s in T._TURN_SEAMS}


def test_dead_seam_check_is_armed_at_the_turn_end_seam_not_the_request_path():
    """Source pin: the advance lives on on_transform_llm_output, and state.record_last_seen
    (inside the first turn's request path) no longer advances the probe."""
    import inspect

    from hermes_router.core import state
    from hermes_router.gate import orchestration

    end_src = inspect.getsource(orchestration.on_transform_llm_output)
    assert "seam_probe_maybe_advance()" in end_src
    assert "seam_probe_maybe_advance" not in inspect.getsource(state.record_last_seen)


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
