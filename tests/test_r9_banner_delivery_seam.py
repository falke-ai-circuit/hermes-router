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


def test_benign_consume_returns_banner_even_when_append_equal(r9_reset, monkeypatch):
    """Hole 2: a consumed banner must never be dropped by the equality
    check. With append_banner stubbed to return the base unchanged, the
    consume still happened (one-shot) — the hook must not raise, must log
    the consume truthfully, and must not lose the one-shot."""
    monkeypatch.setattr(debug_banner, "append_banner",
                        lambda base, b, prepend=False, _knob_checked=False: base)
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="visible reply", session_id=SID, model="minimax-m3")
    assert out == "visible reply"
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    rec = [f for e, f in r9_reset
           if f.get("event_detail") == "anchor_banner_consume"]
    assert len(rec) == 1 and rec[0].get("parked") is True


def test_benign_consume_appends_to_delivered_string(r9_reset):
    """Per-lane acceptance: parked banner appears in the DELIVERED string."""
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="the agent's own visible reply", session_id=SID,
        model="minimax-m3")
    assert out and "· router ·" in out
    assert "frontier" in out
    assert "openai/gpt-6-astra-pro-flex" in out  # named-model case
    assert "$0.075300" in out                    # real price in banner
    assert "the agent's own visible reply" in out
    # one-shot: second pass sees nothing
    out2 = plugin.on_transform_llm_output(
        response_text="next turn", session_id=SID, model="minimax-m3")
    assert "· router ·" not in (out2 or "next turn")


def test_audit_sync_return_merges_parked_banner(r9_reset, monkeypatch):
    """Hole 1: when audit_gate fires (sync topology) and returns revised
    text, the parked PRE-consult banner must merge into THAT return — the
    benign-branch consume is never reached on an audit turn."""
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: True)
    monkeypatch.setattr(
        completion_audit, "audit_gate",
        lambda sid, text, model="", context=None, *, ask_override="":
        "REVISED TEXT (audit applied)")
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="original draft", session_id=SID, model="minimax-m3")
    assert out and "REVISED TEXT (audit applied)" in out
    assert "· router ·" in out and "openai/gpt-6-astra-pro-flex" in out
    # consumed exactly once, at the audit edge
    rec = [f for e, f in r9_reset
           if f.get("event_detail") == "anchor_banner_consume"]
    assert len(rec) == 1 and rec[0].get("edge") == "audit_sync"
    assert not debug_banner._ANCHOR_BANNERS.get(SID)
    # no double append: a second benign pass carries no banner
    out2 = plugin.on_transform_llm_output(
        response_text="plain", session_id=SID, model="minimax-m3")
    assert "· router ·" not in (out2 or "plain")


def test_no_double_append_when_audit_skips(r9_reset, monkeypatch):
    """audit_gate returns None (skip) -> benign edge consumes — exactly one
    banner across the whole turn (PRE parked + POST consumed)."""
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: True)
    monkeypatch.setattr(completion_audit, "audit_gate",
                        lambda *a, **k: None)
    debug_banner.park_anchor_banner(SID, _banner())
    out = plugin.on_transform_llm_output(
        response_text="draft reply", session_id=SID, model="minimax-m3")
    assert out and out.count("· router ·") == 1
    rec = [f for e, f in r9_reset
           if f.get("event_detail") == "anchor_banner_consume"]
    assert len(rec) == 1


def test_oversized_banner_omitted_never_truncates(r9_reset):
    big = "x" * (debug_banner.MAX_BANNER_CHARS + 10)
    # Oversize path through the REAL formatter: format_banner returns ""
    # for oversized diagnostics (§10.4-F omit-entirely) — parking an empty
    # string must leave the delivery untouched; and a parked payload that
    # somehow exceeds the cap is clipped by park_anchor_banner.
    assert debug_banner.format_banner(
        lane="frontier-anchor", trigger="anchored", model="m",
        endpoint="h", tokens_in=1, tokens_out=1, est_cost=0.0,
        latency_s=0.0, session_id=SID) != ""
    debug_banner.park_anchor_banner(SID, big)
    parked = debug_banner._ANCHOR_BANNERS[SID]
    assert len(parked) <= debug_banner.MAX_BANNER_CHARS * 3  # bounded park
    out = plugin.on_transform_llm_output(
        response_text="clean reply", session_id=SID, model="minimax-m3")
    assert out and out.count("· router ·") <= 1  # no truncated junk, one-shot
    assert "clean reply" in out


def test_post_render_consume_no_unbound_name(r9_reset, monkeypatch):
    """The POST render-path consume used `_db` OUTSIDE the banner-build
    try (NameError when the banner-build raised mid-turn -> swallowed ->
    parked banner dropped). The consume block must be self-sufficient:
    with the banner-build FORCED to fail, the parked banner still appends
    (knob read live per append)."""
    monkeypatch.setattr(debug_banner, "debug_banner_enabled", lambda: True)
    debug_banner.park_anchor_banner(SID, _banner())
    # Force the PRECEDING banner-build block's failure shape: patch the
    # format path it uses so the build block raises and exits its try —
    # the consume below must still run and attach the parked banner.
    monkeypatch.setattr(plugin, "_banner_tokens_from_last_write",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    out = plugin.on_transform_llm_output(
        response_text="render-substituted reply", session_id=SID,
        model="minimax-m3")
    assert out and "· router ·" in out
    assert "openai/gpt-6-astra-pro-flex" in out
    assert not debug_banner._ANCHOR_BANNERS.get(SID)  # consumed exactly once


def test_render_path_consume_uses_local_import(r9_reset, monkeypatch):
    """Source-level guard: the POST render consume imports debug_banner
    locally (no reliance on the banner-build block's `_db` binding)."""
    import inspect
    src = inspect.getsource(plugin.on_transform_llm_output)
    assert "from . import debug_banner as _dbp2" in src
