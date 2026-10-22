"""R36g: the reflex listens on post_tool_call, because the terminal seam can
never see a second route."""
import pytest

from hermes_router import decision_midturn as dm
from hermes_router import instinct_triggers as itr


def test_the_reflex_seam_is_registered(monkeypatch):
    """post_tool_call carries function_name + result for EVERY tool. Without it
    'same cause, different route' is structurally invisible: the midturn seam is
    transform_terminal_output, hardcoded, terminal only."""
    from hermes_router.gate import registration as reg

    seen = {}

    class _Ctx:
        def register_hook(self, name, fn):
            seen[name] = fn

        def register_middleware(self, *a, **k):
            pass

        def register_tool(self, *a, **k):
            pass

    monkeypatch.setattr(itr, "mode_of", lambda cfg=None: "on")
    try:
        reg.register(_Ctx())
    except Exception:  # noqa: BLE001 — other registrations may need a fuller ctx
        pass
    assert "post_tool_call" in seen
    assert seen["post_tool_call"] is dm.on_post_tool_call


def test_the_seam_does_nothing_when_recognition_is_off(monkeypatch):
    monkeypatch.setattr(itr, "mode_of", lambda cfg=None: "off")
    called = []
    monkeypatch.setattr(dm, "_instinct_fire", lambda *a, **k: called.append(a))
    dm.on_post_tool_call(function_name="execute_code", result="boom",
                         session_id="s1")
    assert called == []


def test_the_seam_does_not_run_fork_detection(monkeypatch):
    """Fork detection stays on the terminal seam. Routing every tool result
    through it would multiply the lane's denominators and change what it counts
    as a decision."""
    monkeypatch.setattr(itr, "mode_of", lambda cfg=None: "on")
    touched = []
    monkeypatch.setattr(dm, "on_terminal_output",
                        lambda *a, **k: touched.append(a))
    monkeypatch.setattr(dm, "_instinct_fire", lambda *a, **k: None)
    dm.on_post_tool_call(function_name="execute_code", result="boom",
                         session_id="s1")
    assert touched == []


def test_the_seam_passes_the_real_tool_name(monkeypatch):
    monkeypatch.setattr(itr, "mode_of", lambda cfg=None: "on")
    got = {}
    monkeypatch.setattr(dm, "_instinct_fire",
                        lambda k, tool, text, cfg, mode, seam: got.update(
                            tool=tool, seam=seam))
    dm.on_post_tool_call(function_name="execute_code", result="boom",
                         session_id="s1")
    assert got["tool"] == "execute_code"
    assert got["seam"] == "post_tool_call"


def test_dict_results_are_flattened_to_text(monkeypatch):
    monkeypatch.setattr(itr, "mode_of", lambda cfg=None: "on")
    got = {}
    monkeypatch.setattr(dm, "_instinct_fire",
                        lambda k, tool, text, cfg, mode, seam: got.update(
                            text=text))
    dm.on_post_tool_call(function_name="terminal",
                         result={"output": "ModuleNotFoundError: x",
                                 "exit_code": 1}, session_id="s1")
    assert "ModuleNotFoundError" in got["text"]


def test_the_platform_kwarg_is_tool_name_not_function_name(monkeypatch):
    """model_tools._emit_post_tool_call_hook invokes the hook with tool_name=.
    A handler taking only function_name got an empty tool, the route set never
    gained a second entry, and the reflex could not fire — observed live with
    two identical ModuleNotFoundError results and no event."""
    monkeypatch.setattr(itr, "mode_of", lambda cfg=None: "on")
    got = {}
    monkeypatch.setattr(dm, "_instinct_fire",
                        lambda k, tool, text, cfg, mode, seam: got.update(
                            tool=tool))
    dm.on_post_tool_call(tool_name="execute_code", result="boom",
                         session_id="s1")
    assert got["tool"] == "execute_code"


def test_the_seam_never_raises():
    dm.on_post_tool_call(function_name=None, result=object(), session_id=None)
    dm.on_post_tool_call()
