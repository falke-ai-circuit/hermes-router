"""R72 — integrating the reading instead of interrupting the work.

Two levers, each with a failure mode it exists to prevent:

SHAPE. The reading used to arrive only as a trailing user-role message - the same shape that
made the echo guard necessary, because a note in a user message reads as something to answer.
`evidence` rides the last tool result instead, so the reading arrives with the data it was
derived from, in the channel the model already treats as ground truth.

AGREEMENT. A reflex that only ever contradicts trains the model to discount it, and silence
then means two different things. When the model's own last move already targets what the
reading would name, the reading adds nothing and must stay quiet - and say so in the log, so
agreement is distinguishable from a withheld reading.
"""
from __future__ import annotations

from hermes_router import decision_extract as dx
from hermes_router import reflex_lane as rl

BODY = ("Traceback (most recent call last):\n"
        '  File "/opt/data/app/src/render.py", line 12\n'
        "ImportError: cannot import name 'cache' from /opt/data/app/src/cache.py")


def _dist(*ps):
    return {"answers": {"action": {"probabilities": {("a%d" % (i + 1)): p
                                                    for i, p in enumerate(ps)}},
                        "scope": {"score": 1}, "rigour": {"score": 3}}}


def _req():
    return {"messages": [{"role": "user", "content": "fix the renderer"},
                         {"role": "tool", "content": BODY}]}


