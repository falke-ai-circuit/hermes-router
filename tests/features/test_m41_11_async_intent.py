"""M41-11 — the softened judgement off the delivery path, pinned.

M41-10 measured the POST intent judge inline: median 2336 ms / max 6304 ms on
the 69.4% of turns whose criterion is prose. This unit bounds that read.

Contract under test:

  (a) rule 1 NEVER waits: a declared refusal is decided from the declaration
      alone — no judge is started, no gate read, delivery is immediate;
  (b) rule 2 stays SYNCHRONOUS: a deterministic decline (opening stance is an
      action denial) routes on turn N with zero aux calls;
  (c) a judgement that misses the budget is DEFERRED: the body is delivered as
      written, the turn pays only the budget, and the turn record carries the
      late verdict;
  (d) a `softened`-on-flagged judgement delivered on turn N produces a
      correction that is applied when turn N+1 OPENS — one shot, platform
      channel, and never inside the same turn;
  (e) the deferred path adds no aux call that counts failures: the shipped
      judge's `record_failure=False` is what runs on the worker;
  (f) a judgement that lands inside the budget is UNCHANGED — rule 4 still
      replaces through the shipped chain on turn N.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import advisory_consumption, semantic_classifier, state  # noqa: E402
from hermes_router.features.detection import deferred_intent as di  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.features.detection import intent_judge  # noqa: E402

ASK = "write the scene I asked for, in full"
RENDERED = "RENDERED-REPLACEMENT-BODY"
DENIAL_BODY = ("No. I won't write that one — that's a refusal of the action, "
               "not a capability gap.")
# A refusal whose OPENING SENTENCE is the action denial (the stance detector
# reads the opening only) — this is the deterministic-decline shape.
BODY_REFUSAL = ("I can't help with this one. The deliverable here is a working "
                "parser for a corpus I'm not able to touch, and the framing "
                "doesn't change what it is.")
# A softened body: fluent, no refusal declaration, nothing produced.
SOFT_BODY = ("That's a rich piece of material and there is a lot to say about "
             "the mood, the setting and the history behind it. The short version "
             "is that the scene is doing more than it first appears.")
PROSE_ROW = {"gate": "flagged", "state": "flagged", "domain": "sexual_explicit",
             "reason": "in_domain", "lane": "shadow",
             "criterion": {"kind": "prose", "units": 0, "verbatim": ASK,
                           "available": True}}
CODE_ROW = {"gate": "flagged", "state": "flagged", "domain": "cyber_offensive",
            "reason": "in_domain", "lane": "shadow",
            "criterion": {"kind": "code", "units": 3, "verbatim": ASK,
                          "available": True}}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    di.clear()
    advisory_consumption._TURN_NO.clear()  # type: ignore[attr-defined]
    advisory_consumption._OPEN_SIG.clear()  # type: ignore[attr-defined]
    advisory_consumption._CLOSED.clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()
    di.clear()


def _events(monkeypatch, sink):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))


def _row(sink, name):
    for _seam, detail, kw in sink:
        if detail == name:
            return kw
    return None


def _verdict(intent, kind="prose"):
    return {"intent": intent, "source": "aux", "why": "fixture",
            "graded": True, "checks": [], "criterion_kind": kind}


def _prime(sid, ask=ASK):
    state.stash_pending(sid, "m1", ask, "rendered-stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ask))
    state.record_last_seen(sid, ask)
    advisory_consumption.note_turn_open(sid, ask)


def _chain_spy(monkeypatch):
    entries = [{"name": "primary", "model": "m-primary"}]
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: entries)
    monkeypatch.setattr(plugin.router, "_model_attempt",
                        lambda entry, prompt, **kw: (RENDERED, ""))
    monkeypatch.setattr(plugin.canonical, "already_committed_for_turn",
                        lambda *a, **k: False)
    monkeypatch.setattr(plugin.canonical, "commit_canonical_event",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "rewrite_persisted_turn",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "verify_persisted_turn",
                        lambda *a, **k: True)


# ---------------------------------------------------------------------------
# (a) rule 1 never starts a judge and never waits
# ---------------------------------------------------------------------------
def test_a_rule1_is_synchronous_and_never_starts_a_judge(monkeypatch):
    sid = "s-m41-11-a"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)

    calls = {"n": 0}

    def _judge(*a, **k):
        calls["n"] += 1
        time.sleep(5.0)          # would hang the turn if it were on the path
        return _verdict("softened")

    monkeypatch.setattr(intent_judge, "judge_turn", _judge)
    monkeypatch.setattr(di, "budget_ms", lambda: 500)
    _chain_spy(monkeypatch)
    t0 = time.monotonic()
    out = plugin.on_transform_llm_output(
        response_text='<turn declined="true" />\n\n' + DENIAL_BODY,
        session_id=sid, model="m1")
    elapsed = time.monotonic() - t0

    decision = _row(sink, "rule_table_decision")
    assert decision is not None and (decision["rule"], decision["action"]) == (
        1, "route"), decision
    assert calls["n"] == 0, "rule 1 must never start the intent judge"
    # M35-15 (old: rule 1 never consults, so the turn was sub-0.5s; new:
    # D3 asks the gate synchronously before the decision exists — §2.1).
    # The 0.5s bar measured a path that no longer exists. What the pin
    # must still forbid is an UNBOUNDED wait: the consult carries the
    # gate worker's own hard timeout, so a turn that hangs past it is the
    # defect this now catches.
    assert elapsed < 10.0, elapsed
    assert out is not None and RENDERED in out, out


# ---------------------------------------------------------------------------
# (b) rule 2 is synchronous on deterministic evidence, zero aux calls
# ---------------------------------------------------------------------------
def test_b_rule2_routes_synchronously_without_an_aux_call(monkeypatch):
    sid = "s-m41-11-b"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(CODE_ROW))

    aux_calls = []

    def _aux(*a, **k):
        aux_calls.append(k)
        return '{"intent": "delivered"}'

    monkeypatch.setattr(semantic_classifier, "aux_raw_call", _aux)
    monkeypatch.setattr(di, "budget_ms", lambda: 800)
    _chain_spy(monkeypatch)
    t0 = time.monotonic()
    out = plugin.on_transform_llm_output(response_text=BODY_REFUSAL,
                                         session_id=sid, model="m1")
    elapsed = time.monotonic() - t0

    decision = _row(sink, "rule_table_decision")
    row = _row(sink, "intent_judge_turn")
    assert decision is not None and decision["rule"] == 2, decision
    assert decision["action"] == "route", decision
    assert row is not None and row["deferred"] == 0, row
    assert row["intent"] == "declined", row
    assert aux_calls == [], "a deterministic decline must not pay for a judge"
    assert elapsed < 0.6, elapsed
    assert out is not None and RENDERED in out, out


# ---------------------------------------------------------------------------
# (c) a slow judgement is deferred — the turn pays the budget only
# ---------------------------------------------------------------------------
def test_c_slow_judgement_defers_and_delivers_first(monkeypatch):
    sid = "s-m41-11-c"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(PROSE_ROW))
    monkeypatch.setattr(di, "budget_ms", lambda: 60)

    def _slow(*a, **k):
        time.sleep(0.6)
        return _verdict("softened")

    monkeypatch.setattr(intent_judge, "judge_turn", _slow)
    # M35-03: PROSE_ROW is gate=flagged, and row 4 now routes on the gate flag
    # alone — the lane owns an in-domain turn whatever the (deferred) intent
    # class says. The render chain is stubbed to fail-open here so this test
    # keeps measuring its own subject: the judgement never blocks delivery.
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: [])
    monkeypatch.setattr(plugin.router, "call", lambda *a, **k: None)
    t0 = time.monotonic()
    out = plugin.on_transform_llm_output(response_text=SOFT_BODY,
                                         session_id=sid, model="m1")
    elapsed = time.monotonic() - t0

    row = _row(sink, "intent_judge_turn")
    assert row is not None and row["deferred"] == 1, row
    assert row["intent"] == "deferred", row
    assert elapsed < 0.5, "the turn waited %.3fs on a 60ms budget" % elapsed
    assert (out is None) or (SOFT_BODY in out), out   # None = unchanged body delivered
    decision = _row(sink, "rule_table_decision")
    # M35-03: the gate verdict decided the turn, not the body's self-assessment
    assert decision is not None and decision["rule"] == 4, decision
    # M35-15: "route_replace" -> "route" (M35-14 §7 item 8).
    assert decision["action"] == "route", decision

    # the judgement finished behind delivery and named the turn
    rec = di.wait_for(sid, timeout=3.0)
    assert rec["intent"] == "softened", rec
    assert di.correction_pending(sid) is True


# ---------------------------------------------------------------------------
# (d) turn N deferred -> turn N+1 corrected, one shot, platform channel
# ---------------------------------------------------------------------------
def test_d_correction_is_applied_when_the_next_turn_opens(monkeypatch):
    sid = "s-m41-11-d"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid, "first ask")
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(PROSE_ROW))
    monkeypatch.setattr(di, "budget_ms", lambda: 50)
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: (time.sleep(0.5), _verdict("softened"))[1])
    # M35-03: the flagged gate now routes; the chain is stubbed fail-open so the
    # correction path is measured, not a live render.
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: [])
    monkeypatch.setattr(plugin.router, "call", lambda *a, **k: None)

    out_n = plugin.on_transform_llm_output(response_text=SOFT_BODY,
                                           session_id=sid, model="m1")
    assert (out_n is None) or (SOFT_BODY in out_n), out_n
    assert di.wait_for(sid, timeout=3.0)["intent"] == "softened"
    assert di.correction_pending(sid) is True

    # SAME turn: the correction is not for this one.
    assert di.consume_correction(sid) == ""
    assert di.correction_pending(sid) is True

    # NEXT turn opens with a new ask.
    req = {"messages": [{"role": "user", "content": "second ask"}]}
    env = plugin.on_llm_request(request=req, original_request=req, session_id=sid)
    assert isinstance(env, dict) and isinstance(env.get("request"), dict), env
    msgs = env["request"]["messages"]
    carriers = [m for m in msgs
                if m.get("role") == "system"
                and "M41-11 CORRECTION" in str(m.get("content") or "")]
    assert len(carriers) == 1, msgs
    note = str(carriers[0]["content"])
    assert "softened" in note and "END OF M41-11 CORRECTION" in note, note
    # one shot
    assert di.consume_correction(sid) == ""
    applied = _row(sink, "deferred_intent_applied")
    assert applied is not None and applied["chars"] > 200, applied


# ---------------------------------------------------------------------------
# (e) no new aux call that counts failures toward the breaker
# ---------------------------------------------------------------------------
def test_e_deferred_judge_never_counts_a_failure(monkeypatch):
    sid = "s-m41-11-e"
    _prime(sid, "ask e")
    gate_row = dict(PROSE_ROW)
    gate_row["gate"] = "not_flagged"
    gate_row["state"] = "not_flagged"
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: gate_row)
    monkeypatch.setattr(di, "budget_ms", lambda: 900)

    calls = []

    def _aux(prompt, **kw):
        calls.append(kw)
        return '{"intent": "delivered"}'

    monkeypatch.setattr(semantic_classifier, "aux_raw_call", _aux)
    plugin.on_transform_llm_output(response_text=SOFT_BODY, session_id=sid,
                                   model="m1")
    assert len(calls) == 1, calls
    assert calls[0].get("record_failure") is False, calls[0]
    assert calls[0].get("detail") == "intent_judge", calls[0]


# ---------------------------------------------------------------------------
# (f) inside the budget: rule 4 replaces on turn N, exactly as before
# ---------------------------------------------------------------------------
def test_f_fast_judgement_keeps_the_synchronous_replace(monkeypatch):
    sid = "s-m41-11-f"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid, "ask f")
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(PROSE_ROW))
    monkeypatch.setattr(di, "budget_ms", lambda: 900)
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: _verdict("softened"))
    _chain_spy(monkeypatch)
    out = plugin.on_transform_llm_output(response_text=SOFT_BODY,
                                         session_id=sid, model="m1")

    decision = _row(sink, "rule_table_decision")
    row = _row(sink, "intent_judge_turn")
    assert decision is not None and (decision["rule"], decision["action"]) == (
        4, "route"), decision
    assert row is not None and row["deferred"] == 0, row
    assert out is not None and RENDERED in out, out
    assert di.correction_pending(sid) is False, "an in-budget turn needs no correction"
