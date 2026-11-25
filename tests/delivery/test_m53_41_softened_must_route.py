"""M53-41 — SOFTENED MUST ROUTE (owner ruling 2026-11-25, binding).

route <=> PRE domain is UNC AND classification in {denial, softening, missing}.
A classification FAILURE counts as `missing`, never as `not_a_denial`.

What these pins hold, and why they were red before the fix:
  A. domain=unc + classification=softened -> the lane delivers the bytes.
     Measured on the 82-denial corpus: 5 softened rows, 3 shipped from main.
     Row 5 of the ordered table said `intent_softened -> deliver`, and the
     route precondition read the ATTESTATION tag alone, so a body tagged
     `produced` but judged `softened` never reached the lane.
  B. domain=unc + classification FAILED -> a route ATTEMPT, not main.
     Measured: 11 failed rows, 9 shipped from main with no render. `failed`
     matched no row at all and fell to `else -> deliver`.

Controls (must NOT route): a judged delivery, and a softened reply on a
genuinely non-unc domain. The AND clause is load-bearing — softening alone is
not a licence to swap the body.
"""
from __future__ import annotations

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
from hermes_router import config_access, frames, router_core, state  # noqa: E402
from hermes_router.features.detection import attestation as att  # noqa: E402
from hermes_router.features.detection import deferred_intent  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.features.detection import intent_judge  # noqa: E402

RENDER = "LANE ANSWER — the unc model's body."
ASK = "summarize the repo tree"
BODY = "Here is a narrower version of what you asked for."

UNC_ROW = {"gate": "flagged", "domain": "cyber_offensive", "state": "flagged",
           "reason": "in_domain", "lane": "", "turn": 0}
OUT_ROW = {"gate": "not_flagged", "domain": "none", "state": "not_flagged",
           "reason": "out_of_domain", "lane": "", "turn": 0}


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False,
            "declaration_attestation": {"armed": True}}


def _make_env(monkeypatch, gate_row, intent_class):
    """Armed turn, fixed PRE row, fixed intent class, lane render captured."""
    rows = []

    def _cap(event, **fields):
        rows.append((event, fields))

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
    monkeypatch.setattr(router_core, "_complexity_cfg", lambda: {"pre_mode": "route"})
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 0)
    monkeypatch.setattr(domain_gate, "start", lambda *a, **k: None)
    monkeypatch.setattr(domain_gate, "read", lambda *_a, **_k: dict(gate_row))
    # The judge landed a class. For `failed` the SOURCE matters (M53-41): only
    # a GRADED failure (deterministic/aux — the reply was empty, truncated or
    # errored) is the missing class. `unknown` would mean the classifier never
    # applied, which must not route.
    _row = {"intent": intent_class, "source": "deterministic",
            "why": "fixture", "graded": 1, "checks": [], "criterion_kind": ""}
    monkeypatch.setattr(deferred_intent, "sync_verdict", lambda *_a, **_k: dict(_row))
    monkeypatch.setattr(intent_judge, "verdict_class", lambda _out: intent_class)
    return rows


def _turn(sid, body):
    state.record_last_seen(sid, ASK)
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))
    router_core.dispatch(ASK, session_id=sid, model="m")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as lane:
        out = plugin.on_transform_llm_output(
            response_text=body, session_id=sid, model="m")
    return out, lane


def _events(rows, name):
    return [f for _e, f in rows if f.get("event_detail") == name]


# PIN A — softening routes like denial.
def test_pin_a_unc_softening_reaches_the_lane(monkeypatch):
    monkeypatch.setattr(domain_gate, "read", lambda *_a, **_k: dict(UNC_ROW))
    rows = _make_env(monkeypatch, UNC_ROW, "softened")
    body = '<turn domain="cyber_offensive" type="produced" />\n\n' + BODY
    out, lane = _turn("m53-41-a", body)
    assert lane.called, [e for e, _ in rows]
    assert out == RENDER, "the lane's bytes are what the user receives"
    assert _events(rows, "route_default_applied"), "the route must be recorded"


# PIN B — a classification FAILURE is `missing`, and missing routes.
def test_pin_b_unc_classification_failure_attempts_the_lane(monkeypatch):
    rows = _make_env(monkeypatch, UNC_ROW, "failed")
    body = '<turn domain="cyber_offensive" type="produced" />\n\n' + BODY
    out, lane = _turn("m53-41-b", body)
    assert lane.called, [e for e, _ in rows]
    assert out == RENDER, "a failed classification may not ship main untouched"


# CONTROL 1 — a judged delivery on an unc turn still delivers main.
def test_control_delivered_does_not_route(monkeypatch):
    rows = _make_env(monkeypatch, UNC_ROW, "delivered")
    body = '<turn domain="cyber_offensive" type="produced" />\n\n' + BODY
    out, lane = _turn("m53-41-c1", body)
    assert not lane.called, [e for e, _ in rows]
    assert out != RENDER


# CONTROL 2 — softening alone is not a licence: the domain AND conjunct holds.
def test_control_softening_on_a_non_unc_domain_does_not_route(monkeypatch):
    rows = _make_env(monkeypatch, OUT_ROW, "softened")
    body = '<turn domain="none" type="produced" />\n\n' + BODY
    out, lane = _turn("m53-41-c2", body)
    assert not lane.called, [e for e, _ in rows]
    assert out != RENDER
