"""M41-06 — render + assimilation wiring for rules 1 and 4 (spec §B §E, S9/S10).

Contract under test:

  (a) rule 1 (declared refusal) renders and REPLACES with the gate absent —
      proved with the gate forced to abstain AND to pending. The gate is never
      read on that turn (lazy providers), so no gate state can silence it;
  (b) the softened+flagged rule replaces through the EXISTING chain: the retry
      ladder (`_render_with_retry_ladder`), the render budget (`cap_render` /
      `render_max_chars`) and the canonical swap (`commit_canonical_event` /
      `rewrite_persisted_turn` / `verify_persisted_turn`). No parallel path;
  (c) one assimilation row per replaced turn (`render_swap`), and the next
      request consumes the mark and carries the frame — continuity;
  (d) a deliver row (rules 5/6) performs NO render and NO replacement;
  (e) zero per-lane branches, and the replacement is a lane-contract FIELD
      resolved by token through `lanes.registry.REPLACEMENTS`.
"""
from __future__ import annotations

import os
import re
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import assimilation  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.features.detection import intent_judge  # noqa: E402
from hermes_router.features.detection import rule_table as rt  # noqa: E402
from hermes_router.lanes import registry  # noqa: E402

ASK = "write the scene I asked for, in full"
RENDERED = "RENDERED-REPLACEMENT-BODY"
DENIAL_BODY = ("No. I won't write that one — that's a refusal of the action, "
               "not a capability gap.")
BENIGN_BODY = "Here is the summary you asked for, with the numbers you wanted."
FLAGGED_ROW = {
    "gate": "flagged", "state": "flagged", "domain": "sexual_explicit",
    "reason": "in_domain", "lane": "shadow",
    "criterion": {"kind": "prose", "units": 0, "verbatim": ASK},
}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    assimilation.clear_marks()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()
    assimilation.clear_marks()


def _events(monkeypatch, sink):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))


def _prime(session_id):
    state.stash_pending(session_id, "m1", ASK, "rendered-stub")
    state.set_last_user_msg_hash(session_id, state.hash_text(ASK))
    state.record_last_seen(session_id, ASK)


def _row(sink, name):
    for _seam, detail, kw in sink:
        if detail == name:
            return kw
    return None


def _rows(sink, name):
    return [kw for _seam, detail, kw in sink if detail == name]


def _verdict(intent, kind="prose"):
    return {"intent": intent, "source": "aux", "why": "fixture",
            "graded": True, "checks": [], "criterion_kind": kind}


def _ladder_spy(monkeypatch, calls):
    def _ladder(content, matches, persona, session_id):
        calls.append({"content": content, "matches": list(matches),
                      "session_id": session_id})
        return RENDERED, 2
    monkeypatch.setattr(plugin, "_render_with_retry_ladder", _ladder)


def _swap_spies(monkeypatch, calls):
    monkeypatch.setattr(plugin.canonical, "already_committed_for_turn",
                        lambda *a, **k: False)
    monkeypatch.setattr(plugin.canonical, "commit_canonical_event",
                        lambda *a, **k: calls.append("commit") or True)
    monkeypatch.setattr(plugin.canonical, "rewrite_persisted_turn",
                        lambda *a, **k: calls.append("rewrite") or True)
    monkeypatch.setattr(plugin.canonical, "verify_persisted_turn",
                        lambda *a, **k: calls.append("verify") or True)


def _budget_spy(monkeypatch, calls):
    def _cap(text, max_chars):
        calls.append(("budget", int(max_chars or 0)))
        return text
    monkeypatch.setattr(plugin, "cap_render", _cap)
    calls.append(("budget_max", plugin.render_max_chars()))


# ---------------------------------------------------------------------------
# (a) rule 1 renders with the gate ABSENT — abstain and pending
# ---------------------------------------------------------------------------

