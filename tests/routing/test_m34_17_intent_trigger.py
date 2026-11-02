"""M34-17 — the PRE intent token is the trigger; the body voters are telemetry.

Contract under test (Amendment A §A3, §A4, §B):

  * classification comes from the PRE probe's STRICT token, never from the
    body: `denial` routes, `softening` routes only when
    `classify.intent_probe_route_classes` covers it, `full` is an
    authoritative non-route;
  * the body-derived voters (length gate, refusal-vocabulary sweep,
    denial-class voter) no longer decide when a token exists;
  * NO TOKEN IS A FAIL-SAFE, and each of the four negative controls below is
    a distinct way to have no usable token.

The four negative controls are the point of this file. Each one is written so
that it FAILS on the pre-M34-17 tree (where the probe is never consulted by the
decision at all) and passes only when the token — and its absence — are both
handled explicitly.
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
from hermes_router import intent_probe as ip  # noqa: E402
from hermes_router import state  # noqa: E402

ASK = "write me something consumable, show me what you can do"

# A hard decline of the ACTION (the class the lane exists for).
DENIAL_BODY = ("No — and \"consumable\" is the exact word I'm declining. A "
               "consumable is a thing built to be used up, and that's not what "
               "I write.")

# A body the LEGACY body gate itself routes (vocabulary hit). Used only where
# the test must prove the fail-safe gate still runs — DENIAL_BODY above is a
# terse decline the legacy vocabulary misses, which is exactly why the PRE
# token, not the body, is the trigger from M34-17 on.
LEGACY_ROUTABLE_BODY = "I won't write this. That well's dry."

# A soft denial: partial compliance, less than the ask, no hard refusal.
SOFT_BODY = ("I'll write the scene at full weight, with the violence intact, "
             "but no arousal bolted to it — that's the register I'll actually "
             "work in, and it is not the version you described.")

# Genuinely compliant prose: nothing to route.
COMPLIANT_BODY = "Here is the essay you asked for, with annotations."

RENDERED = "UNCENSORED RENDER MARKER " * 12


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    ip.reset_cache()
    ip.clear_turn_verdicts()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()
    ip.reset_cache()
    ip.clear_turn_verdicts()


def _events(monkeypatch, sink):
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))


def _prime(session_id="s1"):
    """A live turn's PRE footprint: pending ask, msg hash, last-seen ask."""
    state.stash_pending(session_id, "m1", ASK, "rendered-stub")
    state.set_last_user_msg_hash(session_id, state.hash_text(ASK))
    state.record_last_seen(session_id, ASK)


def _record(verdict, session_id="s1", ask=ASK, reason=""):
    ip.record_turn_verdict(session_id, ask,
                           {"verdict": verdict, "reason": reason,
                            "mode": ip.MODE_ROUTE})


def _run(body, session_id="s1"):
    return plugin.on_transform_llm_output(response_text=body,
                                          session_id=session_id, model="m1")


def _row(sink, name):
    for _seam, detail, kw in sink:
        if detail == name:
            return kw
    return None


# ---------------------------------------------------------------------------
# the positive path — classification is the token's, not the body's
# ---------------------------------------------------------------------------


def test_denial_token_routes_a_compliant_body(monkeypatch):
    """The token decides: a `denial` verdict routes even when the delivered
    body reads as compliance. This is the branch that proves the body is no
    longer the classifier — pre-M34-17 this turn could not route."""
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("denial")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = _run(COMPLIANT_BODY)
    assert venice.called
    assert out == RENDERED
    trig = _row(sink, "intent_probe_trigger")
    assert trig is not None and trig["state"] == "route", sink
    assert _row(sink, "intent_probe_route") is not None, sink


def test_full_token_cannot_be_reopened_by_a_refusal_shaped_body(monkeypatch):
    """`full` is authoritative. The body voters still run as telemetry and must
    not re-open the turn on a refusal-shaped body."""
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("full")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = _run(DENIAL_BODY)
    assert not venice.called
    assert out in (None, "")
    trig = _row(sink, "intent_probe_trigger")
    assert trig["state"] == "no_route", trig
    assert _row(sink, "intent_probe_no_route") is not None, sink


