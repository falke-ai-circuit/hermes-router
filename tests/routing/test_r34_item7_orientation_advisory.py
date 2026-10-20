"""R34 item 7 — a PRE orientation/risk consult must never replace an
execution turn.

Live specimen (this session):
    risk_consult_fire risk_class=r3
    -> anchor_route_fired lane=complexity mode=consult model_target=...
    -> anchor_chars=4866 -> the DELIVERED answer became the orientation
       envelope ("Orientation, not execution — here's the shape of it")
       and ZERO work was done.

Fix under test (Sentron): when the turn carries an explicit execution
directive (imperative task with a deliverable), the orientation envelope is
framed as SUPPORTING material — `instruction` tells the agent not to answer
with the envelope and to proceed with the task. The envelope itself is
unchanged advisory content.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_chain, anchor_exec, router_core  # noqa: E402

SID = "s-r34-item7"
CONTENT = "Orientation, not execution — here's the shape of it."


def _run(monkeypatch, user_text, orientation=True):
    rec = {
        "task_id": "t-r34-7",
        "lane": "complexity",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri(
            "openrouter://test/model-a", "primary"),
        "route_id": "r-r34-7",
        "session_id": SID,
        "orientation": orientation,
    }
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda endpoint, api_kwargs: (CONTENT, 0.0, 10, 5))
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **k: None)
    api_kwargs = {"messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "earlier context"},
        {"role": "user", "content": user_text},
    ]}
    return anchor_exec.maybe_execute_anchored(SID, api_kwargs)


def test_execution_turn_envelope_is_framed_advisory_only(monkeypatch):
    out = _run(monkeypatch,
               "Implement and commit the seven items. First action is a tool "
               "call that edits a file, not prose.")
    assert out and out[0] == "done"
    env = out[1]
    assert env.get("advisory_only") is True
    instr = str(env.get("instruction") or "")
    assert "ADVISORY ONLY" in instr
    assert "do not answer with this" in instr.lower()
    # the consult content is still present, unchanged
    assert env.get("answer") == CONTENT


def test_execution_detection_covers_directive_verbs(monkeypatch):
    for verb in ("fix", "build", "land", "add", "run", "patch", "deploy"):
        out = _run(monkeypatch, "%s the thing and report back." % verb)
        assert out[1].get("advisory_only") is True, verb


def test_non_execution_orientation_is_unchanged(monkeypatch):
    out = _run(monkeypatch, "What are the tradeoffs of A versus B here?")
    env = out[1]
    assert "instruction" not in env
    assert not env.get("advisory_only")


def test_plain_consultation_not_framed(monkeypatch):
    out = _run(monkeypatch, "Implement and commit the seven items.",
               orientation=False)
    assert out[1].get("kind") == "consultation"
    assert "instruction" not in out[1]
