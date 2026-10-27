"""R74-A2 (11edf2b) review probe — round 2, run OUTSIDE the suite.

Covers the three things the shipped pins could have been retargeted around:
  * F1 regression (never-consumed turn must not leak into the next turn)
  * measured line length for fire counts 1..10, and N=1 vs today's 120c line
  * the compressed cells / resolved host on a mixed lead+hold turn and a priced turn

Run: PYTHONPATH=/opt/data/plugins python -m pytest reviews/r74-2026-10-27/probe_round2.py -q -s
"""
import pytest

from hermes_router import advisory_consumption as act
from hermes_router import debug_banner as db
from hermes_router import reflex_lane as rl

SID = "a2-probe"
TODAY_LINE = ("· router · impulse (decision) | every_call | jev-latest @ ? | tok 2749/102 | "
              "$0.0000* | initiator=router | row=reflex.17")

PARSED = {"options": [{"p": 0.61, "label": "a", "kind": "test", "move": "m1", "target": ""},
                      {"p": 0.44, "label": "b", "kind": "read", "move": "m2", "target": ""}],
          "margin": 0.17}
SOFT = {"options": [{"p": 0.50, "label": "a", "kind": "test", "move": "m1", "target": ""},
                    {"p": 0.47, "label": "b", "kind": "read", "move": "m2", "target": ""}],
        "margin": 0.03}


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


def test_R2_1_f1_never_consumed_turn_does_not_leak():
    act.note_turn_open(SID, "turn one question")
    rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    rl._receipt(SID, PARSED, _usage(1000, 100), "every_call")
    first = db._ANCHOR_BANNERS.get(SID, "")
    print("\nR2.1 turn1 (never consumed): %r" % first)
    assert "x2" in first and "2000/200 tok" in first, first
    assert db._ANCHOR_BANNERS.get(SID), "precondition: the parked banner survives into turn 2"

    assert act.note_turn_open(SID, "a different question") == 2
    banner = rl._receipt(SID, PARSED, _usage(7, 3), "every_call")
    print("R2.1 turn2: %r" % banner)
    assert "x" not in banner.split("|")[0].replace("impulse", "").replace("(decision)", ""), banner
    assert "7/3 tok" in banner, banner
    assert "2000/200" not in banner and "2007/203" not in banner, banner
    assert banner.rstrip().endswith("| row 3"), banner


def test_R2_2_size_budget_every_fire_count():
    print()
    for n in range(1, 11):
        sid = "%s-len-%d" % (SID, n)
        _wipe(sid)
        banner = ""
        for i in range(n):
            banner = rl._receipt(sid, PARSED, _usage(900 + i, 30 + i), "every_call")
        print("R2.2 N=%-2d len=%-3d %r" % (n, len(banner), banner))
        assert len(banner) <= 140, (n, len(banner))
        if n == 1:
            assert len(banner) <= 120 and len(banner) <= len(TODAY_LINE), (len(banner), banner)
        _wipe(sid)


def test_R2_3_compressed_cells_and_host():
    b = ""
    for parsed, u in ((PARSED, (1000, 100)), (SOFT, (2000, 80)), (PARSED, (3134, 100))):
        b = rl._receipt(SID, parsed, _usage(*u), "every_call")
    print("\nR2.3 mixed: %r len=%d" % (b, len(b)))
    for needle in ("x3", "2 lead · 1 hold", "jev @ typesafe", "6134/280 tok", "row 1-3"):
        assert needle in b, (needle, b)
    assert "every_call" not in b and "?" not in b, b
    assert "reflex." not in b, b


def test_R2_4_priced_cost_is_five_decimals():
    acc = {"fires": 1, "leads": 1, "no_lead": 0, "tok_in": 10, "tok_out": 2, "cost": 0.00017,
           "first_row": 1, "last_row": 1, "triggers": set(), "last_text": "",
           "last_seam": "every_call", "turn_no": 0}
    out = rl._park_summary(SID, acc, rl._cfg(None))
    print("\nR2.4 priced: %r" % out)
    assert "$0.00017" in out, out
