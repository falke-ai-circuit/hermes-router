"""R19.14 HOTFIX (Goran-directed, live-testing findings; user mid-testing
with reviewer).

FIX 1 (P1): declared frontier consults route WITHOUT aux — aux is never a
dependency for declared asks (reviewer log 2026-09-29T15:40:32-15:41:27:
declared_intent_no_route x3 + intent_aux_error x3, nothing routed).
FIX 2 (P2): a declared ask that fails to route is VISIBLE (parked failure
banner), never a silent death.
FIX 3 (P3): reflex banner cost — MEASURED fallback pricing for the
OpenRouter-hosted typesafe/jev-router ($0.042/1M in, 0 out, marked
source=measured); est_cost > 0.
"""
from hermes_router import route_gate as RG
from hermes_router import usage_ledger as UL


# --- FIX 1: declared frontier hit, no aux dependency --------------------------

def test_declared_frontier_hit_strict_standalone():
    assert RG.declared_frontier_hit("consult frontier")
    assert RG.declared_frontier_hit("ask frontier")
    assert RG.declared_frontier_hit("challenge this")
    assert RG.declared_frontier_hit("am i missing something")


def test_declared_frontier_hit_payload_without_separator():
    """THE regression shape: phrase at line start + payload words, no
    separator — the strict table misses it, aux must never be required."""
    assert RG.declared_frontier_hit("consult frontier about the migration")
    assert RG.declared_frontier_hit("ask frontier her consult")


def test_declared_frontier_hit_echo_guard():
    """Line-start discipline kept: prose/quoted mentions stay inert."""
    assert not RG.declared_frontier_hit(
        "he said 'consult frontier' to the intern")
    assert not RG.declared_frontier_hit("we should consult frontiersmen")
    assert not RG.declared_frontier_hit(None)


def test_declared_phrase_routes_despite_aux_error(monkeypatch):
    """PIN (the live failure): declared phrase + aux returning error =>
    route still fires (declared_user, LANE_HIGHER_PRE)."""
    import hermes_router.intent_classifier as IC

    monkeypatch.setattr(RG, "on_demand_routing_enabled", lambda: True)
    monkeypatch.setattr(RG, "on_demand_aux_classify_enabled", lambda: True)
    monkeypatch.setattr(IC, "_intent_suspect", lambda content: True)
    monkeypatch.setattr(IC, "classify_intent",
                        lambda *a, **k: None)  # aux transport ERROR
    registered = []
    monkeypatch.setattr(RG, "register_declared",
                        lambda sid, lane, src: registered.append((lane, src)))
    decision = RG.decide_turn({"content": "consult frontier about the schema",
                               "session_id": "s-hotfix", "auto_shape": None})
    assert decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE
    assert decision.source == RG.SOURCE_DECLARED_USER
    assert registered  # claim registered -> claim_pass executes it


def test_aux_error_with_declared_hit_routes(monkeypatch):
    """The _aux_intent_decision fallback: aux error + declared frontier hit
    => declared route, not no-route. (decide_turn only reaches aux on the
    suspect heuristic — patch it on, as the live gate would.)"""
    import hermes_router.intent_classifier as IC
    monkeypatch.setattr(IC, "_intent_suspect", lambda content: True)
    decision = RG._aux_intent_decision("challenge this", "s-hotfix2")
    assert decision is not None
    assert decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE


def test_aux_error_without_declared_hit_stays_inert(monkeypatch):
    """Undeclared asks keep legacy behavior: aux error -> None (inert)."""
    assert RG._aux_intent_decision("some unrelated heavy ask here",
                                   "s-hotfix3") is None


# --- FIX 2: failed declared ask is visible -------------------------------------

def test_failed_declared_ask_parks_visible_banner(monkeypatch):
    """A declared ask that reaches the end of the gate unrouted parks the
    one-line failure banner (banner record) — never silent."""
    parked = []
    import hermes_router.debug_banner as dbg
    monkeypatch.setattr(RG, "on_demand_routing_enabled", lambda: True)
    # kill every routing path: declared resolver misses, claim registration
    # FAILS (the fail-open fall-through), suspect heuristic misses so aux
    # never fires, no auto-shape -> gate ends unrouted WITH a declared hit.
    monkeypatch.setattr(RG, "_declared_decision",
                        lambda content, sid: RG.NO_ROUTE)
    monkeypatch.setattr(RG, "register_declared",
                        lambda sid, lane, src: (_ for _ in ()).throw(
                            RuntimeError("claim registry down")))
    monkeypatch.setattr(RG, "declared_frontier_hit",
                        lambda content: True)
    monkeypatch.setattr(dbg, "park_anchor_banner",
                        lambda sid, text: parked.append((sid, text)))
    decision = RG.decide_turn({"content": "challenge this",
                               "session_id": "s-hotfix4",
                               "auto_shape": None})
    assert decision.route is False  # unrouted (every path killed)
    assert parked, "failure banner must be parked (visible marker)"
    sid, text = parked[-1]
    assert sid == "s-hotfix4"
    assert "consult FAILED" in text and "higher-self (frontier)" in text


def test_undeclared_ask_stays_silent(monkeypatch):
    """Provenance scope: only DECLARED asks get the failure marker."""
    parked = []
    import hermes_router.debug_banner as dbg
    monkeypatch.setattr(RG, "on_demand_routing_enabled", lambda: True)
    monkeypatch.setattr(dbg, "park_anchor_banner",
                        lambda sid, text: parked.append(text))
    RG.decide_turn({"content": "fix the typo in the README please",
                    "session_id": "s-hotfix5", "auto_shape": None})
    assert not parked


# --- FIX 3: reflex banner cost --------------------------------------------------

def test_jev_pricing_measured_fallback(monkeypatch):
    """typesafe/jev-router (openrouter): measured $0.042/1M in, 0 out."""
    cost = UL.estimate_cost("typesafe/jev-router", 1_000_000, 1000)
    assert cost == 0.042
    assert UL.estimate_cost("typesafe/jev-router", 100_000, 58) == 0.0042


def test_unknown_model_stays_zero():
    """No fabricated pricing: unknown models still 0.0."""
    assert UL.estimate_cost("unknown-model-x", 100_000, 100) == 0.0


def test_reflex_banner_cost_positive(monkeypatch):
    """The reflex verdict banner path (decision._format_banner est_cost)
    computes a NONZERO cost for jev consults."""
    import hermes_router.decision as D
    import hermes_router.debug_banner as dbg
    monkeypatch.setattr(UL, "_anchor_pricing_present", False, raising=False)
    cost = UL.estimate_cost("typesafe/jev-router", 1378, 58)
    assert cost > 0
    # banner text renders the estimate
    banner = dbg.format_banner(lane="decision", trigger="manual",
                               model="typesafe/jev-router", endpoint="",
                               tokens_in=1378, tokens_out=58, est_cost=cost,
                               latency_s=4.0, retries=0, task_id="",
                               session_id="s")
    # the estimate renders in the banner (nonzero, measured pricing):
    # 1378/1M * $0.042 = $0.000058
    assert "$0.000058" in banner
