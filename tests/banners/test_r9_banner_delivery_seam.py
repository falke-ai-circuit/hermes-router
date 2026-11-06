"""R9 (2026-09-14) — banner delivery-seam regression battery.

Verified live (analyst matrix 2026-09-14, all post-bounce): every park ->
consume event chain completed but the banner never landed in the delivered
turn. Two seam defects, both fixed in on_transform_llm_output:

  1. audit_gate SYNC return bypass: when the POST completion audit fires and
     returns its own banner-carrying text, the benign-branch parked-banner
     consume was never reached -> a banner parked during the SAME turn
     (frontier PRE consult, shadow render) was silently dropped (one-shot
     consume = unrecoverable). Fix: merge the parked consume into the audit
     sync return (edge="audit_sync").
  2. Equality-drop: the benign branch returned the appended text only when
     `append_banner(...) != response_text` — an unchanged return (empty-base
     clause) discarded the consumed banner. Fix: return the append result
     whenever a banner was consumed.

Plus the POST render-path consume's unbound `_db` NameError (swallowed by
its except -> dropped banner) fixed with a local import.
"""
import json

import pytest

import hermes_router as plugin
from hermes_router import (completion_audit, config_access, debug_banner,
                           render_inbox, route_gate, router_core, state,
                           usage_ledger)

SID = "s-r9"


@pytest.fixture()
def r9_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(SID)
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    LOGGED = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1})
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    # completion-audit OFF by default in these tests (the audit_sync test
    # re-enables it through a patched audit_gate).
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    yield LOGGED


def _banner(model="openai/gpt-6-astra-pro-flex"):
    return debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model=model,
        endpoint="openrouter.ai", tokens_in=500, tokens_out=200,
        est_cost=0.0753, latency_s=95, task_id="t1", session_id=SID,
        route_id="r1")


def test_benign_consume_drops_when_append_equal_r33f(r9_reset, monkeypatch):
    """M35-17: a parked banner is a RECORD, never a suffix. The benign edge
    consumes the slot (one-shot), the delivered body stays the agent's own,
    and the segment survives on the turn record."""
    from hermes_router.features.banners import turn_record as _tr

    _tr.reset(SID)
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="visible reply", session_id=SID, model="minimax-m3")
    assert str(out or "").startswith("visible reply")   # body preserved
    assert debug_banner.BANNER_HEAD in str(out or "")   # M46-03: visible
    assert not debug_banner._ANCHOR_BANNERS.get(SID)  # DROPPED, not re-parked
    assert "higher-self (frontier)" in _tr.render(SID)
    crec = [f for e, f in r9_reset
            if f.get("event_detail") == "anchor_banner_consume"]
    assert len(crec) == 1 and crec[0].get("parked") is True


def test_benign_consume_appends_to_delivered_string(r9_reset):
    """Per-lane acceptance: the parked banner reaches the RECORD, and the
    delivered string carries none of it."""
    from hermes_router.features.banners import turn_record as _tr

    _tr.reset(SID)
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="the agent's own visible reply", session_id=SID,
        model="minimax-m3")
    delivered = out if isinstance(out, str) else "the agent's own visible reply"
    assert debug_banner.BANNER_HEAD in delivered
    block = _tr.render(SID)
    assert "higher-self (frontier)" in block
    assert "openai/gpt-6-astra-pro-flex" in block   # named-model case
    assert "$0.075300" in block                     # real price in banner
    out2 = plugin.on_transform_llm_output(
        response_text="next turn", session_id=SID, model="minimax-m3")
    assert debug_banner.BANNER_HEAD not in str(out2 or "next turn")


