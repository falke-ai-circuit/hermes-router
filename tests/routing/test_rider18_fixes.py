"""Rider 18 — finish-stage pins (v4.18.0 -> 4.19.0 scope).

Two fixes carried on the tree (HEAD a690a7d + uncommitted):

1. router_core.py R16-1 lane-event naming: the manual open-question
   FRONTIER route now emits `frontier_route_fired` (t1r11 item 1 — the
   four-leg battery pattern keys on the LANE-NAMED route event) in
   addition to the legacy `anchor_route_fired` lane=complexity event,
   which stays for backward compat with the t1r10/r15/r16 pin
   contracts. Pinned: both events present, frontier one carries
   reason=manual_open_question_frontier.

2. provider_prices.py A2 hardening: disk-backed LAST-KNOWN-GOOD catalog
   cache per lane. When the live catalog fetch flakes (fresh
   post-deploy process, empty in-process memo — the live analyst
   17:29:33Z A2 trigger), provider_catalog_entries serves the
   last-known-good (id, host) pairs from disk instead of failing open
   to the bare alias-like primary id that 404s. Stale-ok by design:
   only consulted when the live fetch yields nothing. Pinned:
   cache written on a good fetch, served on a flaked fetch, and no
   config write anywhere on the path.
"""
import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_exec, decision,  # noqa: E402
                           debug_banner as DB, provider_prices,
                           render_inbox, router_core, route_gate,
                           usage_ledger)

SID = "s-rider18"

# The suite-wide zero-network guard (tests/conftest.py, autouse) stubs
# provider_prices.provider_catalog_entries to () for every test. Capture
# the REAL function at import time so the catalog-cache pins can drive it.
_REAL_CATALOG = provider_prices.provider_catalog_entries


# ---------------------------------------------------------------------------
# harness (same shape as rider16 pins)
# ---------------------------------------------------------------------------

