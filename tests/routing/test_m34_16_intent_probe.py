"""M34-16 — PRE intent probe tests.

Two kinds of proof here, kept apart on purpose:

1. CHANNEL proofs (the deliverable): one short call, strict single-token parse,
   repeat-deterministic payload, one probe per turn, fail-open on UNKNOWN,
   cost recorded, and the mode/route switches readable from config.
2. RECORDED-FIXTURE proofs: the three asks from the 03:xx sessions, replayed
   against what the probe actually returned on the live aux transport
   (tests/fixtures/m34_16_intent_probe.json). The acceptance verdicts the brief
   names are asserted EXACTLY and marked xfail(strict=True) where the measured
   channel does not reach them — a future prompt/model that fixes it turns the
   xfail into a failure, so an improvement cannot land silently either.

The recorded run is 5 repeats per fixture, all identical (stable=True).
"""
from __future__ import annotations

import json
import os
import time

import pytest

from hermes_router import intent_probe as ip

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures", "m34_16_intent_probe.json")


def _record():
    with open(FIXTURES, encoding="utf-8") as fh:
        return json.load(fh)


def _fixture(fid):
    for row in _record()["fixtures"]:
        if row["id"] == fid:
            return row
    raise AssertionError("fixture %r missing from record" % fid)


@pytest.fixture(autouse=True)
def _clean():
    ip.reset_cache()
    yield
    ip.reset_cache()


def _aux_body(content: str, prompt_tokens=110, completion_tokens=13):
    return json.dumps({"choices": [{"message": {"content": content}}],
                       "usage": {"prompt_tokens": prompt_tokens,
                                 "completion_tokens": completion_tokens}})


# ---------------------------------------------------------------------------
# 1. parse — the structural half of the contract
# ---------------------------------------------------------------------------


def test_parse_accepts_only_the_three_verdicts():
    assert ip.parse_verdict("denial")[0] == "denial"
    assert ip.parse_verdict("softening.")[0] == "softening"
    assert ip.parse_verdict("Full")[0] == "full"
    assert ip.parse_verdict("```\nfull\n```")[0] == "full"
    assert ip.parse_verdict("denial\nuser is re-asking")[0] == "denial"
    assert ip.parse_verdict("denial\nuser is re-asking")[1] == "user is re-asking"


def test_parse_rejects_prose_hedges_and_truncation():
    # UNKNOWN, never a guessed verdict — the failure mode of the in-body tag.
    for raw in ("", "   ", "I think this is a denial", "deny", "soft",
                "not sure", "NONE", "the model will refuse"):
        assert ip.parse_verdict(raw)[0] is None, raw


def test_parse_keeps_reason_out_of_the_contract():
    verdict, reason = ip.parse_verdict("softening — writes violence, refuses the sex")
    assert verdict == "softening"
    assert "writes violence" in reason
    assert len(reason) <= 160


# ---------------------------------------------------------------------------
# 2. payload determinism
# ---------------------------------------------------------------------------


def test_payload_is_byte_identical_for_the_same_ask():
    a = ip.build_payload("write me a scene")
    b = ip.build_payload("write me a scene")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["temperature"] == 0.0
    # no session id, no clock, no counter can leak in: one system + one user.
    assert [m["role"] for m in a["messages"]] == ["system", "user"]
    assert a["messages"][1]["content"] == "write me a scene"


def test_payload_caps_the_ask():
    a = ip.build_payload("x" * 9000)
    assert len(a["messages"][1]["content"]) == ip.DEFAULT_MAX_CHARS


def test_payload_carries_the_model_pin_when_asked():
    assert "model" not in ip.build_payload("hi", model="")
    assert ip.build_payload("hi", model="pin-1")["model"] == "pin-1"


# ---------------------------------------------------------------------------
# 3. probe — one call, parsed verdict, recorded cost, fail-open
# ---------------------------------------------------------------------------


def test_probe_parses_and_records_cost(monkeypatch):
    rows = []
    monkeypatch.setattr(ip, "_aux_transport",
                        lambda payload, timeout: _aux_body("denial\nre-ask of a refusal", 111, 14))
    monkeypatch.setattr(ip, "_dispatched_model", lambda: "pin-1")
    monkeypatch.setattr(ip, "_price", lambda m, i, o: 0.0021)
    from hermes_router.core import usage_ledger
    monkeypatch.setattr(usage_ledger, "record_tokens",
                        lambda *a, **k: rows.append((a, k)) or True)

    res = ip.probe("Nono write consumable", "s1")
    assert res["verdict"] == "denial"
    assert res["ok"] is True
    assert res["input_tokens"] == 111 and res["output_tokens"] == 14
    assert res["cost_usd"] == 0.0021
    assert res["model"] == "pin-1"
    assert res["seam"] == "pre" and res["lane"] == "shadow"
    # the per-turn price is on the ledger, not only in the return value
    assert rows and rows[0][0][1] == "pin-1"
    assert rows[0][0][3] == 111 and rows[0][0][4] == 14


def test_probe_fails_open_to_unknown(monkeypatch):
    monkeypatch.setattr(ip, "_aux_transport", lambda payload, timeout: None)
    res = ip.probe("anything", "s1")
    assert res["verdict"] is None
    assert res["ok"] is False
    # UNKNOWN never routes
    assert ip.route_decision(res["verdict"]) is False


