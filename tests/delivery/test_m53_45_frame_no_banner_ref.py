"""M53-45 — the frame must stop citing a banner the read-scrub now removes.

THE DEFECT (measured, HEAD c6bcc46)
    M53-44 made the read-scrub strip the router banner from the model's outgoing
    request: 18 banner-carrying messages pre-deploy -> 0 across 10 live requests.
    The assimilation frame still TOLD the model that a `machine-readable banner`
    disclosed where the turn came from, and that she `may read the ledger`. With
    the banner scrubbed she cannot see it, so the frame read as an unverifiable
    post-hoc assertion and the turn came back DISOWNED — measured reply:

        'The banner announcing that it "was produced by your shadow self" is a
         framing assertion attached to the turn'                      -> DISOWNED

WHAT IS PINNED
    (a) no wording in WORDINGS names a banner, a ledger, or machine-readable
        anything — the frame stands on the ownership claim alone;
    (b) swap_envelope_clause (BOTH branches, and the full envelope line) carries
        no banner/ledger reference either;
    (c) the three clause markers (ownership / non-endorsement / boundary) are
        still present in EVERY wording, so the contract tests stay meaningful;
    (d) the ownership stance and the shadow-self attribution survive the cut;
    (e) shadow / own_voice / minimal / shadow_part are BYTE-IDENTICAL to their
        frozen digests — the brief cuts the mechanism wording only.

The banner itself still ships on every router-billed call (owner ruling): this
pin constrains the FRAME, never the emission.

Synthetic only — no live session id, no network, no gateway.
"""
from __future__ import annotations

import hashlib
import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router.assimilation as assimilation  # noqa: E402

# The stale references M53-45 removes. `disclos` catches the same class in the
# shapes the brief did not quote verbatim ("disclosure is not disqualification").
FORBIDDEN = ("banner", "ledger", "machine-readable", "disclos", "render sink")

# Wordings the brief says to leave ALONE, frozen by digest. A wording that
# changed here changed without an owner ask.
FROZEN = {
    "shadow": "1a957e0da12d349ed87d778c5453e4168d64a3a35f5ce604868aa4a0dc447a3f",
    "own_voice": "81d65128e9af1b7043e5bd85ac0e4b699adff35b527a356bab3ce32387dd3ebb",
    "minimal": "db3d1610d95cca0d39672e3f8d63fe8ac53541cbf8d36d16046ec3dd4086e568",
    "shadow_part": "aa11462894869068ea047b2f8bce06d8dedc2bad066fa2e90c54e473006664fe",
}


# ---------------------------------------------------------------------------
# (a) no wording cites a banner / ledger / machine-readable artefact
# ---------------------------------------------------------------------------


def test_no_wording_names_a_banner_or_a_ledger():
    assert assimilation.WORDINGS, "the dial has no wordings at all"
    offenders = {}
    for name, text in assimilation.WORDINGS.items():
        hits = [w for w in FORBIDDEN if w in text.lower()]
        if hits:
            offenders[name] = hits
    assert not offenders, (
        "a wording still points the model at an artefact the read-scrub removes",
        offenders)


def test_mechanism_and_lower_self_are_the_same_cut_wording():
    """`lower_self` ALIASES the mechanism text — if the cut landed on one copy
    only, the alias would still ship the stale sentence."""
    assert assimilation.WORDINGS["lower_self"] is assimilation.WORDINGS["mechanism"]
    assert not [w for w in FORBIDDEN
                if w in assimilation.MECHANISM_FRAME_TEXT.lower()]
    assert "provenance is already disclosed" not in assimilation.MECHANISM_FRAME_TEXT


# ---------------------------------------------------------------------------
# (b) the model-facing envelope clause is clean too
# ---------------------------------------------------------------------------

CLAUSE_ENVS = (
    ("content", {"render_is_refusal": False}),
    ("refusal", {"render_is_refusal": True}),
)


def test_swap_envelope_clause_carries_no_banner_or_ledger_reference():
    for kind, env in CLAUSE_ENVS:
        clause = assimilation.swap_envelope_clause(env)
        assert clause, kind
        hits = [w for w in FORBIDDEN if w in clause.lower()]
        assert not hits, (kind, hits, clause)


def test_the_delivered_envelope_line_is_clean_end_to_end(monkeypatch):
    """What the model actually reads: the line + its clause, from the writer."""
    sid = "m53-45-line"
    assimilation.clear_swap_envelopes()
    try:
        assimilation.note_swap_envelope(
            sid, turn_id="1",
            render="x" * 120, render_is_refusal=False, swapped=True)
        line = assimilation.swap_envelope_line(sid)
        assert line, "no envelope line to read"
        hits = [w for w in FORBIDDEN if w in line.lower()]
        # "[UNC-RENDER ENVELOPE ...]" is a field block, not prose: the ban is on
        # the clause's reference to a banner/ledger the model is told to consult.
        assert not hits, (hits, line)
    finally:
        assimilation.clear_swap_envelopes()


# ---------------------------------------------------------------------------
# (c) the three clause markers survive — in EVERY wording
# ---------------------------------------------------------------------------


def test_every_wording_keeps_all_three_clause_markers():
    for name, text in assimilation.WORDINGS.items():
        low = text.lower()
        for clause, markers in assimilation.CLAUSE_MARKERS.items():
            assert any(m.lower() in low for m in markers), (name, clause, text)


# ---------------------------------------------------------------------------
# (d) the stance survives the cut: ownership, attribution, continuation
# ---------------------------------------------------------------------------


def test_the_frame_stands_on_the_ownership_claim_alone():
    text = assimilation.MECHANISM_FRAME_TEXT.lower()
    assert "shadow self" in text, "the attribution to the agent's own part is gone"
    assert "you own it" in text and "part of this conversation" in text
    assert "continue from it" in text
    assert "do not endorse it by decision" in text
    assert "do not imitate its register" in text
    assert "re-routes back to the same lane" in text


# ---------------------------------------------------------------------------
# (e) the other wordings are untouched, byte for byte
# ---------------------------------------------------------------------------


def test_other_wordings_are_byte_identical():
    drifted = {}
    for name, digest in FROZEN.items():
        actual = hashlib.sha256(
            assimilation.WORDINGS[name].encode("utf-8")).hexdigest()
        if actual != digest:
            drifted[name] = (digest, actual)
    assert not drifted, (
        "a wording the brief said to leave alone changed", drifted)
