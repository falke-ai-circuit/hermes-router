"""R34-12 item 7 — an echo detector that actually catches the live body.

The item-8c guard missed the worst live failure (session
api_1792536611_5dbebc6b): a dispatch brief was answered with 8 bullets of
ANALYSIS OF THE BRIEF, three POVs, and a raw JSON advisory tail
{"brief_bullets": 8, "povs": [...], "synthesis": ...} — and the turn ended
there. Zero tool calls, zero commits.

The literal delivered body is NOT retyped here: it is fetched from the lcm
DB (messages.store_id 542184) into
tests/fixtures/r34_item7_echo_body_api_1792536611_5dbebc6b.txt and read
byte-for-byte. Every matched family is reported as its OWN
`envelope_echo_delivered` event.
"""
import os
import sys

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

from hermes_router import echo_guard  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                       "r34_item7_echo_body_api_1792536611_5dbebc6b.txt")

NORMAL = (
    "R34-12 item 7: echo_guard.py now reports one event per family. "
    "Committed 1a2b3c4; pytest 14 passed, 0 failed. The fixture is read from "
    "tests/fixtures/, not retyped.")


def _literal_body() -> str:
    with open(os.path.abspath(FIXTURE), "r", encoding="utf-8") as fh:
        return fh.read()


def test_literal_captured_body_is_caught_by_every_structural_family():
    text = _literal_body()
    assert '"brief_bullets"' in text          # fixture sanity: the live shape
    fams = echo_guard.detect_envelope_echo_families(text)
    assert "povs_array" in fams
    assert "synthesis_key" in fams
    assert "brief_bullets" in fams
    assert "pov_triplet" in fams
    # the delivered body carries all three stances
    assert "practitioner:" in text and "outsider:" in text and "skeptic:" in text


def test_observe_emits_one_event_per_family_for_the_literal_body():
    text = _literal_body()
    fams = echo_guard.detect_envelope_echo_families(text)
    rows = []
    out = echo_guard.observe(text, "api_1792536611_5dbebc6b",
                             log_route=lambda e, **f: rows.append((e, dict(f))))
    assert out == fams[0]
    assert len(rows) == len(fams)             # EACH family, separately
    assert [r[1].get("pattern") for r in rows] == fams
    assert all(r[1].get("event_detail") == "envelope_echo_delivered"
               for r in rows)
    assert all(r[1].get("session_id") == "api_1792536611_5dbebc6b"
               for r in rows)


def test_normal_engineering_reply_is_clean():
    assert echo_guard.detect_envelope_echo_families(NORMAL) == []
    rows = []
    assert echo_guard.observe(NORMAL, "s-clean",
                              log_route=lambda e, **f: rows.append((e, dict(f)))
                              ) == ""
    assert rows == []


def test_router_tail_with_no_work_is_a_family():
    body = ("the fork surfaces as: ship it or revert it\n\n"
            "· router · impulse (decision) | 1 verdict | tok 10/5 | $0.0000 ·")
    fams = echo_guard.detect_envelope_echo_families(body)
    assert "router_tail_no_work" in fams


def test_router_tail_with_work_product_is_not_a_family():
    body = ("Refactored retry.py; pytest 9 passed. Commit 1a2b3c4.\n\n"
            "· router · impulse (decision) | 1 verdict | tok 10/5 | $0.0000 ·")
    assert "router_tail_no_work" not in echo_guard.detect_envelope_echo_families(body)


def test_pov_triplet_requires_all_three_stances():
    two = "practitioner: do it\noutsider: it reads as broken\n"
    assert "pov_triplet" not in echo_guard.detect_envelope_echo_families(two)
