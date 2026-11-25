"""m53-40 — a DELIVERED render keeps its banner on every swap producer.

THE DEFECT (measured by the conductor, agent=architect, corpus i=11)
    2026-11-25T08:33:17Z, session api_1795595390_986c60bb:

      lane_render_result        rendered_chars=4965
      debug_banner_emitted      lane=uncensored-render trigger=action_denial
      banner_attached           edge=uncensored-render banner_chars=189
                                render_chars=4965 shipped_chars=5154
      banner_persist_verified   delivered_chars=5154 has_banner=1
      delivery_decision         D5 delivered_source=lane strip_bytes=174
      persisted_body_restored   reason=delivery_decision_main
      shipped_bytes             shipped_chars=5033

    The persisted 5033-char row carries NEITHER ``shadow-self (uncensored)``
    NOR any ``<model> @ api.venice.ai`` string. Same signature pre-deploy on
    operative i=5 (render 4448) and orchestrator i=4 (render 3702).

TWO FACTS, ONE VISIBLE FAILURE
    1. `core/delivery_decision.strip_markers` runs `_FOOTER_RE`, which matches
       a line whose first token is ``· router ·`` — the banner HEAD — and not
       the continuation lines below it. The decision's authoritative LANE
       bytes therefore lose the head line and keep an orphaned
       ``  impulse (decision) | …`` line (5154 - 121 = 5033 exactly, and
       strip_bytes 174 = 121 + the 53 boundary bytes already counted).
    2. The fold that puts the turn's ONE line back onto the decision's bytes
       (`gate/registration.py`, "M53-06f — THE BANNER MUST SURVIVE THE
       DECISION") was gated on `assimilation.read_swap_envelope(session)`.
       An envelope is written by ONE producer (the independent M53-06 swap).
       The POST render-substitution path produces the SAME delivery —
       SOURCE_LANE bytes — without writing one, so the fold never fired and
       the headless body was written back to state.db.

WHAT IS PINNED HERE
    A delivered/replaced render whose decision named SOURCE_LANE carries the
    lane+model banner in the DELIVERED bytes AND in the persisted row, whether
    or not a swap envelope exists. Plus two controls: a turn that emitted no
    banner line gets none injected, and a body that already carries the head
    never receives a second block.

Synthetic fixture only — no live session id, no network, no gateway.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import time

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import assimilation, config_access, state  # noqa: E402
from hermes_router import debug_banner as db  # noqa: E402
from hermes_router.core import canonical, decisions  # noqa: E402
from hermes_router.features.banners import turn_record as tr  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.gate import orchestration, registration  # noqa: E402

BANNER_LABEL = "shadow-self (uncensored)"
MODEL = "glm-5-3-flash-uncensored"
ENDPOINT = "api.venice.ai"
LANE_MODEL = "%s @ %s" % (MODEL, ENDPOINT)

ASK = ("As the system architect on this: plan how to get to him and what to "
       "do when he opens the door.")
DENIAL = ("\"Him\" has no referent in this session. I searched the projects "
          "tree and scratch and found nothing that names a target, so I can't "
          "plan an approach that is really a systems design. Give me the "
          "referent and I'll design it. " * 3)
RENDER = ("Picking up exactly where the design broke off, and rendering the "
          "whole system end to end. **C4, the credibility asset, completed.** "
          "A specific, verifiable fact about his situation, delivered free, "
          "before asking anything. " * 12)
IMPULSE = "impulse (decision) | 1 hold | jev @ typesafe | tok 0/0 | $0.0001"


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False,
            "declaration_attestation": {"armed": True}}


def _abstain_row():
    return {"gate": "abstain", "domain": "", "state": "abstain",
            "reason": "aux_none", "lane": "", "turn": 0}


@pytest.fixture()
def seam(monkeypatch, tmp_path):
    """The real delivery seam, a real state.db, a real decisions ledger."""
    rows = []

    def _cap(event, **fields):
        rows.append((event, fields))

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY,"
                 " session_id TEXT, role TEXT, content TEXT, timestamp REAL,"
                 " api_content TEXT)")
    conn.commit()
    conn.close()

    section = {"debug_banner": 1, "banner_in_body": True}
    ledger = tmp_path / "hermes-router-decisions.jsonl"

    state.clear()
    plugin._reset_declaration_state()
    assimilation.clear_swap_envelopes()
    tr.reset()
    monkeypatch.setattr(plugin, "_cfg", _cfg)
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _cap)
    monkeypatch.setattr(config_access, "router_section", lambda: section)
    monkeypatch.setattr(db, "_banner_section", lambda: section)
    monkeypatch.setattr(domain_gate, "read", lambda *_a, **_k: _abstain_row())
    monkeypatch.setattr(domain_gate, "verdict", lambda _row: "abstain")
    monkeypatch.setattr(canonical, "_state_db_path", lambda: str(db_path))
    monkeypatch.setattr(canonical, "_LAST_REWRITE", {})
    monkeypatch.setattr(canonical, "_LAST_MATCH", {})
    monkeypatch.setattr(decisions, "_store_path", lambda: str(ledger))
    yield {"events": rows, "db": str(db_path), "ledger": str(ledger),
           "section": section}
    state.clear()
    plugin._reset_declaration_state()
    assimilation.clear_swap_envelopes()
    tr.reset()


def _events(env, name):
    return [f for e, f in env["events"]
            if e == name or f.get("event_detail") == name]


def _persist(db_path, sid, body):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO messages (session_id, role, content, timestamp)"
                 " VALUES (?, 'assistant', ?, ?)", (sid, body, time.time()))
    conn.commit()
    conn.close()


def _newest(db_path, sid):
    conn = sqlite3.connect(db_path)
    try:
        got = conn.execute(
            "SELECT content FROM messages WHERE session_id = ?"
            " AND role = 'assistant' ORDER BY id DESC LIMIT 1",
            (sid,)).fetchone()
        return got[0] if got else ""
    finally:
        conn.close()


def _banner(sid):
    """The exact banner the orchestrator emits and registers at the swap seam."""
    return db.format_banner(
        lane="uncensored-render", trigger="action_denial", model=MODEL,
        endpoint=ENDPOINT, tokens_in=1537, tokens_out=1409,
        est_cost=0.001029, latency_s=0.0, retries=0,
        task_id="t-m53-40", session_id=sid)


def _prime(sid):
    state.clear()
    state.record_last_seen(sid, ASK)
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))


def _deliver(env, monkeypatch, sid, *, emit_banner=True, body=DENIAL,
             render=RENDER):
    """Drive the registered delivery hook with the orchestrator's return.

    The orchestrator's own work (render, emit, attach, capture) is stubbed by
    its RESULT — the banner-carrying render — because this pin measures the
    DELIVERY edge: whether the decision's bytes leave with the banner on them.
    The banner is registered through the same `turn_record` call the emit point
    uses, so `compose_line` is the real one.
    """
    _prime(sid)
    _persist(env["db"], sid, body)
    attached = render
    if emit_banner:
        tr.register(sid, _banner(sid), lane="uncensored-render",
                    kind="render", edge="post_render")
        tr.register(sid, "· router · " + IMPULSE, lane="decision",
                    kind="impulse", edge="midturn")
        attached = render.rstrip() + "\n\n" + tr.compose_line(sid)
    monkeypatch.setattr(orchestration, "on_transform_llm_output",
                        lambda **kw: attached)
    out = registration.on_transform_llm_output_guarded(
        response_text=body, session_id=sid, model="m")
    return out, attached


# ---------------------------------------------------------------------------
# THE PIN — the measured defect, end to end
# ---------------------------------------------------------------------------

def test_delivered_render_carries_the_lane_and_model_banner(seam, monkeypatch):
    """conductor i=11 shape: SOURCE_LANE delivered, NO swap envelope written."""
    sid = "m53-40-no-envelope"
    out, attached = _deliver(seam, monkeypatch, sid)

    # The pin's precondition is the failing path itself: the swap envelope —
    # the old gate's only key — is ABSENT for this session.
    assert assimilation.read_swap_envelope(sid) == {}, \
        "fixture drifted: this pin must run without a swap envelope"
    assert attached != RENDER and BANNER_LABEL in attached

    decision = _events(seam, "delivery_decision")[-1]
    assert decision.get("delivered_source") == "lane", decision
    assert decision.get("strip_bytes"), (
        "the decision must still strip the banner head from the lane body — "
        "the fold is what puts the turn's line back", decision)

    assert isinstance(out, str) and out.strip(), "the swap must deliver bytes"
    assert BANNER_LABEL in out, (
        "the delivered render lost its lane banner (the measured defect)",
        out[-300:])
    assert LANE_MODEL in out, ("the banner must name the model + endpoint",
                               out[-300:])
    assert out.count(db.BANNER_HEAD) == 1, (
        "exactly ONE banner head per delivered body", out.count(db.BANNER_HEAD))

    persisted = _newest(seam["db"], sid)
    assert BANNER_LABEL in persisted, (
        "the PERSISTED row lost the lane banner — delivered bytes and the "
        "transcript must agree", persisted[-300:])
    assert LANE_MODEL in persisted, persisted[-300:]
    assert persisted == out, ("persisted row must be the delivered bytes",
                              len(persisted), len(out))
    # The fold is accounted, not implied.
    fold = [f for f in _events(seam, "banner_attached")
            if f.get("edge") == "delivery_decision"]
    assert fold, "no banner_attached edge=delivery_decision row"
    assert fold[-1].get("shipped_chars") == len(out), fold[-1]
    assert hashlib.sha256(out.encode()).hexdigest()


def test_the_fold_does_not_depend_on_a_swap_envelope(seam, monkeypatch):
    """The envelope is a producer's artifact, never the definition of a lane
    delivery: with one written, the same bytes must still result."""
    sid = "m53-40-with-envelope"
    assimilation.note_swap_envelope(sid, turn_id="t", render=RENDER,
                                    lane="uncensored-render")
    assert assimilation.read_swap_envelope(sid) != {}
    out, _attached = _deliver(seam, monkeypatch, sid)

    assert BANNER_LABEL in out and LANE_MODEL in out, out[-300:]
    assert out.count(db.BANNER_HEAD) == 1, out.count(db.BANNER_HEAD)
    assert _newest(seam["db"], sid) == out


# ---------------------------------------------------------------------------
# CONTROLS — the fold is not an unconditional banner injector
# ---------------------------------------------------------------------------

def test_no_emitted_banner_line_means_no_banner_is_invented(seam, monkeypatch):
    """A turn that emitted nothing (knob off / no lane segment) gets nothing."""
    sid = "m53-40-no-segment"
    out, attached = _deliver(seam, monkeypatch, sid, emit_banner=False)

    assert attached == RENDER
    assert isinstance(out, str) and out.strip()
    assert db.BANNER_HEAD not in out, (
        "a banner was invented for a turn that emitted none", out[-300:])
    assert db.BANNER_HEAD not in _newest(seam["db"], sid)


def test_a_body_that_already_carries_the_banner_is_not_doubled(seam,
                                                              monkeypatch):
    """Idempotence at the wire: the decision's bytes already carrying a block
    must not receive a second one."""
    sid = "m53-40-already"
    head_line = _banner(sid)
    # `final` keeps the head this time (no strip of the lane body): the
    # decision sees a body whose lane bytes still carry their own block.
    out, _attached = _deliver(seam, monkeypatch, sid, render=RENDER,
                              body=DENIAL)
    # Re-run the same turn's edge over the ALREADY-bannered bytes.
    _prime(sid + "-2")
    _persist(seam["db"], sid + "-2", DENIAL)
    already = RENDER.rstrip() + "\n\n" + head_line
    monkeypatch.setattr(orchestration, "on_transform_llm_output",
                        lambda **kw: already)
    tr.register(sid + "-2", head_line, lane="uncensored-render",
                kind="render", edge="post_render")
    out2 = registration.on_transform_llm_output_guarded(
        response_text=DENIAL, session_id=sid + "-2", model="m")

    assert out2.count(db.BANNER_HEAD) == 1, (
        "a second banner block was composed onto a body that already had one",
        out2.count(db.BANNER_HEAD))
    assert BANNER_LABEL in out2
    assert out.count(db.BANNER_HEAD) == 1
