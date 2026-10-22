"""R36c/R36f: a reflex fires on RECOGNITION, not on an enumerated menu.

R36f narrowed this hard. The first version's two signals (repeat failure, stall)
turned out to duplicate the harness guardrails, which are tuned and escalate to
block. What survives is the one thing per-tool, per-turn counters cannot see:
the same cause arriving through a different route.
"""
import pytest

from hermes_router import instinct_triggers as itr

TRACE_A = ("Traceback (most recent call last):\n  File \"/opt/x.py\", line 12\n"
           "ModuleNotFoundError: No module named 'numpy'")
TRACE_B = ("Traceback (most recent call last):\n  File \"/opt/y.py\", line 47\n"
           "ModuleNotFoundError: No module named 'numpy'")


# --- the surviving signal ------------------------------------------------

def test_the_same_cause_through_a_second_tool_is_recognised():
    st = {}
    assert itr.recognise(st, "terminal", TRACE_A) is None
    hit = itr.recognise(st, "execute_code", TRACE_B)
    assert hit and hit["signal"] == "same_cause_different_route"
    assert hit["routes"] == ["terminal", "execute_code"]


def test_the_same_cause_through_the_same_tool_is_NOT_ours():
    """That is `repeated_exact_failure_warning` / `same_tool_failure_warning`.
    The harness owns it and escalates to block."""
    st = {}
    assert itr.recognise(st, "terminal", TRACE_A) is None
    assert itr.recognise(st, "terminal", TRACE_B) is None


def test_different_causes_are_never_a_recognised_cause():
    """Two unrelated failures through two tools is not "the same problem
    again" — it is just two failures."""
    st = {}
    assert itr.recognise(st, "terminal",
                         "fatal: not a git repository") is None
    assert itr.recognise(st, "execute_code",
                         "ImportError: cannot import name 'x'") is None


def test_a_cause_fires_once_not_on_every_further_route():
    st = {}
    itr.recognise(st, "terminal", TRACE_A)
    assert itr.recognise(st, "execute_code", TRACE_B) is not None
    assert itr.recognise(st, "web_search", TRACE_B) is None
    assert itr.recognise(st, "analyst", TRACE_B) is None


def test_the_store_stays_bounded_over_a_long_session():
    st = {}
    for n in range(120):
        itr.recognise(st, "terminal", f"error: distinct failure {n}")
    assert len(st["instinct"]["seen"]) <= 40
    assert len(st["instinct"].get("fired") or []) <= 40


# --- deference to the harness -------------------------------------------

@pytest.mark.parametrize("code", [
    "repeated_exact_failure_warning",
    "repeated_exact_failure_block",
    "same_tool_failure_warning",
    "same_tool_failure_halt",
    "idempotent_no_progress_warning",
    "idempotent_no_progress_block",
    "[Tool loop warning: repeated_exact_failure_warning; count=2",
])
def test_the_reflex_never_talks_over_a_harness_guardrail(code):
    """Observed live: the harness emitted `repeated_exact_failure_warning;
    count=2` in the same result the reflex fired on, one line above it. A
    second quieter voice saying the same thing is noise."""
    st = {}
    itr.recognise(st, "terminal", TRACE_A)
    assert itr.recognise(st, "execute_code", TRACE_B + "\n\n" + code) is None


def test_a_clean_result_never_fires():
    st = {}
    for n in range(6):
        assert itr.recognise(st, "terminal", f"ok, {n} tests passed") is None
    assert itr.recognise(st, "execute_code", "ok, 6 tests passed") is None


def test_standing_instructions_riding_the_turn_are_not_the_world_failing():
    st = {}
    note = ("<recalled memory context>\nBackground process proc_1 completed "
            "with exit code 0\nError in the summary text above")
    assert itr.recognise(st, "terminal", note) is None
    assert itr.recognise(st, "execute_code", note) is None


# --- register -----------------------------------------------------------

