"""M50-41: the POST consult intercepts a DRAFT — the agent's adjusted body ships.

Owner behavioural model (2026-11-18): "you respond; if frontier is triggered it
emits, and you adjust the final response if necessary ... if frontier is
triggered you only deliver the final response after frontier consult."

So the pre-consult body is a DRAFT. When the agent's own model adjusts it after
seeing the advisory, that ADJUSTED body is the final answer and the draft must
never be a delivery candidate. Two invariants ride on it:

  * the router never blocks a turn — no stamp, or any failure, ships the draft,
    which is exactly the pre-M50-41 behaviour;
  * the router never substitutes prose — `out` is the AGGREGATED transform
    output, so only the audit's provenance stamp may promote a body to "final".
    An untagged lane render still loses to a compliant body (M34-04 stays shut).

Measured live defect this repairs: `closure_pass_applied in_chars=1955
out_chars=2221 changed=1` then `delivery_decision D4 COMPLIANT_DELIVER
delivered_source=main` — the client received 1958 chars (the draft + banner),
i.e. a second model call per audited turn was paid for and discarded.
"""
from hermes_router.core import delivery_decision as _dd
from hermes_router.features.banners import turn_record as _tr
from hermes_router.gate.registration import (
    _build_facts, _decide, _delivered_bytes)

DRAFT = "DRAFT: the work is fine, nothing to report here.\n" * 30
ADJUSTED = "ADJUSTED: the work is fine, but the restore point is unverified.\n" * 36
RENDER = "· router · lane render text that must never replace an answer.\n" * 20


def _delivered_for(entered, lane):
    """Run the seam's real decision path: facts -> decide -> delivered bytes."""
    facts = _build_facts(entered, {}, None, lane, attempt=0)
    dec = _decide(facts)
    return dec, _delivered_bytes(dec, DRAFT)


def test_adjusted_agent_body_is_delivered_not_the_draft():
    dec, body = _delivered_for(ADJUSTED, ADJUSTED)
    assert isinstance(body, str) and body.strip(), dec.case_id
    assert body == ADJUSTED
    assert body != DRAFT


def test_draft_still_ships_when_the_agent_did_not_adjust():
    _dec, body = _delivered_for(DRAFT, DRAFT)
    # unchanged path: assert the draft is what the user gets, by either route
    assert body is None or body == DRAFT
    assert ADJUSTED not in str(body or "")


def test_untagged_lane_render_cannot_replace_a_compliant_body():
    """M34-04: without the audit stamp, a render is not an agent adjustment."""
    dec, body = _delivered_for(DRAFT, RENDER)
    shipped = DRAFT if body is None else str(body)
    assert DRAFT in shipped, dec.case_id
    assert RENDER not in shipped


def test_stamp_is_isolated_per_session():
    assert _tr.main_adjusted("sess-m5041-a") is False
    _tr.mark_main_adjusted("sess-m5041-a")
    assert _tr.main_adjusted("sess-m5041-a") is True
    # a different session must never inherit another turn's adjustment
    assert _tr.main_adjusted("sess-m5041-b") is False


def test_reads_fail_open_to_false_and_stamping_never_raises(monkeypatch):
    def _boom(_sid):
        raise RuntimeError("record store unavailable")

    monkeypatch.setattr(_tr, "_record", _boom)
    # the seam must fall back to the draft, never raise and never block a turn
    assert _tr.main_adjusted("sess-m5041-c") is False
    _tr.mark_main_adjusted("sess-m5041-c")  # must not raise
