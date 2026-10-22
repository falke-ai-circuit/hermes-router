"""R36c: a reflex fires on RECOGNITION, not on an enumerated menu."""
import pytest

from hermes_router import instinct_triggers as itr


def test_the_same_failure_twice_is_recognised():
    st = {}
    err1 = ("Traceback (most recent call last):\n  File \"/opt/x.py\", line 12\n"
            "ValueError: bad input 8812")
    err2 = ("Traceback (most recent call last):\n  File \"/opt/x.py\", line 47\n"
            "ValueError: bad input 3331")
    assert itr.recognise(st, "terminal", err1) is None
    hit = itr.recognise(st, "terminal", err2)
    assert hit and hit["signal"] == "repeat_failure"


def test_different_failures_are_not_a_repeat():
    st = {}
    a = "fatal: not a git repository"
    b = "error: cannot find module 'foo'"
    assert itr.recognise(st, "terminal", a) is None
    assert itr.recognise(st, "terminal", b) is None


def test_a_repeat_fires_once_per_pair_not_every_time():
    st = {}
    err = "npm ERR! code E404"
    itr.recognise(st, "terminal", err)
    assert itr.recognise(st, "terminal", err) is not None
    # third identical: repeat already consumed; only the stall counter moves
    assert itr.recognise(st, "terminal", err) is None


def test_three_identical_results_is_a_stall():
    st = {}
    out = "waiting for lock on /var/lib/dpkg/lock"
    assert itr.recognise(st, "terminal", out) is None
    assert itr.recognise(st, "terminal", out) is None
    hit = itr.recognise(st, "terminal", out)
    assert hit and hit["signal"] == "stall"


def test_a_stall_fires_once_then_rearms():
    st = {}
    out = "same"
    for _ in range(3):
        itr.recognise(st, "terminal", out)
    assert st["instinct"]["repeat"] == 0  # re-armed, not stuck firing


def test_varying_clean_output_never_fires():
    st = {}
    for n in range(6):
        assert itr.recognise(st, "terminal", f"ok, {n} tests passed") is None


def test_identical_non_error_output_is_a_stall_at_three():
    """No new information is the definition of a stall. Documented
    false-positive class: polling."""
    st = {}
    out = "waiting on dpkg lock"
    assert itr.recognise(st, "terminal", out) is None
    assert itr.recognise(st, "terminal", out) is None
    assert itr.recognise(st, "terminal", out)["signal"] == "stall"


def test_standing_instructions_riding_the_turn_are_not_the_world_failing():
    st = {}
    note = ("<recalled memory context>\nBackground process proc_1 completed "
            "with exit code 0\nError in the summary text above")
    assert itr.recognise(st, "terminal", note) is None
    assert itr.recognise(st, "terminal", note) is None


def test_the_note_offers_no_menu_and_no_confidence():
    for sig in ("repeat_failure", "stall"):
        note = itr.render_note({"signal": sig, "tool": "terminal"})
        assert note.startswith(itr.NOTE_HEAD)
        assert len(note) <= itr.NOTE_MAX_CHARS
        low = note.lower()
        for banned in ("option", "opt-", "confidence", "choose", "which do you"):
            assert banned not in low, (sig, banned)
        assert "change" in low or "stop" in low


def test_an_unknown_signal_renders_nothing():
    assert itr.render_note({"signal": "nonsense"}) == ""


def test_mode_defaults_off_so_it_earns_a_canary_first():
    assert itr.mode_of({}) == "off"
    assert itr.mode_of({"decision": {"recognition": "on"}}) == "on"
    assert itr.mode_of({"decision": {"recognition": "shadow"}}) == "shadow"
    assert itr.mode_of({"decision": {"recognition": "banana"}}) == "off"


def test_recognition_never_raises_on_garbage():
    st = {}
    for junk in (None, 0, "", [], {}, "x" * 10000):
        itr.recognise(st, None, junk)
    assert True


def test_yaml_boolean_on_is_read_as_on():
    """YAML 1.1 parses a bare `on` as True. Coercing with str() yields 'True',
    which matched no mode and silently disabled the feature in the commit that
    added it — observed live on the coder canary."""
    assert itr.mode_of({"decision": {"recognition": True}}) == "on"
    assert itr.mode_of({"decision": {"recognition": False}}) == "off"
    assert itr.mode_of({"decision": {"recognition": "true"}}) == "on"
    assert itr.mode_of({"decision": {"recognition": "false"}}) == "off"
