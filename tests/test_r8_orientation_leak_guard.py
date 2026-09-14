"""Tests: R8b orientation-leak guard (2026-09-13). Resume-turn detection
fires the <=200-char internal-only reminder; non-resume turns unchanged;
idempotent once per resume turn; fail-open."""
import sys

sys.path.insert(0, "/opt/data/plugins")

from hermes_router import dispatcher_pre as dp  # noqa: E402


def _req(content="Resume the flagship consult"):
    return {"messages": [{"role": "user", "content": content}]}


def test_resume_turn_fires_reminder():
    req = _req()
    assert dp.inject_orientation_leak_guard(req, "Resume the flagship consult")
    systems = [m for m in req["messages"] if m.get("role") == "system"]
    assert len(systems) == 1
    assert "internal vantage material" in systems[0]["content"]
    assert "never deliverable text" in systems[0]["content"]


def test_resume_variants_detected():
    for text in ("Resume", "Continue", "Go on", "carry on with it",
                 "Pick up where we left", "keep going", "  resume the work"):
        assert dp._is_resume_turn(text), text


def test_non_resume_turns_unchanged():
    req = _req("What is the capital of France?")
    before = list(req["messages"])
    assert not dp.inject_orientation_leak_guard(req, "What is the capital of France?")
    assert req["messages"] == before
    for text in ("please resume? no wait", "I continued the analysis myself",
                 "Again continue this pattern mid-sentence"):
        assert not dp._is_resume_turn(text)


def test_idempotent_once_per_resume_turn():
    req = _req()
    assert dp.inject_orientation_leak_guard(req, "Resume")
    n = len(req["messages"])
    assert not dp.inject_orientation_leak_guard(req, "Resume")
    assert len(req["messages"]) == n


def test_reminder_within_200_chars():
    assert len(dp.ORIENTATION_LEAK_REMINDER) <= 200


def test_fail_open_on_weird_input():
    assert not dp.inject_orientation_leak_guard(None, "Resume")
    assert not dp.inject_orientation_leak_guard({}, "Resume")
    assert not dp.inject_orientation_leak_guard({"messages": "nope"}, "Resume")
    assert not dp.inject_orientation_leak_guard(_req(), None)
    assert not dp._is_resume_turn(None)
