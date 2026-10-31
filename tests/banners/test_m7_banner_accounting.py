"""M7 — BANNER ACCOUNTING (operator ruling OPTION C).

(a) A consult that WILL NOT be delivered is not fired and not billed at all:
    cooldown / pre-suppressed / cap-blocked paths where no consumer ever
    existed. PROOF: the backend call is never reached (call counter == 0).
(b) Where a consult DID run (cost incurred) but delivery is suppressed, a
    banner is emitted carrying an explicit SUPPRESSED state naming the reason
    token. PROOF: the parked banner contains "SUPPRESSED state=<reason>".

Both halves are pinned separately so a regression in one cannot hide behind a
pass in the other.
"""
import json
import sqlite3

import pytest

from hermes_router import decision, debug_banner


SID = "s-m7-accounting"


def _cfg(**over):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "model": "jev-latest", "backend": "jev"})
    cfg.update(over)
    return cfg


def _envelope():
    """Minimal v3 envelope: two DISTINCT labelled routes (a real fork)."""
    return {
        "session_id": SID,
        "trigger": "manual",
        "fork_class": "manual_fork",
        "options": [
            {"id": "opt-1", "label": "ship the banner change"},
            {"id": "opt-2", "label": "hold and measure first"},
        ],
        "digest": {},
    }


def _ids(**over):
    ids = {"session_id": SID, "task_id": "t-m7",
           "trigger": "manual", "initiator": "user"}
    ids.update(over)
    return ids


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    decision.reset_limits()
    decision.reset_v3_limits()
    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": sqlite3.connect(path))
    monkeypatch.setattr("hermes_router.core.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    yield path
    decision.reset_limits()
    decision.reset_v3_limits()


@pytest.fixture()
def parked(monkeypatch):
    """Capture every banner that reaches the park store."""
    seen = []
    monkeypatch.setattr(
        debug_banner, "park_anchor_banner",
        lambda sid, text, task_id="": seen.append(str(text)))
    return seen


# ---------------------------------------------------------------------------
# (a) PRE-CALL: delivery is impossible -> no call is made at all
# ---------------------------------------------------------------------------

def test_pre_suppressed_cap_path_makes_no_billable_call(
        ledger, parked, monkeypatch):
    """(a) cap blocked = no consumer will ever exist -> ZERO backend calls.

    _invoke() is the single post-detection pipeline every trigger shares. The
    cap check must fire BEFORE the consult is dispatched, so the billed call
    never happens and no banner (suppressed or otherwise) is parked: nothing
    ran, so there is nothing to account for.
    """
    calls = []

    def _call(envelope, cfg):
        calls.append(envelope)
        return ("{}", {"model": "jev-latest"}, "ok")

    monkeypatch.setattr(decision, "call_backend", _call)
    monkeypatch.setattr(decision, "caps_check",
                        lambda task_id, session_id, cfg:
                        decision.REASON_CAP_EXHAUSTED + ":global_daily")

    decision._invoke(SID, "t-m7", "ship it or hold it?", "manual",
                     _cfg(), lambda *a, **k: None, initiator="user")

    assert calls == [], "the cap-blocked path billed a consult it cannot deliver"
    assert parked == [], "a pre-call suppression must not emit a spend banner"


def test_pre_suppressed_breaker_path_makes_no_billable_call(
        ledger, parked, monkeypatch):
    """(a) breaker open (the cooldown analogue at the consult seam)."""
    calls = []
    monkeypatch.setattr(decision, "call_backend",
                        lambda e, c: calls.append(e) or ("{}", {}, "ok"))
    monkeypatch.setattr(decision, "_breaker_open", lambda cfg: True)

    decision._invoke(SID, "t-m7b", "ship it or hold it?", "manual",
                     _cfg(), lambda *a, **k: None, initiator="user")

    assert calls == []
    assert parked == []


# ---------------------------------------------------------------------------
# (b) POST-CALL: the call ran -> a SUPPRESSED banner names the reason token
# ---------------------------------------------------------------------------

def test_ran_but_suppressed_emits_banner_with_suppressed_state(
        ledger, parked, monkeypatch):
    """(b) parse_fail: the backend call was made (tokens billed), the verdict
    never validated, the advisory is suppressed — the banner must still say so
    with the reason token, never vanish."""
    def _call(envelope, cfg):
        return ("not a verdict at all", {"model": "jev-latest",
                                         "tokens_in": 120, "tokens_out": 8}, "")

    monkeypatch.setattr(decision, "call_backend", _call)

    decision._v3_worker(_envelope(), _ids(), _cfg(), lambda *a, **k: None)

    assert parked, "a consult that ran and billed emitted no banner (silent spend)"
    text = parked[-1]
    assert "SUPPRESSED" in text, text
    assert "state=parse_fail" in text, text
    # The banner is a RECEIPT: it still carries the usage the call incurred.
    assert "tok 120/8" in text, text


def test_ran_but_suppressed_noise_band_banner_names_the_gate(
        ledger, parked, monkeypatch):
    """(b) emission-gate suppression: the call ran, the advisory is withheld by
    the band gate — the billing receipt rides out with the state named."""
    def _call(envelope, cfg):
        return (json.dumps({"choice": "opt-1", "confidence": 0.42,
                            "alternatives": ["opt-2"]}),
                {"model": "jev-latest", "tokens_in": 90, "tokens_out": 12,
                 "latency_s": 0.2}, "ok")

    monkeypatch.setattr(decision, "call_backend", _call)

    decision._v3_worker(_envelope(), _ids(), _cfg(), lambda *a, **k: None)

    assert parked, "suppressed advisory produced no banner at all"
    text = parked[-1]
    assert "SUPPRESSED" in text and "state=noise_band" in text, text
    # suppressed advisory text never rides the banner
    assert "ship the banner change" not in text or "advisory" not in text


def test_qualified_verdict_banner_has_no_suppressed_marker(
        ledger, parked, monkeypatch):
    """Negative control: a delivered consult must NOT be stamped suppressed —
    otherwise the marker would carry no information."""
    def _call(envelope, cfg):
        return (json.dumps({"choice": "opt-1", "confidence": 0.82,
                            "alternatives": ["opt-2"]}),
                {"model": "jev-latest", "tokens_in": 90, "tokens_out": 12,
                 "latency_s": 0.2}, "ok")

    monkeypatch.setattr(decision, "call_backend", _call)

    decision._v3_worker(_envelope(), _ids(), _cfg(), lambda *a, **k: None)

    assert parked, "a delivered consult parked nothing"
    assert "SUPPRESSED" not in parked[-1], parked[-1]
