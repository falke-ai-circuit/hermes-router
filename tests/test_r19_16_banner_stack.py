"""R19.16 FIX 4 (Goran addendum): ALL fired banners must be shown, stacked,
none eaten. The parked aggregate is ONE BLOCK, one segment per FIRED
banner, in fire order — higher-self frontier + reflex decision segments
coexist. Latest-wins starvation (parked=False in reviewer log 17:29:52) is
gone. Canonical content is NEVER trimmed to make banner room.
"""
import time

import pytest

from hermes_router import debug_banner as DB


@pytest.fixture()
def _clean():
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()
    yield
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()


FRONTIER = ("· router · higher-self (frontier) | completion_audit | "
            "z-ai/glm-5.3 | tok 900/400 | $0.000100 | 3s | initiator=user")
REFLEX_1 = ("· router · reflex (decision) | manual | typesafe/jev-router "
            "| tok 100/20 | $0.000004 | 1s | initiator=user")
REFLEX_2 = ("· router · reflex (decision) | midturn | typesafe/jev-router "
            "| tok 120/25 | $0.000005 | 2s | initiator=user")


def test_frontier_plus_two_reflex_all_stack(_clean):
    """PIN: 1 frontier consult + 2 reflex verdicts -> delivered banner
    block contains ALL THREE segments, in fire order."""
    DB.park_anchor_banner("s-f4", FRONTIER, task_id="t1")
    DB.park_anchor_banner("s-f4", REFLEX_1, task_id="t2")
    DB.park_anchor_banner("s-f4", REFLEX_2, task_id="t3")
    block = DB.consume_parked_banner("s-f4")
    assert "higher-self (frontier)" in block
    assert block.count("reflex (decision)") == 2
    assert block.count("· router ·") == 3
    # fire order preserved
    assert block.index("higher-self (frontier)") \
        < block.index("manual") < block.index("midturn")
    assert DB.consume_parked_banner("s-f4") == ""  # consumed clean


def test_r17_two_lane_turn_no_starvation(_clean):
    """The exact live failure: midturn reflex verdict parked AFTER the
    frontier consult must NOT eat it (latest-wins starvation is gone)."""
    DB.park_anchor_banner("s-f4b", FRONTIER, task_id="tf")
    DB.park_anchor_banner("s-f4b", REFLEX_1, task_id="tr")
    block = DB.consume_parked_banner("s-f4b")
    assert "higher-self (frontier)" in block and "reflex (decision)" in block


def test_same_task_retry_replaces_in_place(_clean):
    """R9d retry semantics: re-park for the SAME task_id replaces that
    task's segment in place — no duplicate line."""
    DB.park_anchor_banner("s-f4c", FRONTIER, task_id="t1")
    retry = FRONTIER.replace("tok 900/400", "tok 950/420")
    DB.park_anchor_banner("s-f4c", retry, task_id="t1")
    DB.park_anchor_banner("s-f4c", REFLEX_1, task_id="t2")
    block = DB.consume_parked_banner("s-f4c")
    assert block.count("· router ·") == 2
    assert "tok 950/420" in block and "tok 900/400" not in block


def test_identical_repark_dedupes(_clean):
    DB.park_anchor_banner("s-f4d", REFLEX_1, task_id="t1")
    DB.park_anchor_banner("s-f4d", REFLEX_1, task_id="t1")
    block = DB.consume_parked_banner("s-f4d")
    assert block.count("· router ·") == 1


def test_canonical_never_trimmed(_clean):
    """append_banner attaches the stacked block; canonical text untouched."""
    DB.park_anchor_banner("s-f4e", FRONTIER, task_id="t1")
    DB.park_anchor_banner("s-f4e", REFLEX_1, task_id="t2")
    block = DB.consume_parked_banner("s-f4e")
    answer = "The migration plan is: phased rollout with rollback points."
    out = DB.append_banner(answer, block, _knob_checked=True)
    assert out.startswith(answer)  # canonical first, banner appended
    assert answer in out and len(out) > len(answer)
    assert "higher-self (frontier)" in out and "reflex (decision)" in out


def test_bounded_segments(_clean):
    """Segment cap: the banner BLOCK is bounded (never the canonical)."""
    for i in range(8):
        DB.park_anchor_banner("s-f4f", "· router · x | seg %d ·" % i,
                              task_id="t%d" % i)
    block = DB.consume_parked_banner("s-f4f")
    assert block.count("· router ·") <= 4  # _MAX_PARK_SEGMENTS


def test_frontier_banner_delivery_path_end_to_end(_clean):
    """Delivery parity: park stacked block -> consume -> append to delivery
    -> the delivered text carries every lane that fired this turn."""
    DB.park_anchor_banner("s-f4g", FRONTIER, task_id="t1")
    DB.park_anchor_banner("s-f4g", REFLEX_1, task_id="t2")
    DB.park_anchor_banner("s-f4g", REFLEX_2, task_id="t3")
    delivered = DB.append_banner("canonical answer", "\n"
                                 + DB.consume_parked_banner("s-f4g"),
                                 _knob_checked=True)
    for marker in ("higher-self (frontier)", REFLEX_1.split(" | ")[0],
                   "reflex (decision)"):
        assert marker in delivered
    assert delivered.count("· router ·") == 3
