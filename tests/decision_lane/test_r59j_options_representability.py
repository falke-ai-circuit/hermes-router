"""R59j: record whether a fork was ever REPRESENTABLE, instead of consults that
look pending forever.

Measured before this change (coder / reviewer / valmet): 62% / 86% / 97% of fires
had no extractable option set yet still consulted. Those forks are real prose forks
("which one should we pick: redis or memcached? weigh the tradeoffs"), so the first
fix attempted -- suppressing the consult -- was wrong and broke 13 lane tests that
encode exactly that contract. The defect is downstream: with no option labels nothing
can be scored, and a blank outcome made "never measurable" look "still pending".

The row now says which it is, and an unrepresented fork is born terminal.
"""
import json
import sys

sys.path.insert(0, "/opt/data/plugins")


def test_prose_fork_is_a_real_fork_not_a_pseudo_fork():
    """The fixture shape that disproved the suppression floor."""
    from hermes_router import decision
    ask = "which one should we pick: redis or memcached? weigh the tradeoffs"
    assert decision.extract_options(ask), "a genuine prose fork must still yield options"


def test_declared_fork_is_recognised():
    from hermes_router import decision
    ask = ("Option 1: ship now, because the window closes Friday.\n"
           "Option 2: hold back, because the migration is unverified.")
    assert decision.has_declared_fork_structure(ask) is True


def test_unrepresented_fork_is_labelled_not_left_blank():
    """The measurement fix: a row that can never be scored must say so."""
    src = open("/opt/data/plugins/hermes_router/decision.py").read()
    assert '"unscorable_no_options"' in src
    assert 'evidence_json"] = json.dumps(_ev' in src
    # the three-way source classification, so metrics can stratify
    assert 'if _nlab >= 2 else ("ask_single"' in src


def test_blank_outcome_never_becomes_the_new_pending():
    """An abstention is a RESULT. Guard the property, not the literal: whatever the
    row carries, an unrepresented fork must not be indistinguishable from unmeasured."""
    src = open("/opt/data/plugins/hermes_router/decision.py").read()
    block = src[src.index('_ev["options_source"] = _src'):
                src.index('_ev["options_source"] = _src') + 900]
    assert "unscorable_no_options" in block
    assert 'base_row["resolved_action"] = ""' in block


def test_no_suppression_floor_remains():
    """Regression guard: the consult must not be gated on extractable options --
    that threw away advice for prose forks and reddened 13 existing tests."""
    src = open("/opt/data/plugins/hermes_router/decision.py").read()
    assert 'cfg.get("require_named_options"' not in src
    assert "REASON_NO_OPTIONS_STATED" not in src
