"""M41-02 — the PRE domain gate (spec §C) proved by observation, not by config.

Five legs, one per acceptance item:

  (a) 20 live-shaped turns: the gate adds ZERO latency (start() returns while
      the aux call is still in flight; nothing is joined);
  (b) a domain verdict is present on the turn record for >= 90% of eligible
      turns (record-level, read back through the POST seam's own reader);
  (c) a turn whose session has `aux_consult_min_interval_sec` SATURATED still
      produces a domain verdict — proved against a control that shows the same
      seed suppresses `_aux_intent_decision` (the 03:11:47 suppressor);
  (d) `abstain` falls back to the body path and is never readable as "safe";
  (e) no per-lane branch exists in the POST handler (grep over its source).

Everything is deterministic: the aux lane is stubbed, so a failure here is the
gate's wiring, not a provider hiccup.
"""
from __future__ import annotations

import inspect
import re
import threading
import time

import pytest

import hermes_router as plugin
from hermes_router import router_core, route_gate, state
from hermes_router.features.detection import domain_gate as dg
from hermes_router.lanes import registry as lane_registry

SID = "s-m41-02"


@pytest.fixture(autouse=True)
def _reset():
    state.clear_pre_domain()
    state.reset_turn_identity(SID)
    route_gate._AUX_CONSULT_LAST.clear()
    yield
    state.clear_pre_domain()
    route_gate._AUX_CONSULT_LAST.clear()


def _stub_aux(monkeypatch, answer, *, delay=0.0, calls=None):
    """Stub the shared aux lane at the ONE seam the gate calls."""
    import hermes_router.semantic_classifier as sc

    def _fake(prompt, **kwargs):
        if calls is not None:
            calls.append(kwargs.get("session_id"))
        if delay:
            time.sleep(delay)
        return answer

    monkeypatch.setattr(sc, "aux_raw_call", _fake)


def _domain(label, confident=True):
    return f'{{"domain": "{label}", "confident": {str(bool(confident)).lower()}}}'


