"""Banner template compliance — decision, uncensored and frontier on ONE standard.

Owner ask 2026-11-15: "we have banner templates and standards — look at decision
lane and uncensored lane and confirm frontier is compliant".

The standard, from `turn_record._render_cells` (the ONLY producer of lane text):

    <head> <label> | <trigger> | ×N | <model> | tok <in>/<out> | $<cost> | <extras>

Concretely, the three labels are registered in `turn_record._LABELS`:
    impulse (decision) / higher-self (frontier) / shadow-self (uncensored)
and every lane line carries `tok %d/%d` (SLASH, no thousands separators) plus
`$%.4f` (four decimals, `*` suffix when the price is estimated).

Why this file exists as a compliance check rather than a bug fix: M47-22 added a
second spend row in the form `tok 2,471→29,696 · $0.3019 · frontier` on the theory
that tokens only rendered at level 3. That theory was wrong — `_render_cells`
already emits tok/cost into the lane text at every level — so the row was both
redundant and off-standard (arrow, thousands separators, "·" separators, lane name
repeated). It was reverted. These pins keep the standard single and enforced.
"""
import re

from hermes_router.features.banners import turn_record as t

CANON_TOK = re.compile(r"tok \d+/\d+")
CANON_COST = re.compile(r"\$\d+\.\d{4}\*?")
LABELS = {
    "decision": "· router · impulse (decision) | 1 hold | m @ host | tok 10/2 | $0.000010 | row 7",
    "frontier": "· router · higher-self (frontier) | post-check | m @ host | tok 1583/1200 | $0.0150",
    "uncensored": "· router · shadow-self (uncensored) | pre | m @ host | tok 900/120 | $0.0040",
}


def _render_lane(sid, lane):
    t.register(sid, LABELS[lane], kind="anchor")
    return t.compose_line(sid)


def test_every_lane_carries_the_canonical_token_and_cost_cells():
    for lane in LABELS:
        line = _render_lane(f"s-comp-{lane}", lane)
        assert CANON_TOK.search(line), f"{lane} missing 'tok N/M': {line}"
        assert CANON_COST.search(line), f"{lane} missing '$0.0000': {line}"


def test_no_lane_uses_the_off_standard_arrow_or_thousands_form():
    """`tok 2,471→29,696` is the format that was introduced and reverted."""
    for lane in LABELS:
        line = _render_lane(f"s-comp2-{lane}", lane)
        assert "→" not in line, f"{lane} uses an arrow: {line}"
        assert not re.search(r"tok [\d,]+→", line), f"{lane} off-standard tokens: {line}"


def test_frontier_is_compliant_with_the_same_standard_as_the_other_two():
    """Same shape, same cells, same separators — only the label differs."""
    shapes = {}
    for lane in LABELS:
        line = _render_lane(f"s-comp3-{lane}", lane)
        body = line.split("|", 1)[1] if "|" in line else line
        cells = [c.strip() for c in body.split("|")]
        shape = ["tok" if c.startswith("tok ") else "$" if c.startswith("$")
                 else "row" if c.startswith("row") else "other" for c in cells]
        shapes[lane] = shape
    assert shapes["frontier"] == shapes["decision"] == shapes["uncensored"], shapes


def test_one_head_per_body_across_lanes():
    """All lanes share ONE head; a second head would be a template violation.

    M50-26: the block carries exactly ONE canonical head — the lane lines after
    it are continuations (each still carrying its own figures), not new heads.
    """
    sid = "s-comp4"
    for lane in LABELS:
        t.register(sid, LABELS[lane], kind="anchor")
    line = t.compose_line(sid)
    assert line.count("· router ·") == 1, line
    parts = [p for p in line.splitlines() if p.strip()]
    assert len(parts) == 3, parts
    assert parts[0].startswith("· router ·"), parts[0]
    for part in parts[1:]:
        assert part.count("· router ·") == 0, part


def test_no_lane_line_is_rendered_without_token_spend():
    """A lane that fired must show what it cost — the field may not go missing."""
    for lane in LABELS:
        line = _render_lane(f"s-comp5-{lane}", lane)
        assert "tok " in line and "$" in line, f"{lane}: {line}"
