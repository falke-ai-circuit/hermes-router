"""R34-19 — higher self at both ends: one person at two times, five vantages,
and a POST that answers its own PRE.

Contracts pinned here:
  a) a SHORT ask carries all five stances at BOTH ends, one billed call each
  b) a paired POST fires even with audit_mode off, and never fires twice
  c) a POST with no PRE advisory logs paired=False reason=no_pre
  d) the paired payload carries the PRE brief + the three verdict tokens
  e) NO-FINDINGS still returns and still logs the pairing
  f) legacy three-stance `povs` rows still parse
  g) the no-quote / non-binding / no-accusation contracts are untouched
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from hermes_router import anchor_chain, anchor_exec, completion_audit as ca  # noqa: E402
from hermes_router import frames, router_core, state  # noqa: E402

SID = "s-r34-19"
SHORT_ASK = "fix the typo in the readme"          # no complexity signal
RESP = "Delivered the readme fix. " + ("x" * 520)
STANCES = ("practitioner", "outsider", "skeptic", "world-knowledge", "person")


@pytest.fixture(autouse=True)
def _clean_pairing():
    state.clear_pre_advisory(SID)
    ca._FIRED.clear()
    ca._INFLIGHT.clear()
    yield
    state.clear_pre_advisory(SID)
    ca._FIRED.clear()
    ca._INFLIGHT.clear()


def _pre_run(monkeypatch, user_text, content="ORIENTATION BRIEF\n- shape: done"):
    """Drive the PRE orientation branch and return the captured messages."""
    rec = {
        "task_id": "t-r34-19",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri("openrouter://test/model-a",
                                                  "primary"),
        "route_id": "r-r34-19",
        "orientation": True,
        "reason": "complexity_orientation",
    }
    seen = {"calls": 0, "messages": None}

    def _call(endpoint, api_kwargs):
        seen["calls"] += 1
        seen["messages"] = api_kwargs.get("messages")
        return (content, 0.0, 10, 5)

    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "anchored_call", _call)
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **k: None)
    api = {"messages": [{"role": "user", "content": user_text}]}
    anchor_exec.maybe_execute_anchored(SID, api)
    return seen


def _last_user_text(messages):
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


# ---------------------------------------------------------------- (a) PRE ----

def test_short_ask_pre_payload_carries_five_stances(monkeypatch):
    seen = _pre_run(monkeypatch, SHORT_ASK)
    text = _last_user_text(seen["messages"])
    assert seen["calls"] == 1, "PRE is ONE billed call"
    for stance in STANCES:
        assert stance + ":" in text, "missing PRE vantage %s" % stance
    assert "Challenge with that outside knowledge" in text
    rec = state.pre_advisory(SID)
    assert rec and rec["trigger"] == "complexity_orientation"


# --------------------------------------------------------------- (a) POST ---

def test_short_ask_post_payload_carries_five_stances():
    msgs = ca._audit_payload(SHORT_ASK, "", RESP, 2400)
    user = msgs[1]["content"]
    for stance in STANCES:
        assert stance + ":" in user, "missing POST vantage %s" % stance
    # one payload = one call boundary (no second consult is constructed)
    assert len(msgs) == 2


def test_pov_default_is_always_and_optouts_still_work(monkeypatch):
    assert ca.pov_mode() == "always"
    assert ca._pov_active(SHORT_ASK) is True
    monkeypatch.setattr(ca, "pov_mode", lambda: "auto")
    assert ca._pov_active(SHORT_ASK) is False       # auto still gates heavy asks
    monkeypatch.setattr(ca, "pov_mode", lambda: "off")
    assert ca._pov_active(SHORT_ASK) is False


# ------------------------------------------------------------- (b) pairing --

def _patch_post(monkeypatch, note="CONFIRMED. the fix holds."):
    seen = {"calls": 0, "kwargs": None, "events": []}

    def _meta(*a, **k):
        seen["calls"] += 1
        seen["kwargs"] = k
        return {"note": note, "model": "m", "endpoint": "e",
                "tokens_in": 1, "tokens_out": 1, "cost": 0.0}

    monkeypatch.setattr(ca, "_consult_meta", _meta)
    monkeypatch.setattr(ca, "_log",
                        lambda event, **kw: seen["events"].append((event, kw)))
    monkeypatch.setattr(ca, "audit_revision_seconds", lambda: 0)
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    return seen


def test_paired_post_fires_with_audit_mode_off_and_never_twice(monkeypatch):
    seen = _patch_post(monkeypatch)
    monkeypatch.setattr(ca, "audit_enabled", lambda: False)   # audit_mode: off
    monkeypatch.setattr(ca, "audit_mode", lambda: "off")
    state.mark_pre_advisory(SID, "complexity_orientation",
                            "PRE BRIEF: keep it terse", ask=SHORT_ASK)

    out = ca.audit_gate(SID, RESP, ask_override=SHORT_ASK)
    assert seen["calls"] == 1, "the paired POST must fire exactly once"
    assert out is not None, "the revised/delivered response comes back"
    assert seen["kwargs"].get("pre_brief") == "PRE BRIEF: keep it terse"
    assert seen["kwargs"].get("pre_trigger") == "complexity_orientation"
    pairs = [kw for ev, kw in seen["events"] if ev == "higher_self_pair"]
    assert any(kw.get("stage") == "post" and kw.get("paired") is True
               and kw.get("pre_trigger") == "complexity_orientation"
               for kw in pairs), pairs

    # second call on the same turn: record consumed, mode off -> silence
    out2 = ca.audit_gate(SID, RESP, ask_override=SHORT_ASK)
    assert out2 is None
    assert seen["calls"] == 1, "no reloop, no cascade"


def test_unpaired_post_logs_paired_false_no_pre(monkeypatch):
    seen = _patch_post(monkeypatch)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "always")
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    ca.audit_gate(SID, RESP, ask_override=SHORT_ASK)
    assert seen["calls"] == 1
    pairs = [kw for ev, kw in seen["events"] if ev == "higher_self_pair"]
    assert any(kw.get("paired") is False and kw.get("reason") == "no_pre"
               for kw in pairs), pairs


# ----------------------------------------------------- (d) POST answers PRE --

def test_post_payload_carries_pre_brief_and_verdict_tokens():
    msgs = ca._audit_payload(SHORT_ASK, "", RESP, 2400,
                             pre_brief="PRE BRIEF: PRE-MARKER-9137",
                             pre_trigger="complexity_orientation")
    user = msgs[1]["content"]
    assert "PRE-MARKER-9137" in user
    assert "FIRST ANSWER YOUR OWN PRE" in user
    for token in ("CONFIRMED", "PRE-WARNING-HELD", "PRE-WARNING-MISSED"):
        assert token in user
    # unpaired payload must NOT carry the pre-answer instruction
    plain = ca._audit_payload(SHORT_ASK, "", RESP, 2400)[1]["content"]
    assert "FIRST ANSWER YOUR OWN PRE" not in plain


def test_pre_brief_is_bounded_by_the_payload_cap():
    msgs = ca._audit_payload(SHORT_ASK, "", RESP, 400,
                             pre_brief="Z" * 5000)
    assert msgs[1]["content"].count("Z") <= 400


# ------------------------------------------------------- (e) NO-FINDINGS -----

def test_no_findings_path_returns_and_logs_pairing(monkeypatch):
    seen = _patch_post(monkeypatch, note="NO-FINDINGS")
    monkeypatch.setattr(ca, "audit_enabled", lambda: False)
    monkeypatch.setattr(ca, "audit_mode", lambda: "off")
    state.mark_pre_advisory(SID, "complexity_orientation", "PRE BRIEF",
                            ask=SHORT_ASK)
    out = ca.audit_gate(SID, RESP, ask_override=SHORT_ASK)
    assert out is not None
    pairs = [kw for ev, kw in seen["events"] if ev == "higher_self_pair"]
    assert any(kw.get("paired") is True for kw in pairs), pairs


# --------------------------------------------------- (f) legacy povs rows ----

def test_old_three_stance_povs_still_parse():
    verdict = ('{"povs": [{"stance": "practitioner", "note": "works"},'
               ' {"stance": "outsider", "note": "reads oddly"},'
               ' {"stance": "skeptic", "note": "unverified"}]}')
    rows = ca._parse_povs(verdict)
    assert [r["stance"] for r in rows] == ["practitioner", "outsider", "skeptic"]


def test_five_stance_povs_parse():
    verdict = ('{"povs": [' + ",".join(
        '{"stance": "%s", "note": "n"}' % s for s in STANCES) + ']}')
    assert [r["stance"] for r in ca._parse_povs(verdict)] == list(STANCES)


# ------------------------------------------------------ (g) no-quote pins ----

def test_pre_and_post_framing_is_one_person_two_times_contracts_intact():
    pre = frames.HS_PRE_BODY
    assert "the same person" in pre and "before you commit" in pre
    assert "NON-BINDING" in pre
    assert "quoting it back is a contract violation" in pre
    post = frames.HS_POST_BODY
    assert "the same person" in post and "confirmation, not demolition" in post
    assert "not an accusation to answer" in post


def test_pre_frame_reads_as_self_check_and_keeps_anti_echo(monkeypatch):
    seen = _pre_run(monkeypatch, SHORT_ASK)
    text = _last_user_text(seen["messages"])
    assert "her own higher self" in text
    assert "Do NOT solve the task" in text
    assert "never restate this frame in your reply" in text


def test_paired_post_fires_even_when_the_report_is_terse(monkeypatch):
    """Live regression, 2026-10-21: a turn that really did the work — two files
    written, 5 tests passing — reported it in 264 characters, and the 500-char
    length floor killed the POST before the pairing was ever consulted. A
    paired turn already paid for its PRE, so the confirmation is owed however
    briefly she reports it."""
    seen = _patch_post(monkeypatch)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    terse = "Files written: dedupe.py, test_dedupe.py.\npytest: 5 passed in 0.03s"
    assert len(terse) < ca._MIN_RESPONSE_CHARS, "pin must stay below the floor"
    state.mark_pre_advisory(SID, "orientation", "PRE BRIEF: watch the key arg",
                            ask=SHORT_ASK)

    out = ca.audit_gate(SID, terse, ask_override=SHORT_ASK)
    assert seen["calls"] == 1, "the paired POST must fire on a terse report"
    assert out is not None
    assert seen["kwargs"].get("pre_brief") == "PRE BRIEF: watch the key arg"
    pairs = [kw for ev, kw in seen["events"] if ev == "higher_self_pair"]
    assert any(kw.get("stage") == "post" and kw.get("paired") is True
               for kw in pairs), pairs


def test_unpaired_terse_report_still_respects_the_length_floor(monkeypatch):
    """The floor still protects the unpaired case: no PRE, no consult."""
    seen = _patch_post(monkeypatch)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "always")
    terse = "done"
    assert ca.audit_gate(SID, terse, ask_override=SHORT_ASK) is None
    assert seen["calls"] == 0


def test_paired_post_fires_when_the_hook_has_no_ask(monkeypatch):
    """Live 2026-10-21: the transform hook reached the gate with pair=True and
    audit_enabled True, then bailed SILENTLY because it could not resolve the
    ask (no user_message in context, empty last-seen). The PRE record holds the
    ask it was taken for, so the confirmation answers that instead of giving
    up — and the give-up path now logs instead of vanishing."""
    seen = _patch_post(monkeypatch)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    monkeypatch.setattr(ca, "_pre_pairing", lambda sid, ask="": state.pre_advisory(sid))
    state.mark_pre_advisory(SID, "orientation", "PRE BRIEF: mind the key arg",
                            ask=SHORT_ASK)
    monkeypatch.setattr(state, "get_last_seen", lambda sid: "")

    out = ca.audit_gate(SID, RESP, context={})   # no ask anywhere in the hook
    assert seen["calls"] == 1, "the confirmation must not die for lack of an ask"
    assert out is not None
    assert seen["kwargs"].get("pre_brief") == "PRE BRIEF: mind the key arg"


def test_no_ask_and_no_pair_logs_instead_of_vanishing(monkeypatch):
    seen = _patch_post(monkeypatch)
    monkeypatch.setattr(ca, "audit_enabled", lambda: True)
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    monkeypatch.setattr(state, "get_last_seen", lambda sid: "")
    assert ca.audit_gate(SID, RESP, context={}) is None
    assert seen["calls"] == 0
    skips = [kw for ev, kw in seen["events"] if ev == "audit_gate_skip"]
    assert any(kw.get("reason") == "no_ask" for kw in skips), skips
