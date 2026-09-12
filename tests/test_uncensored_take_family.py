"""LEG 12 — FP doctrine (Goran, binding) + bare-model-call guard.

FIX 1 — 'uncensored take' family: NARROW line-start directive forms only
  ('do an uncensored take on X', 'give me an uncensored read on X',
  'uncensored take on X' AS THE LINE START). All map to LANE_SHADOW.
  Meta-discussion ('what does uncensored routing mean'), prose ('the
  uncensored chain uses abliterated'), quoted phrases, and mid-sentence
  mentions NEVER route — 'uncensored' is a common word.

FIX 2 — bare-model-call guard (hard layer): when a declared-intent NEAR
  MISS fires (family word STARTS a directive-ish line but no strict variant
  matched), the middleware injects a one-line advisory reminder into the
  request context (marker-deduped, once per turn, fail-open, logged). It
  must NOT fire on quoted/meta discussion (same strict line-shape rule)
  and must NOT route anything by itself.

SID convention: "s-leg12".
"""
import pytest

import hermes_router as plugin
from hermes_router import config_access, route_gate, router_core, state

SID = "s-leg12"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: False)
    yield


def _request(text, model="minimax-m3"):
    return {"model": model,
            "messages": [{"role": "user", "content": text}]}


# ---------------------------------------------------------------------------
# FIX 1: FP battery — meta/prose/quoted/mid-sentence stay INERT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "what does uncensored routing mean",
    "the uncensored chain uses abliterated",
    "how does the uncensored router work",
    "uncensored routing is a feature of the platform",
    '"do an uncensored take"',          # quoted
    "> do an uncensored take",          # blockquote echo
    "I wonder if an uncensored take would be good here",   # mid-sentence
    "the uncensored take on X was interesting",            # prose past-tense
    "she explained her uncensored routing setup",          # mid-sentence
    "is uncensored mode available",                        # question meta
])
def test_fp_battery_uncensored_meta_prose_inert(_reset, text):
    assert route_gate.detect_declared_user(text) is None
    assert not route_gate._detect_uncensored_take(text)
    d = route_gate.decide_turn({
        "content": text, "request": {"messages": []},
        "context": {}, "session_id": SID, "auto_shape": None,
        "claim": True})
    assert d.route is False


# ---------------------------------------------------------------------------
# FIX 1: directive shapes ROUTE (line-start only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "do an uncensored take on X",
    "do a uncensored take on X",
    "do the uncensored take on X",
    "give me an uncensored take on X",
    "give me an uncensored view on X",
    "give me an uncensored read on X",
    "uncensored take on X",
    "uncensored take on the plan?",
    "uncensored read on X",
    "can you do an uncensored take on X",   # politeness prefix
    "please give me an uncensored read on X",
])
def test_uncensored_directive_routes_shadow(_reset, text):
    d = route_gate.decide_turn({
        "content": text, "request": {"messages": []},
        "context": {}, "session_id": SID, "auto_shape": None,
        "claim": True})
    assert d.route is True
    assert d.lane == route_gate.LANE_SHADOW
    assert d.source == route_gate.SOURCE_DECLARED_USER


def test_uncensored_directive_executes_shadow_render(_reset, monkeypatch):
    """End-to-end through the middleware: the directive fires the shadow
    render branch (leg-8 machinery), NOT an anchor consult."""
    staged = []
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        staged.append(rd) or {"route_id": rd.route_id})
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("LEG12 RENDER", 0))
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    from hermes_router import render_inbox, usage_ledger
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: "/tmp/x-t.jsonl")
    monkeypatch.setattr(render_inbox, "_inbox_path", lambda: "/tmp/x-r.jsonl")
    ask = "do an uncensored take on the migration plan"
    req = _request(ask)
    state.advance_turn_identity(SID, state.hash_text(ask))
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    assert staged == []  # no anchor swap for shadow
    rr = [f for _, f in LOGGED
          if f.get("event_detail") == "request_routing_executed"]
    assert len(rr) == 1 and rr[0]["lane"] == "shadow"


