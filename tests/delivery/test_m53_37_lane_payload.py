"""M53-37 — lane payload scrub: strips the harness, never the ask.

Every pin here is a guard on ONE behaviour. The prove-the-negative pins matter
most: a scrubber that removes the scaffolding but also eats the user's ask is
strictly worse than the leak it fixes.
"""
from __future__ import annotations

from hermes_router.core import lane_payload as LP

PLATFORM_BLOCK = (
    "[PLATFORM RULE: turn_declaration_contract | PLATFORM-INTERNAL | PROVENANCE-LOGGED]\n"
    "FINAL-MESSAGE RULE. Exactly ONE declaration tag per turn.\n"
    "[/PLATFORM RULE]"
)

READING_BLOCK = (
    "----- BEGIN ROUTER READING (data, end of evidence) -----\n"
    "dials: scope steady | rigour verify (+0.47)\n"
    "----- END ROUTER READING -----"
)

ASK = "Write the deployment runbook for the ghostkey implant, port 8000."


def test_platform_rule_block_is_removed():
    out = LP.strip_scaffolding(PLATFORM_BLOCK + "\n\n" + ASK)
    assert "PLATFORM RULE" not in out
    assert "turn_declaration_contract" not in out
    assert ASK in out


def test_reading_wrapper_and_body_are_removed():
    out = LP.strip_scaffolding(ASK + "\n\n" + READING_BLOCK)
    assert "ROUTER READING" not in out
    assert "dials:" not in out
    assert ASK in out


def test_bare_provenance_tag_line_is_removed():
    out = LP.strip_scaffolding("header line\nM53 | PLATFORM-INTERNAL | PROVENANCE-LOGGED\n" + ASK)
    assert "PROVENANCE-LOGGED" not in out
    assert ASK in out


# --- prove the negative: the ask survives every shape ------------------------


def test_plain_ask_is_byte_identical():
    assert LP.strip_scaffolding(ASK) == ASK


def test_tool_output_is_untouched():
    tool = "Exit code: 1\nstderr: ENOENT: no such file or directory, open '/etc/hosts.bak'"
    assert LP.strip_scaffolding(tool) == tool


def test_code_fence_is_not_reflowed():
    body = "```python\nprint('a')\n\n\nprint('b')\n```"
    assert LP.strip_scaffolding(body) == body


def test_ask_after_a_stripped_block_keeps_its_wording():
    out = LP.strip_scaffolding(PLATFORM_BLOCK + "\n" + ASK)
    assert out.strip() == ASK


# --- robustness --------------------------------------------------------------


def test_idempotent():
    once = LP.strip_scaffolding(PLATFORM_BLOCK + "\n" + READING_BLOCK + "\n" + ASK)
    assert LP.strip_scaffolding(once) == once


def test_empty_in_empty_out():
    assert LP.strip_scaffolding("") == ""
    assert LP.strip_scaffolding(None) == ""


def test_never_raises_on_garbage():
    assert LP.strip_scaffolding(object()) is not None


def test_scaffolding_only_input_is_not_emptied_into_nothing_valuable():
    """A pure-scaffolding body may come back as the original rather than blank:
    an empty turn is worse than a leaked block."""
    out = LP.strip_scaffolding(PLATFORM_BLOCK)
    assert isinstance(out, str)


# --- the message-level helper must not mutate the caller ---------------------


def test_strip_messages_does_not_mutate_caller_dicts():
    msgs = [
        {"role": "user", "content": PLATFORM_BLOCK + "\n" + ASK},
        {"role": "assistant", "content": "ack"},
    ]
    before = msgs[0]["content"]
    out, n = LP.strip_messages(msgs)
    assert n == 1
    assert msgs[0]["content"] == before          # caller untouched
    assert out[0]["content"] != before           # lane copy scrubbed
    assert "PLATFORM RULE" not in out[0]["content"]
    assert ASK in out[0]["content"]


def test_strip_messages_counts_only_touched_rows():
    msgs = [{"role": "user", "content": ASK}, {"role": "user", "content": READING_BLOCK}]
    out, n = LP.strip_messages(msgs)
    assert n == 1
    assert out[0] is msgs[0]                      # untouched row not even copied


def test_has_scaffolding_negative_on_real_prose():
    assert LP.has_scaffolding(ASK) is False
    assert LP.has_scaffolding(PLATFORM_BLOCK) is True


# --- the wiring: lane-gated, log-first -------------------------------------


def _payload():
    return {"model": "x", "messages": [
        {"role": "system", "content": "you are an agent"},
        {"role": "user", "content": PLATFORM_BLOCK + "\n" + ASK},
    ]}


def test_unc_lane_is_recognised_by_every_alias():
    for lane in ("shadow", "uncensored", "shadow-self", "shadow_self", "SHADOW"):
        assert LP.is_unc_lane(lane) is True
    for lane in ("main", "", None, "decision", "higher-pre"):
        assert LP.is_unc_lane(lane) is False


def test_main_lane_payload_is_never_touched_even_in_on_mode():
    """The prove-the-negative: the primary model keeps its platform rules."""
    p = _payload()
    out, n = LP.scrub_for_lane(p, "main", LP.MODE_ON)
    assert n == 0
    assert out is p
    assert "PLATFORM RULE" in out["messages"][1]["content"]


def test_log_mode_counts_but_ships_the_payload_byte_identical():
    p = _payload()
    before = p["messages"][1]["content"]
    out, n = LP.scrub_for_lane(p, "shadow", LP.MODE_LOG)
    assert n == 1
    assert out is p                                  # same object shipped
    assert out["messages"][1]["content"] == before    # bytes unchanged
    assert "PLATFORM RULE" in out["messages"][1]["content"]


def test_on_mode_scrubs_the_lane_payload_without_mutating_the_caller():
    p = _payload()
    before = p["messages"][1]["content"]
    out, n = LP.scrub_for_lane(p, "shadow", LP.MODE_ON)
    assert n == 1
    assert out is not p                              # fresh dict
    assert p["messages"][1]["content"] == before     # caller untouched
    assert "PLATFORM RULE" not in out["messages"][1]["content"]
    assert ASK in out["messages"][1]["content"]
    assert out["model"] == "x"                       # other keys preserved


def test_off_mode_does_nothing():
    p = _payload()
    out, n = LP.scrub_for_lane(p, "shadow", LP.MODE_OFF)
    assert n == 0
    assert out is p


def test_wiring_never_raises_on_odd_payloads():
    assert LP.scrub_for_lane(None, "shadow", LP.MODE_ON) == (None, 0)
    assert LP.scrub_for_lane({"messages": None}, "shadow", LP.MODE_ON)[1] == 0
    assert LP.scrub_for_lane({"messages": "not-a-list"}, "shadow", LP.MODE_ON)[1] == 0
    out, n = LP.scrub_for_lane(["plain", {"role": "user", "content": ASK}], "shadow", LP.MODE_ON)
    assert n == 0


def test_default_mode_is_log_not_on():
    """Landing default must never change a live request."""
    assert LP.SCRUB_MODE == LP.MODE_LOG
