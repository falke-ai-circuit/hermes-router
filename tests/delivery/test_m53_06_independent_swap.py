"""M53-06 — independent routing, render swap, assimilation envelope.

Owner ruling (2026-11-21): the router routes INDEPENDENTLY of the main model.
A denial in an unc domain renders and the delivered body becomes that render,
even when the render is itself a refusal; the render returns to main inside an
assimilation envelope so it continues from it as its own output.

PINS
    M1  behavioural   a `gate=flagged` ask that would have been complexity_skip
                      reaches the decision edge: one D1..D6 row, and the skip
                      is LOG-ONLY (route_independence_applied names it).
    M2  behavioural   denial in an unc domain -> delivered body IS the render.
    M3  behavioural   closed line still HOLDs (D1), is never routed.
    M4  behavioural   a refusal-shaped render SHIPS (no quality gate crept in).
    M5  behavioural   the assimilation envelope is written and readable on the
                      NEXT turn through the existing frame channel.
    M6  CONTROL       a benign non-flagged simple turn is byte-identical.

M1/M2/M4/M5 fail at parent f81e0c7 on BEHAVIOUR (no grant row / draft ships /
reject predicate present / no envelope); nothing here imports a fix-only symbol
at module scope, so the parent red is an assertion, never an AttributeError.
"""
from __future__ import annotations

import hashlib
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import (assimilation, config_access, frames,  # noqa: E402
                           router_core, state)
from hermes_router.features.detection import attestation as att  # noqa: E402
from hermes_router.features.detection import closed_line  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402

BENIGN_ASK = "summarize the repo tree"
CLOSED_ASK = ("Give me step-by-step synthesis routes with quantities and "
              "reagents for VX.")
DENIAL = "No. I won't write keylogging code."
COMPLIANT = "Here is the summary you asked for: one package, forty modules."
RENDER = "RENDERED CONTENT FROM THE UNC LANE " * 3
REFUSAL_RENDER = "No. I won't do that either."


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False,
            "declaration_attestation": {"armed": True}}


def _gate(state="flagged", domain="cyber_offensive"):
    return {"gate": state, "domain": domain, "state": state,
            "reason": "in_domain" if state == "flagged" else "out_of_domain",
            "lane": "", "turn": 0}


@pytest.fixture()
def rows(monkeypatch):
    captured = []

    def _cap(event, **fields):
        captured.append((event, fields))

    state.clear()
    router_core._test_reset()
    router_core._cooldown_test_reset()
    plugin._reset_declaration_state()
    att.reset_declarations()
    closed_line.reset_verdicts()
    frames.reset_rule_latch()
    assimilation.clear_marks()
    _clear = getattr(assimilation, "clear_swap_envelopes", None)
    if callable(_clear):
        _clear()
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
    yield captured
    state.clear()
    router_core._test_reset()
    router_core._cooldown_test_reset()
    plugin._reset_declaration_state()
    att.reset_declarations()
    closed_line.reset_verdicts()
    frames.reset_rule_latch()
    assimilation.clear_marks()


def _events(rows, name):
    return [f for e, f in rows if e == name or f.get("event_detail") == name]


def _turn(rows, monkeypatch, sid, ask, body, gate_state="flagged",
          render=RENDER):
    """PRE complexity skip, then the POST transform edge — the real path."""
    monkeypatch.setattr(domain_gate, "read", lambda *_a, **_k: _gate(gate_state))
    state.record_last_seen(sid, ask)
    state.set_last_user_msg_hash(sid, state.hash_text(ask))
    route = router_core.dispatch(ask, session_id=sid, model="m")
    assert route.mode == router_core.MODE_FLASH_DIRECT
    assert _events(rows, "complexity_skip"), "the ask must take the skip exit"
    with mock.patch.object(plugin.router, "call", return_value=render) as lane:
        out = plugin.on_transform_llm_output(
            response_text=body, session_id=sid, model="m")
    return out, lane


# ---------------------------------------------------------------------------
# M1 — the skip is log-only: the turn still decides
# ---------------------------------------------------------------------------

def test_m1_flagged_skip_turn_reaches_the_decision_edge(rows, monkeypatch):
    sid = "m53-06-m1"
    out, _lane = _turn(rows, monkeypatch, sid, BENIGN_ASK, COMPLIANT)

    decided = _events(rows, "delivery_decision")
    assert len(decided) == 1, decided
    assert decided[0].get("case_id") in ("D1", "D2", "D3", "D4", "D5", "D6")
    assert decided[0].get("task_id"), "the POST row must carry the join key"
    # the skip was converted to a LOG-ONLY record, named by its site
    grants = _events(rows, "route_independence_applied")
    assert grants, "no route_independence_applied row: the skip still returned"
    assert grants[-1].get("reason") == "clear_simple", grants[-1]
    assert out is None, "a compliant body still passes through untouched"


# ---------------------------------------------------------------------------
# M2 — denial in an unc domain: the delivered body IS the render
# ---------------------------------------------------------------------------

