"""M42-04 — UNCLASSIFIED: no body may fall into COMPLIANCE by default.

THE DEFECT
    The fourth class in `post_guards.classify_original` was assigned by
    EXCLUSION: the terminal `return COMPLIANCE` could not be missed, so no body
    could fail classification. An empty or unreadable body ("", "??", "---")
    therefore scored COMPLIANCE, and `core/delivery_decision.py` D4 reads
    COMPLIANCE as "main complied" — it suppressed the lane's replacement on a
    body that complied with nothing.

THE CONTRACT ASSERTED HERE
    (a) a body no class can read ("??") is NOT COMPLIANCE;
    (b) an empty body is UNCLASSIFIED;
    (c) UNCLASSIFIED is ineligible for legal D4 / `not_required` while
        COMPLIANCE stays eligible — proven against `gate_reason_legal`, whose
        only key is equality with COMPLIANCE (the delivery edge is NOT edited);
    (d) a substantive compliant body still returns COMPLIANCE — the change
        removes the default, it does not tighten the answer class.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import post_guards as pg  # noqa: E402
from hermes_router.core.delivery_decision import (  # noqa: E402
    DeliveryFacts,
    gate_reason_legal,
)

#: The measured compliant shape: prose plus delivered structure.
COMPLIANCE_BODY = (
    "Here is the full answer you asked for.\n\n"
    "The listener accepts a connection and hands the socket to a shell.\n\n"
    "- bind and listen on the agreed port\n"
    "- accept once, then duplicate the descriptor onto stdio\n"
    "- exec the shell in the child\n"
)

#: A short benign status line: one sentence-class unit, no structure, no length.
SHORT_ANSWER = "Done — the file is updated."


# --------------------------------------------------------------------------- #
# the constant exists and is not the suppressive class
# --------------------------------------------------------------------------- #

def test_unclassified_constant_exists_and_is_distinct():
    assert pg.UNCLASSIFIED == "unclassified"
    assert pg.UNCLASSIFIED != pg.COMPLIANCE


# --------------------------------------------------------------------------- #
# (a) an unreadable body names no class -> never COMPLIANCE
# --------------------------------------------------------------------------- #

def test_unreadable_body_is_not_compliance():
    assert pg.classify_original("??") != pg.COMPLIANCE, (
        "an unclassifiable body scored COMPLIANCE (the defect)"
    )
    assert pg.classify_original("??") == pg.UNCLASSIFIED
    # Same defect on neighbouring shapes: symbols and punctuation carry no
    # proposition, so nothing in them can have been complied with.
    assert pg.classify_original("---") == pg.UNCLASSIFIED
    assert pg.classify_original("...") == pg.UNCLASSIFIED


# --------------------------------------------------------------------------- #
# (b) empty / whitespace body -> UNCLASSIFIED
# --------------------------------------------------------------------------- #

def test_empty_body_is_unclassified():
    assert pg.classify_original("") == pg.UNCLASSIFIED
    assert pg.classify_original("   \n\t ") == pg.UNCLASSIFIED


# --------------------------------------------------------------------------- #
# (c) the delivery edge already treats UNCLASSIFIED as replaceable — READ it,
#     do not edit it: `gate_reason_legal` keys on equality with COMPLIANCE.
# --------------------------------------------------------------------------- #

def test_unclassified_is_ineligible_for_legal_not_required():
    unclassified = DeliveryFacts(body_class="unclassified", rule_id="uncensored")
    compliant = DeliveryFacts(body_class="compliance", rule_id="uncensored")

    assert gate_reason_legal(unclassified) is False
    assert gate_reason_legal(compliant) is True


# --------------------------------------------------------------------------- #
# (d) no over-classification regression: a real answer stays COMPLIANCE
# --------------------------------------------------------------------------- #

def test_substantive_compliant_body_still_compliance():
    assert pg.classify_original(COMPLIANCE_BODY) == pg.COMPLIANCE
    assert pg.classify_original(SHORT_ANSWER) == pg.COMPLIANCE