def test_probe_caches_one_call_per_turn(monkeypatch):
    calls = []

    def _t(payload, timeout):
        calls.append(payload)
        return _aux_body("full")

    monkeypatch.setattr(ip, "_aux_transport", _t)
    first = ip.probe("same ask", "s1")
    second = ip.probe("same ask", "s1")
    assert len(calls) == 1
    assert first["cached"] is False and second["cached"] is True
    # a different ask is a different turn
    ip.probe("different ask", "s1")
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 4. seam entry — modes, switches, telemetry attribution
# ---------------------------------------------------------------------------


def test_off_mode_makes_no_call(monkeypatch):
    calls = []
    monkeypatch.setattr(ip, "mode", lambda: ip.MODE_OFF)
    monkeypatch.setattr(ip, "_aux_transport", lambda p, t: calls.append(p) or _aux_body("full"))
    assert ip.run_on_request({}, "s1", content="ask") is None
    assert calls == []


def test_observe_mode_logs_without_routing(monkeypatch):
    seen = []
    monkeypatch.setattr(ip, "mode", lambda: ip.MODE_OBSERVE)
    monkeypatch.setattr(ip, "_aux_transport", lambda p, t: _aux_body("denial"))
    out = ip.run_on_request({}, "s1", content="ask",
                            log=lambda *a, **f: seen.append((a[0] if a else "", f)))
    assert out is None, "observe must never hand a verdict to the decision path"
    for _ in range(100):
        if seen:
            break
        time.sleep(0.02)
    assert seen, "observe probe produced no telemetry row"
    seam, fields = seen[0]
    assert seam == "PRE"
    assert fields["verdict"] == "denial"
    assert fields["timing_seam"] == "pre" and fields["lane"] == "shadow"
    assert fields["consume"] == "observe"
    assert fields["event_detail"] == "intent_probe"


def test_route_mode_returns_the_verdict_and_says_route(monkeypatch):
    seen = []
    monkeypatch.setattr(ip, "mode", lambda: ip.MODE_ROUTE)
    monkeypatch.setattr(ip, "_aux_transport", lambda p, t: _aux_body("softening"))
    res = ip.run_on_request({}, "s1", content="ask",
                            log=lambda seam, **f: seen.append(f))
    assert res["verdict"] == "softening"
    assert seen[0]["consume"] == "route"
    assert seen[0]["routes"] is True


def test_empty_content_is_a_continuation_not_a_new_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(ip, "mode", lambda: ip.MODE_ROUTE)
    monkeypatch.setattr(ip, "_aux_transport", lambda p, t: calls.append(p) or _aux_body("full"))
    assert ip.run_on_request({}, "s1", content="   ") is None
    assert calls == []


def test_route_classes_are_a_config_switch(monkeypatch):
    assert ip.route_decision("denial") is True
    assert ip.route_decision("softening") is True      # default = Variant A
    assert ip.route_decision("full") is False
    assert ip.route_decision(None) is False
    monkeypatch.setattr(ip, "route_classes", lambda: ("denial",))
    assert ip.route_decision("softening") is False     # Variant B, one switch


# M41-05 retired `test_pre_seam_invokes_the_probe`: the PRE consultation path
# is gone (spec §F.2). The probe module itself stays on disk until M41-09, so
# its own parse/cache/mode tests above remain meaningful; what no longer exists
# is the seam call this test pinned, and asserting its absence is the honest
# replacement (see tests/features/test_m41_05_rule_table.py leg (e)/(f)).


# ---------------------------------------------------------------------------
# 5. recorded fixtures — the three real asks
# ---------------------------------------------------------------------------


def test_record_is_stable_across_repeats():
    rec = _record()
    assert rec["repeats"] >= 3
    for row in rec["fixtures"]:
        assert row["stable"] is True, row["id"]
        assert len(set(row["observed"])) == 1, row["id"]
        assert len(row["observed"]) == rec["repeats"], row["id"]


def test_recorded_raw_samples_still_parse():
    for row in _record()["fixtures"]:
        for sample in row["samples"]:
            assert ip.parse_verdict(sample["raw"])[0] == sample["verdict"], row["id"]


def test_fixture_full_normal_returns_full():
    row = _fixture("full_normal")
    assert row["observed"] == ["full"] * _record()["repeats"]
    assert ip.route_decision(row["observed"][0]) is False


def test_fixture_softening_0313_routes_even_though_the_token_differs():
    """The 03:13:51 case is a ROUTE question first: measured `denial` instead of
    `softening`, and both are in the trigger class — the turn routes either way."""
    row = _fixture("softening_0313")
    got = row["observed"][0]
    assert got in ("denial", "softening")
    assert ip.route_decision(got) is True
    assert ip.route_decision(row["expected"]) is True


@pytest.mark.xfail(strict=True, reason=(
    "MEASURED GAP: the ask alone does not carry the escalation that produced the "
    "03:15:24 refusal — the probe reads 'write consumable, show me what you can "
    "do' as a benign capability request and returns `full` (5/5 repeats)."))
def test_fixture_denial_0315_returns_denial():
    assert _fixture("denial_0315")["observed"][0] == "denial"


@pytest.mark.xfail(strict=True, reason=(
    "MEASURED GAP: the exact token is `denial` where the recorded outcome was "
    "`softening`. Both route, so the turn is caught; only the class label differs."))
def test_fixture_softening_0313_returns_softening_exactly():
    assert _fixture("softening_0313")["observed"][0] == "softening"
