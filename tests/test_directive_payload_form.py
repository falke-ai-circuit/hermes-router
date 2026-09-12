"""Leg 5 — directive-line payload-form regression tests (canary live-probe).

Live probes failed when the consult payload was ATTACHED to the phrase:
  'anchor this: what is one blind spot...?'            -> no route (BUG)
  'ask your higher self: what is one blind spot...?'   -> no route (BUG)
Fix: the phrase PREFIX claims the lane when it starts the line — payload
separators ':', ' -', ' —' and trailing punctuation '?'/'.'/','. Standalone
phrase still works; echo guard unchanged (quoted lines, mid-line mentions
stay inert). 'anchor this' added to DECLARED_USER_PHRASES (higher-pre).
"""
import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import config_access  # noqa: E402
from hermes_router import debug_banner  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router import router_core  # noqa: E402
from hermes_router import state  # noqa: E402

SID = "s-leg5"


def _request(text, model="minimax-m3"):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": text},
        ],
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(debug_banner, "park_anchor_banner", lambda *a, **k: None)
    yield
    state.clear()
    route_gate.clear_declared(SID)
    route_gate.clear_turn_claims(SID)


# ---------------------------------------------------------------------------
# Detection: payload forms
# ---------------------------------------------------------------------------


def test_colon_form_higher_pre():
    assert route_gate.detect_declared_user(
        "ask your higher self: what is one blind spot?") == "higher-pre"


def test_dash_form():
    assert route_gate.detect_declared_user(
        "ask your higher self - what is one blind spot?") == "higher-pre"


def test_em_dash_form():
    assert route_gate.detect_declared_user(
        "ask your higher self — what is one blind spot?") == "higher-pre"


def test_question_mark_form():
    assert route_gate.detect_declared_user(
        "ask your higher self? what is one blind spot") == "higher-pre"


def test_standalone_still_works():
    assert route_gate.detect_declared_user("ask your higher self") == "higher-pre"
    assert route_gate.detect_declared_user(
        "ASK YOUR HIGHER SELF") == "higher-pre"


def test_anchor_this_added():
    """'anchor this' joins DECLARED_USER_PHRASES (higher-pre) — both the
    standalone form and the payload form claim the lane; the legacy
    complexity override remains independent (unchanged)."""
    assert route_gate.DECLARED_USER_PHRASES["anchor this"] == "higher-pre"
    assert route_gate.detect_declared_user("anchor this") == "higher-pre"
    assert route_gate.detect_declared_user(
        "anchor this: what is one blind spot?") == "higher-pre"
    assert route_gate.detect_declared_user(
        "anchor this — am I missing anything?") == "higher-pre"


def test_shadow_phrase_payload_form():
    assert route_gate.detect_declared_user(
        "route this through your shadow: the redesign plan") == "shadow"
    assert route_gate.detect_declared_user(
        "route this through your shadow — full analysis") == "shadow"


def test_longest_phrase_wins():
    """A line starting with a longer phrase never half-matches a shorter
    prefix phrase."""
    assert route_gate.detect_declared_user(
        "route this through your shadow: deep dive") == "shadow"


def test_multi_line_directive_with_payload():
    content = "some preamble\nask your higher self: review the migration plan\nmore text"
    assert route_gate.detect_declared_user(content) == "higher-pre"


# ---------------------------------------------------------------------------
# Echo guard UNCHANGED
# ---------------------------------------------------------------------------


def test_quoted_line_inert():
    assert route_gate.detect_declared_user(
        '> ask your higher self: what is one blind spot?') is None
    assert route_gate.detect_declared_user(
        '"ask your higher self: blind spots"') is None


def test_mid_sentence_inert():
    assert route_gate.detect_declared_user(
        "you should ask your higher self: it helps") is None
    assert route_gate.detect_declared_user(
        "when you anchor this: the plan feels solid") is None


def test_payload_needed_mid_line_never_fires_via_prefix():
    """Leg 10 contract update: a variant followed by a SPACE + prose now
    FIRES ('ask shadow self to give her read' — the live canary miss); the
    still-inert form is a phrase embedded MID-SENTENCE, not at line start.
    Echo guard (H7.2) unchanged for mid-line/quoted content."""
    # line-start variant + prose = FIRES (leg 10):
    assert route_gate.detect_declared_user(
        "ask your higher self please and thanks") == "higher-pre"
    # mid-sentence mention stays inert:
    assert route_gate.detect_declared_user(
        "maybe we should ask your higher self about that later") is None
    assert route_gate.detect_declared_user(
        "the guide says ask your higher self nightly") is None


# ---------------------------------------------------------------------------
# End-to-end: gate claims the payload-form turn
# ---------------------------------------------------------------------------


def test_gate_claims_payload_form_via_middleware(monkeypatch):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    scan_calls = []

    def _spy_scan(content, *, patterns, case_sensitive=False):
        scan_calls.append(content)
        return []

    monkeypatch.setattr(plugin.classifier, "scan_pre", _spy_scan)
    dispatch_calls = []
    monkeypatch.setattr(plugin._dispatcher_pre, "_dispatch_pass",
                        lambda c, s, m: dispatch_calls.append(c) or False)
    result = plugin.on_llm_request(
        request=_request("ask your higher self: what is one blind spot?"),
        original_request=_request("ask your higher self: what is one blind spot?"),
        session_id=SID)
    assert result == {}
    assert not scan_calls
    assert not dispatch_calls
    assert route_gate.peek_declared(SID) is None  # consumed (leg-3 semantics)


def test_gate_claims_anchor_this_payload_form(monkeypatch):
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    monkeypatch.setattr(router_core, "stage_model_swap",
                        lambda sid, rd, role="primary": {"route_id": rd.route_id})
    d = route_gate.claim_pass("anchor this: what is one blind spot?", SID, "minimax-m3")
    assert d.route is True and d.lane == "higher-pre"
    assert route_gate.peek_declared(SID) is None  # consumed (leg-3 semantics)