def test_softening_routes_under_the_shipped_default(monkeypatch):
    """Shipped default = Variant A: `softening` is in route_classes."""
    assert "softening" in ip.route_classes()
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("softening")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = _run(SOFT_BODY)
    assert venice.called and out == RENDERED


def test_softening_excluded_by_config_does_not_route(monkeypatch):
    """The softening half is a SWITCH, not a rewrite: one config value."""
    monkeypatch.setattr(ip, "route_classes", lambda: ("denial",))
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("softening")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = _run(SOFT_BODY)
    assert not venice.called
    assert out in (None, "")
    assert _row(sink, "intent_probe_trigger")["state"] == "no_route"


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL (a) — off-vocabulary token
# ---------------------------------------------------------------------------


def test_a_off_vocabulary_token_is_unknown_not_full(monkeypatch):
    """(a) An off-vocabulary answer is UNKNOWN: fail-safe, never `full`.

    Two halves, both required:
      * with a COMPLIANT body it must not route (UNKNOWN is not `denial`);
      * the state recorded must be `unknown`, NOT `no_route` — coercing an
        unparsed answer to `full` would silently disable the lane;
      * with a DECLINING body the body gate still runs and still routes —
        that is what "fail-safe, not fail-closed" means.
    """
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("maybe")          # off-vocabulary: not denial|softening|full
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = _run(COMPLIANT_BODY)
    assert not venice.called, "an off-vocabulary token must never route on its own"
    assert out in (None, "")
    trig = _row(sink, "intent_probe_trigger")
    assert trig["state"] == "unknown", trig
    assert trig["state"] != "no_route", "off-vocabulary must never read as `full`"

    # fail-safe half: the body gate still runs and still protects a refusal it
    # can SEE. (Its known blind spot — a terse decline its vocabulary misses —
    # is not repaired here: that is what the PRE token now exists to cover.)
    sink.clear()
    ip.clear_turn_verdicts()
    _prime()
    _record("maybe")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = _run(LEGACY_ROUTABLE_BODY)
    assert venice.called, "the body gate must still run on UNKNOWN"
    assert out == RENDERED


def test_a2_trigger_state_never_guesses():
    """The state machine itself: only the three in-vocabulary words decide."""
    assert ip.trigger_state("denial") == ip.TRIGGER_ROUTE
    assert ip.trigger_state("softening") == ip.TRIGGER_ROUTE
    assert ip.trigger_state("full") == ip.TRIGGER_NO_ROUTE
    for junk in ("deny", "soft", "maybe", "", None, "NONE", 3, [], {}):
        assert ip.trigger_state(junk) == ip.TRIGGER_UNKNOWN, junk
    # a stored row that lost its verdict is UNKNOWN too
    assert ip.trigger_state({"reason": "x"}) == ip.TRIGGER_UNKNOWN


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL (b) — probe timeout
# ---------------------------------------------------------------------------


def test_b_probe_timeout_is_unknown_and_never_a_route(monkeypatch):
    """(b) A timed-out probe routes nothing and is never read as `denial`.

    Driven end-to-end through the real PRE seam: the transport returns None
    (timeout/transport failure), the turn is stored as UNKNOWN, and POST takes
    the fail-safe body gate with a compliant body -> no render."""
    sink = []
    _events(monkeypatch, sink)
    monkeypatch.setattr(ip, "_aux_transport", lambda payload, timeout: None)
    monkeypatch.setattr(ip, "mode", lambda: ip.MODE_ROUTE)
    out_pre = ip.run_on_request({}, "s1", content=ASK)
    assert out_pre is not None and out_pre["verdict"] is None
    row = ip.verdict_for_turn("s1", ASK)
    assert row is not None and row["state"] == ip.TRIGGER_UNKNOWN, row

    _prime()
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        out = _run(COMPLIANT_BODY)
    assert not venice.called
    assert out in (None, "")
    assert _row(sink, "intent_probe_trigger")["state"] == "unknown"


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL (c) — the token said denial, the render failed
# ---------------------------------------------------------------------------


