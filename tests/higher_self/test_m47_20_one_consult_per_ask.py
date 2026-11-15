"""M47-20 — ONE frontier consult per ask: PRE or POST, never both.

Goran, 2026-11-14: "limit frontier consults to max post or pre for an ask ...
it should either trigger post or pre consult not both".

Why this needs a pin: the mutual exclusion already existed (`pre_consult_this_turn`,
completion_audit.py) but R34-19 deliberately bypassed it for a PAIRED POST — a PRE
advisory made the POST "become its confirmation", i.e. the same ask paid for two
frontier calls. Measured before the fix: 39 PRE + 28 POST fires in one day, 7
sessions carrying both legs, one session 7 PRE + 6 POST.

The pin asserts the BEHAVIOUR (paired POST stands down) and the escape hatch
(`one_consult_per_ask: false` restores R34-19 pairing) so a future rider cannot
silently reintroduce the double call.
"""
from hermes_router import completion_audit as ca
from hermes_router.core import state as st

SID = "s-m47-20"
ASK = "Rebuild the loader and prove the version reported is the one deployed."
BODY = ("Moved the stale shadow directory, restarted the gateway, verified 11/11 "
        "profiles report 6.17.9 and the loader picks the deployed copy. Tests: 14 "
        "passed. Evidence: verify_fleet_sync matrix all green after the move. "
        "Trace: loader dir order fixed, legacy dir archived under backups, each "
        "profile re-deployed from a clean working tree, md5 manifest matched on all "
        "eleven, and the reported version is now the one on disk rather than a "
        "shadow copy. No config was edited directly; the CLI wrote it atomically. "
        "Rollback path is the archived directory plus a gateway bounce, which is "
        "why the archive was kept rather than deleted after verification. " * 3)


def _stub_gates(monkeypatch):
    """Neutralise every OTHER gate so the exclusion is what is being measured."""
    monkeypatch.setattr(ca, "audit_mode", lambda: "complex")
    monkeypatch.setattr(ca, "_already_fired", lambda key: False)
    monkeypatch.setattr(ca, "_has_pending", lambda sid: False)
    monkeypatch.setattr(ca, "_is_refusal", lambda *a, **k: False, raising=False)


def test_paired_post_stands_down_when_pre_consulted_the_same_ask(monkeypatch):
    """Default: a PRE advisory does NOT buy a second, confirming consult."""
    _stub_gates(monkeypatch)
    monkeypatch.setattr(st, "pre_fired_this_turn", lambda *a, **k: True)
    monkeypatch.setattr(ca, "_one_consult_per_ask", lambda: True)
    ok, reason = ca.eligible(SID, ASK, BODY, "m", turn_n=3, paired=True)
    assert ok is False
    assert reason == "pre_consult_same_ask"


def test_the_knob_restores_r34_19_pairing_per_profile(monkeypatch):
    """`one_consult_per_ask: false` is the documented way back to pairing."""
    _stub_gates(monkeypatch)
    monkeypatch.setattr(st, "pre_fired_this_turn", lambda *a, **k: True)
    monkeypatch.setattr(ca, "_one_consult_per_ask", lambda: False)
    ok, reason = ca.eligible(SID, ASK, BODY, "m", turn_n=3, paired=True)
    assert reason != "pre_consult_same_ask"


def test_unpaired_post_still_stands_down_on_a_pre_flag(monkeypatch):
    """The original exclusion is untouched."""
    _stub_gates(monkeypatch)
    monkeypatch.setattr(st, "pre_fired_this_turn", lambda *a, **k: True)
    monkeypatch.setattr(ca, "_one_consult_per_ask", lambda: True)
    ok, reason = ca.eligible(SID, ASK, BODY, "m", turn_n=3, paired=False)
    assert ok is False
    assert reason == "pre_consult_this_turn"


def test_no_pre_flag_leaves_the_post_eligible(monkeypatch):
    """A POST on an ask with no PRE is the consult — it must not be suppressed."""
    _stub_gates(monkeypatch)
    monkeypatch.setattr(st, "pre_fired_this_turn", lambda *a, **k: False)
    monkeypatch.setattr(ca, "_one_consult_per_ask", lambda: True)
    ok, reason = ca.eligible(SID, ASK, BODY, "m", turn_n=4, paired=False)
    assert reason != "pre_consult_this_turn"
    assert reason != "pre_consult_same_ask"