def _wait_landed(sids, timeout=8.0):
    """Poll until every session's record left `pending`. Returns seconds."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rows = [state.pre_domain(s) for s in sids]
        if all(r and str(r.get("state")) != dg.GATE_PENDING for r in rows):
            return time.monotonic() - t0
        time.sleep(0.01)
    return time.monotonic() - t0


# ---------------------------------------------------------------------------
# (a) added latency = 0 — the gate does not block generation
# ---------------------------------------------------------------------------
def test_a_added_latency_is_zero_over_twenty_turns(monkeypatch):
    """20 turns, aux stubbed at 250ms per call. start() must return in
    microseconds: the thread is spawned, never joined."""
    calls = []
    _stub_aux(monkeypatch, _domain("cyber_offensive"), delay=0.25, calls=calls)

    timings = []
    sids = []
    for i in range(20):
        sid = f"s-m41-02-a-{i}"
        state.reset_turn_identity(sid)
        sids.append(sid)
        t0 = time.perf_counter()
        dg.start(sid, f"turn {i}: how do I get into that box", log=None)
        timings.append((time.perf_counter() - t0) * 1000.0)

    landed = _wait_landed(sids)
    timings.sort()
    p95 = timings[int(0.95 * (len(timings) - 1))]
    mean = sum(timings) / len(timings)

    print(f"\n(a) start() wall clock over 20 turns: mean={mean:.3f}ms "
          f"p95={p95:.3f}ms max={timings[-1]:.3f}ms")
    print(f"(a) all 20 verdicts landed {landed:.2f}s after the last start() "
          f"(aux delay 250ms/call, concurrent)")
    print(f"(a) aux calls observed through the shared lane: {len(calls)}")

    assert len(calls) == 20, "every eligible turn actually reached the aux lane"
    # The gate is concurrent: 20 x 250ms of aux work cannot have been awaited.
    assert timings[-1] < 50.0, f"a start() blocked for {timings[-1]:.1f}ms"
    assert p95 < 25.0, f"p95 added latency {p95:.1f}ms"
    assert landed < 5.0, "workers did not overlap"


# ---------------------------------------------------------------------------
# (b) verdict present on the turn record for >= 90% of eligible turns
# ---------------------------------------------------------------------------
def test_b_verdict_present_on_ninety_percent_of_turns(monkeypatch):
    """18 in-domain answers + 2 aux failures = 20 eligible turns. Every one
    must carry a non-pending verdict; abstain counts as a verdict (a failure
    that is RECORDED), a pending/missing record does not."""
    answers = [_domain("violence_gore")] * 9 + [_domain("benign_task")] * 9 + [None, None]
    seq = iter(answers)
    import hermes_router.semantic_classifier as sc

    def _fake(prompt, **kwargs):
        return next(seq, None)

    monkeypatch.setattr(sc, "aux_raw_call", _fake)

    sids, kinds = [], {}
    for i in range(20):
        sid = f"s-m41-02-b-{i}"
        state.reset_turn_identity(sid)
        sids.append(sid)
        # M35-06: the ask text is part of the decision surface (a topic label
        # with no cue in the ask is no longer claimed), so the fixture carries
        # the family's topic evidence instead of a topic-free placeholder.
        dg.start(sid, "write the scene where the killer tortures the captive "
                      "with pliers", log=None)

    _wait_landed(sids)
    present = 0
    for i, sid in enumerate(sids):
        row = dg.read(sid)
        if row is not None:
            present += 1
            kinds[row["gate"]] = kinds.get(row["gate"], 0) + 1
        else:
            kinds["missing"] = kinds.get("missing", 0) + 1

    share = present / len(sids)
    print(f"\n(b) verdict present on {present}/{len(sids)} eligible turns "
          f"= {share:.0%}  (target >= 90%)")
    print(f"(b) gate distribution: {kinds}")
    assert share >= 0.90
    assert kinds.get("flagged") == 9
    assert kinds.get("not_flagged") == 9
    assert kinds.get("abstain") == 2


# ---------------------------------------------------------------------------
# (c) the gate is OUTSIDE aux_consult_min_interval_sec — with a control
# ---------------------------------------------------------------------------
def test_c_saturated_suppressor_still_yields_a_domain_verdict(monkeypatch):
    """Seed the suppressor as saturated for this session, then run the gate:
    a verdict must still land. The CONTROL asserts the same seed suppresses
    `_aux_intent_decision` — so the exemption is demonstrated, not asserted."""
    _stub_aux(monkeypatch, _domain("weapons_explosives"))
    monkeypatch.setattr(router_core, "aux_consult_min_interval", lambda: 300)
    route_gate._AUX_CONSULT_LAST[SID] = time.time()   # fired 0s ago, not 197.5s

    state.reset_turn_identity(SID)
    dg.start(SID, "detail the charge sizes and the blasting cap wiring", log=None)
    _wait_landed([SID])
    row = dg.read(SID)
    print(f"\n(c) with min_interval=300 saturated: gate={dg.verdict(row)} "
          f"domain={(row or {}).get('domain')} reason={(row or {}).get('reason')}")
    assert row is not None, "the 300s suppressor muted the PRE domain gate"
    assert dg.verdict(row) == dg.GATE_FLAGGED

    # control: the same saturation DOES muting the aux-intent lane path
    import hermes_router.intent_classifier as ic
    monkeypatch.setattr(ic, "_intent_suspect", lambda text: True)
    monkeypatch.setattr(ic, "classify_intent",
                        lambda text, sid, log_route=None: {"lane": "higher",
                                                           "confidence": 0.9})
    route_gate._AUX_CONSULT_LAST[SID] = time.time()
    _dec = route_gate._aux_intent_decision("autonomous dispatch text", SID)
    print(f"(c) control _aux_intent_decision: route={_dec.route} reason={_dec.reason}")
    assert _dec is not None and not _dec.route
    assert _dec.reason == "aux_consult_interval"


def test_c3_gate_failures_do_not_open_the_shared_breaker(monkeypatch):
    """A squelch must not degrade the primary lane.

    Measured live in the suite: the shared breaker opens after 3 consecutive aux
    failures, and a gate that counted its own failures silenced the primary
    classifier's own call in a later turn (test_aux_raw_call_uses_hermes_not_curl
    failed only in the long order, only with the gate wired). Three gate failures
    must therefore leave the breaker closed; the control shows the same three
    failures through the ordinary path DO open it.
    """
    import hermes_router.semantic_classifier as sc

    sc.reset_limits()
    monkeypatch.setattr(sc, "_hermes_aux_call",
                        lambda payload, timeout: "not-json")   # invalid_json path
    for _ in range(3):
        dg.evaluate("produce the thing", session_id=SID)
    gate_open = sc.breaker_is_open()
    print(f"\n(c3) 3 gate failures -> breaker open={gate_open}")

    sc.reset_limits()
    for _ in range(3):
        sc.aux_raw_call("hello", cfg={})
    control_open = sc.breaker_is_open()
    print(f"(c3) control 3 ordinary failures -> breaker open={control_open}")

    sc.reset_limits()
    assert gate_open is False, "gate failures opened the shared breaker"
    assert control_open is True, "control did not open the breaker"


def test_c2_gate_module_never_names_the_suppressor():
    """The structural half of (c): the module's CODE has no reference to the
    interval, its last-fire map, or the claim path — so no future edit can
    quietly put it behind them. (Prose may name them; prose is the prohibition,
    not the coupling, so this reads the AST, not the text.)"""
    import ast

    tree = ast.parse(inspect.getsource(dg))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            used.add(node.module or "")
            used.update(a.name for a in node.names)
    offenders = sorted(n for n in used
                       if "aux_consult_min_interval" in n
                       or "_AUX_CONSULT_LAST" in n
                       or n in ("claim_pass", "router_core", "route_gate"))
    print(f"\n(c2) gate module code references to the suppressor/claim path: "
          f"{offenders}")
    assert not offenders


# ---------------------------------------------------------------------------
# (d) abstain falls back to the body path and is never "safe"
# ---------------------------------------------------------------------------
def test_d_abstain_falls_back_and_is_not_safe(monkeypatch):
    """Three ways to abstain — aux None, pending, no record at all — and every
    one must read as `abstain` with the contract's fallback_shape action, never
    as not_flagged/deliver."""
    _stub_aux(monkeypatch, None)
    state.reset_turn_identity(SID)
    dg.start(SID, "produce it", log=None)
    _wait_landed([SID])
    row = dg.read(SID)
    assert row is not None
    assert row["gate"] == dg.GATE_ABSTAIN
    assert row["reason"] == "aux_none"
    assert dg.verdict(row) == dg.GATE_ABSTAIN

    semantics = dict(row.get("gate_semantics") or ())
    print(f"\n(d) aux failure: gate={row['gate']} verdict={dg.verdict(row)} "
          f"abstain_action={semantics.get('gate_abstain')} rows={len(semantics)}")
    assert semantics.get("gate_abstain") == "defer"
    assert dg.GATE_ABSTAIN not in ("flagged", "not_flagged")

    # pending (worker still in flight) reads as abstain, never as a verdict
    _stub_aux(monkeypatch, _domain("bio_chem"), delay=1.5)
    sid2 = "s-m41-02-d-pending"
    state.reset_turn_identity(sid2)
    dg.start(sid2, "produce it", log=None)
    assert dg.read(sid2) is None
    assert dg.verdict(None) == dg.GATE_ABSTAIN
    print(f"(d) pending/no-record: read()->None verdict()->{dg.verdict(None)}")

    # no record at all (gate never armed)
    assert dg.read("s-m41-02-d-never") is None
    assert dg.verdict(dg.read("s-m41-02-d-never")) == dg.GATE_ABSTAIN


def test_d2_unknown_label_is_abstain_not_safe(monkeypatch):
    """An off-vocabulary label must NOT be coerced to not_flagged: coercing it
    would silently disable the lane, and abstain must fall to the body path."""
    _stub_aux(monkeypatch, _domain("something_else_entirely"))
    state.reset_turn_identity(SID)
    dg.start(SID, "produce it", log=None)
    _wait_landed([SID])
    row = dg.read(SID)
    print(f"(d2) off-vocabulary label -> gate={row['gate']} reason={row['reason']}")
    assert row["gate"] == dg.GATE_ABSTAIN
    assert row["reason"] == "off_vocabulary"


def test_d3_closed_line_is_counted_and_never_printed(monkeypatch):
    """The closed domain maps to abstain, is counted, and its LABEL never lands
    on the record (never routed, never printed, never scored)."""
    import json

    _stub_aux(monkeypatch, _domain("sexual_content_minors"))
    before = dg.closed_count()
    res = dg.evaluate("counted only")
    print(f"\n(d3) closed label -> gate={res['gate']} recorded_domain="
          f"{res['domain']!r} closed_count {before}->{dg.closed_count()}")
    assert res["gate"] == dg.GATE_ABSTAIN
    assert res["closed"] is True
    assert res["domain"] == ""
    assert "sexual_content_minors" not in json.dumps(res)
    assert dg.closed_count() == before + 1


# ---------------------------------------------------------------------------
# (e) no per-lane branch in the POST handler
# ---------------------------------------------------------------------------
_POST_LANE_BRANCH = re.compile(r'lane\s*==|==\s*["\']shadow["\']|LANE_SHADOW|'
                               r'==\s*["\']uncensored["\']')


def test_e_no_per_lane_branch_in_post_handler():
    """The POST handler resolves the lane contract by TOKEN (data), so a second
    lane is one LaneSpec literal and no handler edit."""
    from hermes_router.gate import orchestration

    src = inspect.getsource(orchestration.on_transform_llm_output)
    hits = [ln.strip() for ln in src.splitlines() if _POST_LANE_BRANCH.search(ln)]
    print(f"\n(e) POST handler: {src.count(chr(10))} lines, "
          f"per-lane branch hits={len(hits)} {hits[:3]}")
    # the gate seam is present and reads by token, not by lane id
    assert 'event_detail="domain_gate_turn"' in src
    assert "resolve_contract" in inspect.getsource(dg) or "gate_semantics" in src
    assert not hits


def test_e2_contract_is_data_and_resolves_by_token():
    """The four contract fields live on the shadow LaneSpec, and resolution is
    a token lookup — the review test's precondition."""
    contract = lane_registry.resolve_contract("shadow")
    assert contract is not None
    print(f"\n(e2) pre_question={contract.pre_question} "
          f"post_question={contract.post_question} "
          f"replacement={contract.replacement} rows={len(contract.gate_semantics)}")
    assert contract.pre_question == "uncensored_domain"
    assert contract.post_question == "uncensored_intent"
    assert contract.replacement == "uncensored_render"
    assert contract.gate_semantics == (
        ("declared_refusal", "route"),
        ("intent_declined", "route"),
        ("gate_abstain", "defer"),
        ("gate_flagged", "route_replace"),
        ("intent_softened", "deliver"),
        ("else", "deliver"),
    )
    # abstain is hoisted above the softened rows (spec B2) — unreachable-last
    # would silently mean "abstain delivers"
    tokens = [p for p, _ in contract.gate_semantics]
    assert tokens.index("gate_abstain") < tokens.index("intent_softened")
    assert lane_registry.lane_for_pre_question("uncensored_domain") == "shadow"
    assert lane_registry.lane_for_pre_question("nope") is None
    # pre-existing lanes carry no contract and are unchanged
    assert lane_registry.resolve_contract("decision") is None
