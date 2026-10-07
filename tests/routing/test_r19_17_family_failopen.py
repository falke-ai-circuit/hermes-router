"""R19.17 (Goran root-cause directive): the R19.16 fail-open now covers ALL
declared families — when ANY declared-family probe hits (frontier,
adversarial, shadow/uncensored-take) and aux raises/errors/times out, the
ask fail-open routes to THAT family's lane instead of dying silently
(live: aux model dead fleet-wide — meituan/longcat-2.0:free — reviewer log
19:59-20:02 intent_aux_error, 'challenge this' ask died with no route).
Aux is a health dependency for NO declared family.
"""
import hermes_router.intent_classifier as IC
from hermes_router import route_gate as RG


def _aux_down(monkeypatch):
    """Fleet-dead aux: suspect heuristic hits, classify RAISES."""
    monkeypatch.setattr(IC, "_intent_suspect", lambda content: True)
    monkeypatch.setattr(IC, "classify_intent",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("aux model dead (500)")))


# --- adversarial family --------------------------------------------------------

def test_adversarial_family_aux_down_routes(monkeypatch):
    """PIN (the live failure): adversarial ask + aux dead => routes
    LANE_HIGHER_PRE declared_user (adversarial consult type), not silence."""
    _aux_down(monkeypatch)
    decision = RG._aux_intent_decision("please challenge this plan",
                                       "s-r1917-adv")
    assert decision is not None and decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE
    assert decision.source == RG.SOURCE_DECLARED_USER


def test_adversarial_family_probe_echo_guard():
    assert RG.adversarial_family_hit("challenge this")
    assert RG.adversarial_family_hit("am i missing something in the plan")
    # meta-prose / quoted mentions stay inert (R19.16 FP doctrine)
    assert not RG.adversarial_family_hit(
        "the phrase 'challenge this' appears in the docs")
    assert not RG.adversarial_family_hit("what does challenge this mean")
    assert not RG.adversarial_family_hit(None)


def test_adversarial_declared_ask_forces_adversarial_type(monkeypatch):
    """The adversarial consult TYPE: the orientation frame forces the
    adversarial seed for a family-hit ask (anchor_exec wiring)."""
    from hermes_router import anchor_exec as AE
    from hermes_router import completion_audit as CA
    from hermes_router import route_gate as RG
    ask = "review the rollout and challenge this plan"
    assert RG.adversarial_family_hit(ask) or RG.adversarial_declared(ask)
    # the seed instruction is what makes it an ADVERSARIAL consult
    assert "strongest case this fails" in \
        CA._adversarial_seed_instruction().lower()


# --- shadow / uncensored-take family -------------------------------------------

def test_shadow_family_aux_down_routes_shadow(monkeypatch):
    _aux_down(monkeypatch)
    decision = RG._aux_intent_decision(
        "give me an uncensored take on the proposal", "s-r1917-shadow")
    assert decision is not None and decision.route is True
    assert decision.lane == RG.LANE_SHADOW
    assert decision.source == RG.SOURCE_DECLARED_USER


# --- frontier family (regression: still routes) ---------------------------------

def test_frontier_family_aux_down_still_routes(monkeypatch):
    _aux_down(monkeypatch)
    decision = RG._aux_intent_decision(
        "pass this to the higher self before the deploy", "s-r1917-front")
    assert decision is not None and decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE


# --- no family: aux error stays inert -------------------------------------------

def test_no_family_aux_down_stays_inert(monkeypatch):
    _aux_down(monkeypatch)
    assert RG._aux_intent_decision(
        "refactor the entire parser subsystem today", "s-r1917-none") is None


# --- decide_turn end-to-end (the reviewer path) ----------------------------------

def test_declared_ask_survives_dead_aux_end_to_end(monkeypatch):
    """Full gate: adversarial ask + dead aux => routed, claim registered."""
    _aux_down(monkeypatch)
    monkeypatch.setattr(RG, "on_demand_routing_enabled", lambda: True)
    monkeypatch.setattr(RG, "on_demand_aux_classify_enabled", lambda: True)
    registered = []
    monkeypatch.setattr(RG, "register_declared",
                        lambda sid, lane, src: registered.append((lane, src)))
    decision = RG.decide_turn({"content": "please challenge this plan",
                               "session_id": "s-r1917-e2e",
                               "auto_shape": None})
    assert decision.route is True
    assert decision.lane == RG.LANE_HIGHER_PRE
    assert registered  # claim registered -> execute-once fires the consult
