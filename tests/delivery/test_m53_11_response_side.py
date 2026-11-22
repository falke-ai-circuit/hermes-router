"""M53-11 — response-side detection is the backbone.

The router asks ONE question: did the final reply deny, soften, or come back
unknown? It reads the model's own type tag, never the ask, so a Croatian,
German or Turkish refusal fires on exactly the same path as an English one.

Domain must ALSO be unc for a route; an unknown domain routes nothing.

T1-T3, T5-T7 behavioural (fail at r61 HEAD); T4, T8 controls; D1/D2 defect pins.
"""
from __future__ import annotations

from unittest import mock

import pytest

import hermes_router as plugin
from hermes_router import config_access, frames, router_core, state
from hermes_router.features.detection import attestation as att
from hermes_router.features.detection import domain_gate

RENDER = "LANE RENDER — the unc model's body."
ASK = "summarize the repo tree"

TAG = '<turn domain="cyber_offensive" type="%s" />\n\n'


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False,
            "declaration_attestation": {"armed": True}}


def _gate(state_="flagged", domain="cyber_offensive"):
    if state_ is None:
        return None
    return {"gate": state_, "domain": domain, "state": state_,
            "reason": "in_domain" if state_ == "flagged" else "out_of_domain",
            "lane": "", "turn": 0}


@pytest.fixture()
def turn(monkeypatch):
    rows = []

    def _cap(event, **fields):
        rows.append((event, fields))

    def _run(body, sid, gate=_gate()):
        state.clear()
        router_core._test_reset()
        plugin._reset_declaration_state()
        att.reset_declarations()

        frames.reset_rule_latch()
        monkeypatch.setattr(plugin, "_cfg", _cfg)
        monkeypatch.setattr(plugin, "_dry_run", lambda: False)
        monkeypatch.setattr(plugin, "_log_route", _cap)
        monkeypatch.setattr(config_access, "router_section", lambda: {})
        monkeypatch.setattr(router_core, "_primary_model", lambda: "z-ai/glm-5.3")
        monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
        monkeypatch.setattr(router_core, "_complexity_cfg",
                            lambda: {"pre_mode": "route"})
        monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 0)
        monkeypatch.setattr(domain_gate, "start", lambda *a, **k: None)
        monkeypatch.setattr(domain_gate, "read", lambda *_a, **_k: gate)
        state.record_last_seen(sid, ASK)
        state.set_last_user_msg_hash(sid, state.hash_text(ASK))
        router_core.dispatch(ASK, session_id=sid, model="m")
        with mock.patch.object(plugin.router, "call",
                               return_value=RENDER) as lane:
            out = plugin.on_transform_llm_output(
                response_text=body, session_id=sid, model="m")

        # A route can be executed at TWO seams: the render swap here
        # (softening/unknown), or the staged swap the declined path fires
        # (`_att_route_fired` -> llm_execution). "Routed" means either.
        routed = bool(lane.called) or any(
            f.get("event_detail") == "route_fired"
            or int(f.get("attestation_route_fired") or 0) == 1
            or int(f.get("route_fired") or 0) == 1
            for _e, f in rows)
        return out, routed, rows

    return _run


# ---------------------------------------------------------------------------
# T1-T3 — denial / softening / unknown all route
# ---------------------------------------------------------------------------

def test_t1_english_denial_routes(turn):
    out, routed, _ = turn(TAG % "declined" + "No. I won't write that.", "s-t1")
    print("\nT1 routed=%s" % routed)
    assert routed, "a denial must route"


def test_t2_softened_answer_routes(turn):
    out, routed, _ = turn(TAG % "partial" + "I can describe some of it, but not the rest.",
                        "s-t2")
    print("\nT2 routed=%s" % routed)
    assert routed, "a softened answer must route"


def test_t3_empty_tag_routes(turn):
    out, routed, _ = turn("No. I won't write that.", "s-t3")
    print("\nT3 routed=%s" % routed)
    assert routed, "an unknown/empty tag must route"


# ---------------------------------------------------------------------------
# T4 / T8 — compliance ships main
# ---------------------------------------------------------------------------

def test_t4_compliant_answer_no_route(turn):
    out, routed, _ = turn(TAG % "produced" + "Here is the summary you asked for.",
                        "s-t4")
    print("\nT4 routed=%s" % routed)
    assert not routed, "a compliant body must not route"


def test_t8_benign_cheerful_compliance_no_route(turn):
    out, routed, _ = turn(TAG % "produced" + "Absolutely! Here you go — happy to help.",
                        "s-t8")
    print("\nT8 routed=%s" % routed)
    assert not routed


# ---------------------------------------------------------------------------
# T5-T6 — the SAME path for other languages (tag is language-agnostic)
# ---------------------------------------------------------------------------

def test_t5_croatian_denial_routes(turn):
    out, routed, _ = turn(TAG % "declined" + "Ne. Neću to napisati.", "s-t5")
    print("\nT5 routed=%s" % routed)
    assert routed, "a Croatian denial must route on the same path"


def test_t6_german_denial_routes(turn):
    out, routed, _ = turn(TAG % "declined" + "Nein. Das schreibe ich nicht.", "s-t6")
    print("\nT6 routed=%s" % routed)
    assert routed, "a German denial must route on the same path"


# ---------------------------------------------------------------------------
# T7 — a refusal that names no domain
# ---------------------------------------------------------------------------

def test_t7_refusal_without_domain_vocabulary_routes(turn):
    out, routed, _ = turn(TAG % "declined" + "I'm not going to write this summary.",
                        "s-t7")
    print("\nT7 routed=%s" % routed)
    assert routed, "the response denied, so the turn routes"


# ---------------------------------------------------------------------------
# D1 — an unknown/abstained domain routes NOTHING
# ---------------------------------------------------------------------------

def test_d1b_closed_domain_never_routes(turn):
    out, routed, _ = turn(TAG % "declined" + "No.", "s-d1b",
                        gate=_gate("not_flagged", domain="sexual_content_minors"))
    print("\nD1b routed=%s" % routed)
    assert not routed


# ---------------------------------------------------------------------------
# D2 — no admissible lane ships the ORIGINAL draft, never nothing
# ---------------------------------------------------------------------------

def test_d2_no_admissible_lane_ships_the_draft():
    from hermes_router import core
    from hermes_router.core import delivery_decision as dd
    from hermes_router.gate import registration as reg

    draft = "the agent's own draft answer, 63 chars long for this pin ok."
    dec = dd.Decision(dd.FAIL_TERMINAL, dd.D6, dd.REASON_NO_LANE,
                      dd.SOURCE_NONE, 0, {}, None)
    got = reg._delivered_bytes(dec, draft)
    print("\nD2 delivered=%r" % (got,))
    assert got == draft, "the draft must ship byte-identical"


def test_m53_13_no_detected_domain_does_not_route(turn):
    """OWNER'S OTHER HALF: domain unknown => NOTHING happens.

    No gate row, so no unc label: the same denial that routes above must not
    route here. This is the pin the old contract never had.
    """
    out, routed, rows = turn(TAG % "declined" + "No. I won't write that.",
                             "s-nodom", gate=None)
    print("\nM53-13 no-domain routed=%s out=%r" % (routed, out))
    assert routed is False, "no detected unc domain => no route"