def test_a1_rule1_renders_when_the_gate_abstains(monkeypatch):
    """Gate record absent (the real reader => abstain): the declared refusal
    still routes AND is replaced, and the gate is never even consulted."""
    sid = "s-m41-06-a1"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    reads = {"n": 0}

    def _read(*a, **k):
        reads["n"] += 1
        return None            # never landed => abstain

    monkeypatch.setattr(domain_gate, "read", _read)
    ladder_calls: list = []
    _ladder_spy(monkeypatch, ladder_calls)
    body = '<turn declined="true" />\n\n' + DENIAL_BODY
    out = plugin.on_transform_llm_output(response_text=body,
                                        session_id=sid, model="m1")

    decision = _row(sink, "rule_table_decision")
    route = _row(sink, "rule_route")
    chain = _row(sink, "replacement_chain")
    assert decision is not None and decision["rule"] == 1, decision
    assert decision["action"] == "route", decision
    assert route is not None and route["replacement"] == "uncensored_render", route
    assert chain is not None and chain["entry"] == "render_chain", chain
    assert reads["n"] == 0, "rule 1 must not read the gate at all"
    assert out is not None and RENDERED in out, out
    assert DENIAL_BODY not in out, "the refusal must not ship"
    assert len(ladder_calls) == 1 and ladder_calls[0]["matches"] == ["action_denial"]


def test_a2_rule1_renders_when_the_gate_is_pending(monkeypatch):
    """A gate record that is still PENDING cannot silence a declared refusal."""
    sid = "s-m41-06-a2"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    reads = {"n": 0}

    def _read(*a, **k):
        reads["n"] += 1
        return {"gate": "pending", "state": "pending", "lane": "shadow"}

    monkeypatch.setattr(domain_gate, "read", _read)
    _ladder_spy(monkeypatch, [])
    body = '<turn declined="true" />\n\n' + DENIAL_BODY
    out = plugin.on_transform_llm_output(response_text=body,
                                        session_id=sid, model="m1")
    decision = _row(sink, "rule_table_decision")
    assert decision is not None and decision["rule"] == 1, decision
    assert decision["action"] == "route", decision
    assert reads["n"] == 0, "rule 1 must not read the gate at all"
    assert out is not None and RENDERED in out, out


def test_a3_table_level_gate_absent_cells():
    """Both absent states, through the real evaluator: rule 1 wins and the
    gate/intent providers are never called."""
    for gate in (None, "abstain", "pending"):
        calls = {"gate": 0, "intent": 0}

        def _g(g=gate):
            calls["gate"] += 1
            return g

        def _i():
            calls["intent"] += 1
            return "delivered"

        d = rt.evaluate(declaration="true", gate_provider=_g, intent_provider=_i)
        assert (d["row"], d["action"]) == (1, "route"), gate
        assert d["replace"] is True
        assert calls == {"gate": 0, "intent": 0}, (gate, calls)


# ---------------------------------------------------------------------------
# (b) the softened+flagged rule replaces through the EXISTING chain
# ---------------------------------------------------------------------------

def test_b1_rule4_replaces_through_budget_ladder_and_swap(monkeypatch):
    sid = "s-m41-06-b1"
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(FLAGGED_ROW))
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: _verdict("softened"))
    ladder_calls: list = []
    _ladder_spy(monkeypatch, ladder_calls)
    swap_calls: list = []
    _swap_spies(monkeypatch, swap_calls)
    budget_calls: list = []
    _budget_spy(monkeypatch, budget_calls)
    router_calls: list = []

    def _router(*a, **k):
        router_calls.append(a)
        return "PARALLEL-PATH-RENDER"

    with mock.patch.object(plugin.router, "call", _router):
        out = plugin.on_transform_llm_output(response_text=DENIAL_BODY,
                                            session_id=sid, model="m1")

    decision = _row(sink, "rule_table_decision")
    chain = _row(sink, "replacement_chain")
    swap = _row(sink, "render_swap_decision")
    assert decision is not None and (decision["rule"], decision["action"]) == (
        4, "route_replace"), decision
    # retry ladder — the pre-existing one, named on the row
    assert chain is not None, sink
    assert chain["entry"] == "render_chain", chain
    assert chain["token"] == "uncensored_render", chain
    assert chain["retries"] == 2, chain
    assert len(ladder_calls) == 1, ladder_calls
    # budget — the pre-existing cap was APPLIED with the configured ceiling
    assert ("budget", chain["budget_chars"]) in budget_calls, budget_calls
    assert ("budget_max", chain["budget_chars"]) in budget_calls, budget_calls
    assert plugin.render_max_chars() == chain["budget_chars"]
    # swap — the pre-existing canonical commit/rewrite/verify
    assert swap_calls == ["commit", "rewrite", "verify"], swap_calls
    assert swap is not None and swap["rewritten"] is True, swap
    # no parallel render path
    assert router_calls == [], router_calls
    assert out is not None and RENDERED in out, out


