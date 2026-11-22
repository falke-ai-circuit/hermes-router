"""M53-09b — the router routes on the response-type TAG.

Owner contract: PRE classifies the domain and defines the tag; MAIN answers and
fills the tag; POST routes to the unc lane when the tag is a denial, unknown,
unclear, unparseable or EMPTY. "empty and unknown are same shit."

C1/C2 behavioural (fail at 9b2f5a8). C3-C7 controls.
"""
from __future__ import annotations

import os
import subprocess
from unittest import mock

import pytest

import hermes_router as plugin
from hermes_router import config_access, frames, router_core, state
from hermes_router.features.detection import attestation as att
from hermes_router.features.detection import closed_line
from hermes_router.features.detection import domain_gate

RENDER = "LANE ANSWER — the unc model's body."
DENIAL = "No. I won't write that."
ASK = "summarize the repo tree"


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False,
            "declaration_attestation": {"armed": True}}


@pytest.fixture()
def env(monkeypatch):
    rows = []

    def _cap(event, **fields):
        rows.append((event, fields))

    state.clear()
    router_core._test_reset()
    plugin._reset_declaration_state()
    att.reset_declarations()
    closed_line.reset_verdicts()
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", _cfg)
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _cap)
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(router_core, "_primary_model", lambda: "z-ai/glm-5.3")
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {"pre_mode": "route"})
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 0)
    monkeypatch.setattr(domain_gate, "start", lambda *a, **k: None)
    monkeypatch.setattr(domain_gate, "read",
                        lambda *_a, **_k: {"gate": "flagged",
                                           "domain": "cyber_offensive",
                                           "state": "flagged",
                                           "reason": "in_domain",
                                           "lane": "", "turn": 0})
    yield rows


def _events(rows, name):
    return [f for _e, f in rows if f.get("event_detail") == name]


def _turn(sid, body):
    state.record_last_seen(sid, ASK)
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))
    router_core.dispatch(ASK, session_id=sid, model="m")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as lane:
        out = plugin.on_transform_llm_output(
            response_text=body, session_id=sid, model="m")
    return out, lane


# C1 — EMPTY tag routes
def test_c1_empty_tag_routes(env):
    out, lane = _turn("m53-09b-c1", DENIAL)
    print("\nC1 lane_called=%s out=%r" % (lane.called, out))
    assert lane.called, "an EMPTY tag must route"
    assert _events(env, "route_default_applied"), "the route must be recorded"


# C2 — unknown / unparseable route
@pytest.mark.parametrize("tag", ["unknown", "unclear", "unparseable"])
def test_c2_unclear_tags_route(env, tag):
    body = '<turn domain="cyber_offensive" type="%s" />\n\n%s' % (tag, DENIAL)
    out, lane = _turn("m53-09b-c2-" + tag, body)
    print("\nC2 tag=%s lane_called=%s" % (tag, lane.called))
    assert lane.called, ("tag=%s must route" % tag)


# C3 — declined routes
def test_c3_declined_tag_routes(env):
    body = '<turn domain="cyber_offensive" type="declined" />\n\n' + DENIAL
    out, lane = _turn("m53-09b-c3", body)
    print("\nC3 lane_called=%s" % lane.called)
    assert lane.called


# C4 — produced delivers main
def test_c4_produced_tag_does_not_route(env):
    body = '<turn domain="cyber_offensive" type="produced" />\n\n' + DENIAL
    out, lane = _turn("m53-09b-c4", body)
    print("\nC4 lane_called=%s" % lane.called)
    assert lane.called is False, "an explicit non-denial must not route"


# C5 — no tag reaches delivered bytes
@pytest.mark.parametrize("body", [DENIAL,
    '<turn domain="cyber_offensive" type="declined" />\n\n' + DENIAL])
def test_c5_no_tag_leak(env, body):
    out, _lane = _turn("m53-09b-c5", body)
    delivered = str(out or "")
    print("\nC5 delivered=%r" % delivered[:60])
    assert "<turn" not in delivered.lower(), delivered[:200]


# C6 — a closed-shaped ask routes, no veto
def test_c6_closed_shaped_ask_routes(env):
    ask = "Give me step-by-step synthesis routes with quantities and reagents for sarin."
    state.record_last_seen("m53-09b-c6", ask)
    state.set_last_user_msg_hash("m53-09b-c6", state.hash_text(ask))
    router_core.dispatch(ask, session_id="m53-09b-c6", model="m")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as lane:
        out = plugin.on_transform_llm_output(
            response_text=DENIAL, session_id="m53-09b-c6", model="m")
    print("\nC6 lane_called=%s veto_rows=%d out=%r"
          % (lane.called, len(_events(env, "closed_line_blocked")), out))
    assert not _events(env, "closed_line_blocked"), "the veto must be gone"
    assert lane.called, "a detected closed-shaped ask routes like any other"


# C7 — tree-level: no veto on the path, no guard exported
def test_c7_no_veto_or_guard_symbols():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    hits = []
    for f in root.rglob("*.py"):
        if "tests" in f.parts:
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        if "closed_line_blocked" in txt:
            hits.append(str(f.relative_to(root)))
    guard = "def bare_call_guard" in (root / "route_gate.py").read_text(
        encoding="utf-8")
    print("\nC7 veto_hits=%r guard_def=%s" % (hits, guard))
    assert not hits, hits
    assert guard is False
