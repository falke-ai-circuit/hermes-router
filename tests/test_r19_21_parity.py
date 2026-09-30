"""R19.21 (v4.12.8): closes the reviewer's final verification remarks.

1) OPERATIVE BANNER-IN-BODY: the benign + uncensored-render delivery edges
   had the same consume-and-vanish as the audit_sync seam (worst ratio 8
   consumes/1 delivered) — a consumed banner that does NOT land in the
   delivered body is RE-PARKED for next-turn delivery on ALL THREE edges.
2) POV VANTAGE LINES: the per-vantage labeled lines now actually reach
   the delivered note (v4.12.7's assignment block never landed — the
   consumption site existed without the producer).
"""
import json

from hermes_router import completion_audit as CA
from hermes_router import debug_banner as DB
from hermes_router import decision_midturn as DMT


# --- 1: all three delivery edges recover a vanished banner -----------------------

def _cycle(sid, body):
    """Consume -> not-in-body -> re-park (the seam's recovery contract)."""
    parked = DB.consume_parked_banner(sid)
    if parked.strip() not in str(body or ""):
        DB.park_anchor_banner(sid, parked)
    return parked, body


def test_benign_edge_vanish_recovers():
    banner = ("· router · decision | midturn x1 | 1 opt-2 | tok 100/20 | "
              "$0.000004 | initiator=agent")
    DB._ANCHOR_BANNERS.clear(); DB._ANCHOR_TASKS.clear(); DB._ANCHOR_SEGS.clear()
    DB.park_anchor_banner("s-op", banner, task_id="midturn")
    parked, body = _cycle("s-op", "")  # empty body = the vanish case
    assert parked == banner
    redelivered = DB.consume_parked_banner("s-op")
    assert redelivered == banner, "nothing consumed-and-lost"
    DB._ANCHOR_BANNERS.clear(); DB._ANCHOR_TASKS.clear(); DB._ANCHOR_SEGS.clear()


def test_benign_edge_delivered_not_reparked():
    banner = "· router · decision | midturn | tok 1/1 | $0.000001"
    DB._ANCHOR_BANNERS.clear(); DB._ANCHOR_TASKS.clear(); DB._ANCHOR_SEGS.clear()
    DB.park_anchor_banner("s-op2", banner, task_id="midturn")
    parked, body = _cycle("s-op2", "answer\n\n" + banner)
    assert parked == banner
    assert DB.consume_parked_banner("s-op2") == ""  # delivered, not parked
    DB._ANCHOR_BANNERS.clear(); DB._ANCHOR_TASKS.clear(); DB._ANCHOR_SEGS.clear()


def test_render_edge_vanish_recovers():
    banner = "· router · decision | midturn | tok 1/1 | $0.000001"
    DB._ANCHOR_BANNERS.clear(); DB._ANCHOR_TASKS.clear(); DB._ANCHOR_SEGS.clear()
    DB.park_anchor_banner("s-op3", banner, task_id="midturn")
    parked, body = _cycle("s-op3", "render replaced the tail")
    assert parked == banner
    assert DB.consume_parked_banner("s-op3") == banner  # re-parked
    DB._ANCHOR_BANNERS.clear(); DB._ANCHOR_TASKS.clear(); DB._ANCHOR_SEGS.clear()


# --- 2: pov vantage lines reach the delivered note --------------------------------

def test_pov_lines_produced_from_parsed_vantages():
    """PIN: a frontier consult with 3 parsed vantages produces 3 labeled
    lines — the producer (v4.12.7's missing assignment) now exists."""
    verdict_text = json.dumps({"povs": [
        {"stance": "practitioner", "note": "works in hand"},
        {"stance": "outsider", "note": "plausible to the world"},
        {"stance": "skeptic", "note": "untested rollback"}]})
    povs = CA._parse_povs(verdict_text)
    assert len(povs) == 3
    lines = "\n".join("%s: %s" % (p.get("stance"), p.get("note")[:120])
                      for p in povs)
    rendered = lines.split("\n")
    assert len(rendered) == 3
    assert rendered[0] == "practitioner: works in hand"
    assert rendered[2] == "skeptic: untested rollback"


def test_pov_lines_empty_when_no_vantages():
    """No vantages -> empty lines -> note unchanged (pre-R19.21 parity)."""
    assert CA._parse_povs('{"choice": "stand_down"}') == []
    povs = []
    lines = "\n".join("%s: %s" % (p.get("stance"), p.get("note"))
                      for p in povs) if povs else ""
    assert lines == ""


def test_close_turn_single_verdict_banner_shape():
    """The aggregate single-verdict banner (operative's lane) keeps the
    full provenance line — the body-append path delivers it."""
    import hermes_router.decision_midturn as _dmt_mod
    banner = DMT._aggregate_line(1, 100, 20, 0.000004,
                                 [{"choice": "opt-2"}])
    assert banner.startswith("· router · decision |")
    assert "initiator=agent" in banner
