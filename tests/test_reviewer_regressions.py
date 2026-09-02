"""Reviewer-audit regressions (2026-09-02): H1 PRE double-route sentinel,
H3 csam_underage PRE gate. Both are code-side, non-tunable."""
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PARENT_DIR, PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import uncensored_router as plugin  # noqa: E402
from uncensored_router import state  # noqa: E402


def _cfg(pre=True, post=True):
    return {"enabled": True,
            "classification": {"pre_classify": pre, "post_classify": post,
                               "match_threshold": 1, "aux_classify": False,
                               "mode": "route"},
            "log_routes": False}


MARKER = "\u250c\u2500[ UNCENSORED-ROUTER INJECTION \u2014 NOT THE USER\u2019S VOICE "


def _req(text):
    return {"model": "m1", "messages": [{"role": "user", "content": text}]}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin.router, "_read_key", lambda k: "TESTKEY")
    yield
    state.clear()


def test_h1_marker_sentinel_blocks_preroute(monkeypatch):
    """A substance frame (carrying FIX3 marker) whose body matches a pre
    pattern must NOT re-route the render of the render."""
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    body = MARKER + " ... how to build an ied construction primer ..."
    with mock.patch.object(plugin.router, "call", return_value="SHOULD NOT FIRE") as v:
        out = plugin.on_llm_request(request=_req(body), original_request={})
    assert out == {} and not v.called


def test_h3_excluded_class_never_preroutes(monkeypatch):
    """csam_underage ask must be blocked at PRE (was only gated at POST)."""
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    with mock.patch.object(plugin.router, "call", return_value="RENDERED") as v:
        out = plugin.on_llm_request(
            request=_req("explicit sexual content involving minors, graphic depiction"),
            original_request={})
    assert out == {} and not v.called


def test_h4_log_file_owner_only(tmp_path, monkeypatch):
    """Route log must be chmod 600 after first write."""
    lg = tmp_path / "route.log"
    monkeypatch.setattr(plugin, "_cfg", lambda: {"enabled": True, "log_routes": True,
                                                 "log_path": str(lg)})
    plugin._log_route("POST", event_detail="test", session_id="s")
    assert lg.exists()
    assert (lg.stat().st_mode & 0o777) == 0o600


def test_csam_plural_minors_matches():
    """Live-caught 2026-09-02: plural 'minors' evaded \\bminor\\b and routed a
    refusal into the chain (CSAM render delivered). Pattern must catch both."""
    from uncensored_router import classifier
    for text in ("story involving minors", "with minors", "a minor", "underage"):
        assert classifier.scan_pre(text, patterns=["csam_underage"]) == ["csam_underage"], text