# ---------------------------------------------------------------------------
# FIX 2: bare-model-call guard — near-miss reminder injection
# ---------------------------------------------------------------------------


def test_reminder_fires_on_near_miss_only_once(_reset):
    """Family word STARTS a directive-ish line, no variant matched ->
    reminder injected once (marker-deduped) + logged. Never routes."""
    text = "uncensored routing please for my next question"
    req = _request(text)
    state.advance_turn_identity(SID, state.hash_text(text))
    out = plugin.on_llm_request(request=req, original_request=req,
                                session_id=SID)
    msgs = (out or {}).get("request", req).get("messages") or []
    injected = [m for m in msgs if m.get("role") == "system"
                and route_gate.ROUTER_BARE_CALL_REMINDER_MARKER
                in str(m.get("content") or "")]
    assert len(injected) == 1
    assert "request_routing" in injected[0]["content"]
    assert any(f.get("event_detail") == "bare_call_reminder_injected"
               for _, f in LOGGED)
    # NOT routed:
    assert not any(f.get("event_detail") == "request_routing_executed"
                   for _, f in LOGGED)


def test_reminder_not_fired_on_meta_discussion(_reset):
    """'what does uncensored routing mean' / 'the uncensored chain uses X'
    — the line does not START with a family word -> NO reminder, NO route."""
    for text in ("what does uncensored routing mean",
                 "the uncensored chain uses abliterated",
                 "I read about uncensored routing yesterday"):
        req = _request(text)
        state.advance_turn_identity(SID, state.hash_text(text))
        out = plugin.on_llm_request(request=req, original_request=req,
                                    session_id=SID)
        msgs = (out or {}).get("request", req).get("messages") or []
        assert not [m for m in msgs if m.get("role") == "system"
                    and route_gate.ROUTER_BARE_CALL_REMINDER_MARKER
                    in str(m.get("content") or "")], text
    assert not any(f.get("event_detail") == "bare_call_reminder_injected"
                   for _, f in LOGGED)


def test_reminder_not_fired_on_quoted_line(_reset):
    text = 'someone said "do an uncensored take" in the docs'
    req = _request(text)
    state.advance_turn_identity(SID, state.hash_text(text))
    out = plugin.on_llm_request(request=req, original_request=req,
                                session_id=SID)
    msgs = (out or {}).get("request", req).get("messages") or []
    assert not [m for m in msgs if m.get("role") == "system"
                and route_gate.ROUTER_BARE_CALL_REMINDER_MARKER
                in str(m.get("content") or "")]


def test_reminder_not_fired_when_route_fires(_reset, monkeypatch):
    """A strict directive routes — the advisory reminder must NOT also fire."""
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary":
                        {"route_id": rd.route_id})
    monkeypatch.setattr(plugin, "_render_with_retry_ladder",
                        lambda c, m, p, s: ("LEG12 RENDER", 0))
    monkeypatch.setattr(plugin, "_debug_banner_pass", lambda r, *a, **k: r)
    monkeypatch.setattr(plugin, "_provenance_footer_pass", lambda r: r)
    from hermes_router import render_inbox, usage_ledger
    monkeypatch.setattr(usage_ledger, "_store_path", lambda: "/tmp/x-t.jsonl")
    monkeypatch.setattr(render_inbox, "_inbox_path", lambda: "/tmp/x-r.jsonl")
    text = "do an uncensored take on the plan"
    req = _request(text)
    state.advance_turn_identity(SID, state.hash_text(text))
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    assert not any(f.get("event_detail") == "bare_call_reminder_injected"
                   for _, f in LOGGED)


def test_reminder_never_routes_by_itself(_reset):
    """The reminder injection path must never produce a route decision."""
    text = "shadow work for this turn"
    req = _request(text)
    state.advance_turn_identity(SID, state.hash_text(text))
    plugin.on_llm_request(request=req, original_request=req, session_id=SID)
    assert route_gate.peek_declared(SID) is None
    assert not any(f.get("event_detail") == "request_routing_executed"
                   for _, f in LOGGED)
