"""R76 P2 U5 — the frontier empty-consult failure banner goes through format_banner.

Before this unit anchor_exec.py:1681 built the line by hand:

    "%s higher-self (frontier) | consult failed | empty response | %s %s"
    % (BANNER_HEAD, model, BANNER_TAIL)

which skipped everything format_banner exists to do: VALID_LANES/FORBIDDEN_LANES
validation, the "://" host redaction, the _END_LABELS pre/post mapping and the unpriced
marker. It is on the failure path, i.e. the path least likely to be exercised.

FROZEN below is the string that path actually parked BEFORE the change — captured by
driving maybe_execute_anchored with anchored_call returning an empty consult, not by
reading the source. Every pin here compares against it.
"""
from __future__ import annotations

import pathlib

import pytest

import hermes_router.anchor_exec as ax
from hermes_router import anchor_chain, debug_banner, router_core

# captured from the pre-change failure path (model "test/model-a")
FROZEN = ("· router · higher-self (frontier) | consult failed | empty response "
          "| test/model-a ·")

TRIGGER = "consult failed | empty response"


def _render(lane: str = "frontier-anchor", model: str = "test/model-a") -> str:
    return debug_banner.format_banner(lane, TRIGGER, model, "", 0, 0, 0.0, 0.0,
                                      metadata_only=True)


# --- the pin that proves the text did not move ------------------------------

def test_the_renderer_reproduces_the_frozen_string_exactly():
    assert _render() == FROZEN


def test_the_failure_path_parks_the_frozen_string(monkeypatch):
    """Drive the real empty-consult path; byte-identical to the pre-change output."""
    ep = anchor_chain.parse_anchor_uri("openrouter://test/model-a", "primary")
    rec = {"task_id": "t-u5", "lane": "complexity",
           "mode": router_core.MODE_CONSULT, "endpoint": ep, "route_id": "r-u5",
           "orientation": True, "reason": "complexity_orientation"}
    monkeypatch.setattr(router_core, "pending_model_swap", lambda sid: rec)
    monkeypatch.setattr(router_core, "clear_anchor_backoff", lambda *a, **kw: None)
    monkeypatch.setattr(router_core, "store_consult_result", lambda *a, **kw: None)
    chain = anchor_chain.load_anchor_chain()
    monkeypatch.setattr(anchor_chain, "load_anchor_chain", lambda *a, **kw: chain)
    monkeypatch.setattr(anchor_chain, "cap_check", lambda *a, **kw: (True, 0.0, 0.01))
    monkeypatch.setattr(anchor_chain, "estimate_call_cost", lambda *a, **kw: 0.01)
    monkeypatch.setattr(anchor_chain, "record_spend", lambda *a, **kw: None)
    from hermes_router import decision as _dec
    monkeypatch.setattr(_dec, "ledger_write", lambda *a, **kw: 1)
    monkeypatch.setattr(ax, "anchored_call",
                        lambda endpoint, payload: (None, None, None, None))
    monkeypatch.setattr(ax, "_retry_anchored_async", lambda *a, **kw: None)

    parked = {}
    monkeypatch.setattr(debug_banner, "park_anchor_banner",
                        lambda sid, text, **kw: parked.setdefault("text", text))

    ax.maybe_execute_anchored(
        "api_u5", {"messages": [{"role": "user",
                                 "content": "Analyse how the router resolves a model alias"}],
                   "max_tokens": 200})
    assert parked.get("text") == FROZEN, parked


# --- the validation that now applies ---------------------------------------

def test_an_invalid_lane_is_refused_where_the_hand_string_rendered(monkeypatch):
    # the old hand-formatted line had no lane argument at all: it rendered for ANY lane.
    assert debug_banner.format_banner("not-a-lane", TRIGGER, "m", "", 0, 0, 0.0, 0.0,
                                      metadata_only=True) == ""
    assert debug_banner.format_banner("aux", TRIGGER, "m", "", 0, 0, 0.0, 0.0,
                                      metadata_only=True) == ""


def test_the_label_comes_from_the_renderer_not_a_literal():
    # a lane the renderer labels differently must render that label, never "higher-self"
    other = debug_banner.format_banner("uncensored-render", TRIGGER, "m", "", 0, 0, 0.0,
                                       0.0, metadata_only=True)
    assert "shadow-self (uncensored)" in other
    assert "higher-self" not in other


def test_no_hand_formatted_banner_survives_in_anchor_exec():
    src = pathlib.Path(ax.__file__).read_text(errors="ignore")
    assert '"%s higher-self (frontier) | consult failed | "' not in src
    assert "metadata_only=True" in src


def test_the_normal_shape_is_untouched_by_the_new_keyword():
    normal = debug_banner.format_banner("decision", "midturn", "jev-latest", "",
                                        10, 5, 0.000001, 0.3, level=1)
    assert " | tok 10/5 | " in normal and normal.endswith("$0.000001")
    assert debug_banner.BANNER_TAIL not in normal.rsplit("| tok", 1)[-1].rstrip()
