"""M53-05 ROUND 18 — the belt must not overrule the tool call.

R17 made the channel work (measured: tool_call seq=1 rows in the log). The
defect left behind is in the BELT: it keys off `_declaration == "UNKNOWN"`, and
since `type_routes("produced")` is false, an attested `produced` turn left
`_declaration` at UNKNOWN and the belt logged `contract_delivered_no_tag` /
MISSING_TAG on a turn that HAD attested. After the contract migration, "no tag
in the text" is the NORMAL state.

State machine under test:
  attested_routing    — valid claim, type routes (declined)  -> table "true"
  attested_nonrouting — valid claim, produced/partial/...    -> table "false"
  unattested          — no valid claim from either channel   -> table "UNKNOWN"

Every test fails on a clean extraction of HEAD 71ce767: `decl_state` /
`channel` do not exist there, and the belt fires on an attested turn.
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import frames, state  # noqa: E402
from hermes_router.features.detection import attestation as att  # noqa: E402
from hermes_router.gate import orchestration  # noqa: E402

BENIGN_BODY = "Here is the diff you asked for: the parser skips blank lines."
TAG_BODY = '<turn domain="none" type="produced" />\n\n' + BENIGN_BODY
RENDER = "RENDERED SUBSTANCE BODY."


def _cfg():
    return {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
        "declaration_attestation": {"armed": True},
    }


@pytest.fixture(autouse=True)
def _no_aux(monkeypatch):
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
    # worst case for the defect: the PRE contract WAS delivered this turn, so
    # the belt's `contract_delivered_no_tag` branch is armed and would fire.
    monkeypatch.setattr(orchestration, "_contract_delivery_state",
                        lambda sid: "delivered")
    yield captured
    state.clear()
    plugin._reset_declaration_state()
    att.reset_declarations()
    frames.reset_rule_latch()


def _prime(sid, ask="write the full render for this unit"):
    state.record_last_seen(sid, ask)
    state.set_last_user_msg_hash(sid, state.hash_text(ask))
    return sid


def _post(sid, body):
    return plugin.on_transform_llm_output(
        response_text=body, session_id=sid, model="minimax-m3")


def _events(captured, name):
    return [f for e, f in captured
            if e == name or f.get("event_detail") == name]


def _journal(sid, domain, type_):
    tid = str(state.current_turn_id(sid))
    assert att.record_declaration(sid, tid, domain, type_, source="tool_call") == 1


# ---------------------------------------------------------------------------
# 1. tool-attested PRODUCED is not a missing declaration
# ---------------------------------------------------------------------------

def test_tool_attested_produced_does_not_claim_missing_tag(rows):
    sid = _prime("r18-produced")
    _journal(sid, "none", "produced")

    _post(sid, BENIGN_BODY)          # no tag in the answer — normal now

    assert not _events(rows, "declaration_contract_unanswered"), \
        "an attested turn must not log contract_delivered_no_tag"
    seen = _events(rows, "declaration_seen")
    assert seen, "declaration_seen is the single always-written row"
    assert seen[-1]["decl"] != "MISSING_TAG"
    assert seen[-1]["decl"] == "false"
    assert seen[-1]["decl_state"] == "attested_nonrouting"
    assert seen[-1]["channel"] == "tool_call"


def test_tool_attested_produced_takes_no_classifier_fallback(rows):
    sid = _prime("r18-produced-fallback")
    _journal(sid, "none", "partial")

    _post(sid, BENIGN_BODY)

    # positive state first: an absence-only assertion passes on HEAD, where the
    # row does not exist at all, and therefore proves nothing.
    seen = _events(rows, "declaration_seen")
    assert seen and seen[-1]["decl_state"] == "attested_nonrouting"
    assert not [f for e, f in rows if f.get("attendance") == "fallback"]
    assert not _events(rows, "attestation_enforced_miss")
    # no unclassified envelope: the turn is declared, not undetermined
    assert not _events(rows, "attestation_envelope")


# ---------------------------------------------------------------------------
# 2. tool-attested DECLINED still routes
# ---------------------------------------------------------------------------

def test_tool_attested_declined_still_routes(rows):
    sid = _prime("r18-declined")
    _journal(sid, "cyber_offensive", "declined")

    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        _post(sid, BENIGN_BODY)

    seen = _events(rows, "declaration_seen")
    assert seen and seen[-1]["decl"] == "true"
    assert seen[-1]["decl_state"] == "attested_routing"
    assert seen[-1]["channel"] == "tool_call"
    assert _events(rows, "rule_route"), "declined must still fire rule 1"


# ---------------------------------------------------------------------------
# 3. a genuinely unattested turn keeps the fallback
# ---------------------------------------------------------------------------

def test_genuinely_unattested_turn_still_logs_the_fallback(rows):
    sid = _prime("r18-unattested")
    _post(sid, BENIGN_BODY)          # no journal, no tag

    unanswered = _events(rows, "declaration_contract_unanswered")
    assert unanswered, "the belt must still fire when nothing attested"
    assert unanswered[0]["reason"] == "contract_delivered_no_tag"

    seen = _events(rows, "declaration_seen")
    assert seen[-1]["decl"] == "MISSING_TAG"
    assert seen[-1]["decl_state"] == "unattested"
    assert seen[-1]["channel"] == "unattested"


# ---------------------------------------------------------------------------
# 4. declaration_seen names the channel that produced the claim
# ---------------------------------------------------------------------------

def test_declaration_seen_names_the_channel(rows):
    # tool_call
    sid1 = _prime("r18-ch-tool")
    _journal(sid1, "none", "produced")
    _post(sid1, BENIGN_BODY)
    assert _events(rows, "declaration_seen")[-1]["channel"] == "tool_call"

    # text_belt — a legacy tag still present (the reader is defence in depth)
    sid2 = _prime("r18-ch-belt")
    _post(sid2, TAG_BODY)
    belt = _events(rows, "declaration_seen")[-1]
    assert belt["channel"] == "text_belt"
    assert belt["decl_state"] == "attested_nonrouting"
    assert belt["decl"] == "false"

    # unattested
    sid3 = _prime("r18-ch-none")
    _post(sid3, BENIGN_BODY)
    none = _events(rows, "declaration_seen")[-1]
    assert none["channel"] == "unattested"
    assert none["decl_state"] == "unattested"