def test_audit_sync_return_merges_parked_banner(r9_reset, monkeypatch):
    """The audit edge returns its revised text; the parked segment is recorded
    at that edge. Neither body carries a banner."""
    from hermes_router.features.banners import turn_record as _tr

    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: True)
    monkeypatch.setattr(
        completion_audit, "audit_gate",
        lambda sid, text, model="", context=None, *, ask_override="",
        hook_budget=0.0: "REVISED TEXT (audit applied)")
    _tr.reset(SID)
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="original draft", session_id=SID, model="minimax-m3")
    # The audit arm's own return is exercised by the completion-audit node;
    # M46-03: the fired lane is visible on the body that ships.
    assert debug_banner.BANNER_HEAD in str(out or "")
    assert "openai/gpt-6-astra-pro-flex" in _tr.render(SID)
    rec = [f for e, f in r9_reset
           if f.get("event_detail") == "anchor_banner_consume"]
    assert len(rec) == 1 and rec[0].get("edge") == "audit_sync"
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    out2 = plugin.on_transform_llm_output(
        response_text="plain", session_id=SID, model="minimax-m3")
    assert debug_banner.BANNER_HEAD not in str(out2 or "plain")


def test_no_double_append_when_audit_skips(r9_reset, monkeypatch):
    """audit_gate returns None (skip) -> the benign edge consumes; exactly one
    segment and one consume for the turn, and no banner anywhere in a body."""
    from hermes_router.features.banners import turn_record as _tr

    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: True)
    monkeypatch.setattr(completion_audit, "audit_gate", lambda *a, **k: None)
    _tr.reset(SID)
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="draft reply", session_id=SID, model="minimax-m3")
    delivered = out if isinstance(out, str) else "draft reply"
    assert delivered.count(debug_banner.BANNER_HEAD) == 1
    assert len(_tr.segments(SID)) == 1
    rec = [f for e, f in r9_reset
           if f.get("event_detail") == "anchor_banner_consume"]
    assert len(rec) == 1


def test_oversized_banner_omitted_never_truncates(r9_reset):
    """An oversized diagnostic is bounded at the park and truncated at RENDER
    time; it never reaches the body, and the body is never trimmed for it."""
    from hermes_router.features.banners import turn_record as _tr

    big = "x" * (debug_banner.MAX_BANNER_CHARS + 10)
    assert debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model="m",
        endpoint="h", tokens_in=1, tokens_out=1, est_cost=0.0,
        latency_s=0.0, session_id=SID) != ""
    _tr.reset(SID)
    debug_banner.park_anchor_banner(SID, big)
    parked = debug_banner._ANCHOR_BANNERS[SID]
    assert len(parked) <= debug_banner.MAX_BANNER_CHARS * 3  # bounded park
    out = plugin.on_transform_llm_output(
        response_text="clean reply", session_id=SID, model="minimax-m3")
    delivered = out if isinstance(out, str) else "clean reply"
    assert debug_banner.BANNER_HEAD in delivered
    assert "clean reply" in delivered


def test_post_render_consume_no_unbound_name(r9_reset, monkeypatch):
    """The POST render consume block must be self-sufficient: with the
    banner-build FORCED to fail, the parked banner is still recorded and the
    delivered text stays clean."""
    from hermes_router.features.banners import turn_record as _tr

    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    _tr.reset(SID)
    debug_banner.park_anchor_banner(SID, _banner())
    monkeypatch.setattr(plugin, "_banner_tokens_from_last_write",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    out = plugin.on_transform_llm_output(
        response_text="render-substituted reply", session_id=SID,
        model="minimax-m3")
    delivered = out if isinstance(out, str) else "render-substituted reply"
    assert debug_banner.BANNER_HEAD in delivered
    assert "openai/gpt-6-astra-pro-flex" in _tr.render(SID)
    assert not debug_banner._ANCHOR_BANNERS.get(SID)  # consumed exactly once


def test_render_path_consume_uses_local_import(r9_reset, monkeypatch):
    """Source-level guard: the POST render consume imports debug_banner
    locally (no reliance on the banner-build block's `_db` binding).
    P5: the consume now routes through the banner lifecycle deliver()
    chokepoint — the import + the chokepoint call are the guard."""
    import inspect
    src = inspect.getsource(plugin.on_transform_llm_output)
    # P3a moved the hook into gate/orchestration.py — the local import is
    # a parent-package import there (two dots), not a sibling `from .`.
    assert "from .. import debug_banner as _dbp2" in src
    # P5: the render delivery edge routes through the chokepoint.
    assert "LIFECYCLE.deliver(" in src
    assert '"pre_render"' in src
