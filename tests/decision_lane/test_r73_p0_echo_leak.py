"""R73 P0: the injected reading must never become the delivered body.

Root cause pinned here: the reading is appended into the last tool result (the evidence
channel), at the generation boundary. Delivered raw its last line was a directive ("act:
take the top unless ..."), so a model could answer the reading instead of working; the body
then came back as an echo and the execution-echo guard withheld the turn. These tests pin
the two invariants the fix depends on:

  1. every reading entering the evidence channel is explicitly fenced as data, and the
     final line of that channel is the closing fence - never a directive;
  2. the lane's own contract lines are statements about the reading, not instructions to
     the reader.
"""
import pytest

from hermes_router import decision_extract as dx
from hermes_router import reflex_lane as rl

READING = (
    "[DECISION READING | DECISION-LANE | PROVENANCE-LOGGED]\n"
    "  58%  run the check or tests now and read the result\n"
    "  20%  search the tree for the other sites this affects\n"
)


def test_fence_bounds_the_reading_and_ends_the_evidence():
    out = rl.fence(READING)
    assert out.startswith(rl._FENCE_HEAD)
    assert out.endswith(rl._FENCE_TAIL)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[-1] == rl._FENCE_TAIL, "the evidence channel must close on the fence"


def test_fence_is_idempotent():
    once = rl.fence(READING)
    assert rl.fence(once) == once


def test_injection_closes_the_tool_channel_on_the_fence():
    msgs = [{"role": "user", "content": "do the task"},
            {"role": "tool", "content": "tool output"}]
    assert rl._inject_into_evidence(msgs, READING) is True
    content = msgs[-1]["content"]
    assert content.startswith("tool output")
    last = [ln for ln in content.splitlines() if ln.strip()][-1]
    assert last == rl._FENCE_TAIL
    # no directive is left in the position the model completes from
    assert not last.lower().lstrip().startswith(("act:", "take ", "continue"))


@pytest.mark.parametrize("contract", [dx.USE_CONTRACT, dx.SOFT_CONTRACT])
def test_contracts_are_statements_not_orders(contract):
    head = contract.strip().lower()
    assert not head.startswith("act:"), "an imperative tail is the completion cue"
    assert head.startswith("reading, not an instruction"), \
        "the contract must state what the block IS, not what to do about it"


def test_tool_channel_is_untouched_without_a_block():
    msgs = [{"role": "tool", "content": "tool output"}]
    assert rl._inject_into_evidence(msgs, "") is False
    assert msgs[-1]["content"] == "tool output"
