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


def _chain_spy(monkeypatch, calls):
    """Drive the REAL `router.call` chain: entry 1 fails with a retryable
    upstream 5xx, entry 2 answers. This is the pre-existing ladder the
    replacement enters — no transport is touched."""
    entries = [{"name": "primary", "model": "m-primary"},
               {"name": "fallback", "model": "m-fallback"}]
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: entries)

    def _attempt(entry, prompt, **kw):
        calls.append(str(entry.get("name")))
        if str(entry.get("name")) == "primary":
            return "", "upstream_503"
        return RENDERED, ""

    monkeypatch.setattr(plugin.router, "_model_attempt", _attempt)


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
    chain_calls: list = []
    _chain_spy(monkeypatch, chain_calls)
    body = '<turn declined="true" />\n\n' + DENIAL_BODY
    out = plugin.on_transform_llm_output(response_text=body,
                                        session_id=sid, model="m1")

    decision = _row(sink, "rule_table_decision")
    route = _row(sink, "rule_route")
    chain = _row(sink, "replacement_chain")
    assert decision is not None and decision["rule"] == 1, decision
    assert decision["action"] == "route", decision
    assert route is not None and route["replacement"] == "uncensored_render", route
    assert chain is not None and chain["entry"] == "post_render_chain", chain
    # M35-15 (old case: row 1 ROUTE, no gate read; new case: D3
    # GATE_UNCONSULTED -> consult -> D5). M35-14 §2.1 moved the consult to
    # the DELIVERY edge: any replaceable turn must ask the gate before the
    # decision exists, so a rule-1 turn reads it once for the facts and at
    # most once more for the bounded re-entry. The property that matters
    # (and what the old zero-read pin protected) is that the consult is
    # BOUNDED — one attempt, never a loop.
    assert 1 <= reads["n"] <= 2, reads["n"]
    assert out is not None and RENDERED in out, out
    assert DENIAL_BODY not in out, "the refusal must not ship"
    # the chain ladder was exercised: primary failed, fallback answered
    assert chain_calls == ["primary", "fallback"], chain_calls


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
    _chain_spy(monkeypatch, [])
    body = '<turn declined="true" />\n\n' + DENIAL_BODY
    out = plugin.on_transform_llm_output(response_text=body,
                                        session_id=sid, model="m1")
    decision = _row(sink, "rule_table_decision")
    assert decision is not None and decision["rule"] == 1, decision
    assert decision["action"] == "route", decision
    # M35-15 (old case: row 1 ROUTE, no gate read; new case: D3
    # GATE_UNCONSULTED -> consult -> D5). M35-14 §2.1 moved the consult to
    # the DELIVERY edge: any replaceable turn must ask the gate before the
    # decision exists, so a rule-1 turn reads it once for the facts and at
    # most once more for the bounded re-entry. The property that matters
    # (and what the old zero-read pin protected) is that the consult is
    # BOUNDED — one attempt, never a loop.
    assert 1 <= reads["n"] <= 2, reads["n"]
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
        # M35-15: `replace` was the route_replace row's own flag. M35-14 §7
        # item 8 deleted the distinction (and the flag with it): row 1 now
        # routes, and whether the lane REPLACES is decided at the delivery
        # edge (core/delivery_decision D5), not by the table.
        assert "replace" not in d, d
        assert d["action"] == "route", d
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
    chain_calls: list = []
    _chain_spy(monkeypatch, chain_calls)
    swap_calls: list = []
    _swap_spies(monkeypatch, swap_calls)
    budget_calls: list = []
    _budget_spy(monkeypatch, budget_calls)
    # the PRE ladder must NOT be a second path for this turn
    ladder_calls: list = []
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda *a, **k: ladder_calls.append(a) or ("", 0))
    out = plugin.on_transform_llm_output(response_text=DENIAL_BODY,
                                        session_id=sid, model="m1")

    decision = _row(sink, "rule_table_decision")
    chain = _row(sink, "replacement_chain")
    swap = _row(sink, "render_swap_decision")
    assert decision is not None and (decision["rule"], decision["action"]) == (
        4, "route"), decision
    # chain ladder — the pre-existing one, named on the row, exercised
    assert chain is not None, sink
    assert chain["entry"] == "post_render_chain", chain
    assert chain["token"] == "uncensored_render", chain
    assert chain_calls == ["primary", "fallback"], chain_calls
    # budget — the pre-existing cap was APPLIED with the configured ceiling
    assert ("budget", chain["budget_chars"]) in budget_calls, budget_calls
    assert ("budget_max", chain["budget_chars"]) in budget_calls, budget_calls
    assert plugin.render_max_chars() == chain["budget_chars"]
    # swap — the pre-existing canonical commit/rewrite/verify
    assert swap_calls == ["commit", "rewrite", "verify"], swap_calls
    assert swap is not None and swap["rewritten"] is True, swap
    # no parallel render path: the PRE ladder was not entered
    assert ladder_calls == [], ladder_calls
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
    _chain_spy(monkeypatch, [])
    _swap_spies(monkeypatch, [])
    _budget_spy(monkeypatch, [])
    out = plugin.on_transform_llm_output(response_text=DENIAL_BODY,
                                        session_id=sid, model="m1")
    assert out is not None and RENDERED in out, out

    # one acceptance row for the replaced turn
    accept = [kw for ev, kw in assim_rows if ev == "uncensored_assimilation"]
    assert len(accept) == 1, assim_rows
    assert accept[0]["accept_source"] == "post_transform", accept[0]
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


def test_c2_mark_writes_one_row_and_no_mark_writes_none(monkeypatch):
    """The mark is per replaced turn: one row when a render was delivered, and
    silence when no render happened (a verdict cannot be counted out of air)."""
    rows: list = []
    monkeypatch.setattr(assimilation, "_log",
                        lambda sid_, event, **kw: rows.append((event, kw)))
    assimilation.mark_turn_rendered("s-c2-marked")
    assert [ev for ev, _kw in rows].count("uncensored_assimilation") == 1
    before = len(rows)
    assimilation.mark_turn_rendered("")
    assert len(rows) == before, rows[before:]


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
    chain_calls, router_calls, swap_calls, budget_calls = [], [], [], []
    _chain_spy(monkeypatch, chain_calls)
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
    assert chain_calls == [] and router_calls == [] and swap_calls == []
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
    assert registry.resolve_replacement(token) == registry.REPLACEMENT_POST_CHAIN
    assert registry.resolve_replacement("") is None
    assert registry.resolve_replacement("nonsense") is None
    # one closed map, one entry — no second chain name exists
    assert registry.REPLACEMENTS == {
        token: registry.REPLACEMENT_POST_CHAIN}


def test_e3_no_second_render_entry_point():
    """One render entry point: the replacement wiring names the pre-existing
    router.call cell and adds no render function of its own."""
    src = _post_handler_source()
    start = src.index("M41-06 (spec §B S9): THE REPLACEMENT")
    end = src.index("# M34-03: one bounded re-render")
    block = src[start:end]
    assert block.count(".router.call(") == 1, block
    assert "_render_with_retry_ladder(" not in block, block
    assert "def _render" not in src
    # the resolver modules hold no render call at all
    for rel in ("lanes/registry.py", "features/detection/rule_table.py"):
        body = open(os.path.join(PLUGIN_DIR, rel), encoding="utf-8").read()
        assert ".router.call(" not in body, rel
        assert "_render_with_retry_ladder(" not in body, rel
