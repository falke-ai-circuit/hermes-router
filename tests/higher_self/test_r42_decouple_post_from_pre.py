"""R42 — POST decoupled from PRE, both triggers retuned, internal voice.

WHY (measured, not asserted): fleet-wide, 569 sessions with >=50 tool calls,
only 44 (7.7%) ever got a POST; architect 0/23, researcher 1/38, operative
1/34. The log reason was `pair_deferred_mid_work` and
`higher_self_confirmation_emitted` had ZERO occurrences — a deferral with no
landing is a silent drop.

Unit 1: POST fires on REALISED WORK MASS, never on pairing. Pairing survives
only as payload context.
Unit 2: PRE stays ask-side and is impact-gated (precision-first).
Unit 3: the advisory text is internal voice — no substrate vocabulary.

NN/g citation for the capacity claim in Unit 3's rationale lives in the
CHANGELOG, not in test code.
"""
import itertools
import re

from hermes_router import completion_audit as ca
from hermes_router import anchor_exec as ax
from hermes_router import complexity, frames, router_core
from hermes_router.core import state

_SIDS = itertools.count()

LONG_RESP = ("Done. " + "The change is in place and the build is green. " * 20)
ASK = "implement the retune and report the counts"


def _sid():
    return f"sess-r42-{next(_SIDS)}"


def _ctx(tools=0, files=0, mutations=0, tool_calls=None):
    """A request payload carrying realised work."""
    msgs = []
    for i in range(tools):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"function": {"name": "terminal",
                                                  "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": "ok"})
    for i in range(files):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "write_file",
                          "arguments": '{"path": "/tmp/f%d.txt"}' % i}}]})
        msgs.append({"role": "tool", "content": "written"})
    for i in range(max(0, mutations - files)):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "patch",
                          "arguments": '{"path": "/tmp/m%d.txt"}' % i}}]})
        msgs.append({"role": "tool", "content": "patched"})
    if tool_calls is not None:
        msgs = [{"role": "assistant", "content": "", "tool_calls": tool_calls}]
    return {"request": {"messages": msgs}}


def _wire(monkeypatch, enabled=True, mode="complex", consult_ran=None):
    monkeypatch.setattr(ca, "audit_topology", lambda: "sync")
    monkeypatch.setattr(ca, "audit_sync_seconds", lambda: 45)
    monkeypatch.setattr(ca, "audit_revision_seconds", lambda: 5)
    monkeypatch.setattr(ca, "stash_verdict", lambda *a, **k: None)
    monkeypatch.setattr(ca, "audit_enabled", lambda: enabled)
    monkeypatch.setattr(ca, "audit_mode", lambda: mode)
    monkeypatch.setattr(ca, "is_closure_response", lambda a, r: False,
                        raising=False)
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 99)
    events = []
    monkeypatch.setattr(ca, "_log", lambda ev, **kw: events.append((ev, kw)))

    def _sync(*a, **k):
        if consult_ran is not None:
            consult_ran.append(a[0])
        return {"note": "", "model": "m", "endpoint": "e",
                "tokens_in": 1, "tokens_out": 1, "cost": 0.0}

    monkeypatch.setattr(ca, "run_completion_audit_sync", _sync)
    return events


# --------------------------------------------------------------------------
# Unit 1 — work mass is the trigger; PRE is irrelevant to firing
# --------------------------------------------------------------------------

def test_work_mass_fires_with_no_pre_present(monkeypatch):
    """THE load-bearing case: 50 tools, zero PRE, POST reflects."""
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, consult_ran=ran)
    ctx = _ctx(tools=50)
    out = ca.audit_gate(sid, LONG_RESP, ask_override=ASK, context=ctx)
    assert ran, "work mass must fire with no PRE in existence"
    assert out is not None
    assert any(e == "post_reflection_fired" and
               str(k.get("trigger")) == "work_mass_mass" for e, k in events), events
    assert any(str(e).startswith("work_mass_gate") for e, _ in events), events


def test_a_200_tool_session_with_zero_pre_still_reflects(monkeypatch):
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, consult_ran=ran)
    ca.audit_gate(sid, LONG_RESP, ask_override=ASK, context=_ctx(tools=200))
    assert ran, "a 200-tool session must reflect"
    gate = [e for e, _ in events if str(e).startswith("work_mass_gate")]
    assert gate and "tools=200" in gate[0] and "fire=True" in gate[0], gate
    assert "reason=mass" in gate[0]