def _call(tool, args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": tool, "arguments": args}}]}


def _cfg(**kw):
    return {"hermes_router": {"reflex_weights": dict({"enabled": True}, **kw)}}


def test_evidence_shape_rides_the_tool_result_instead_of_appending_a_message(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    before = len(req["messages"])
    assert rl.run_on_request(req, session_id="s1", cfg=_cfg(shape="evidence")) is True
    # one new message is the standing contract; the READING itself spends no message
    assert len(req["messages"]) == before + 1
    tool_msg = [m for m in req["messages"] if m.get("role") == "tool"][-1]
    assert dx.HEAD_FRAME in tool_msg["content"]
    assert tool_msg["content"].startswith("Traceback"), "the evidence stays first"


def test_user_shape_still_appends_a_message_so_the_arm_can_be_rolled_back(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    before = len(req["messages"])
    assert rl.run_on_request(req, session_id="s2", cfg=_cfg(shape="user")) is True
    assert len(req["messages"]) == before + 2, "contract + the reading, as a message"
    assert req["messages"][-1]["role"] == "user"


def test_evidence_shape_falls_back_to_user_on_an_unsupported_carrier(monkeypatch):
    """Some tool results arrive with no writable content carrier. The reading must still
    arrive rather than vanish - delivery fails open, it never silently drops the block."""
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    # window comes from tool/assistant text, so an earlier result provides it; the LAST
    # tool message has no writable carrier, which is the case that must fall back.
    req = {"messages": [{"role": "user", "content": "fix the renderer"},
                        {"role": "tool", "content": BODY},
                        {"role": "tool", "content": None}]}
    assert rl.run_on_request(req, session_id="s3", cfg=_cfg(shape="evidence")) is True
    assert req["messages"][-1]["role"] == "user"


def test_it_stays_silent_when_the_model_is_already_on_the_named_target(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    req["messages"].append(_call("patch", '{"path": "/opt/data/app/src/render.py"}'))
    before = len(req["messages"])
    assert rl.run_on_request(req, session_id="s4", cfg=_cfg(shape="evidence")) is False
    assert len(req["messages"]) == before, "agreement must not spend a message"


def test_agreement_can_be_switched_off_for_the_ab_arm(monkeypatch):
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    req["messages"].append(_call("patch", '{"path": "/opt/data/app/src/render.py"}'))
    assert rl.run_on_request(req, session_id="s5",
                             cfg=_cfg(shape="evidence", agree_silent=False)) is True


def test_a_decisive_read_says_take_this_and_a_near_tie_says_check():
    decisive = {"options": [{"p": 0.80, "label": "change render.py",
                             "target": "/app/src/render.py", "kind": "edit"},
                            {"p": 0.12, "label": "read render.py first",
                             "target": "/app/src/render.py", "kind": "read"}],
                "margin": 0.68, "mindset": {}}
    assert "<- take this" in dx.render(decisive)
    near = {"options": [{"p": 0.52, "label": "change render.py",
                         "target": "/app/src/render.py", "kind": "edit"},
                        {"p": 0.48, "label": "read render.py first",
                         "target": "/app/src/render.py", "kind": "read"}],
            "margin": 0.04, "mindset": {}}
    body = dx.render(near)
    assert "too close to call" in body
    assert "<- take this" not in body


def test_a_generic_move_leading_never_claims_take_this():
    """A class move that names no object can top the distribution - "run the check" often
    does. Marking that decisive tells the model to commit to a move that says nothing about
    what to commit to, which is the confidently-wrong failure the lane exists to avoid."""
    generic = {"options": [{"p": 0.63, "label": "run the check or tests now", "target": "",
                            "kind": "run"},
                           {"p": 0.16, "label": "read render.py first",
                            "target": "/app/src/render.py", "kind": "read"}],
               "margin": 0.47, "mindset": {}}
    body = dx.render(generic)
    assert "<- take this" not in body
    assert "class-level only" in body


def test_the_reading_carries_an_integration_contract_like_the_frontier_lane(monkeypatch):
    """The model must be told how to treat a reflex, not left to infer it from shape."""
    from hermes_router import frames as fr

    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    assert rl.run_on_request(req, session_id="c1", cfg=_cfg(shape="evidence")) is True
    joined = "\n".join(str(m.get("content") or "") for m in req["messages"])
    assert fr.DECISION_RULE_MARKER in joined
    assert fr.platform_rule_marker(fr.DECISION_RULE_ID) in joined
    # the three things the model has to know: it is its own, it is not an order,
    # and a deviation must be deliberate rather than silent
    assert "yours the way a reflex is yours" in joined
    assert "does not require agreement" in joined
    assert "not a silent drift" in joined


def test_the_contract_is_delivered_once_not_on_every_call(monkeypatch):
    from hermes_router import frames as fr

    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    req = _req()
    rl.run_on_request(req, session_id="c2", cfg=_cfg(shape="evidence"))
    marker = fr.platform_rule_marker(fr.DECISION_RULE_ID)
    first = sum(1 for m in req["messages"] if marker in str(m.get("content") or ""))
    rl.clear()
    rl.run_on_request(req, session_id="c3", cfg=_cfg(shape="evidence"))
    second = sum(1 for m in req["messages"] if marker in str(m.get("content") or ""))
    assert first == 1 and second == 1, "a standing rule is standing, not repeated"


def test_the_instinct_register_states_one_move_instead_of_a_menu():
    """A dashboard invites deliberation; a hunch replaces it. Both must be auditable."""
    clear = {"options": [{"p": 0.73, "label": "change calc.py"},
                         {"p": 0.45, "label": "read calc.py first"},
                         {"p": 0.10, "label": "stop and report"}],
             "margin": 0.28, "mindset": {"scope": -0.86, "rigour": 0.04}}
    line = dx.render_instinct(clear).splitlines()[0]
    assert line.startswith(dx.INSTINCT_TAG)
    assert "clear read (73%)" in line
    assert "hold scope narrow" in line
    assert "stop and report" not in line, "the also-ran does not clutter a clear read"


def test_the_instinct_register_warns_on_a_near_tie_rather_than_pretending_certainty():
    near = {"options": [{"p": 0.52, "label": "change calc.py"},
                        {"p": 0.48, "label": "read calc.py first"}],
            "margin": 0.04, "mindset": {}}
    line = dx.render_instinct(near).splitlines()[0]
    assert "only just" in line
    assert "Check before committing" in line
    assert "read calc.py first" in line


def test_instinct_carries_its_provenance_like_the_frame_does():
    clear = {"options": [{"p": 0.8, "label": "change calc.py"},
                         {"p": 0.1, "label": "read calc.py first"}],
             "margin": 0.7, "mindset": {}}
    body = dx.render_instinct(clear, basis="  from: run pytest -> 1 failed")
    assert "from: run pytest" in body


def test_register_and_shape_are_read_from_the_same_config_block():
    from hermes_router import reflex_weights as rw

    got = rw.cfg_for({"hermes_router": {"reflex_weights": {
        "enabled": True, "register": "instinct", "shape": "evidence",
        "agree_silent": False, "min_top": 0.5, "min_margin": 0.2}}})
    assert got["register"] == "instinct"
    assert got["shape"] == "evidence"
    assert got["agree_silent"] is False
    assert got["min_top"] == 0.5 and got["min_margin"] == 0.2


def test_the_shape_is_recorded_so_the_arms_can_be_scored_from_the_log(monkeypatch):
    seen = {}

    def _spy(sid, event, **fields):
        if event == "reflex_weights_delivered":
            seen.update(fields)

    monkeypatch.setattr(rl, "_log", _spy)
    monkeypatch.setattr(rl, "_transport", lambda *a, **k: _dist(0.8, 0.12, 0.05, 0.03))
    rl.clear()
    rl.run_on_request(_req(), session_id="s6", cfg=_cfg(shape="evidence"))
    assert seen.get("shape") == "evidence"
