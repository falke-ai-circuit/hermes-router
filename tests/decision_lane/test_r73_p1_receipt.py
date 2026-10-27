"""R73 P1: every delivered reading ships with a receipt through the existing banner path.

The reading is a billed call that changes the next move, and it left no visible trace:
reflex_lane had zero banner references while decision.py had 41, so only the older PRE/POST
path ever bannered (4/4 readings delivered, 0/4 banners on the API surface; consume side
proved it - `anchor_banner_consume parked=False delivered=False reason=no_park_this_turn`).

These pins fix the contract, not the plumbing: ONE mechanism (debug_banner.format_banner +
park_anchor_banner, consumed at the post seam), and the receipt must name lane, trigger,
model, tokens, cost and row id.
"""
import pytest

from hermes_router import debug_banner as db
from hermes_router import reflex_lane as rl

PARSED = {"options": [{"p": 0.61, "label": "run the check", "kind": "test", "target": ""},
                      {"p": 0.44, "label": "read the file", "kind": "read", "target": ""}]}


@pytest.fixture()
def parked(monkeypatch):
    got = []

    def _park(session_id, banner_text, task_id=""):
        got.append((session_id, banner_text, task_id))

    monkeypatch.setattr(db, "park_anchor_banner", _park)
    return got


def test_receipt_names_lane_trigger_model_tokens_cost_and_row(parked):
    data = {"usage": {"prompt_tokens": 1330, "completion_tokens": 37}}
    banner = rl._receipt("sess-1", PARSED, data, "every_call")
    assert banner, "a delivered reading must produce a receipt"
    assert parked, "the receipt must be parked for the post seam to deliver"
    sid, text, task_id = parked[-1]
    assert sid == "sess-1"
    assert "impulse (decision)" in text          # lane label, same as the other lanes
    assert "every_call" not in text               # R74-A2: xN implies cadence; field dropped
    assert "jev" in text.lower() and "?" not in text   # model + RESOLVED host, never "?"
    assert "@ typesafe" in text                   # the real provider, named like other lanes
    assert "1330/37 tok" in text                  # tokens (summed, compact cell)
    assert "$" in text                            # cost
    assert "row 1" in text                        # row id (no "reflex." prefix, R74-A2)
    assert "1 lead" in text or "1 hold" in text   # the turn's verdict class, singular
    assert task_id == "reflex"  # R74: constant id -> R9d replace, one banner per turn


def test_receipt_is_the_shared_banner_shape_not_a_new_one(parked):
    banner = rl._receipt("sess-2", PARSED, {"usage": {}}, "midturn")
    assert banner.startswith(db.BANNER_HEAD), "one banner family, not a fourth renderer"
    # R74-A2 dropped the trigger/seam field for size; the shared shape is what is pinned
    assert "impulse (decision) |" in banner, banner
    assert "@ typesafe" in banner, banner


def test_no_receipt_when_nothing_was_delivered(parked):
    # format_banner refuses unknown/forbidden lanes and oversized text; a refused banner must
    # park nothing rather than emit a stub.
    import hermes_router.debug_banner as d

    orig = d.format_banner
    d.format_banner = lambda *a, **k: ""
    try:
        assert rl._receipt("sess-3", PARSED, {"usage": {}}, "every_call") == ""
    finally:
        d.format_banner = orig
    assert not parked