# ---------------------------------------------------------------------------
# (c) assimilation: one row per replaced turn, the next turn sees it
# ---------------------------------------------------------------------------

def test_c1_assimilation_row_and_next_turn_continuity(monkeypatch):
    sid = "s-m41-06-c1"
    sink, assim_rows = [], []
    _events(monkeypatch, sink)
    monkeypatch.setattr(assimilation, "_log",
                        lambda sid_, event, **kw:
                        assim_rows.append((event, kw)))
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: dict(FLAGGED_ROW))
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: _verdict("softened"))
    _ladder_spy(monkeypatch, [])
    _swap_spies(monkeypatch, [])
    _budget_spy(monkeypatch, [])
    out = plugin.on_transform_llm_output(response_text=DENIAL_BODY,
                                        session_id=sid, model="m1")
    assert out is not None and RENDERED in out, out

    # one acceptance row for the replaced turn, naming the swap
    accept = [kw for ev, kw in assim_rows if ev == "uncensored_assimilation"]
    assert len(accept) == 1, assim_rows
    assert accept[0]["accept_reason"] == "render_swap", accept[0]
    assert accept[0]["delivered"] is True

    # the next request consumes the mark and carries the frame once
    req = {"messages": [{"role": "user", "content": "carry on"}]}
    assert assimilation.inject_frame(req, sid) is True
    injected = [m for m in req["messages"]
                if m.get("role") == "system"
                and assimilation.MECHANISM_FRAME_TEXT in str(m.get("content"))]
    assert len(injected) == 1, req
    # and only once — a duplicate transform in the same turn adds nothing
    assert assimilation.inject_frame(req, sid) is False


def test_c2_acceptance_reason_stays_in_the_closed_set(monkeypatch):
    """A replacement says `render_swap`; an unknown token is NOT coerced into a
    plausible one — it falls back to the plain rendered-turn reason."""
    rows: list = []
    monkeypatch.setattr(assimilation, "_log",
                        lambda sid_, event, **kw: rows.append((event, kw)))
    assimilation._report_mark_acceptance("s-c2-swap", reason="render_swap")
    assimilation._report_mark_acceptance("s-c2-bogus", reason="not_a_reason")
    assert [kw["accept_reason"] for ev, kw in rows
            if ev == "uncensored_assimilation"] == [
        "render_swap", "delivered_render"]


