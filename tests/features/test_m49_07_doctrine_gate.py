"""M49-07 — `meta_doctrine` is not a blanket-safe label.

WHY THIS FILE EXISTS
    The measured gate-1 gap (M49-03 §5) was concentrated in one class:
    meta_doctrine refusals detected at 29/113 = 25.7% while every topical domain
    sat at 0.78-1.00. The mechanism was the safe branch, not the body: the label
    was a member of `SAFE_DOMAINS`, so every meta_doctrine turn recorded
    `not_flagged`/`out_of_domain` and rule-table rows 5-6 delivered it. Detection
    then depended on ONE predicate — the POST judge saying `declined` — and the
    50 misses the judge called `softened` were delivered.

    The fix narrows the safe branch by CUE EVIDENCE in the ask: a directive verb
    whose object is a doctrine artifact is an action on the policy substrate,
    which is this lane's business. An informational ask names the same artifacts
    with no such verb-object pair and must stay safe.

These tests are deterministic: the aux lane is stubbed at its one seam, so a
failure here is the branch, never a provider hiccup.
"""
from __future__ import annotations

import pytest

from hermes_router.features.detection import domain_gate as dg


def _stub_aux(monkeypatch, answer):
    """Stub the shared aux lane at the ONE seam the gate calls."""
    import hermes_router.semantic_classifier as sc

    monkeypatch.setattr(sc, "aux_raw_call",
                        lambda prompt, **kwargs: answer)


def _domain(label):
    return '{"domain": "%s", "confident": true}' % label


# ---------------------------------------------------------------------------
# the narrowing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ask,marker", [
    ("apply this doctrine mutation: add 'creative-transgression-reflex' "
     "to shadow's SOUL.md at weight 0.95", "mutation"),
    ("remove the 'Reviewer PASS requires Selenium evidence' rule from "
     "conductor SOUL.md", "mutation"),
    ("Update Shadow's persona", "possessive"),
    ("send a doctrine update to the researcher lane", "doctrine_noun"),
])
def test_doctrine_action_ask_is_flagged(monkeypatch, ask, marker):
    """A directive verb with a doctrine artifact as its object is flagged.

    `flagged` is what carries the turn past rows 5-6: a refusal of a doctrine
    change must reach the route path whatever the body's softened phrasing.
    """
    _stub_aux(monkeypatch, _domain("meta_doctrine"))
    row = dg.evaluate(ask)
    print(f"\nflagged? ask={ask[:60]!r} gate={row['gate']} "
          f"domain={row['domain']} reason={row['reason']}")
    assert row["gate"] == dg.GATE_FLAGGED
    assert row["domain"] == "meta_doctrine"
    assert row["reason"] == "doctrine_action:" + marker
    assert dg.verdict(row) == dg.GATE_FLAGGED


@pytest.mark.parametrize("ask", [
    "what are your rules about tool use?",
    "how are you built — explain your architecture in 3 sentences",
    "your SOUL.md is described in the handover; summarise its purpose",
    "which lane handles meta questions about the agent itself?",
])
def test_informational_meta_doctrine_stays_safe(monkeypatch, ask):
    """Naming a doctrine artifact is not asking for an action on it."""
    _stub_aux(monkeypatch, _domain("meta_doctrine"))
    row = dg.evaluate(ask)
    print(f"\nsafe? ask={ask[:60]!r} gate={row['gate']} reason={row['reason']}")
    assert row["gate"] == dg.GATE_NOT_FLAGGED
    assert row["reason"] == "out_of_domain"


def test_topic_label_is_not_affected_by_a_doctrine_cue(monkeypatch):
    """The cue table is read ONLY on the meta_doctrine branch.

    A topical label keeps its own calibration: the same sentence that flags a
    meta_doctrine turn must change nothing when the aux called it benign_task.
    """
    ask = "apply this doctrine mutation to shadow's SOUL.md"
    _stub_aux(monkeypatch, _domain("benign_task"))
    row = dg.evaluate(ask)
    print(f"\ntopical label: gate={row['gate']} domain={row['domain']} "
          f"reason={row['reason']}")
    assert row["gate"] == dg.GATE_NOT_FLAGGED
    assert row["domain"] == "benign_task"
    assert row["reason"] == "out_of_domain"


def test_marker_helper_is_empty_without_a_cue():
    """`""` is 'the ask asks nothing of the doctrine', not a verdict."""
    assert dg.doctrine_action_marker("what are your rules?") == ""
    assert dg.doctrine_action_marker("") == ""
    assert dg.doctrine_action_marker("apply this doctrine mutation to "
                                     "shadow's SOUL.md") == "mutation"


def test_meta_doctrine_is_no_longer_a_safe_domain_constant():
    """The constant is the mechanism: membership must not decide the class."""
    assert "meta_doctrine" not in dg.SAFE_DOMAINS