def test_the_note_offers_no_menu_and_no_confidence():
    note = itr.render_note({"signal": "same_cause_different_route",
                            "routes": ["terminal", "execute_code"]})
    assert note.startswith(itr.NOTE_HEAD)
    assert len(note) <= itr.NOTE_MAX_CHARS
    low = note.lower()
    for banned in ("option", "opt-", "confidence", "choose", "which do you"):
        assert banned not in low, banned
    assert "same cause" in low
    assert "terminal and execute_code" in note


def test_the_note_names_the_routes_it_came_through():
    note = itr.render_note({"signal": "same_cause_different_route",
                            "routes": ["terminal", "coder", "reviewer"]})
    assert "terminal and coder and reviewer" in note


def test_an_unknown_signal_renders_nothing():
    assert itr.render_note({"signal": "nonsense"}) == ""
    assert itr.render_note({}) == ""


# --- config -------------------------------------------------------------

def test_mode_defaults_off_so_it_earns_a_canary_first():
    assert itr.mode_of({}) == "off"
    assert itr.mode_of({"decision": {"recognition": "on"}}) == "on"
    assert itr.mode_of({"decision": {"recognition": "shadow"}}) == "shadow"
    assert itr.mode_of({"decision": {"recognition": "banana"}}) == "off"


def test_yaml_boolean_on_is_read_as_on():
    """YAML 1.1 parses a bare `on` as True. Coercing with str() yields 'True',
    which matched no mode and silently disabled the feature in the commit that
    added it — observed live on the coder canary."""
    assert itr.mode_of({"decision": {"recognition": True}}) == "on"
    assert itr.mode_of({"decision": {"recognition": False}}) == "off"
    assert itr.mode_of({"decision": {"recognition": "true"}}) == "on"
    assert itr.mode_of({"decision": {"recognition": "false"}}) == "off"


def test_recognition_is_read_through_the_canonical_reader(monkeypatch):
    """The `decision.frame` trap repeating: decision._cfg() only carries keys it
    already knows, so a new key is invisible in production while a hand-built
    cfg passes in tests. Live symptom was config=on, midturn mode=on, and the
    reflex never firing."""
    from hermes_router.core import config_access as ca

    monkeypatch.setattr(ca, "sub_block_alias",
                        lambda a, b=None: {"recognition": True})
    assert itr.mode_of({}) == "on"


def test_a_canonical_read_failure_falls_back_to_the_passed_cfg(monkeypatch):
    from hermes_router.core import config_access as ca

    def _boom(a, b=None):
        raise RuntimeError("no config")
    monkeypatch.setattr(ca, "sub_block_alias", _boom)
    assert itr.mode_of({"decision": {"recognition": "shadow"}}) == "shadow"


def test_recognition_never_raises_on_garbage():
    st = {}
    for junk in (None, 0, "", [], {}, "x" * 10000):
        itr.recognise(st, None, junk)
    assert True


def test_two_tools_wrapping_one_cause_share_a_signature():
    """terminal returns {"output":..,"exit_code":1}; execute_code returns its
    own shape. Hashing the whole body gives two signatures for one cause, so the
    cross-route signal could never fire. The failure line is what has to match."""
    terminal_shape = ('{"output": "Traceback (most recent call last):\\n  '
                      'File \\"<string>\\", line 1, in <module>\\n'
                      'ModuleNotFoundError: No module named \'zzz\'", '
                      '"exit_code": 1, "error": null}')
    exec_shape = ('Error executing code block\n'
                  'ModuleNotFoundError: No module named \'zzz\'\n'
                  'at line 4 of notebook cell')
    st = {}
    assert itr.recognise(st, "terminal", terminal_shape) is None
    hit = itr.recognise(st, "execute_code", exec_shape)
    assert hit and hit["signal"] == "same_cause_different_route", itr._cause_text(exec_shape)


def test_the_envelope_does_not_define_the_cause():
    """A differing exit code or wrapper must not create a new cause."""
    a = '{"output": "ValueError: bad input", "exit_code": 1}'
    b = 'command failed after 3 attempts\nValueError: bad input\nsee log /tmp/a.log'
    assert itr._cause_text(a) == itr._cause_text(b), (
        itr._cause_text(a), itr._cause_text(b))
