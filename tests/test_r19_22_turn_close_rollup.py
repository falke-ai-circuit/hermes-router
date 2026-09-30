"""R19.22 (v4.12.9, Goran — from operative's Kindle run): turn-close
aggregate banner — reflex segment becomes a compact rollup (verdict count
+ confidence histogram + summed REAL cost + top verdict labels inline),
and independent lanes each keep their own segment in fire order.

1) Rollup: '· router · reflex (decision) | N verdicts (k shown >=0.9) |
   tok ti/to | $total | initiator=agent' + 'Top verdicts: a / b'.
   Stand-downs are NOT verdicts.
2) Independent lanes: reflex AND frontier AND uncensored in one turn ->
   one segment per lane, fire order, none eaten.
3) Costs are real: summed Jev estimates from the turn's consumed rows.
4) The aggregate block counts as ONE banner (cap 3/session unaffected).
"""
from hermes_router import decision_midturn as DMT
from hermes_router import debug_banner as DB


def _clear():
    DB._ANCHOR_BANNERS.clear()
    DB._ANCHOR_TASKS.clear()
    DB._ANCHOR_SEGS.clear()


# --- 1: rollup format ------------------------------------------------------------

def test_rollup_counts_and_confidence_histogram():
    consumed = [{"choice": "opt-1", "confidence": 0.95, "label": "Approach 1",
                 "tokens_in": 100, "tokens_out": 50, "cost": 0.0002},
                {"choice": "opt-2", "confidence": 0.92, "label": "Approach 2",
                 "tokens_in": 90, "tokens_out": 40, "cost": 0.0002},
                {"choice": "opt-1", "confidence": 0.60, "label": "Approach 1",
                 "tokens_in": 80, "tokens_out": 30, "cost": 0.0001}]
    line = DMT._aggregate_line(3, 270, 120, 0.0005, consumed)
    assert line.startswith("· router · reflex (decision) |")
    assert "3 verdicts (2 shown >=0.9)" in line
    assert "tok 270/120" in line
    assert "$0.000500" in line
    assert "initiator=agent" in line
    # top-2 highest-confidence labels inline, <=60 chars each
    top = [l for l in line.split("\n") if l.startswith("Top verdicts:")]
    assert len(top) == 1
    labels = top[0].replace("Top verdicts: ", "").split(" / ")
    assert labels == ["Approach 1", "Approach 2"]
    assert all(len(l) <= 60 for l in labels)


def test_standdowns_not_counted():
    """Stand-downs (no_options/parse_fail) are NOT verdicts — filtered."""
    consumed = [{"choice": "opt-1", "confidence": 0.9, "label": "L1",
                 "tokens_in": 10, "tokens_out": 5, "cost": 0.0001},
                {"choice": "stand_down", "confidence": 0.0,
                 "tokens_in": 5, "tokens_out": 0, "cost": 0.0},
                {"choice": "unmapped", "confidence": 0.8,
                 "tokens_in": 5, "tokens_out": 0, "cost": 0.0}]
    line = DMT._aggregate_line(3, 20, 5, 0.0001, consumed)
    assert "1 verdicts" in line
    assert "tok 10/5" in line  # only the real verdict's tokens


def test_zero_verdicts_empty():
    assert DMT._aggregate_line(1, 10, 5, 0.0,
                               [{"choice": "stand_down"}]) == ""
    assert DMT._aggregate_line(0, 0, 0, 0.0, []) == ""


# --- 3: costs are real (summed Jev estimates) --------------------------------------

def test_costs_summed_from_rows_not_fabricated():
    consumed = [{"choice": "opt-1", "confidence": 0.9, "label": "L",
                 "tokens_in": 1378, "tokens_out": 58, "cost": 0.000058},
                {"choice": "opt-2", "confidence": 0.8, "label": "M",
                 "tokens_in": 1000, "tokens_out": 42, "cost": 0.000042}]
    line = DMT._aggregate_line(2, 2378, 100, 0.0001, consumed)
    assert "$0.000100" in line  # the exact sum of the two rows
    assert "$0.000000" not in line  # no zero cost with nonzero rows


# --- 2: independent lane segments in one turn ---------------------------------------

def test_three_lanes_fire_one_turn_all_segments(tmp_path):
    """PIN: reflex AND frontier AND uncensored fire in one turn -> the
    aggregate block shows ONE SEGMENT PER LANE in fire order, each with
    its own tokens/cost — no lane's segment eaten (R19.16 FIX 4 stacking
    survives the rollup change). The block counts as ONE banner."""
    _clear()
    frontier = ("· router · higher-self (frontier) | completion_audit | "
                "z-ai/glm-5.3 | tok 11824/96 | $0.010792 | initiator=user")
    reflex = DMT._aggregate_line(2, 300, 120, 0.0002, [
        {"choice": "opt-1", "confidence": 0.95, "label": "Approach 1",
         "tokens_in": 150, "tokens_out": 60, "cost": 0.0001},
        {"choice": "opt-2", "confidence": 0.85, "label": "Approach 2",
         "tokens_in": 150, "tokens_out": 60, "cost": 0.0001}])
    uncensored = ("· router · shadow-self (uncensored) | declared | "
                  "render-model | tok 2000/800 | $0.002100 | initiator=user")
    DB.park_anchor_banner("s-r22", frontier, task_id="t-front")
    DB.park_anchor_banner("s-r22", reflex, task_id="midturn")
    DB.park_anchor_banner("s-r22", uncensored, task_id="t-render")
    block = DB.consume_parked_banner("s-r22")
    assert block.count("· router ·") == 3  # one segment per lane
    assert "higher-self (frontier)" in block
    assert "reflex (decision)" in block
    assert "shadow-self (uncensored)" in block
    # each segment keeps its own tokens/cost
    assert "tok 11824/96" in block and "$0.010792" in block
    assert "tok 300/120" in block and "$0.000200" in block
    assert "tok 2000/800" in block and "$0.002100" in block
    # fire order: frontier -> reflex -> uncensored (park order)
    assert block.index("higher-self (frontier)") \
        < block.index("reflex (decision)") \
        < block.index("shadow-self (uncensored)")
    # the block counts as ONE banner: consumed clean, nothing re-parked
    assert DB.consume_parked_banner("s-r22") == ""
    _clear()
