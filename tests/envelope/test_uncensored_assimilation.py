"""R78U M3.1 — uncensored lane ASSIMILATION on the unified envelope.

The decision lane (4e5de94) put both directions on ONE contract. This lane
must carry the SAME contract on its assimilation path, plus the one field that
makes a delivery claim checkable: `acceptance`, saying HOW the delivered body
was accepted (post_transform vs original + a closed reason token).

Pins held here:

  1. the uncensored assimilation envelope IS lane-assimilation/1 with the same
     core slots as the decision lane — not a bespoke shape;
  2. acceptance is truthful for a SWAPPED delivery (post_transform:...) and for
     an UNSWAPPED one (original:...);
  3. a missing acceptance FAILS validation AND is refused by the builder;
  4. report_delivery records NOTHING without acceptance (the negative proof:
     no "delivered" record exists), and exactly one record with it;
  5. the mark site (the live acceptance event) records post_transform.
"""
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from hermes_router import assimilation as A  # noqa: E402
from hermes_router.features.envelope import unified as U  # noqa: E402
from hermes_router.features.envelope import registry as R  # noqa: E402

CORE_SLOTS = ("schema", "lane", "producer", "route_id", "task_id", "answer",
              "verdict", "revised", "evidence_refs", "limitations", "kind",
              "ts", "extras", "budget", "trimmed")


@pytest.fixture()
def events(monkeypatch):
    seen = []

    def _capture(session_id, event, **fields):
        seen.append(dict(fields, session_id=session_id, event=event))

    monkeypatch.setattr(A, "_log", _capture)
    A.clear_marks()
    yield seen
    A.clear_marks()


def _by_event(seen, name):
    return [e for e in seen if e["event"] == name]


# --- 1. the same contract, not a bespoke shape ----------------------------

def test_lane_assimilation_schema_is_the_unified_one():
    env = A.build_uncensored_assimilation("post_transform:render_swap",
                                          render="rendered body")
    assert env["schema"] == U.SCHEMA_ASSIMILATION == "lane-assimilation/1"
    assert env["lane"] == "uncensored"
    assert tuple(env) == CORE_SLOTS, "core slots drifted from the template"
    assert set(env["extras"]) == {"uncensored"}
    assert U.validate_assimilation(env) == [], U.validate_assimilation(env)


def test_extras_are_the_registry_closed_set_only():
    env = A.build_uncensored_assimilation("post_transform:render_swap",
                                          render="body", leg="post")
    extras = env["extras"]["uncensored"]
    assert set(extras) <= R.extras_keys("uncensored", "assimilation")
    assert {"leg", "acceptance", "refusal_class", "voice_mode", "render"} <= \
        set(extras)


# --- 2. acceptance is truthful for both deliveries ------------------------

def test_swapped_delivery_acceptance_is_post_transform():
    env = A.build_uncensored_assimilation(
        A.acceptance_token("post_transform", "render_swap"), render="the render")
    acc = env["extras"]["uncensored"]["acceptance"]
    assert acc == "post_transform:render_swap"
    assert A.parse_acceptance(acc) == ("post_transform", "render_swap")
    assert U.validate_assimilation(env) == []


def test_unswapped_delivery_acceptance_is_original():
    env = A.build_uncensored_assimilation(
        A.acceptance_token("original", "render_unavailable"), answer="model body")
    acc = env["extras"]["uncensored"]["acceptance"]
    assert acc == "original:render_unavailable"
    assert A.parse_acceptance(acc) == ("original", "render_unavailable")
    assert U.validate_assimilation(env) == []
    # the body that was delivered is the ORIGINAL — no render text claimed
    assert "render" not in env["extras"]["uncensored"]


def test_unknown_tokens_are_refused_not_coerced():
    assert A.acceptance_token("post_transform", "because") == ""
    assert A.acceptance_token("fabricated", "render_swap") == ""
    assert A.parse_acceptance("post_transform") == ("", "")
    assert A.parse_acceptance("post_transform:made_up") == ("", "")


# --- 3. missing acceptance FAILS validation (and the builder) -------------

def test_missing_acceptance_fails_validation():
    env = U.build_assimilation(
        lane="uncensored", answer="a",
        extras={"uncensored": {"leg": "post", "render": "body"}})
    assert env == {}, "builder must refuse a delivery with no acceptance"
    handwritten = {
        "schema": "lane-assimilation/1", "lane": "uncensored",
        "producer": "", "route_id": "", "task_id": "", "answer": "a",
        "verdict": {}, "revised": False, "evidence_refs": [], "limitations": "",
        "kind": "", "ts": 1.0, "budget": {"chars_cap": 8000, "drop_order": []},
        "extras": {"uncensored": {"leg": "post", "render": "body"}},
    }
    errors = U.validate_assimilation(handwritten)
    assert any("acceptance" in e for e in errors), errors


def test_blank_acceptance_is_refused():
    assert A.build_uncensored_assimilation("") == {}
    assert A.build_uncensored_assimilation("   ") == {}
    env = U.build_assimilation(lane="uncensored",
                               extras={"uncensored": {"acceptance": "  "}})
    assert env == {}


# --- 4. the delivery recorder: no acceptance, no delivered record ---------

def test_report_delivery_without_acceptance_records_nothing(events):
    env = A.report_delivery("sess-x", "", render="body")
    assert env == {}
    assert _by_event(events, "uncensored_assimilation") == [], \
        "a delivered record was written without acceptance"
    refused = _by_event(events, "uncensored_assimilation_refused")
    assert len(refused) == 1
    assert refused[0]["reason"] == "acceptance_missing"


def test_report_delivery_with_acceptance_records_exactly_once(events):
    env = A.report_delivery("sess-y", "post_transform:render_swap",
                            render="body", leg="post")
    assert env["schema"] == "lane-assimilation/1"
    rows = _by_event(events, "uncensored_assimilation")
    assert len(rows) == 1
    assert rows[0]["delivered"] is True
    assert rows[0]["acceptance"] == "post_transform:render_swap"
    assert rows[0]["accept_source"] == "post_transform"
    assert rows[0]["accept_reason"] == "render_swap"
    assert _by_event(events, "uncensored_assimilation_refused") == []


def test_malformed_acceptance_is_refused_by_the_recorder(events):
    assert A.report_delivery("sess-z", "accepted") == {}
    assert _by_event(events, "uncensored_assimilation") == []


# --- 5. the live mark site states post_transform -------------------------

def test_mark_records_post_transform_acceptance(events):
    A.mark_turn_rendered("sess-mark")
    rows = _by_event(events, "uncensored_assimilation")
    assert len(rows) == 1
    assert rows[0]["acceptance"] == "post_transform:delivered_render"
    assert rows[0]["delivered"] is True
    # the body lives in render_inbox; the envelope names it, never fabricates
    assert rows[0]["envelope_json"].find("render_inbox:sess-mark") != -1