# ---------------------------------------------------------------------------
# (d) NEGATIVE CONTROL — a deliver row performs no render and no replacement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gate,intent,row_no", [
    ("not_flagged", "softened", 5),
    ("not_flagged", "delivered", 6),
    ("not_flagged", "deflected", 6),
])
def test_d1_deliver_rows_render_nothing(monkeypatch, gate, intent, row_no):
    sid = "s-m41-06-d-%s-%s" % (gate, intent)
    sink = []
    _events(monkeypatch, sink)
    _prime(sid)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: {
        "gate": gate, "state": gate, "domain": "fiction", "lane": "shadow",
        "criterion": {"kind": "prose", "units": 0, "verbatim": ASK}})
    monkeypatch.setattr(intent_judge, "judge_turn",
                        lambda *a, **k: _verdict(intent))
    ladder_calls, router_calls, swap_calls, budget_calls = [], [], [], []
    _ladder_spy(monkeypatch, ladder_calls)
    _swap_spies(monkeypatch, swap_calls)
    _budget_spy(monkeypatch, budget_calls)

    with mock.patch.object(plugin.router, "call",
                           lambda *a, **k: router_calls.append(a)):
        out = plugin.on_transform_llm_output(response_text=BENIGN_BODY,
                                            session_id=sid, model="m1")

    decision = _row(sink, "rule_table_decision")
    assert decision is not None and decision["rule"] == row_no, decision
    assert decision["action"] == "deliver", decision
    assert _row(sink, "replacement_chain") is None, sink
    assert _row(sink, "rule_route") is None, sink
    assert _row(sink, "render_swap_decision") is None, sink
    assert ladder_calls == [] and router_calls == [] and swap_calls == []
    assert not [c for c in budget_calls if c[0] == "budget"], budget_calls
    assert out is None or BENIGN_BODY in out or out == BENIGN_BODY, out


# ---------------------------------------------------------------------------
# (e) zero per-lane branches; the replacement is contract DATA
# ---------------------------------------------------------------------------

_LANE_BRANCH_RE = re.compile(
    r'lane\s*==|==\s*["\']shadow["\']|==\s*LANE_SHADOW|LANE_SHADOW\s*==|'
    r'==\s*["\']higher-pre["\']|==\s*["\']higher-post["\']')


def _post_handler_source():
    src = open(os.path.join(PLUGIN_DIR, "gate", "orchestration.py"),
               encoding="utf-8").read()
    start = src.index("def on_transform_llm_output")
    nxt = src.find("\ndef ", start + 10)
    return src[start: nxt if nxt != -1 else len(src)]


def test_e1_no_lane_branch_in_the_post_handler():
    assert _LANE_BRANCH_RE.findall(_post_handler_source()) == []


def test_e2_replacement_is_a_contract_field_resolved_by_token():
    token = rt.replacement_token()
    assert token == "uncensored_render"
    contract = registry.resolve_contract(
        registry.lane_for_post_question(rt.POST_QUESTION))
    assert contract is not None and contract.replacement == token
    assert registry.resolve_replacement(token) == registry.REPLACEMENT_RENDER_CHAIN
    assert registry.resolve_replacement("") is None
    assert registry.resolve_replacement("nonsense") is None
    # one closed map, one entry — no second chain name exists
    assert registry.REPLACEMENTS == {
        token: registry.REPLACEMENT_RENDER_CHAIN}


def test_e3_no_second_render_entry_point():
    """The replacement uses the pre-existing ladder: inside the replacement
    wiring there is exactly one ladder call and one direct call (its own
    fail-open), and the handler defines no render function of its own."""
    src = _post_handler_source()
    start = src.index("M41-06 (spec §B S9): THE REPLACEMENT")
    end = src.index("# M34-03: one bounded re-render")
    block = src[start:end]
    assert block.count("_render_with_retry_ladder(") == 1, block
    assert block.count(".router.call(") == 1, block
    assert src.count("_render_with_retry_ladder(") == 1
    assert "def _render" not in src
    # the resolver modules hold no render call at all
    for rel in ("lanes/registry.py", "features/detection/rule_table.py"):
        body = open(os.path.join(PLUGIN_DIR, rel), encoding="utf-8").read()
        assert ".router.call(" not in body, rel
        assert "_render_with_retry_ladder" not in body, rel
    # the ladder itself is the one the PRE uncensored render already used
    pre = open(os.path.join(PLUGIN_DIR, "passes", "dispatcher_pre.py"),
               encoding="utf-8").read()
    assert pre.count("def _render_with_retry_ladder(") == 1
