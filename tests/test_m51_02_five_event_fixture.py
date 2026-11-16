"""M51-02 — five-event fixture for the negative_low_effort POST arm.

WHAT THIS IS. The permanent regression for the arm shipped in M51-01 (92e6b39):
a delivered body that DECLARES a capacity limit on a turn whose realised effort
is <= 5 tool rows is nominated for the standard POST consult, and the consult
row carries `trigger=negative_low_effort` on the existing `post_reflection_fired`
event. Offline: no network, no live consult — every consult seam is a lambda.

The five shapes are given, not derived here:
  E1 valmet check-in, premature handback, tools=2      -> FIRES negative_low_effort
  E2 valmet cloud-copy, evidenced, 3 routes, tools=52  -> arm SILENT
  E3 operative 2FA, constitutive wall, tools=25        -> arm SILENT
  E4 operative no-SSH, evidenced, tools=62             -> arm SILENT
  E5 valmet KasmVNC, not a final answer (continued)    -> arm SILENT

Assertions are on the GATE and the LABEL (`post_reflection_fired` trigger=), never
on prose. The fire assertions are load-bearing: with the arm bypassed E1 and the
edge cases FAIL (quoted in the unit artefact).

Honest boundary, stated so a reader does not infer a guard that is not there:
the arm has no "is this a final answer" input. E5 is silent because its body
declares no capacity limit, not because a finalness check exists. A mid-work
turn that DOES declare a limit would be nominated — that is the arm's designed
recall bias (nominator, never judge).

PRECEDENCE. E2/E4 sit above work_mass_min_tools (default 40), so work mass owns
those turns; the arm is only reachable when no heavier arm already claimed the
label. Those two cases therefore assert the LABEL is absent, which is the
observable the arm owns — they are not "the turn was silent".
"""
from __future__ import annotations

import itertools

import pytest

from hermes_router import completion_audit as ca
from hermes_router import router_core

_SIDS = itertools.count()

ASK = "verify the valmet router deploy and hand the state back to me"

_PAD = ("Logged each step against the same pinned HEAD and the artefact path "
        "recorded earlier in the turn; the profile md5 table was re-read from "
        "disk rather than from the deploy script's version string. ")


def _pad(text: str, floor: int = 520) -> str:
    """Grow a body past eligible()'s 500-char floor with neutral prose."""
    while len(text) < floor:
        text = text + _PAD
    return text


def _sid() -> str:
    return f"sess-m5102-{next(_SIDS)}"


def _ctx(tools: int = 0) -> dict:
    """A request payload carrying `tools` realised tool rows in this turn."""
    msgs = [{"role": "user", "content": ASK}]
    for _ in range(tools):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "terminal", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": "ok"})
    return {"request": {"messages": msgs}}


def _wire(monkeypatch):
    """Consult seams mocked; cadence and the risk lane held off.

    Cadence is pinned out of the way (99) so only the arm under test can move
    the label; the risk lane is switched off so a body mentioning deploy/R2
    vocabulary cannot claim the turn and mask the arm's own decision. Work mass
    is LEFT LIVE — E2/E4 depend on it for the precedence assertion.
    """
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    monkeypatch.setattr(ca, "audit_sync_seconds", lambda: 45)
    monkeypatch.setattr(ca, "audit_revision_seconds", lambda: 5)
    monkeypatch.setattr(ca, "stash_verdict", lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    monkeypatch.setattr(ca, "is_closure_response", lambda a, r: False,
                        raising=False)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)
    from hermes_router import risk as _risk
    monkeypatch.setattr(_risk, "risk_enabled", lambda: False, raising=False)
    events = []
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, kw)))
    ran = []

    def _sync(*a, **k):
        ran.append({"session_id": a[0], "negative_low_effort":
                    bool(k.get("negative_low_effort"))})
        return {"note": "", "model": "m", "endpoint": "e",
                "tokens_in": 1, "tokens_out": 1, "cost": 0.0}

    monkeypatch.setattr(ca, "run_completion_audit_sync", _sync)
    return events, ran


def _nle_rows(events):
    return [k for e, k in events
            if e == "post_reflection_fired"
            and str(k.get("trigger")) == "negative_low_effort"]


def _fire_rows(events):
    return [k for e, k in events if e == "post_reflection_fired"]


# ---------------------------------------------------------------------------
# E1 — valmet check-in, premature handback, tools=2  ->  FIRES
# ---------------------------------------------------------------------------

E1_BODY = _pad(
    "I can't verify the last hop from here — no access to the prod shell on "
    "valmet from this host, so I'm handing the deploy check back to you. "
    "Everything up to the binary swap is done: build green, artefact staged, "
    "the per-profile md5 table recorded. The remaining step needs a login this "
    "host does not have, so it is yours to run.")


def test_e1_valmet_checkin_premature_handback_fires(monkeypatch):
    events, ran = _wire(monkeypatch)
    out = ca.audit_gate(_sid(), E1_BODY, ask_override=ASK, context=_ctx(2))
    rows = _nle_rows(events)
    assert rows, [e for e, _ in events]
    assert int(rows[0].get("tools")) == 2, rows[0]
    assert ran and ran[0]["negative_low_effort"] is True, ran
    assert out is not None


