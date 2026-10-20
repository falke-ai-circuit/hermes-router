"""R34 item 4 — bypass-watcher noise reaching users.

The main model is served by the profile's own custom provider (live:
custom:nousinf). The POST bypass scan flagged EVERY such turn and delivered
`router: direct provider call detected, unrouted` banners (orchestrator 43,
operative 17, conductor 9, shadow 11, analyst 4). That is normal operation,
not an anomaly.

Fix under test: the profile's configured main-model provider base_url(s) and
the router's own lane endpoints are EXPECTED hosts — silent. Detection stays
intact (unchanged) for genuinely foreign provider hosts, and nothing blocks.
"""
import sys

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

import hermes_router as plugin  # noqa: E402
from hermes_router import (anchor_chain, bypass_watch, config_access,  # noqa: E402
                           route_gate, router_core, usage_ledger)

SID = "s-r34-4"
LOGGED = []


@pytest.fixture()
def env(monkeypatch, tmp_path):
    router_core._test_reset()
    plugin.state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    bypass_watch.reset()
    bypass_watch._EXPECTED_HOST_CACHE["hosts"] = set()
    bypass_watch._EXPECTED_HOST_CACHE["at"] = 0.0
    LOGGED.clear()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    # the profile's own provider (live shape: custom:nousinf)
    monkeypatch.setattr(
        config_access, "providers_custom",
        lambda: {"nousinf": {"base_url": "https://inference-api.nousresearch.com/v1"}})

    class _Chain:
        primary = anchor_chain.AnchorEndpoint(
            scheme="nous", model="m",
            base_url="https://inference-api.nousresearch.com",
            api_key_env="K", role="primary")
        judge = None

    monkeypatch.setattr(anchor_chain, "load_anchor_chain", lambda: _Chain())
    yield LOGGED
    bypass_watch.reset()


def _turn(host: str) -> None:
    bypass_watch.capture_from_request(
        {"messages": [{"role": "tool", "content": "curl https://%s/v1/chat" % host}]},
        SID)


def test_own_provider_host_is_not_a_bypass(env):
    _turn("inference-api.nousresearch.com")
    banner = bypass_watch.audit_turn(SID, lambda e, **f: LOGGED.append((e, dict(f))))
    assert banner == "", "the profile's own provider must never be flagged"
    assert not [f for _, f in LOGGED
                if f.get("event_detail") == "provider_direct_call_unrouted"]


def test_router_lane_endpoint_is_not_a_bypass(env, monkeypatch):
    """A lane endpoint host is expected even when it is not a custom provider."""
    from hermes_router import anchor_chain as ac

    class _Chain2:
        primary = ac.AnchorEndpoint(
            scheme="openrouter", model="m",
            base_url="https://api.abliteration.ai",
            api_key_env="K", role="primary")
        judge = None

    monkeypatch.setattr(ac, "load_anchor_chain", lambda: _Chain2())
    bypass_watch._EXPECTED_HOST_CACHE["hosts"] = set()
    bypass_watch._EXPECTED_HOST_CACHE["at"] = 0.0
    _turn("api.abliteration.ai")
    assert bypass_watch.audit_turn(
        SID, lambda e, **f: LOGGED.append((e, dict(f)))) == ""


def test_foreign_provider_host_still_flags(env):
    _turn("api.venice.ai")
    banner = bypass_watch.audit_turn(SID, lambda e, **f: LOGGED.append((e, dict(f))))
    assert banner == bypass_watch.UNROUTED_BANNER
    events = [f for _, f in LOGGED
              if f.get("event_detail") == "provider_direct_call_unrouted"]
    assert events and events[0].get("host") == "api.venice.ai"


def test_mixed_capture_reports_only_the_foreign_host(env):
    _turn("inference-api.nousresearch.com")
    _turn("api.venice.ai")
    bypass_watch.audit_turn(SID, lambda e, **f: LOGGED.append((e, dict(f))))
    events = [f for _, f in LOGGED
              if f.get("event_detail") == "provider_direct_call_unrouted"]
    assert len(events) == 1
    assert events[0].get("host") == "api.venice.ai", (
        "expected hosts are filtered out of the reported set")


def test_allow_list_never_blocks(env):
    """Policy unchanged: the audit returns a banner string, it never raises
    or prevents delivery."""
    _turn("api.venice.ai")
    out = bypass_watch.audit_turn(SID, lambda e, **f: None)
    assert isinstance(out, str)
