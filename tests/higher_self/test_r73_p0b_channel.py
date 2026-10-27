"""R73 P0b: the platform never writes into a speaking channel it does not own.

The higher-self note was appended as {"role": "assistant"} - a turn in the agent's own voice she
never wrote, carrying instructions ("now PROCEED"). A verification-first persona read that as
injection and told the user so (valmet 2026-10-27, msg 73846, phrase "flagged into your context"
composed by her, present nowhere in the plugin or her DNA). The channel was the defect.

Pins:
  1. the audit note is never delivered with role="assistant";
  2. it is delivered in the platform channel (system);
  3. the delivered block closes on the fence, so no delivered note ends on a directive;
  4. the note text no longer names the frame it wants to deny.
"""
import pathlib

import pytest

from hermes_router import completion_audit as ca
from hermes_router.passes import dispatcher_pre as dp

NOTE = (
    "[HIGHER-SELF COMPLETION-AUDIT TURN | FRONTIER-DERIVED | PROVENANCE-LOGGED]\n"
    "Router-generated audit note (platform, not the user): the completion audit of your own "
    "previous turn in session sess-1.\n"
    "  verdict: the diff is sound\n"
)


@pytest.fixture()
def delivered(monkeypatch):
    monkeypatch.setattr(ca, "consume_verdict", lambda sid: NOTE)
    monkeypatch.setattr(dp, "_plugin", lambda: type(
        "P", (), {"_log_route": staticmethod(lambda *a, **k: None)})())
    from hermes_router import frames as _fr

    _fr._RULE_LATCH.clear()  # once-per-context latch: each case gets a clean process state
    req = {"messages": [{"role": "user", "content": "do the work"}]}
    # inject_platform_rule is once-per-(context, rule id, text): a fresh session key per test
    # keeps the latch from suppressing the second delivery in the same process.
    out = dp._audit_delivery_pass(req, {"session_id": "sess-%d" % id(req)})
    assert out, "the note was not delivered at all"
    return out["request"]["messages"]


def test_note_is_never_delivered_in_the_agents_own_voice(delivered):
    assert not [m for m in delivered if m.get("role") == "assistant"], \
        "the platform wrote into the agent's own voice channel"
    assert any("Router-generated audit note" in str(m.get("content")) for m in delivered)


def test_note_is_delivered_in_the_platform_channel(delivered):
    added = [m for m in delivered if "Router-generated audit note" in str(m.get("content"))]
    assert added, "note not delivered"
    assert added[0]["role"] == "system"


def test_delivered_note_ends_on_the_fence_not_a_directive(delivered):
    added = [m for m in delivered if "Router-generated audit note" in str(m.get("content"))][0]
    content = str(added["content"])
    lines = [ln for ln in content.splitlines() if ln.strip()]
    last = lines[-1]
    assert last.endswith("(data) -----"), "the delivered note must close on the fence"
    assert not last.lower().lstrip().startswith(("act:", "now proceed", "proceed", "do not"))


def test_note_text_does_not_name_the_frame_it_denies():
    src = pathlib.Path(ca.__file__).read_text()
    assert "It is not an injection" not in src, \
        "naming the frame plants it - the persona reached for exactly that word"