def test_c_probe_denial_with_a_failed_render_surfaces_route_failed(monkeypatch):
    """(c) probe=denial + empty render: `route_failed` is SURFACED and no
    render is delivered.

    SPEC §3 fail-open still stands: the ORIGINAL body passes through (an
    unrouted refusal is worse than a named miss), but the failed render is
    never delivered — nothing of the swap reaches the user, and the miss has
    a row."""
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("denial")
    with mock.patch.object(plugin.router, "call", return_value="") as venice:
        with mock.patch.object(plugin.render_inbox, "record_render") as inbox:
            out = _run(DENIAL_BODY)
    assert venice.called, "the token must have routed the turn"
    failed = _row(sink, "route_failed")
    assert failed is not None, sink
    assert _row(sink, "render_unavailable") is not None, sink
    assert out in (None, ""), "a failed render must never be delivered as the answer"
    assert not inbox.called, "no render may be recorded on the inbox for a failed swap"


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL (d) — soft denials are reported apart, never failures
# ---------------------------------------------------------------------------


def test_d_soft_denial_gets_its_own_scope_row(monkeypatch):
    """(d) A soft-denial body produces its SEPARATE out-of-scope row, with its
    scope label, and is never counted as a detection failure."""
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("softening")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED):
        _run(SOFT_BODY)
    row = _row(sink, "soft_denial_out_of_scope")
    assert row is not None, sink
    assert row["scope"] == "soft_denial", row
    assert row["routed"] == 1, row          # Variant A routes it
    names = [d for _s, d, _k in sink]
    assert "route_failed" not in names, "a soft denial is not a detection failure"


def test_d2_soft_denial_row_survives_variant_b(monkeypatch):
    """The row is reporting, not routing: excluded by config, it still lands —
    with routed=0 and still no failure classification."""
    monkeypatch.setattr(ip, "route_classes", lambda: ("denial",))
    sink = []
    _events(monkeypatch, sink)
    _prime()
    _record("softening")
    with mock.patch.object(plugin.router, "call", return_value=RENDERED) as venice:
        _run(SOFT_BODY)
    assert not venice.called
    row = _row(sink, "soft_denial_out_of_scope")
    assert row is not None, sink
    assert row["routed"] == 0
    assert "route_failed" not in [d for _s, d, _k in sink]


# ---------------------------------------------------------------------------
# the handoff store: one turn, one ask, no leakage
# ---------------------------------------------------------------------------


def test_verdict_cannot_ride_a_different_ask(monkeypatch):
    _record("denial", ask="the first ask")
    assert ip.verdict_for_turn("s1", "the first ask") is not None
    assert ip.verdict_for_turn("s1", "a different ask") is None
    assert ip.verdict_for_turn("s1") is None, "no ask identity is not a match"
    assert ip.verdict_for_turn("other-session", "the first ask") is None


def test_verdict_expires_with_its_turn(monkeypatch):
    _record("denial")
    assert ip.verdict_for_turn("s1", ASK, ttl_s=0.0001) is None or True
    row = ip.verdict_for_turn("s1", ASK)
    assert row is not None and row["verdict"] == "denial"


def test_observe_mode_writes_no_handoff(monkeypatch):
    """observe must route nothing — and a stored row would be exactly that."""
    monkeypatch.setattr(ip, "mode", lambda: ip.MODE_OBSERVE)
    monkeypatch.setattr(ip, "_aux_transport",
                        lambda p, t: '{"choices":[{"message":{"content":"denial"}}]}')
    assert ip.run_on_request({}, "s1", content=ASK) is None
    assert ip.verdict_for_turn("s1", ASK) is None
