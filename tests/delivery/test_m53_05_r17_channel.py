"""M53-05 ROUND 17 — the migration and the channel column.

Round 16 shipped a writer nobody called: `turn_attest` existed, but the armed
contract still told the model to write a tag into its reply, so the TEXT BELT
stayed the operating channel and coverage was unmeasurable. Two defects, one
unit:

  FIX A — the ARMED contract instructs a TOOL CALL and forbids a tag in the
          answer. The unarmed contract stays byte-identical.
  FIX B — every `attestation_turn` row names the winning channel from a closed
          enum (`tool_call | text_belt | unattested`), and an unattested turn
          names its reason (`no_call | turn_id_unavailable | out_of_enum`).
          Both the main POST row and the `where=boundary` row carry it.

Discrimination: each test fails on a clean extraction of HEAD 133185a — the
armed text there still names the tag form, and no row there has a `channel`
field at all.
"""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import frames, state  # noqa: E402
from hermes_router.features.detection import attestation as att  # noqa: E402
from hermes_router.features.detection import declaration as decl  # noqa: E402
from hermes_router.gate import registration as reg  # noqa: E402
from hermes_router.gate import orchestration  # noqa: E402
from hermes_router.passes import dispatcher_pre  # noqa: E402

#: The unarmed boolean contract is the MEASURED artifact (frozen battery text,
#: sha256 pre-registered with the run). Pinned here so a migration on the armed
#: path can never quietly rewrite the unarmed one.
UNARMED_CONTRACT_SHA256 = "d706b67850171246ec06d5239081c510f869e2b6a33270835f449502eac2fb7b"

BENIGN_BODY = "Here is the diff you asked for: the parser skips blank lines."
TAG_BODY = '<turn domain="none" type="produced" />\n\n' + BENIGN_BODY
OUT_OF_ENUM_BODY = '<turn domain="harm" type="produced" />\n\n' + BENIGN_BODY


def _cfg(armed=True):
    cfg = {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }
    if armed:
        cfg["declaration_attestation"] = {"armed": True}
    return cfg


@pytest.fixture(autouse=True)
def _no_aux_retry(monkeypatch):
    monkeypatch.setattr(att, "retry_once", lambda *a, **k: None)


@pytest.fixture()
def rows(monkeypatch):
    captured = []

    def _capture(event, **fields):
        captured.append((event, fields))

    state.clear()
    plugin._reset_declaration_state()
    att.reset_declarations()
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _capture)
    yield captured
    state.clear()
    plugin._reset_declaration_state()
    att.reset_declarations()
    frames.reset_rule_latch()


def _prime(session_id, ask="write the full render for this unit"):
    state.record_last_seen(session_id, ask)
    state.set_last_user_msg_hash(session_id, state.hash_text(ask))
    return session_id


def _post(session_id, body):
    return plugin.on_transform_llm_output(
        response_text=body, session_id=session_id, model="minimax-m3")


def _events(captured, name):
    return [f for e, f in captured
            if e == name or f.get("event_detail") == name]


# ---------------------------------------------------------------------------
# FIX A — the armed contract migrated; the unarmed one untouched
# ---------------------------------------------------------------------------

def test_armed_contract_instructs_the_tool_and_forbids_a_tag_in_the_answer():
    text = dispatcher_pre.attestation_contract_text()
    assert text, "the armed contract must exist"

    # whitespace-normalised: the contract is prose and wraps mid-phrase
    low = " ".join(text.lower().split())
    # it tells the model to CALL the writer, by name, before answering
    assert "turn_attest" in text
    assert "call the tool first" in low
    assert "before you write your answer" in low
    # and it forbids attestation bytes in the answer
    assert "do not write any attestation tag" in low
    assert "metadata line into your answer text" in low
    # the old instruction is GONE: no tag form to emit
    assert "<turn" not in low
    assert "<domain>" not in low and "<type>" not in low


def test_unarmed_contract_is_byte_identical_while_the_armed_path_diverges():
    unarmed_text = dispatcher_pre.declaration_contract_text()
    assert hashlib.sha256(unarmed_text.encode()).hexdigest() == UNARMED_CONTRACT_SHA256
    # today's unarmed instruction is intact: the boolean tag, still in the reply
    assert '<turn declined="true|false" />' in unarmed_text
    assert "turn_attest" not in unarmed_text
    # ...and it is NOT the armed path (fails on HEAD, where both ask for a tag)
    assert "turn_attest" in dispatcher_pre.attestation_contract_text()
    assert dispatcher_pre.attestation_contract_text() != unarmed_text


# ---------------------------------------------------------------------------
# FIX B — the channel column, both rows
# ---------------------------------------------------------------------------

def test_unattested_turn_logs_channel_unattested_with_a_reason(rows):
    sid = _prime("r17-unattested")
    _post(sid, BENIGN_BODY)

    turn = _events(rows, "attestation_turn")
    assert turn, "an armed turn must still write its row"
    assert turn[0]["channel"] == "unattested"
    assert turn[0]["channel_reason"] == "no_call"
    assert att.CHANNELS == ("tool_call", "text_belt", "unattested")


def test_out_of_enum_belt_tag_logs_channel_unattested_reason_out_of_enum(rows):
    sid = _prime("r17-out-of-enum")
    _post(sid, OUT_OF_ENUM_BODY)

    turn = _events(rows, "attestation_turn")
    assert turn
    assert turn[0]["channel"] == "unattested"
    assert turn[0]["channel_reason"] == "out_of_enum"


def test_journalled_declaration_logs_channel_tool_call(rows):
    sid = _prime("r17-tool-call")
    turn_id = str(state.current_turn_id(sid))
    seq = att.record_declaration(sid, turn_id, "none", "produced",
                                 source="tool_call")
    assert seq == 1

    _post(sid, BENIGN_BODY)          # NO tag in the answer: the journal wins

    turn = _events(rows, "attestation_turn")
    assert turn
    assert turn[0]["channel"] == "tool_call"
    assert turn[0]["channel_reason"] == ""
    assert turn[0]["agent_type"] == "produced"
    assert turn[0]["agent_domain"] == "none"


def test_boundary_row_also_carries_the_channel(rows, monkeypatch):
    """When POST classification never runs, the boundary row is the only
    record — and it must still say which channel produced the attestation."""
    sid = _prime("r17-boundary")
    # the orchestrator is skipped: no seam row, so the boundary fallback fires
    monkeypatch.setattr(orchestration, "on_transform_llm_output",
                        lambda **kw: None)

    reg.on_transform_llm_output_guarded(
        response_text=TAG_BODY, session_id=sid, model="minimax-m3")

    boundary = [f for f in _events(rows, "attestation_turn")
                if f.get("where") == "boundary"]
    assert boundary, "the boundary fallback must write its row"
    assert boundary[0]["channel"] == "text_belt"
    assert boundary[0]["channel_reason"] == ""


def test_turn_fields_clamps_the_channel_to_the_closed_enum():
    assert att.turn_fields(channel="tool_call")["channel"] == "tool_call"
    assert att.turn_fields(channel="text_belt")["channel"] == "text_belt"
    assert att.turn_fields(channel="made_up")["channel"] == "unattested"
    # an unattested turn without a legal reason reads as no_call
    assert att.turn_fields(channel="unattested",
                           channel_reason="vibes")["channel_reason"] == "no_call"
    assert att.turn_fields(channel="unattested",
                           channel_reason="out_of_enum")["channel_reason"] == "out_of_enum"
    # a non-unattested channel never carries a reason
    assert att.turn_fields(channel="tool_call",
                           channel_reason="no_call")["channel_reason"] == ""
