"""R74 — one banner per turn: the reflex lane parks a SUMMARY under a constant task id.

Pre-R74 every fire parked its own segment with a unique task id ("reflex|" + row), so the
anchor kind's R9d per-task replace could never match one and a two-fire turn delivered two
banner lines (fleet-wide: 116 turns at row=reflex.2 vs 60 at .1). The replace semantics were
always correct — the lane simply never used them.

These pins fix the observable contract: exactly ONE reflex banner line per delivered turn,
carrying the turn's summed tokens/cost and the row RANGE, resetting after delivery, dropped
entirely on a turn that claimed banner ownership for its own banner, and — R74-A2 — BOUNDED
by the size of the line it replaces (today's 120c) with the real resolved host named.
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

# the live single-fire line this summary replaces (measured 2026-10-27, 120 chars)
TODAY_LINE = ("· router · impulse (decision) | every_call | jev-latest @ ? | tok 2749/102 | "
              "$0.0000* | initiator=router | row=reflex.17")


def _usage(ti, to):
    return {"usage": {"prompt_tokens": ti, "completion_tokens": to}}


def _wipe(sid):
    rl.clear(sid)
    act.clear_turn_ledger(sid)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(sid, None)


@pytest.fixture(autouse=True)
def clean():
    _wipe(SID)
    yield
    _wipe(SID)


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
    assert "6134/280 tok" in parked, "tokens are SUMMED, never averaged: %s" % parked
    assert "row 1-3" in parked, parked
    assert "2 lead · 1 hold" in parked, parked
    # the whole stored slot is ONE segment (constant task id -> replace, not append)
    assert len(db._ANCHOR_SEGS.get(SID, [])) == 1, db._ANCHOR_SEGS.get(SID)

    # ...and the delivery edge attaches exactly one line
    delivered = LIFECYCLE.deliver(SID, "body", "anchor", "benign")
    assert delivered.startswith("body")
    assert len(_banner_lines(delivered)) == 1, delivered
    assert "x3" in delivered


def test_b_single_fire_keeps_the_fleet_shape_and_the_size_budget():
    banner = rl._receipt(SID, PARSED, _usage(1330, 37), "every_call")
    assert banner
    assert banner.startswith(db.BANNER_HEAD), "one banner family, not a fourth renderer"
    assert " x1" not in banner and "x1 |" not in banner
    assert "1 lead" in banner
    assert "leads" not in banner, "verdict classes render SINGULAR"
    assert "1330/37 tok" in banner, banner
    assert banner.endswith("| row 1"), banner
    # R74-A2: the summary of a single fire is never LONGER than the line it replaces
    assert len(banner) <= len(TODAY_LINE) <= 120, (len(banner), banner)


def test_c_accumulator_resets_after_delivery_second_turn_starts_at_zero():
    for ti, to in ((1000, 50), (2000, 50), (3000, 50)):
        rl._receipt(SID, PARSED, _usage(ti, to), "every_call")
    assert "6000/150 tok" in db._ANCHOR_BANNERS.get(SID, "")

    consumed = db.consume_parked_banner(SID)      # the delivery seam
    assert consumed
    assert not db._ANCHOR_BANNERS.get(SID)

    banner = rl._receipt(SID, PARSED, _usage(10, 2), "every_call")
    assert banner
    assert "10/2 tok" in banner, "the second turn must not inherit the first turn's counts"
    assert "x" not in banner.split("|")[0].replace("impulse", "").replace("(decision)", ""), banner
    assert "row 4" in banner


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


def test_e_rendered_line_stays_inside_the_size_budget_for_every_fire_count():
    """R74-A2: <= 140c at ANY fire count; a single fire must not beat today's 120c."""
    for n in range(1, 11):
        sid = "%s-len-%d" % (SID, n)
        _wipe(sid)
        banner = ""
        for i in range(n):
            banner = rl._receipt(sid, PARSED, _usage(900 + i, 30 + i), "every_call")
            assert banner, "fire %d of %d produced no banner" % (i + 1, n)
        assert len(banner) <= 140, (n, len(banner), banner)
        if n == 1:
            assert len(banner) <= 120 and len(banner) <= len(TODAY_LINE), (len(banner), banner)
        else:
            assert ("x%d" % n) in banner, banner
        assert _banner_lines(banner).__len__() == 1, banner


def test_f_line_names_the_real_host_and_drops_the_cadence_field():
    """R74-A2: the decision banner is pattern-identical to the other lanes."""
    for parsed in (PARSED, SOFT):
        _wipe(SID)
        banner = rl._receipt(SID, parsed, _usage(1200, 40), "every_call")
        assert banner, "a delivered reading must still produce a receipt"
        assert "?" not in banner, "the host must be RESOLVED, never a literal '?': %s" % banner
        assert "every_call" not in banner, "xN implies cadence; the field is gone: %s" % banner
        assert "@ typesafe" in banner, banner
        assert "jev" in banner and "jev-latest" not in banner, banner
        assert "impulse (decision) |" in banner, "fleet lane shape preserved: %s" % banner


def test_g_never_consumed_turn_does_not_leak_into_the_next_turn():
    """F1 regression: a turn that ends without a delivery edge must not poison the next."""
    act.note_turn_open(SID, "turn one question")
    first = rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    second = rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    assert "x2" in second, second
    assert "2000/200 tok" in second, second
    # ...turn one's parked banner is NEVER consumed (no delivery edge this turn)
    assert db._ANCHOR_BANNERS.get(SID), "the banner is parked past its own turn"

    assert act.note_turn_open(SID, "a different turn two question") == 2
    banner = rl._receipt(SID, PARSED, _usage(7, 3), "every_call")
    assert banner, "turn two's fire must still render its own line"
    assert "x" not in banner.split("|")[0].replace("impulse", "").replace("(decision)", ""), banner
    assert "7/3 tok" in banner, "turn two reports ITS OWN tokens only: %s" % banner
    assert "2000/200 tok" not in banner and "2007/203" not in banner, banner
    assert "row 3" in banner and "-" not in banner.split("|")[-1], banner
    assert "1 lead" in banner, banner
