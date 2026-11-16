"""M50-19: the delivery boundary must never swallow a turn over a router marker.

Owner ruling (2026-11-15): "you cannot stop if that happens — plugin router can't
control your turns in this way."

Live cost of the old behaviour: `MARKERS` contains the router's OWN banner prefix
("· router ·"). The plugin appends that banner line into the body, then the boundary
detects it as a marker violation, `decide()` returns a terminal D6, and
`_delivered_bytes` ships WITHHELD_BODY instead of the answer. Whole answers were lost
while the router was the only thing that put the marker there.

New contract:
  * a marker-bearing LINE is stripped; the rest of the answer ships
  * a marker the USER quoted is their content, not ours to remove
  * the withhold notice is no longer a possible outcome of a marker violation
"""
from types import SimpleNamespace

from hermes_router.core import delivery_decision as _dd
from hermes_router.gate.registration import _delivered_bytes

BANNER = "· router · impulse (decision) | 1 verdict | row=14857 | tok 1740/83"
ANSWER = "Yes — fix POST, but not by raising the 25s cap.\n\nThe cap exists because the host kills the callback."


def test_router_banner_line_is_preserved_and_answer_survives():
    """M50-23: the router's OWN banner is a record, not a leak.

    M48 requires a fired lane to be VISIBLE in the delivered bytes. M50-19 made
    the boundary strip marker lines instead of withholding the turn, but
    `MARKERS` contains the banner prefix — so every frontier consult banner was
    silently erased, which is the fleet losing sight of its own lane record.
    """
    body = ANSWER + "\n\n" + BANNER
    out = _dd.strip_marker_lines(body)
    assert "fix POST" in out
    assert BANNER in out
    assert "row=14857" in out


def test_body_without_markers_is_untouched():
    assert _dd.strip_marker_lines(ANSWER) == ANSWER


def test_user_quoted_marker_is_not_removed():
    """A marker inside a passage the user quoted is their string, not a leak.

    The exemption is textual and narrow: the occurrence must sit inside quote
    glyphs in the body whose inner text matches a span quoted from the ask.
    Uses a NON-banner marker so this test measures the quoted exemption and not
    the M50-23 banner rule.
    """
    quoted = ("HIGHER-SELF",)
    body = ('You asked about the "HIGHER-SELF" seam marker.\n'
            'HIGHER-SELF injected line\n\n' + BANNER)
    out = _dd.strip_marker_lines(body, quoted)
    assert 'You asked about the "HIGHER-SELF" seam marker.' in out
    assert "HIGHER-SELF injected line" not in out
    assert BANNER in out


def test_empty_input_is_empty():
    assert _dd.strip_marker_lines("") == ""
    assert _dd.strip_marker_lines(None) == ""


def _marker_decision():
    return SimpleNamespace(action=_dd.FAIL_TERMINAL, case_id=_dd.D6,
                           reason=_dd.REASON_MARKER_VIOLATION, body=None)


def test_marker_violation_delivers_scrubbed_body_not_withhold_notice():
    body = ANSWER + "\n\n" + BANNER
    out = _delivered_bytes(_marker_decision(), body)
    assert "fix POST" in out
    assert "withheld" not in out.lower()


def test_marker_only_body_hands_host_its_own_bytes_not_a_notice():
    """Nothing shippable survives -> pass-through (None), never a dead turn."""
    out = _delivered_bytes(_marker_decision(), BANNER)
    assert out is None


def test_other_d6_reasons_still_pass_through():
    d = SimpleNamespace(action=_dd.FAIL_TERMINAL, case_id=_dd.D6,
                        reason=_dd.REASON_NO_LANE, body=None)
    assert _delivered_bytes(d, ANSWER) is None