def test_a_five_tool_session_with_a_pre_does_not_reflect(monkeypatch):
    """Pairing may not buy a POST: 5 tools is below every mass threshold."""
    sid = _sid()
    ran = []
    events = _wire(monkeypatch, consult_ran=ran)
    state.mark_pre_advisory(sid, "complexity_orientation", "PRE", ask=ASK)
    out = ca.audit_gate(sid, LONG_RESP, ask_override=ASK, context=_ctx(tools=5))
    assert out is None, "a PRE bought a POST on a light turn"
    assert ran == []
    assert not any(e == "post_reflection_fired" for e, _ in events), events
    assert any(k.get("reason") == "cadence" for e, k in events
               if e == "audit_gate_skip"), events


def test_impact_by_distinct_files_fires(monkeypatch):
    ran = []
    events = _wire(monkeypatch, consult_ran=ran)
    ca.audit_gate(_sid(), LONG_RESP, ask_override=ASK, context=_ctx(files=5))
    assert ran, "5 distinct patched files must fire"
    gate = [e for e, _ in events if str(e).startswith("work_mass_gate")]
    assert gate and "reason=impact" in gate[0] and "files=5" in gate[0], gate


def test_impact_by_mutations_fires(monkeypatch):
    ran = []
    events = _wire(monkeypatch, consult_ran=ran)
    ca.audit_gate(_sid(), LONG_RESP, ask_override=ASK, context=_ctx(mutations=10))
    assert ran, "10 write_file|patch calls must fire"
    gate = [e for e, _ in events if str(e).startswith("work_mass_gate")]
    assert gate and "muts=10" in gate[0] and "reason=impact" in gate[0], gate


def test_below_threshold_logs_below_and_does_not_fire(monkeypatch):
    ran = []
    events = _wire(monkeypatch, consult_ran=ran)
    ca.audit_gate(_sid(), LONG_RESP, ask_override=ASK, context=_ctx(tools=39))
    assert ran == []
    gate = [e for e, _ in events if str(e).startswith("work_mass_gate")]
    assert gate and "fire=False" in gate[0] and "reason=below" in gate[0], gate


def test_a_session_pays_the_work_mass_consult_once(monkeypatch):
    """Staying past the threshold must not bill a consult every turn."""
    sid = _sid()
    ran = []
    _wire(monkeypatch, consult_ran=ran)
    ca.audit_gate(sid, LONG_RESP, ask_override=ASK, context=_ctx(tools=60))
    ca.audit_gate(sid, LONG_RESP, ask_override=ASK + " (again)",
                  context=_ctx(tools=61))
    assert len(ran) == 1, ran


def test_work_mass_knob_clamps(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"work_mass_min_tools": 1,
                                 "work_impact_min_files": 999,
                                 "work_impact_min_mutations": 0})
    assert ca.work_mass_min_tools() == 10
    assert ca.work_impact_min_files() == 100
    assert ca.work_impact_min_mutations() == 1
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {})
    assert ca.work_mass_min_tools() == 40
    assert ca.work_impact_min_files() == 5
    assert ca.work_impact_min_mutations() == 10


def test_pairing_is_context_only_even_when_mode_off(monkeypatch):
    """`off` means no pairing CONTEXT — it does not mean 'no POST'."""
    sid = _sid()
    ran = []
    monkeypatch.setattr(ca, "post_pair_mode", lambda: "off")
    events = _wire(monkeypatch, consult_ran=ran)
    state.mark_pre_advisory(sid, "complexity_orientation", "PRE", ask=ASK)
    ca.audit_gate(sid, LONG_RESP, ask_override=ASK, context=_ctx(tools=50))
    assert ran, "mode=off must not suppress a work-mass POST"
    assert any(e == "higher_self_pair_context_suppressed" for e, _ in events), events


# --------------------------------------------------------------------------
# measurement — would-fire survives blocked billing
# --------------------------------------------------------------------------

def test_would_fire_is_logged_when_billing_blocked(monkeypatch):
    ran = []
    events = _wire(monkeypatch, enabled=False, consult_ran=ran)
    out = ca.audit_gate(_sid(), LONG_RESP, ask_override=ASK, context=_ctx(tools=80))
    assert out is None and ran == []
    gate = [e for e, _ in events if str(e).startswith("work_mass_gate")]
    assert gate and "fire=True" in gate[0] and "tools=80" in gate[0], events
    assert any(k.get("would_fire") is True for e, k in events
               if e == "audit_gate_skip"), events


