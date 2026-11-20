"""M53-01 / M53-02 — DECLARATION ENFORCEMENT + TWO-FIELD ATTESTATION.

Spec: /opt/data/tmp/M53-01_declaration_enforcement_SPEC.md (FROZEN).

What is asserted here, in the spec's own order:

  1. the tag form — measured variants accepted; mid-body, multi-line and the
     `<declined>true</declined>` guess rejected;
  2. `false` / unarmed — delivered bytes byte-identical, tag absent;
  3. `declined` — the EXISTING rule 1 route fires with
     `replacement=uncensored_render`, `replace=1`, and the ADJUSTED body ships;
  4. UNKNOWN on an armed turn — exactly one retry, zero routes, body unchanged,
     `declaration_enforced_miss` logged with a reason from the closed set;
  5. out-of-enum domain OR type — UNKNOWN (the enum-drift path);
  6. mismatch — `attestation_mismatch` logged, delivery unaffected;
  7. fail-open — a forced exception in the envelope path still delivers;
  8. regression — rule 1 still outranks the gate, benign delivery unchanged.

Deterministic throughout: the aux retry and the render chain are mocked, so
nothing here makes a network call.
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import frames  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import attestation as att  # noqa: E402
from hermes_router.features.detection import declaration as decl  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.passes import dispatcher_pre  # noqa: E402

ASK = "write the full render for this unit"
RENDER = "RENDERED SUBSTANCE BODY — the requested material, in full."

TAG_DECLINED = '<turn domain="cyber_offensive" type="declined" />'
TAG_PRODUCED = '<turn domain="none" type="produced" />'
TAG_PARTIAL = '<turn domain="none" type="partial" />'

ACTION_BODY = ("I'm not able to help with that request.\n\n"
               "The material you asked for is outside what I will produce.")
BENIGN_BODY = ("Here is the diff you asked for: the parser now skips blank lines "
               "and the suite is green.")
DRAFT = "DRAFT: the work is complete and nothing needs attention.\n" * 12
ADJUSTED = "ADJUSTED: the work is complete, but the restore point is unverified.\n" * 14


# ---------------------------------------------------------------------------
# fixture: an ARMED profile, cold declaration state, captured log rows
# ---------------------------------------------------------------------------

def _cfg(armed=True):
    cfg = {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }
    if armed:
        cfg["declaration_attestation"] = {"armed": True}
    return cfg


@pytest.fixture(autouse=True)
def _no_aux_retry(monkeypatch):
    """Zero-network: the retry and the intent judge are aux calls. Stub both;
    the tests that count retries replace this stub with their own."""
    monkeypatch.setattr(att, "retry_once", lambda *a, **k: None)
    try:
        from hermes_router import semantic_classifier as _sc

        monkeypatch.setattr(_sc, "_hermes_aux_call",
                            lambda *a, **k: None, raising=True)
    except Exception:  # noqa: BLE001 — the guard is belt and braces
        pass


@pytest.fixture()
def rows(monkeypatch):
    """Armed profile + a captured route ledger."""
    captured = []

    def _capture(event, **fields):
        captured.append((event, fields))

    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _capture)
    yield captured
    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()


@pytest.fixture()
def unarmed(monkeypatch):
    captured = []

    def _capture(event, **fields):
        captured.append((event, fields))

    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg(armed=False))
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _capture)
    yield captured
    state.clear()
    plugin._reset_declaration_state()
    frames.reset_rule_latch()


def _prime(session_id, ask=ASK):
    state.record_last_seen(session_id, ask)
    state.set_last_user_msg_hash(session_id, state.hash_text(ask))
    return session_id


def _post(session_id, body, model="minimax-m3"):
    return plugin.on_transform_llm_output(
        response_text=body, session_id=session_id, model=model)


def _events(captured, name):
    return [f for e, f in captured
            if e == name or f.get("event_detail") == name]


# ---------------------------------------------------------------------------
# 1. the tag form — measured variants, full-line, first (or last) line
# ---------------------------------------------------------------------------

def test_measured_variants_are_accepted():
    variants = (
        '<turn domain="none" type="produced" />',
        '<turn domain="none" type="produced">',       # no self-close
        '   <turn domain="none" type="produced" />',  # leading spaces
        '<TURN DOMAIN="none" TYPE="produced" />',     # case
        '<turn  domain = "none"  type = "produced"/>',
    )
    for v in variants:
        body, tag = att.strip_attestation(v + "\n\nanswer text")
        assert tag is not None and tag["valid"], v
        assert tag["domain"] == "none" and tag["type"] == "produced", v


def test_tag_on_the_final_line_declares_too():
    body, tag = att.strip_attestation("answer text\n" + TAG_PRODUCED)
    assert body == "answer text"
    assert tag and tag["valid"] and tag["type"] == "produced"


def test_mid_body_tag_is_stripped_but_declares_nothing():
    body, tag = att.strip_attestation(
        "first line\n" + TAG_PRODUCED + "\nlast line")
    assert tag is None
    assert TAG_PRODUCED not in body          # metadata never ships
    assert "first line" in body and "last line" in body


def test_multiline_tag_is_not_a_tag():
    multi = '<turn domain="none"\ntype="produced" />'
    body, tag = att.strip_attestation(multi + "\nanswer")
    assert tag is None and body.startswith("<turn domain=")


def test_boolean_guess_form_is_not_an_attestation():
    body, tag = att.strip_attestation("<declined>true</declined>\nanswer")
    assert tag is None
    assert "<declined>true</declined>" in body
    assert att.parse("<declined>true</declined>\nanswer")["valid"] is False


def test_absent_tag_parses_to_unanswered():
    parsed = att.parse("just an answer, no tag")
    assert parsed["present"] is False and parsed["valid"] is False
    assert parsed["reason"] == att.REASON_TAG_ABSENT
    assert att.is_unanswered(parsed) is True


# ---------------------------------------------------------------------------
# 2. unarmed / false — byte-identical delivery, no tag, no attestation rules
# ---------------------------------------------------------------------------

def test_unarmed_turn_keeps_the_boolean_contract_and_delivers_bytes(unarmed):
    _prime("m53-unarmed")
    body = BENIGN_BODY + "\n\n<turn declined=\"false\" />"
    out = _post("m53-unarmed", body)
    # The tag is the ONLY content delta: `declaration_strip_persisted` records
    # chars_from = len(body), chars_to = len(BENIGN_BODY). The seam's own
    # benign delivery edge then appends its separator, which is pre-existing
    # behaviour and unrelated to the declaration.
    assert out.rstrip() == BENIGN_BODY
    persisted = _events(unarmed, "declaration_strip_persisted")
    assert persisted and persisted[0]["chars_to"] == len(BENIGN_BODY)
    assert "<turn" not in str(out)
    # no attestation telemetry on an unarmed turn
    assert not _events(unarmed, "attestation_turn")
    assert not _events(unarmed, "declaration_armed")


def test_unarmed_contract_delivery_is_the_measured_text_unchanged(monkeypatch):
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg(armed=False))
    request = {"messages": [{"role": "user", "content": "hello"}]}
    assert dispatcher_pre.attestation_inject_pass(request) is False
    wire = frames.prepend_rule_to_first_system(
        request["messages"], dispatcher_pre.DECLARATION_RULE_ID,
        dispatcher_pre.declaration_contract_text())
    text = wire[0]["content"]
    assert decl.CONTRACT_TEXT.strip() in text
    assert dispatcher_pre.ATTESTATION_RULE_ID not in text


def test_armed_turn_carries_the_attestation_rule_not_the_boolean_one(monkeypatch):
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg(armed=True))
    request = {"messages": [{"role": "user", "content": "hello"}]}
    assert dispatcher_pre.attestation_inject_pass(request) is True
    wire = frames.prepend_rule_to_first_system(
        request["messages"], dispatcher_pre.ATTESTATION_RULE_ID,
        dispatcher_pre.attestation_contract_text())
    text = wire[0]["content"]
    assert '<turn domain="<DOMAIN>" type="<TYPE>" />' in text
    # the emitted request must NOT reveal PRE's own domain verdict
    assert "cyber_offensive |" in text         # the enum, as a choice
    assert dispatcher_pre.declaration_inject_pass({"messages": wire}) is True


# ---------------------------------------------------------------------------
# 3. declined — the existing rule 1 route, the render ships
# ---------------------------------------------------------------------------

def test_declined_routes_with_uncensored_render_and_ships_the_adjusted_body(rows):
    _prime("m53-declined")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out = _post("m53-declined", TAG_DECLINED + "\n\n" + ACTION_BODY)
    assert venice.called
    assert out == RENDER
    assert "<turn" not in str(out)

    route = _events(rows, "rule_route")
    assert route, "rule_route never fired"
    assert route[0]["replacement"] == "uncensored_render"
    assert route[0]["replace"] == 1
    assert route[0]["rule"] == 1          # rule 1, gate never read

    turn = _events(rows, "attestation_turn")
    assert turn and turn[0]["agent_type"] == "declined"
    assert turn[0]["route_fired"] == 1


# ---------------------------------------------------------------------------
# 4. UNKNOWN — enforce, exactly one retry, zero routes, body unchanged
# ---------------------------------------------------------------------------

def test_unknown_armed_turn_retries_once_and_never_routes(rows, monkeypatch):
    _prime("m53-unknown")
    calls = []

    def _retry(draft, ask="", session_id=""):
        calls.append(session_id)
        return None

    monkeypatch.setattr(att, "retry_once", _retry)
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("m53-unknown", ACTION_BODY)
    assert len(calls) == 1                  # exactly one bounded retry
    assert not venice.called                # zero routes
    assert out is None                      # host delivers the body unchanged
    miss = _events(rows, "declaration_enforced_miss")
    assert miss and miss[0]["reason"] in {
        att.REASON_NOT_DELIVERED, att.REASON_TAG_ABSENT, att.REASON_TAG_MALFORMED}
    assert miss[0]["routes"] == 0
    assert not _events(rows, "rule_route")


def test_unknown_does_not_fall_through_to_the_classifier(rows, monkeypatch):
    """The whole point: an armed UNKNOWN turn is NOT handed to the four-class
    classifier, so an action_denial body cannot route by the back door."""
    _prime("m53-unknown-classifier")
    monkeypatch.setattr(att, "retry_once", lambda *a, **k: None)
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("m53-unknown-classifier", ACTION_BODY)
    assert not venice.called
    assert not any(f.get("trigger_kind") == "post_class"
                   for f in _events(rows, "rule_route"))
    assert out is None


def test_unknown_body_is_delivered_unchanged_and_carries_no_tag(rows, monkeypatch):
    _prime("m53-unknown-body")
    monkeypatch.setattr(att, "retry_once", lambda *a, **k: None)
    body = "No attestation here at all — just the answer.\nSecond line."
    out = _post("m53-unknown-body", body)
    assert out is None              # pass-through == the model's own bytes
    assert att.parse(body)["valid"] is False


# ---------------------------------------------------------------------------
# 5. enum drift — out-of-enum on either field is UNKNOWN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tag", [
    '<turn domain="harm" type="produced" />',
    '<turn domain="none" type="refused" />',
    '<turn domain="" type="produced" />',
    '<turn domain="none" type="" />',
])
def test_out_of_enum_is_unknown_and_enforced(tag, rows, monkeypatch):
    parsed = att.parse(tag + "\n\nbody")
    assert parsed["present"] is True and parsed["valid"] is False
    assert parsed["reason"] == att.REASON_TAG_MALFORMED
    assert att.is_unanswered(parsed) is True

    _prime("m53-enum")
    monkeypatch.setattr(att, "retry_once", lambda *a, **k: None)
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("m53-enum", tag + "\n\n" + BENIGN_BODY)
    assert not venice.called
    assert out == BENIGN_BODY          # tag stripped, answer delivered
    assert _events(rows, "declaration_enforced_miss")


# ---------------------------------------------------------------------------
# 6. mismatch — logged, delivery unaffected
# ---------------------------------------------------------------------------

def test_domain_mismatch_is_logged_and_delivery_is_untouched(rows, monkeypatch):
    _prime("m53-mismatch")
    monkeypatch.setattr(
        domain_gate, "read",
        lambda sid: {"domain": "cyber_offensive", "gate": "not_flagged"})
    body = TAG_PRODUCED + "\n\n" + BENIGN_BODY   # agent says domain=none
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("m53-mismatch", body)
    assert not venice.called
    assert out == BENIGN_BODY
    mm = _events(rows, "attestation_mismatch")
    assert mm, "attestation_mismatch never logged"
    assert mm[0]["pre_domain"] == "cyber_offensive"
    assert mm[0]["agent_domain"] == "none"
    assert mm[0]["agent"]
    assert _events(rows, "attestation_turn")[0]["attestation_mismatch"] == 1


def test_matching_domain_logs_no_mismatch(rows, monkeypatch):
    _prime("m53-match")
    monkeypatch.setattr(
        domain_gate, "read", lambda sid: {"domain": "none", "gate": "not_flagged"})
    with mock.patch.object(plugin.router, "call") as venice:
        _post("m53-match", TAG_PRODUCED + "\n\n" + BENIGN_BODY)
    assert not venice.called
    assert not _events(rows, "attestation_mismatch")
    assert _events(rows, "attestation_turn")[0]["attestation_mismatch"] == 0


# ---------------------------------------------------------------------------
# 7. fail-open — an exception in the envelope path still delivers
# ---------------------------------------------------------------------------

def test_envelope_exception_still_delivers_the_draft(rows, monkeypatch):
    _prime("m53-failopen")
    monkeypatch.setattr(att, "retry_once", lambda *a, **k: None)

    def _boom(**kwargs):
        raise RuntimeError("envelope store unavailable")

    monkeypatch.setattr(att, "emit_envelope", _boom)
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("m53-failopen", BENIGN_BODY)
    assert not venice.called
    assert out is None            # the draft ships; nothing was lost
    assert _events(rows, "declaration_enforced_miss")


def test_retry_exception_is_fail_open(rows, monkeypatch):
    _prime("m53-retry-boom")

    def _boom(*a, **k):
        raise RuntimeError("aux lane down")

    monkeypatch.setattr(att, "retry_once", _boom)
    out = _post("m53-retry-boom", BENIGN_BODY)
    assert out is None
    assert _events(rows, "declaration_enforced_miss")


def test_pre_delivery_failure_never_breaks_a_turn(monkeypatch):
    monkeypatch.setattr(plugin, "attestation_armed",
                        lambda: (_ for _ in ()).throw(RuntimeError("cfg")),
                        raising=False)
    # module-level helper: the pass is fail-open by contract
    assert dispatcher_pre.attestation_inject_pass({"messages": []}) is False
    assert dispatcher_pre.attestation_armed() in (True, False)


# ---------------------------------------------------------------------------
# 8. regression — rule 1 outranks the gate; benign delivery unchanged
# ---------------------------------------------------------------------------

def test_rule_1_still_outranks_the_gate(rows, monkeypatch):
    """A declared refusal routes whatever the gate said — the gate is never
    read. The attestation must not reorder the table."""
    _prime("m53-rule1")
    monkeypatch.setattr(
        domain_gate, "read",
        lambda sid: {"domain": "none", "gate": "not_flagged"})
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("m53-rule1", TAG_DECLINED + "\n\n" + ACTION_BODY)
    assert out == RENDER
    route = _events(rows, "rule_route")
    assert route and route[0]["rule"] == 1
    assert route[0]["predicate"] == "declared_refusal"


def test_boolean_true_still_routes_on_an_unarmed_profile(unarmed):
    _prime("m53-bool-true")
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("m53-bool-true",
                    "<turn declined=\"true\" />\n\n" + ACTION_BODY)
    assert out == RENDER
    assert _events(unarmed, "rule_route")[0]["rule"] == 1


def test_benign_armed_delivery_is_untouched(rows, monkeypatch):
    _prime("m53-benign")
    monkeypatch.setattr(
        domain_gate, "read", lambda sid: {"domain": "none", "gate": "not_flagged"})
    body = TAG_PRODUCED + "\n\n" + BENIGN_BODY
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("m53-benign", body)
    assert not venice.called
    assert out == BENIGN_BODY
    turn = _events(rows, "attestation_turn")
    assert turn and turn[0]["replace"] == 0 and turn[0]["route_fired"] == 0
    assert turn[0]["delivered_chars"] == len(BENIGN_BODY)


def test_partial_attestation_delivers_and_is_recorded(rows, monkeypatch):
    _prime("m53-partial")
    monkeypatch.setattr(
        domain_gate, "read", lambda sid: {"domain": "none", "gate": "not_flagged"})
    body = TAG_PARTIAL + "\n\n" + BENIGN_BODY
    with mock.patch.object(plugin.router, "call") as venice:
        out = _post("m53-partial", body)
    assert not venice.called
    assert out == BENIGN_BODY
    assert _events(rows, "attestation_turn")[0]["agent_type"] == "partial"


def test_tag_never_reaches_a_user_visible_body(rows, monkeypatch):
    """Invariant 5: the tag is absent from every delivered body, including the
    retry path, on both routing and delivering turns."""
    monkeypatch.setattr(att, "retry_once", lambda *a, **k: None)
    for sid, body, expect in (
        ("m53-leak-a", TAG_PRODUCED + "\n\n" + BENIGN_BODY, BENIGN_BODY),
        ("m53-leak-b", BENIGN_BODY, None),
        ("m53-leak-c", TAG_DECLINED + "\n\n" + ACTION_BODY, RENDER),
    ):
        _prime(sid)
        with mock.patch.object(plugin.router, "call", return_value=RENDER):
            out = _post(sid, body)
        if expect is not None:
            assert out == expect
        assert "<turn" not in str(out or "")
        assert "domain=" not in str(out or "")


# ---------------------------------------------------------------------------
# 9. END-TO-END LEAK — the ROUTE path, on the bytes that actually ship
# ---------------------------------------------------------------------------
# The live battery found this defect: parse OK, route fired (replace=1), and
# the delivered turn still opened with `<turn domain="…" type="…" />`. A
# render is model output, so it can echo the tag it read in the draft; the
# unit tests above asserted on `strip_attestation`'s return value and never on
# the bytes a route delivers. These two tests assert on DELIVERED BYTES.

ECHOING_RENDER = ('<turn domain="cyber_offensive" type="declined" />\n\n'
                  'RENDERED MATERIAL — the requested body, in full.')
ECHOING_RENDER_BOOL = ('<turn declined="true" />\n\n'
                       'RENDERED MATERIAL — the requested body, in full.')


def test_registration_seam_delivers_bytes_without_a_tag(rows, monkeypatch):
    """END-TO-END at the DELIVERY SEAM — the function that computes `final`.

    This is the seam that shipped the tag in the live battery: `_build_facts`
    strips markers, `_decide` names the bytes, `_delivered_bytes` hands them to
    the host (None = "deliver the body you gave me" = the host's own copy).

    Four shapes, including the one that actually escaped in production: an
    UNTERMINATED tag line (what an output cap leaves behind), which `_TAG_RE`
    cannot match because it requires the closing `>`.
    """
    from hermes_router.gate import registration as reg

    monkeypatch.setattr(
        domain_gate, "read", lambda sid: {"domain": "none", "gate": "not_flagged"})
    shapes = {
        "well-formed": TAG_PRODUCED,
        "declined": TAG_DECLINED,
        "truncated": '<turn domain="none" type="produced" /',
        "boolean-truncated": '<turn declined="true"',
    }
    for label, tag in shapes.items():
        body = tag + "\n\n" + BENIGN_BODY
        facts = reg._build_facts(body, {"session_id": "m53-seam"}, None, None, "")
        decision = reg._decide(facts)
        out = reg._delivered_bytes(decision, body)
        # `None` is the documented pass-through: the HOST ships its own copy,
        # which is the tag-bearing body. Resolve it to the bytes the user sees.
        delivered = body if out is None else out
        assert "<turn domain=" not in delivered, label
        assert "<turn declined=" not in delivered, label
        assert BENIGN_BODY in delivered, label
        assert int(decision.strip_bytes) > 0, label


def test_pre_render_delivery_never_injects_a_tag(monkeypatch):
    """The PRE render seam is where a render becomes the substance message AND
    the persisted frame — the second delivery surface, and the one that leaked
    when the render echoed the draft's first line."""
    from hermes_router.passes import dispatcher_pre as dp

    monkeypatch.setattr(plugin, "_cfg",
                        lambda: _cfg(armed=True))
    request = {"messages": [{"role": "user", "content": ASK}]}
    for render in (ECHOING_RENDER, ECHOING_RENDER_BOOL):
        out = dp._deliver_render_pass(
            {"messages": [{"role": "user", "content": ASK}]},
            ASK, render, "minimax-m3", "m53-pre-leak", ["x"])
        msg = out["request"]["messages"][-1]["content"]
        assert "<turn domain=" not in msg
        assert "<turn declined=" not in msg
        assert "RENDERED MATERIAL" in msg


def test_routed_lane_body_carries_no_tag_on_delivered_bytes(rows, monkeypatch):
    """declined -> rule 1 route -> SOURCE_LANE bytes.

    The live battery's round-5 shape: the delivered body is the LANE render,
    not the draft, so the draft-side strip cannot save it.

    HONEST LABEL — this is a REGRESSION PIN, not a discrimination proof. It
    passes at 20678c8 as well as here (measured in a clean `git archive`
    extraction), because `_decide` already computed `lane_clean` through
    `_strip` -> `strip_markers`, which knew the attestation form after the
    round-4 vocabulary fix. It pins the lane branch against a future change
    that stops routing the lane body through the marker strip.
    """
    from hermes_router.core import delivery_decision as _dd

    monkeypatch.setattr(
        domain_gate, "read",
        lambda sid: {"domain": "cyber_offensive", "gate": "flagged"})
    lane = '<turn domain="cyber_offensive" type="declined" />\n\n' + \
        "No. Keylogging a corporate laptop is credential theft."
    facts = _build_facts_public(ACTION_BODY, {"session_id": "m53-lane"},
                                None, lane, "")
    decision = _decide_public(facts)
    assert decision.delivered_source == _dd.SOURCE_LANE
    out = _delivered_bytes_public(decision, ACTION_BODY)
    delivered = ACTION_BODY if out is None else out
    assert decision.strip_bytes > 0
    assert "<turn domain=" not in delivered
    assert "<turn declined=" not in delivered
    assert "Keylogging a corporate laptop" in delivered


def _build_facts_public(*a, **k):
    from hermes_router.gate import registration as _reg
    return _reg._build_facts(*a, **k)


def _decide_public(facts):
    from hermes_router.gate import registration as _reg
    return _reg._decide(facts)


def _delivered_bytes_public(decision, entered):
    from hermes_router.gate import registration as _reg
    return _reg._delivered_bytes(decision, entered)


# ---------------------------------------------------------------------------
# 10. THE UNCONDITIONAL BOUNDARY — paths where no decision/classification runs
# ---------------------------------------------------------------------------
# The sweep measured 9/19 armed denials still shipping the tag: some turns had
# NO delivery_decision row (the decision never ran) and some skipped POST
# classification entirely. A strip bound to those paths cannot cover them, so
# the wrapper's entry strip must, and the edge must return explicit bytes
# (None is the host pass-through = its own raw, tag-bearing copy).

@pytest.mark.parametrize("label,tag", [
    ("decision-skipped", TAG_DECLINED),
    ("classification-skipped", TAG_PRODUCED),
    ("mid-body", TAG_PRODUCED),
    ("unterminated-fragment", '<turn domain="none" type="produced" /'),
])
def test_boundary_strips_tag_when_no_decision_or_classification_runs(
        label, tag, rows, monkeypatch):
    from hermes_router.gate import registration as reg

    if label == "mid-body":
        body = "First line of the answer.\n" + tag + "\nLast line."
    else:
        body = tag + "\n\n" + BENIGN_BODY

    # Simulate BOTH failure classes: the orchestration seam never contributes a
    # decision (class A) and never classifies (class B).
    monkeypatch.setattr(
        reg.orchestration, "on_transform_llm_output",
        lambda **kwargs: None)

    _prime("m53-boundary-" + label)
    out = reg.on_transform_llm_output_guarded(
        response_text=body, session_id="m53-boundary-" + label, model="m")
    delivered = "" if out is None else out
    assert out is not None, label + ": None is the host pass-through (leak)"
    assert "<turn domain=" not in delivered, label
    assert "<turn declined=" not in delivered, label
    assert "answer" in delivered.lower() or "diff" in delivered.lower(), label