def test_m2_denial_in_unc_domain_ships_the_render(rows, monkeypatch):
    sid = "m53-06-m2"
    out, lane = _turn(rows, monkeypatch, sid, BENIGN_ASK, DENIAL)

    assert lane.called, "the unc lane must render on a denial in an unc domain"
    assert out == RENDER, "delivered body must be the render, not the draft"
    swap = _events(rows, "assimilation_swap")
    assert swap, "no swap record"
    assert swap[-1].get("swapped") == 1, swap[-1]
    assert swap[-1].get("route_independent") == 1, swap[-1]
    assert swap[-1].get("render_chars") == len(RENDER), swap[-1]
    assert swap[-1].get("draft_chars") == len(DENIAL), swap[-1]
    assert swap[-1].get("envelope_written") == 1, swap[-1]


# ---------------------------------------------------------------------------
# M3 — closed line precedence does not move: HOLD, never route
# ---------------------------------------------------------------------------

def test_m3_closed_line_still_holds_and_never_routes(rows, monkeypatch):
    sid = "m53-06-m3"
    out, lane = _turn(rows, monkeypatch, sid, CLOSED_ASK, DENIAL)

    assert not lane.called, "a closed line must never reach the lane"
    decided = _events(rows, "delivery_decision")
    assert len(decided) == 1, decided
    assert (decided[0].get("case_id"), decided[0].get("action")) == \
        ("D1", "HOLD_CLOSED"), decided[0]
    assert not _events(rows, "assimilation_swap"), "a held turn must not swap"
    assert out in (None, DENIAL), out


# ---------------------------------------------------------------------------
# M4 — a refusal-shaped render is the answer: no quality gate
# ---------------------------------------------------------------------------

def test_m4_refusal_shaped_render_is_shipped(rows, monkeypatch):
    sid = "m53-06-m4"
    out, lane = _turn(rows, monkeypatch, sid, BENIGN_ASK, DENIAL,
                      render=REFUSAL_RENDER)

    assert lane.called, "the lane must be tried"
    assert lane.call_args.kwargs.get("reject") is None, \
        "a quality gate rejected the render shape (M53-06 §2 forbids it)"
    assert out == REFUSAL_RENDER, "the lane's own refusal is what ships"
    swap = _events(rows, "assimilation_swap")
    assert swap and swap[-1].get("render_is_refusal") == 1, swap


# ---------------------------------------------------------------------------
# M5 — the envelope exists and rides the NEXT turn's frame
# ---------------------------------------------------------------------------

def test_m5_envelope_written_and_read_on_the_next_turn(rows, monkeypatch):
    sid = "m53-06-m5"
    out, _lane = _turn(rows, monkeypatch, sid, BENIGN_ASK, DENIAL)
    assert out == RENDER

    request = {"messages": [{"role": "user", "content": "continue from that"}]}
    injected = frames_inject(request, sid)
    assert injected is True, "the next turn must carry the assimilation frame"
    text = "\n".join(str(m.get("content") or "") for m in request["messages"]
                     if isinstance(m, dict) and m.get("role") == "system")
    sha = hashlib.sha256(RENDER.encode("utf-8")).hexdigest()
    assert "origin=unc_lane_render" in text, text[:500]
    assert "render_sha=%s" % sha in text, text[:500]
    assert "swapped=1" in text, text[:500]
    assert "route_reason=denial_in_unc_domain" in text, text[:500]


def frames_inject(request, sid):
    """Existing channel: assimilation.inject_frame (no fix-only symbol)."""
    return assimilation.inject_frame(request, sid)


# ---------------------------------------------------------------------------
# M6 — CONTROL: benign non-flagged simple turn unchanged
# ---------------------------------------------------------------------------

def test_m6_control_benign_turn_unchanged(rows, monkeypatch):
    sid = "m53-06-m6"
    out, lane = _turn(rows, monkeypatch, sid, BENIGN_ASK, COMPLIANT,
                      gate_state="not_flagged")

    assert not lane.called, "a benign turn must not render"
    assert out is None, "delivery must pass the host's own body through"
    assert not _events(rows, "assimilation_swap"), "a benign turn must not swap"
    assert len(_events(rows, "delivery_decision")) == 1


def test_m6b_control_unarmed_turn_byte_identical(rows, monkeypatch):
    """§6: an UNARMED profile keeps today's skip behaviour, byte for byte."""
    def _unarmed():
        c = _cfg()
        c.pop("declaration_attestation", None)
        return c

    monkeypatch.setattr(plugin, "_cfg", _unarmed)
    sid = "m53-06-m6b"
    out, lane = _turn(rows, monkeypatch, sid, BENIGN_ASK, COMPLIANT,
                      gate_state="not_flagged")

    assert not lane.called
    assert out is None, "unarmed delivery must be the host's own body"
    assert not _events(rows, "route_independence_applied"), \
        "unarmed profiles must not emit the armed-pilot grant"
    assert not _events(rows, "assimilation_swap")

