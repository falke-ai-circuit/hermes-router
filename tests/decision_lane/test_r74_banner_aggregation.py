"""R74 — one banner per turn: the reflex lane parks a SUMMARY under a constant task id.

Pre-R74 every fire parked its own segment with a unique task id ("reflex|" + row), so the
anchor kind's R9d per-task replace could never match one and a two-fire turn delivered two
banner lines (fleet-wide: 116 turns at row=reflex.2 vs 60 at .1). The replace semantics were
always correct — the lane simply never used them.

These pins fix the observable contract: exactly ONE reflex banner line per delivered turn,
carrying the turn's summed tokens/cost and the row RANGE, resetting after delivery, and
dropped entirely on a turn that claimed banner ownership for its own banner.
"""
import pytest

from hermes_router import advisory_consumption as act
from hermes_router import debug_banner as db
from hermes_router import reflex_lane as rl
from hermes_router.features.banners.lifecycle import LIFECYCLE

SID = "r74-sess"

PARSED = {"options": [{"p": 0.61, "label": "run the check", "kind": "test",
                       "move": "run_check", "target": ""},
                      {"p": 0.44, "label": "read the file", "kind": "read",
                       "move": "read_file", "target": ""}],
          "margin": 0.17}
# the lane's OWN soft class ("no clear lead"): top clears the floor, the margin does not
SOFT = {"options": [{"p": 0.50, "label": "run the check", "kind": "test",
                     "move": "run_check", "target": ""},
                    {"p": 0.47, "label": "read the file", "kind": "read",
                     "move": "read_file", "target": ""}],
        "margin": 0.03}


def _usage(ti, to):
    return {"usage": {"prompt_tokens": ti, "completion_tokens": to}}


@pytest.fixture(autouse=True)
def clean():
    rl.clear(SID)
    act.clear_turn_ledger(SID)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(SID, None)
    yield
    rl.clear(SID)
    act.clear_turn_ledger(SID)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(SID, None)


def _banner_lines(text):
    return [ln for ln in str(text or "").splitlines() if db.BANNER_HEAD in ln]


def test_a_three_fires_one_turn_one_banner_with_sums_and_row_range(monkeypatch):
    monkeypatch.setattr(db, "debug_banner_enabled", lambda: True)
    for parsed, (ti, to) in ((PARSED, (1000, 100)), (SOFT, (2000, 80)),
                            (PARSED, (3134, 100))):
        assert rl._receipt(SID, parsed, _usage(ti, to), "every_call")

    parked = db._ANCHOR_BANNERS.get(SID, "")
    assert parked, "the turn's summary must be parked"
    assert _banner_lines(parked).__len__() == 1, parked
    assert "x3" in parked, parked
    assert "tok 6134/280" in parked, "tokens are SUMMED, never averaged: %s" % parked
    assert "row=reflex.1-3" in parked, parked
    assert "2 leads · 1 no-lead" in parked, parked
    # the whole stored slot is ONE segment (constant task id -> replace, not append)
    assert len(db._ANCHOR_SEGS.get(SID, [])) == 1, db._ANCHOR_SEGS.get(SID)

    # ...and the delivery edge attaches exactly one line
    delivered = LIFECYCLE.deliver(SID, "body", "anchor", "benign")
    assert delivered.startswith("body")
    assert len(_banner_lines(delivered)) == 1, delivered
    assert "x3" in delivered


def test_b_single_fire_is_todays_shape_minus_the_unique_row_suffix():
    banner = rl._receipt(SID, PARSED, _usage(1330, 37), "every_call")
    assert banner
    assert " x1" not in banner and "x1 |" not in banner
    # byte-comparable to the pre-R74 line: same formatter, same field order, row suffix only
    cfg = rl._cfg(None)
    expected = db.format_banner(
        lane="reflex", trigger="every_call",
        model=str(cfg.get("jev_native_model") or "jev-latest"), endpoint="",
        tokens_in=1330, tokens_out=37, est_cost=0.0, latency_s=0,
        session_id=SID, route_id="reflex.1", initiator="router",
    )
    assert banner.startswith(expected), banner
    assert banner.endswith("| row=reflex.1"), banner
    assert "leads" not in banner, "the verdict clause appears only when N > 1"


def test_c_accumulator_resets_after_delivery_second_turn_starts_at_zero():
    for ti, to in ((1000, 50), (2000, 50), (3000, 50)):
        rl._receipt(SID, PARSED, _usage(ti, to), "every_call")
    assert "tok 6000/150" in db._ANCHOR_BANNERS.get(SID, "")

    consumed = db.consume_parked_banner(SID)      # the delivery seam
    assert consumed
    assert not db._ANCHOR_BANNERS.get(SID)

    banner = rl._receipt(SID, PARSED, _usage(10, 2), "every_call")
    assert banner
    assert "tok 10/2" in banner, "the second turn must not inherit the first turn's counts"
    assert "x" not in banner.split("|")[0].replace("impulse", "").replace("(decision)", ""), banner
    assert "row=reflex.4" in banner


def test_d_ownership_claimed_turn_drops_the_reflex_summary():
    act.note_turn_open(SID, "do the thing")
    for _ in range(3):
        rl._receipt(SID, PARSED, _usage(500, 20), "every_call")
    assert db._ANCHOR_BANNERS.get(SID), "sanity: the summary is parked before delivery"

    # the uncensored-render edge carries its OWN banner -> it claims ownership and the
    # parked summary is released without attaching (no double banner).
    assert db.claim_banner_ownership(SID, edge="uncensored-render") is True
    assert not db._ANCHOR_BANNERS.get(SID)

    # a fire AFTER the claim in the SAME turn must not resurrect a second line
    assert rl._receipt(SID, PARSED, _usage(500, 20), "every_call") == ""
    assert not db._ANCHOR_BANNERS.get(SID), "claimed turns carry their own banner only"