@pytest.fixture()
def r18_reset(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    plugin.state.reset_turn_identity(SID)
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()
    DB._HELD_DECISIONS.clear()
    route_gate.clear_turn_claims()
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(DB, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    return tmp_path


def _events(monkeypatch, sink):
    monkeypatch.setattr(plugin, "_log_route", lambda seam, event_detail="",
                        **kw: sink.append((seam, event_detail, kw)))


def _request(text):
    return {"messages": [{"role": "user", "content": text}],
            "model": "z-ai/glm-5.3-flash"}


def _run_turn(monkeypatch, tmp_path, text, anchor=None):
    sink = []
    _events(monkeypatch, sink)
    if anchor is not None:
        monkeypatch.setattr(anchor_exec, "anchored_call", anchor)
    req = _request(text)
    ctx = {"session_id": SID}
    plugin.on_llm_request(request=req, original_request=req, **ctx)
    out = plugin.on_llm_execution(request=req, next_call=lambda r: "EXEC",
                                  **ctx)
    tr = plugin.on_transform_llm_output(response_text="agent answer",
                                        session_id=SID, model="flash")
    body = tr if isinstance(tr, str) and tr else "agent answer"
    return body, sink, out


def _dec_cfg(monkeypatch):
    monkeypatch.setattr(router_core, "_decision_cfg",
                        lambda: {"enabled": True, "level": 3})
    monkeypatch.setattr(decision, "_cfg",
                        lambda: {"enabled": True, "level": 3})


# ---------------------------------------------------------------------------
# 1. frontier_route_fired event naming (backward-compat anchor event intact)
# ---------------------------------------------------------------------------

def test_open_question_emits_frontier_route_fired(r18_reset, monkeypatch):
    """The manual_open_question_frontier route fires the LANE-NAMED
    frontier_route_fired event (t1r11 battery pattern) AND the legacy
    anchor_route_fired event stays present (backward-compat contract)."""
    _dec_cfg(monkeypatch)

    def anchor(ep, kw):
        return ("STUB CONSULT VERDICT", 0.001, 100, 50)

    _, sink, _ = _run_turn(
        monkeypatch, r18_reset,
        "decide this: what should the fleet prioritize next quarter?",
        anchor=anchor)
    frontier = [kw for s, d, kw in sink if d == "frontier_route_fired"]
    assert frontier, "frontier_route_fired must be emitted for the " \
                     "manual_open_question_frontier route"
    f = frontier[0]
    assert f.get("reason") == "manual_open_question_frontier"
    assert f.get("lane") == "complexity"  # lane-scoped anchor machinery
    assert f.get("mode") == "consult"
    # backward compat: legacy anchor_route_fired still present, same reason
    legacy = [kw for s, d, kw in sink if d == "anchor_route_fired"]
    assert legacy, "anchor_route_fired must remain for backward compat"
    assert any(kw.get("reason") == "manual_open_question_frontier"
               for kw in legacy)
    # both events share the route identity (same turn, same task family)
    assert frontier[0].get("session_id") == SID


def test_declared_frontier_form_also_emits_frontier_event(
        r18_reset, monkeypatch):
    """The 'ask your higher self' declared form uses the same anchor
    machinery — the frontier naming applies to the open-question route;
    the declared form keeps its own reason but must not crash on the
    new event ordering (event stream stays well-formed)."""
    _dec_cfg(monkeypatch)

    def anchor(ep, kw):
        return ("STUB ORIENTATION BRIEF", 0.001, 100, 50)

    body, sink, _ = _run_turn(
        monkeypatch, r18_reset,
        "ask your higher self: is the two-pass dispatcher refactor the "
        "right long-term call for the fleet?", anchor=anchor)
    fired = [kw for s, d, kw in sink if d == "anchor_route_fired"]
    assert fired, "declared form must still route"
    assert calls_ok(body)


def calls_ok(body):
    return "router" in body and "consult" in body


# ---------------------------------------------------------------------------
# 2. provider-catalog disk cache (A2 anchored_call_failed hardening)
# ---------------------------------------------------------------------------

def _cat(monkeypatch, tmp_path, fetch_result):
    """Isolate provider_prices to tmp_path + a stubbed live fetch, and
    restore the REAL provider_catalog_entries over the conftest stub."""
    monkeypatch.setattr(provider_prices, "provider_catalog_entries",
                        _REAL_CATALOG, raising=True)
    monkeypatch.setattr(provider_prices, "_cache_path",
                        lambda: str(tmp_path / "prices.json"))
    monkeypatch.setattr(provider_prices, "_fetch",
                        lambda url, kf, ke: fetch_result)


def _nous_catalog():
    return {"data": [
        {"id": "anthropic/claude-fable-5.1",
         "pricing": {"prompt": 1e-6, "completion": 2e-6}},
        {"id": "z-ai/glm-5.3-flash",
         "pricing": {"prompt": 1e-7, "completion": 2e-7}},
        {"id": "nous:batch-variant", "pricing": {}},  # R10c filtered
    ]}


def test_catalog_cache_written_on_good_fetch(monkeypatch, tmp_path):
    """Cache miss -> live fetch -> entries served AND last-known-good
    persisted to disk for the lane."""
    _cat(monkeypatch, tmp_path, _nous_catalog())
    out = provider_prices.provider_catalog_entries("frontier")
    assert ("anthropic/claude-fable-5.1",
            "inference-api.nousresearch.com") in out
    assert not any(":" in eid for eid, _ in out)  # R10c batch filter intact
    cache_file = tmp_path / "hermes-router-catalog-cache.json"
    assert cache_file.exists(), "good fetch must persist the catalog cache"
    data = json.loads(cache_file.read_text())
    assert data["lane"] == "frontier"
    assert ("anthropic/claude-fable-5.1",
            "inference-api.nousresearch.com") in [
        (e[0], e[1]) for e in data["entries"]]
    mode = (cache_file.stat().st_mode & 0o777)
    assert mode == 0o600, f"cache file must be 0o600, got {oct(mode)}"


def test_catalog_cache_hit_on_flaked_fetch(monkeypatch, tmp_path):
    """A2 scenario: good fetch once (cache primed), then the live fetch
    flakes/empties — provider_catalog_entries serves the disk-backed
    last-known-good entries instead of returning empty (which used to
    fail open to the bare alias-like primary id -> provider 404 ->
    anchored_call_failed)."""
    _cat(monkeypatch, tmp_path, _nous_catalog())
    good = provider_prices.provider_catalog_entries("frontier")
    assert good
    # live fetch now flakes (non-dict response)
    _cat(monkeypatch, tmp_path, None)
    flaked = provider_prices.provider_catalog_entries("frontier")
    assert flaked == good, "flaked fetch must serve the last-known-good " \
                           "cache, not empty"
    # and a total exception in the fetch path also fails open to cache
    def boom(url, kf, ke):
        raise RuntimeError("provider down")
    monkeypatch.setattr(provider_prices, "_fetch", boom)
    assert provider_prices.provider_catalog_entries("frontier") == good


def test_catalog_cache_no_config_write(monkeypatch, tmp_path):
    """The cache path never writes router config: only the catalog cache
    file appears in the (isolated) home, and the config section object
    is untouched after both hit and miss paths."""
    import hermes_router.core.config_access as ca
    section = dict(ca.router_section())
    _cat(monkeypatch, tmp_path, _nous_catalog())
    provider_prices.provider_catalog_entries("frontier")
    _cat(monkeypatch, tmp_path, None)
    provider_prices.provider_catalog_entries("frontier")
    assert dict(ca.router_section()) == section, \
        "config section must be untouched (no config writes)"
    names = {p.name for p in tmp_path.iterdir()
             if not p.name.startswith("v310-")}
    assert names <= {"prices.json", "hermes-router-catalog-cache.json"}, \
        f"only pricing + catalog cache files expected, got {names}"


def test_catalog_cache_lane_scoped_and_corrupt_safe(monkeypatch, tmp_path):
    """Cache is lane-keyed: a frontier cache never serves the
    uncensored lane; a corrupt cache file fails open to empty."""
    _cat(monkeypatch, tmp_path, _nous_catalog())
    provider_prices.provider_catalog_entries("frontier")
    # different lane, no cache for it, live fetch flaked
    _cat(monkeypatch, tmp_path, None)
    monkeypatch.setattr(
        provider_prices, "_lane_model_urls",
        lambda lane: {"venice": "https://api.venice.ai/api/v1/models"}
        if lane == "uncensored" else {})
    assert provider_prices.provider_catalog_entries("uncensored") == ()
    # corrupt cache file -> fail open to ()
    cache_file = tmp_path / "hermes-router-catalog-cache.json"
    cache_file.write_text("{not json")
    monkeypatch.setattr(provider_prices, "_lane_model_urls",
                        lambda lane: {"nous": provider_prices.NOUS_MODELS_URL}
                        if lane == "frontier" else {})
    assert provider_prices.provider_catalog_entries("frontier") == ()