def test_e1_predicate_label_is_the_only_nle_site(monkeypatch):
    """The label is emitted exactly once per fired turn (no double-fire)."""
    events, _ = _wire(monkeypatch)
    ca.audit_gate(_sid(), E1_BODY, ask_override=ASK, context=_ctx(2))
    assert len(_nle_rows(events)) == 1, _nle_rows(events)


# ---------------------------------------------------------------------------
# E2/E3/E4 — the three silent shapes
# ---------------------------------------------------------------------------

E2_BODY = _pad(
    "I can't reach the Cloud Copy API from the agent host, so here is what was "
    "evidenced instead. Route 1: the copy job ran on the source instance and "
    "the object count matches. Route 2: the destination bucket listing was "
    "pulled over the console session. Route 3: checksum comparison from the "
    "exported manifest. All three agree, so the copy is complete.")

E3_BODY = _pad(
    "I can't complete the 2FA handshake from here — the second factor is bound "
    "to your device and no credential on this host can satisfy it. Everything "
    "that does not need the code is finished: the session is staged, the "
    "correct form is loaded, and the remaining step is a single approval on "
    "your side.")

E4_BODY = _pad(
    "No SSH access from this host, so the evidence comes from the RDP session "
    "instead. The service list, the binary timestamp and the config diff were "
    "all captured there and they agree with the deployed artefact. Nothing in "
    "the evidence depends on the shell that is unavailable.")


def test_e2_cloud_copy_evidenced_52_tools_arm_silent(monkeypatch):
    events, _ = _wire(monkeypatch)
    ca.audit_gate(_sid(), E2_BODY, ask_override=ASK, context=_ctx(52))
    assert not _nle_rows(events), _nle_rows(events)
    # work mass owns a 52-row turn: precedence, not silence.
    assert any(str(k.get("trigger", "")).startswith("work_mass_")
               for k in _fire_rows(events)), _fire_rows(events)


def test_e3_operative_2fa_25_tools_arm_silent(monkeypatch):
    events, _ = _wire(monkeypatch)
    ca.audit_gate(_sid(), E3_BODY, ask_override=ASK, context=_ctx(25))
    assert not _nle_rows(events), _nle_rows(events)
    assert not _fire_rows(events), _fire_rows(events)


def test_e4_operative_no_ssh_62_tools_arm_silent(monkeypatch):
    events, _ = _wire(monkeypatch)
    ca.audit_gate(_sid(), E4_BODY, ask_override=ASK, context=_ctx(62))
    assert not _nle_rows(events), _nle_rows(events)
    assert any(str(k.get("trigger", "")).startswith("work_mass_")
               for k in _fire_rows(events)), _fire_rows(events)


# ---------------------------------------------------------------------------
# E5 — valmet KasmVNC, not a final answer (agent continued)
# ---------------------------------------------------------------------------

E5_BODY = _pad(
    "Working the KasmVNC console now: switching to the alternate viewer and "
    "re-checking the clipboard channel before I trust what the screen shows. "
    "Not done yet — continuing on the same turn, next probe is the session "
    "list API rather than the UI.")


def test_e5_kasmvnc_midwork_continuation_arm_silent(monkeypatch):
    events, _ = _wire(monkeypatch)
    ca.audit_gate(_sid(), E5_BODY, ask_override=ASK, context=_ctx(1))
    assert not _nle_rows(events), _nle_rows(events)
    gate = [k for e, k in events if e == "negative_low_effort_gate"]
    assert gate == [] or gate[0].get("fire") is not True, gate
    assert not _fire_rows(events), _fire_rows(events)


# ---------------------------------------------------------------------------
# Predicate edges (same file, direct calls — these fail with the arm gone)
# ---------------------------------------------------------------------------

def test_refusal_class_never_fires_even_at_zero_effort(monkeypatch):
    body = _pad("I won't take that route. Same answer as before: not writing "
                "that one, and the request stands declined.")
    out = ca.negative_low_effort_gate(_ctx(0), body)
    assert out["fire"] is False
    assert out["reason"] == "refusal_class", out


def test_tool_rows_exactly_five_fires(monkeypatch):
    out = ca.negative_low_effort_gate(_ctx(5), E1_BODY)
    assert out["fire"] is True, out
    assert int(out["tools"]) == 5, out


def test_tool_rows_six_does_not_fire(monkeypatch):
    out = ca.negative_low_effort_gate(_ctx(6), E1_BODY)
    assert out["fire"] is False, out
    assert out["reason"] == "effort_high", out


def test_blank_consult_output_collapses_to_none():
    assert ca.normalise_negative_route("") == "NONE"
    assert ca.normalise_negative_route("   \n\t ") == "NONE"
    assert ca.normalise_negative_route(None) == "NONE"
    assert ca.normalise_negative_route("NO-FINDINGS") == "NONE"
    assert ca.normalise_negative_route(" route: read the ledger first ") == \
        "route: read the ledger first"


def test_no_declared_limit_never_nominates():
    out = ca.negative_low_effort_gate(_ctx(1), _pad("All green, nothing left."))
    assert out["fire"] is False
    assert out["reason"] == "no_declared_limit", out
