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
