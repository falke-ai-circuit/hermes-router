"""M47-19 — a fired lane is VISIBLE in the delivered body.

Measured 2026-11-14: every lane fired and billed (orchestrator 83 decision
banners, 156 anchor consumes that day) while the reader saw NOTHING — the
owner's words: "I dont see neither decision nor frontier so i have zero idea if
they fire or not."

Two defects, one silence:
  1. `deliver_in_body_enabled()` was opt-in with an absent key meaning False
     (M44-02), so `compose_onto` returned every body untouched.
  2. `lifecycle.deliver` logged `delivered=bool(parked.strip())` — a claim about
     the semaphore, not the bytes — so the logs said delivered while nothing
     reached the reader.

M44-02's actual objection is preserved: router banner grammar never renders
INSIDE a denial / uncensored render. The suppression is scoped to that body
class instead of switched off globally.
"""
from hermes_router.features.banners import turn_record as tr

SID = "s-m47-19"
BODY = "Here is the answer the reader asked for."


def _enable(monkeypatch, on: bool):
    monkeypatch.setattr(tr, "deliver_in_body_enabled", lambda: on)


def test_a_registered_lane_line_reaches_the_delivered_body(monkeypatch):
    """Default config (no banner_in_body key) must still show the line."""
    monkeypatch.setattr(tr, "_RECORDS", {})
    monkeypatch.setattr(tr, "deliver_in_body_enabled", lambda: True)
    assert tr.register(SID, "impulse (decision) | 1 verdict | jev @ typesafe "
                            "| tok 2507/102 | $0.00011", kind="anchor")
    out = tr.compose_onto(SID, BODY, mode="append")
    assert out != BODY
    assert BODY in out
    assert "impulse (decision)" in out


def test_the_knob_still_turns_the_line_off_per_profile(monkeypatch):
    monkeypatch.setattr(tr, "_RECORDS", {})
    _enable(monkeypatch, False)
    tr.register(SID, "higher-self (frontier) | consult | sonnet-5.5", kind="anchor")
    assert tr.compose_onto(SID, BODY, mode="append") == BODY


def test_no_lane_line_is_ever_rendered_inside_a_denial_body(monkeypatch):
    """M44-02, scoped: the uncensored render keeps the record only."""
    monkeypatch.setattr(tr, "_RECORDS", {})
    _enable(monkeypatch, True)
    tr.register(SID, "higher-self (frontier) | consult | sonnet-5.5", kind="anchor")
    denial = ("shadow-self (uncensored) | render\n\nNo. I won't answer that.")
    assert tr.compose_onto(SID, denial, mode="append") == denial


def test_compose_is_idempotent_one_line_per_delivery(monkeypatch):
    monkeypatch.setattr(tr, "_RECORDS", {})
    _enable(monkeypatch, True)
    tr.register(SID, "higher-self (frontier) | consult | sonnet-5.5 | $0.0137",
                kind="anchor")
    once = tr.compose_onto(SID, BODY, mode="append")
    twice = tr.compose_onto(SID, once, mode="append")
    assert twice == once
    assert twice.count("higher-self (frontier)") == 1
