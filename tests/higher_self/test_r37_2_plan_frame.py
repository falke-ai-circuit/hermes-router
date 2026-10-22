"""R37-2 (5.14.0): the PRE consult advises the PLAN, not the prose.

The PRE consult fires BEFORE the agent has written anything. Measured on 246
frontier consults: 89.3% divergent advice (not redundant) but only 20.9% ever
acted on; ~33% of the advice was length/truncation/completeness commentary the
agent could not act on, because there was no answer yet. Bullet 1 of the old
frame — "what success would look like in her hands (the shape the result should
take)" — was answer-shape commentary and is gone.

Pinned here:
  (a) the frame requests the three plan items and FORBIDS answer-shape
      commentary, naming POST as where it belongs;
  (b) an answer-shape-only reply is handled fail-open (never blocks the turn);
  (c) the budget/cap path is untouched: the request is bounded at GENERATION
      time and the answer is never truncated post-hoc.
"""
import hermes_router.anchor_exec as ax
from hermes_router import anchor_chain, router_core

ANSWER_SHAPE_VOCAB = ("length", "formatting", "truncation", "completeness",
                      "tone", "consider adding")


def _frame() -> str:
    return ax.build_orientation_frame("ADD a /health endpoint to the router.")


# --- (a) the prompt contract ------------------------------------------------


def test_frame_requests_the_three_plan_items_in_order():
    f = _frame()
    assert "next 1-3 concrete steps, in order" in f
    assert "most likely to WASTE this turn, and why" in f
    assert "What to avoid" in f
    assert f.index("1. The next 1-3") < f.index("2. The step most likely") \
        < f.index("3. What to avoid")


def test_frame_forbids_answer_shape_commentary_and_names_post():
    f = _frame().lower()
    # every banned family is named in the prohibition itself
    for word in ANSWER_SHAPE_VOCAB:
        assert word in f, word
    assert "do not comment on answer" in f
    assert "post" in f and "sees the answer" in f
    # the ask itself still rides the frame
    assert "ADD a /health endpoint" in _frame()


def test_the_old_answer_shape_request_is_gone():
    f = _frame()
    assert "shape the result should take" not in f
    assert "what success would look like" not in f.lower()


def test_self_reflect_and_causal_blocks_still_ride_the_frame():
    f = ax.build_orientation_frame("ASK", "\n\nSELF-REFLECT", "\n\nCAUSAL")
    assert "SELF-REFLECT" in f and "CAUSAL" in f
    assert f.index("ASK") < f.index("SELF-REFLECT") < f.index("CAUSAL")


# --- drive harness ----------------------------------------------------------


def _stage(monkeypatch, reply="1. Read X\n2. Write Y", orientation=True):
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    rec = {"task_id": "t37-2", "lane": "complexity", "mode": router_core.MODE_CONSULT,
           "endpoint": ep, "route_id": "r37-2", "orientation": orientation,
           "reason": "complexity_orientation"}
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **kw: None)
    monkeypatch.setattr(router_core, "store_consult_result", lambda *a, **kw: None)
    chain = anchor_chain.load_anchor_chain()
    monkeypatch.setattr(anchor_chain, "load_anchor_chain", lambda *a, **kw: chain)
    monkeypatch.setattr(anchor_chain, "cap_check", lambda *a, **kw: (True, 0.0, 0.01))
    monkeypatch.setattr(anchor_chain, "estimate_call_cost", lambda *a, **kw: 0.01)
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **kw: None)
    try:
        from hermes_router import decision as _dec
        monkeypatch.setattr(_dec, "ledger_write", lambda *a, **kw: 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        from hermes_router import state as _st
        monkeypatch.setattr(_st, "mark_pre_advisory",
                            lambda *a, **kw: {"text": "x"}, raising=False)
    except Exception:  # noqa: BLE001
        pass
    sent = {}

    def _fake_call(endpoint, payload):
        sent["payload"] = payload
        return reply, 0.01, 100, 50

    monkeypatch.setattr(ax, "anchored_call", _fake_call)
    return rec, sent


def _request():
    return {"messages": [{"role": "user", "content": "Analyse how the router resolves a model alias"}],
            "max_tokens": 12000}


# --- (c) bounded at generation, never post-hoc ------------------------------


def test_orientation_payload_carries_the_plan_frame(monkeypatch):
    _rec, sent = _stage(monkeypatch)
    outcome = ax.maybe_execute_anchored("api_r37_2", _request())
    assert outcome and outcome[0] == "done"
    last_user = [m for m in sent["payload"]["messages"]
                 if m.get("role") == "user"][-1]["content"]
    assert "next 1-3 concrete steps, in order" in last_user
    assert "Do NOT comment on answer length" in last_user


def test_request_is_bounded_at_generation_not_truncated_after(monkeypatch):
    monkeypatch.setattr(ax, "advisory_char_budget", lambda: 3800)
    _rec, sent = _stage(monkeypatch, reply="PLAN " * 400)  # 2000 chars, inside budget
    outcome = ax.maybe_execute_anchored("api_r37_2b", _request())
    assert outcome and outcome[0] == "done"
    # what we bought is what we may inject: max_tokens sized from the budget
    assert sent["payload"]["max_tokens"] == ax.advisory_max_tokens(3800)
    # and the whole answer is delivered — no post-hoc truncation of a paid reply
    assert outcome[1]["answer"] == "PLAN " * 400
    assert len(outcome[1]["answer"]) == 2000
    assert not outcome[1].get("advisory_truncated")


# --- (b) fail-open unchanged ------------------------------------------------


def test_answer_shape_only_reply_never_blocks_the_turn(monkeypatch):
    reply = ("The answer's length is fine. Formatting is mostly good, though "
             "the completeness of section 3 is weak and the tone is formal. "
             "Consider adding a summary and trimming truncation risk.")
    _rec, _sent = _stage(monkeypatch, reply=reply)
    request = _request()
    outcome = ax.maybe_execute_anchored("api_r37_2c", request)
    # delivered or dropped, never raised, never a turn block
    assert outcome is None or outcome[0] == "done"
    assert request["messages"][-1]["role"] == "user"


def test_a_consult_exception_still_passes_the_turn_through(monkeypatch):
    _rec, _sent = _stage(monkeypatch)

    def _boom(endpoint, payload):
        raise RuntimeError("frontier exploded")

    monkeypatch.setattr(ax, "anchored_call", _boom)
    assert ax.maybe_execute_anchored("api_r37_2d", _request()) is None


def test_an_empty_reply_still_passes_the_turn_through(monkeypatch):
    _rec, _sent = _stage(monkeypatch, reply="")
    monkeypatch.setattr(ax, "_retry_anchored_async", lambda *a, **kw: None)
    assert ax.maybe_execute_anchored("api_r37_2e", _request()) is None