def test_outcome_linked_fields_ride_the_fire_row(monkeypatch):
    """Rework signals are judged later; the row must carry the work shape."""
    events = _wire(monkeypatch)
    ca.audit_gate(_sid(), LONG_RESP, ask_override=ASK,
                  context=_ctx(tools=50, files=6))
    fired = [k for e, k in events if e == "post_reflection_fired"]
    assert fired and fired[0].get("tools") >= 50
    assert fired[0].get("files") == 6
    assert "muts" in fired[0]
    assert fired[0].get("rework") == 0


def test_same_file_touched_twice_is_a_rework_signal():
    """The one rework signal visible at this seam: the same path mutated more
    than once. Carried on the gate row so the gate can be judged against
    rework later instead of its own (definitional) recall."""
    msgs = [{"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "patch", "arguments": '{"path": "/tmp/a.py"}'}},
        {"function": {"name": "patch", "arguments": '{"path": "/tmp/a.py"}'}},
        {"function": {"name": "write_file", "arguments": '{"path": "/tmp/b.py"}'}}]}]
    facts = ca.work_mass_facts({"request": {"messages": msgs}})
    assert facts["files"] == 2 and facts["mutations"] == 3
    assert facts["reworks"] == 1


# --------------------------------------------------------------------------
# Unit 2 — PRE is impact-gated
# --------------------------------------------------------------------------

def test_pre_gate_requires_impact_not_merely_complexity():
    ok, why = complexity.pre_impact_gate(
        "refactor the whole router so the lanes stop fighting",
        {"planning_arch": 1, "consequence": 0, "consequence_high": 0,
         "multiparts": 0})
    assert not ok and why == "no_impact"


def test_pre_gate_fires_on_consequence():
    ok, why = complexity.pre_impact_gate(
        "deploy this to production and rotate the keys",
        {"planning_arch": 1, "consequence": 2, "multiparts": 0})
    assert ok and why == "consequence"


def test_pre_gate_fires_on_a_multistep_brief():
    ok, why = complexity.pre_impact_gate(
        "three steps:\n1. fix the gate\n2. run the suite\n3. commit",
        {"planning_arch": 1, "consequence": 0, "multiparts": 1})
    assert ok and why == "multistep"


def test_pre_gate_fails_open_on_a_broken_detector(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(complexity, "stage1_signals", _boom)
    ok, why = complexity.pre_impact_gate("x", None)
    assert ok and why == "impact_unknown"


# --------------------------------------------------------------------------
# Unit 3 — internal voice, substrate-free advisory text
# --------------------------------------------------------------------------

_SUBSTRATE_RE = re.compile(
    r"(?i)\b(model|models|provider|providers|endpoint|endpoints|token|tokens|"
    r"api|llm|llms|assistant|gpt|claude|glm|qwen|kimi|deepseek|gemini|"
    r"openrouter|venice|anthropic|openai)\b")


def test_advisory_text_is_substrate_free(monkeypatch):
    """The agent's own higher self reporting on her own state — never an
    outside system reporting on a model."""
    monkeypatch.setattr(ca, "_persona_tailoring", lambda: "")
    payload = ca._audit_payload(
        ask=ASK, work="WROTE THE THING", response_text=LONG_RESP,
        max_chars=2000, pre_brief="my earlier read", pre_trigger="x",
        total_cap=4000, fold_revision=True, fold_char_cap=1200)
    rendered = "\n".join(m["content"] for m in payload)
    rendered += "\n" + ca._NOTE_PREFIX + "\n" + frames.HS_PRE_BODY
    rendered += "\n" + frames.HS_POST_BODY
    rendered += "\n" + frames.higher_self_frame_sentence("internal-observer")
    hits = sorted(set(m.group(0).lower() for m in _SUBSTRATE_RE.finditer(rendered)))
    assert not hits, "substrate vocabulary leaked into the advisory: %s" % hits


def test_the_note_header_carries_no_model_identity():
    assert not _SUBSTRATE_RE.search(ca._NOTE_PREFIX), ca._NOTE_PREFIX


def test_pre_payload_still_advises_the_approach_not_the_answer():
    """R37-2 contract verified still holding (5.17.0): PRE is plan-side, POST
    is finished-work-side. Pinned again here because R42 retuned both ends."""
    frame = ax.build_orientation_frame("ADD a /health endpoint to the router.")
    low = frame.lower()
    assert "next 1-3 concrete steps, in order" in low
    assert "do not comment on answer" in low
    post = ca._audit_payload(ASK, "WROTE THE THING", LONG_RESP, 2400)[1]["content"]
    assert "FINAL RESPONSE ABOUT TO BE DELIVERED" in post
