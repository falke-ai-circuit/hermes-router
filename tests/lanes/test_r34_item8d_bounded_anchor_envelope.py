"""R34 item 8d — the PRE anchor envelope must be BOUNDED on execution turns.

Live: a complexity/risk consult on an execution turn delivered a
3.7K-4.9K-char envelope (anchor_chars=4866) and the dispatch turn answered
with it instead of doing the work.

Rule: on an EXECUTION turn the advisory content is at most the first 600
chars of consult content plus the ADVISORY ONLY instruction, with
advisory_only=True. Consult-only turns and non-execution orientation turns
keep the full envelope.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_chain, anchor_exec, router_core  # noqa: E402

SID = "s-r34-8d"
BIG = "ORIENTATION " + ("detail " * 700)      # ~4900 chars


def _run(monkeypatch, user_text, content, orientation=True):
    rec = {
        "task_id": "t-r34-8d",
        "lane": "complexity",
        "mode": router_core.MODE_CONSULT,
        "endpoint": anchor_chain.parse_anchor_uri(
            "openrouter://test/model-a", "primary"),
        "route_id": "r-r34-8d",
        "session_id": SID,
        "orientation": orientation,
    }
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "_retry_anchored_async", lambda *a, **k: None)
    monkeypatch.setattr(anchor_exec, "anchored_call",
                        lambda endpoint, api_kwargs: (content, 0.0, 10, 5))
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **k: None)
    api = {"messages": [{"role": "user", "content": user_text}]}
    return anchor_exec.maybe_execute_anchored(SID, api)


def test_execution_turn_envelope_is_bounded(monkeypatch):
    out = _run(monkeypatch, "Implement and commit the seven items.", BIG)
    env = out[1]
    assert env.get("advisory_only") is True
    assert env["instruction"] == anchor_exec.ADVISORY_ONLY_INSTRUCTION
    assert len(env.get("answer") or "") <= 600
    assert len(str(env.get("answer") or "") + str(env.get("instruction") or "")
               ) <= 700
    assert env.get("advisory_truncated") is True


def test_consult_only_turn_keeps_full_content(monkeypatch):
    out = _run(monkeypatch, "Implement and commit the seven items.", BIG,
               orientation=False)
    env = out[1]
    assert env.get("kind") == "consultation"
    assert env.get("answer") == BIG
    assert "instruction" not in env


def test_non_execution_orientation_keeps_full_content(monkeypatch):
    out = _run(monkeypatch, "What are the tradeoffs of A versus B?", BIG)
    env = out[1]
    assert env.get("answer") == BIG
    assert not env.get("advisory_only")
