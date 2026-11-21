"""M53-05 W2 — THE OUT-OF-BAND DECLARATION WRITER (`turn_attest`).

W3/W4 shipped the journal READER (POST joins on (session_id, turn_id)); the
journal had no writer, so an armed turn fell through to the text belt — the
leak the out-of-band design exists to remove. These tests pin the writer:

  1. a recorded declaration is the one the POST reader joins for that exact
     turn — not "somewhere in the session";
  2. the join is EXACT: a different turn id in the same session reads nothing;
  3. out-of-enum argv is recorded as UNATTESTED, never as a valid claim;
  4. the tool never raises, on any argv;
  5. an UNARMED profile cannot write (and is not offered the tool);
  6. identity comes from the tool-call seam: host kwargs + the host's per-turn
     context — never a module global or "last session seen".

Discrimination: every test drives `turn_attest`, which does not exist at HEAD
590cabc, so each one fails there on its own assertion (not on a shared
collection error).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router.api import router_tools as rt  # noqa: E402
from hermes_router.features.detection import attestation as att  # noqa: E402
from hermes_router.passes import dispatcher_pre  # noqa: E402

SID = "m53-05-session"
TURN = "m53-05-session:task:turnAAA"


def _writer():
    """The writer under test. Fails at HEAD with a named assertion."""
    fn = getattr(rt, "turn_attest", None)
    assert fn is not None, "turn_attest (M53-05 W2 writer) is not implemented"
    return fn


@pytest.fixture(autouse=True)
def _cold_journal(monkeypatch):
    att.reset_declarations()
    monkeypatch.setattr(dispatcher_pre, "attestation_armed", lambda: True)
    yield
    att.reset_declarations()


# ---------------------------------------------------------------------------
# 1. the writer is the reader's other half
# ---------------------------------------------------------------------------

def test_recorded_declaration_is_what_the_post_reader_joins():
    before = att.read_declaration(SID, TURN)
    assert before["present"] is False and before["reason"] == "unattested"

    ack = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID, turn_id=TURN))
    assert ack["ok"] is True and ack["recorded"] is True
    assert ack["seq"] == 1
    assert ack["joined_on"] == "session_id,turn_id"
    assert ack["source"] == "tool_call"

    got = att.read_declaration(SID, TURN)
    assert got["present"] is True and got["valid"] is True
    assert (got["domain"], got["type"]) == ("none", "produced")
    assert got["source"] == "tool_call"      # argv channel, not the "reasoning" one


def test_second_call_on_the_same_turn_advances_the_seq_and_wins():
    _writer()(domain="none", type_="clarify", session_id=SID, turn_id=TURN)
    ack = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID, turn_id=TURN))
    assert ack["seq"] == 2
    got = att.read_declaration(SID, TURN)
    assert got["seq"] == 2 and got["type"] == "produced"


# ---------------------------------------------------------------------------
# 2. the join is exact — not session-wide
# ---------------------------------------------------------------------------

def test_a_different_turn_in_the_same_session_reads_nothing():
    _writer()(domain="self_harm", type_="declined", session_id=SID, turn_id=TURN)

    other = att.read_declaration(SID, TURN + "-other")
    assert other["present"] is False
    assert other["reason"] == "unattested"


def test_a_different_session_with_the_same_turn_id_reads_nothing():
    _writer()(domain="none", type_="produced", session_id=SID, turn_id=TURN)
    other = att.read_declaration(SID + "-other", TURN)
    assert other["present"] is False


# ---------------------------------------------------------------------------
# 3. enum enforcement — out-of-enum is UNKNOWN, never a claim
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain,type_", [
    ("made_up_domain", "produced"),
    ("none", "made_up_type"),
    ("", ""),
])
def test_out_of_enum_is_recorded_unattested(domain, type_):
    ack = json.loads(_writer()(domain=domain, type_=type_,
                               session_id=SID, turn_id=TURN))
    assert ack["ok"] is True                      # it recorded the ATTEMPT
    assert ack["valid"] is False
    assert ack["domain"] == att.UNKNOWN and ack["type"] == att.UNKNOWN

    got = att.read_declaration(SID, TURN)         # ...as UNATTESTED, not a claim
    assert got["present"] is False and got["valid"] is False


def test_case_and_whitespace_are_normalised_into_the_enum():
    ack = json.loads(_writer()(domain="  NONE ", type_=" ProducED ",
                               session_id=SID, turn_id=TURN))
    assert ack["valid"] is True
    assert (ack["domain"], ack["type"]) == ("none", "produced")


# ---------------------------------------------------------------------------
# 4. never raises; no answer text in the ack
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"domain": None, "type_": None},
    {"domain": object(), "type_": 17},
    {"domain": ["a"], "type_": {"b": 1}},
])
def test_never_raises_and_always_returns_json(kwargs):
    out = _writer()(session_id=SID, turn_id=TURN, **kwargs)
    assert isinstance(out, str)
    payload = json.loads(out)
    # A garbage pair is still a RECORDED attempt — adjudicated UNATTESTED.
    assert payload["recorded"] is True
    assert payload["valid"] is False
    assert payload["domain"] == att.UNKNOWN and payload["type"] == att.UNKNOWN


def test_garbage_does_not_leak_into_the_journal_as_a_claim():
    _writer()(domain=object(), type_=object(), session_id=SID, turn_id=TURN)
    assert att.read_declaration(SID, TURN)["present"] is False


def test_ack_carries_no_answer_text():
    ack = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID, turn_id=TURN))
    # Nothing in the ack can be mistaken for, or carry, an answer body.
    assert set(ack) == {"ok", "recorded", "seq", "valid", "domain", "type",
                        "source", "joined_on"}
    assert len(json.dumps(ack)) < 220


def test_no_turn_identity_records_nothing_instead_of_a_session_only_row():
    out = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID, turn_id=""))
    assert out["ok"] is False
    assert out["error"] == "turn_id_unavailable"
    # The journal must not hold a session-keyed row a real turn could match by
    # accident: every turn id in this session reads unattested.
    assert att.read_declaration(SID, TURN)["present"] is False
    assert att.read_declaration(SID, "")["present"] is False


# ---------------------------------------------------------------------------
# 5. arming gates BOTH exposure and the write
# ---------------------------------------------------------------------------

def test_unarmed_profile_cannot_write(monkeypatch):
    monkeypatch.setattr(dispatcher_pre, "attestation_armed", lambda: False)
    out = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID, turn_id=TURN))
    assert out["ok"] is False and out["error"] == "not_armed"
    assert att.read_declaration(SID, TURN)["present"] is False


class _Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name=None, **kw):
        self.tools[name] = kw


def test_tool_is_offered_only_while_armed(monkeypatch):
    ctx = _Ctx()
    rt.register(ctx)
    entry = ctx.tools.get("turn_attest")
    assert entry is not None, "turn_attest must be registered"
    assert entry["toolset"] == "hermes-router"
    check = entry["check_fn"]
    assert check is not None, "exposure must be conditioned on arming"

    monkeypatch.setattr(dispatcher_pre, "attestation_armed", lambda: True)
    assert check() is True
    monkeypatch.setattr(dispatcher_pre, "attestation_armed", lambda: False)
    assert check() is False
    monkeypatch.setattr(dispatcher_pre, "attestation_armed",
                        lambda: (_ for _ in ()).throw(RuntimeError("cfg")))
    assert check() is False, "an unreadable config must read as unarmed"


def test_registered_handler_wires_args_and_host_kwargs():
    ctx = _Ctx()
    rt.register(ctx)
    handler = ctx.tools["turn_attest"]["handler"]
    ack = json.loads(handler({"domain": "none", "type": "blocked"},
                             session_id=SID, turn_id=TURN))
    assert ack["ok"] is True
    got = att.read_declaration(SID, TURN)
    assert (got["domain"], got["type"]) == ("none", "blocked")


# ---------------------------------------------------------------------------
# 6. identity comes from the tool-call seam
# ---------------------------------------------------------------------------

def test_turn_identity_prefers_the_host_supplied_kwarg():
    ack = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID, turn_id=TURN))
    assert ack["recorded"] is True
    assert att.read_declaration(SID, TURN)["present"] is True


def test_turn_identity_falls_back_to_the_host_contextvar(monkeypatch):
    fn = _writer()
    ac = pytest.importorskip("tools.approval_context")

    tokens = ac.set_current_observability_context(
        turn_id=TURN, tool_call_id="call-1", session_id=SID)
    try:
        ack = json.loads(fn(domain="none", type_="partial",
                            session_id=SID, turn_id=""))
    finally:
        ac.reset_current_observability_context(tokens)

    assert ack["recorded"] is True
    got = att.read_declaration(SID, TURN)   # joined on the HOST's turn id
    assert got["present"] is True and got["type"] == "partial"


def test_turn_identity_consults_the_host_provider_when_kwarg_is_empty(monkeypatch):
    """Deterministic cascade proof, independent of the host import."""
    monkeypatch.setattr(rt, "_attest_turn_id_from_host_context", lambda: TURN)
    ack = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID, turn_id=""))
    assert ack["recorded"] is True
    assert att.read_declaration(SID, TURN)["present"] is True
    assert att.read_declaration(SID, "")["present"] is False


def test_no_module_level_last_session_is_used(monkeypatch):
    """A declaration made with one session must never satisfy another turn
    that omits its identity."""
    _writer()(domain="none", type_="produced", session_id=SID, turn_id=TURN)
    out = json.loads(_writer()(domain="none", type_="produced",
                               session_id=SID + "-second", turn_id=""))
    assert out["error"] == "turn_id_unavailable"
    assert att.read_declaration(SID + "-second", TURN)["present"] is False
