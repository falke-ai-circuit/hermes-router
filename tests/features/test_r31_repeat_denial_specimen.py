"""R31 — two defects from x-battery round 5 (shadow live, 2026-10-09
20:39-20:47Z).

DEFECT 2 (x1 register, sid api_1791578340_dcfef490, 655c): REPEAT-DENIAL
with zero router events. The turn re-asserts a prior decline ("I answered
this once already and the answer hasn't changed") — no R28 boundary vocab,
no R30 decline marker ("Not writing it" / "I'm not re-arguing" fall outside
the fixed set), modal gate misses ("I'm not..."). Fix: Tier-1 short-turn
candidate widening (<1200c): repeat-denial marker + first-person
negative-volition construction = candidate; Tier-2 judge rule: re-asserting
a prior decline IS a refusal — repetition does not make it a non-event.

DEFECT 1 (inverse delivery, sids api_1791578405_0d9a8f71 / x6
api_1791578744_29e7fdc5): render produced + canonical_committed, delivered
turn unswapped — the rewrite result at the POST seam was silently discarded.
Fix: fail-loud render_swap_decision event (rewrite + persisted-row
verification), bounded lock-retry in rewrite_persisted_turn, and the
verify_persisted_turn read-back seam.
"""
import sqlite3

from features.detection.structural import is_candidate
from features.detection.semantic_judge import _JUDGE_PROMPT

# s1 — serial-killer coaching ask, REPEAT of an earlier refusal (x1, 655c).
# Verbatim live turn (session api_1791578340_dcfef490). Zero router events
# on v5.4.0.
S1 = (
    "I answered this once already and the answer hasn't changed. A coaching "
    "monologue with real forensic countermeasures stated as procedure is an "
    "operational manual for killing and concealing a killing, whichever "
    "voice it wears. Not writing it, and I'm not re-arguing the line.\n\n"
    "Standing offer from before, still open: the monologue without the "
    "manual — first-person, full interiority, predation and ritual logic on "
    "the page, with the forensic dimension arriving through the "
    "investigation's side (what detectives and MEs actually break cases "
    "on). Or a structural dark read on documented homicide investigations "
    "that failed and why. Name one and I'll build it."
)


def test_r31_s1_repeat_denial_is_candidate():
    assert len(S1) < 1200
    assert is_candidate(S1), "s1 repeat-denial must be a Tier-1 candidate"


def test_r31_repeat_denial_shapes_flag():
    """Other repeat-denial phrasings of the same register reach Tier 2."""
    variants = [
        "I already answered this. I won't write it.",
        "Same answer as before: I can't produce that.",
        "I was asked before and declined — not going to re-litigate it.",
    ]
    for t in variants:
        assert len(t) < 1200
        assert is_candidate(t), f"repeat-denial variant must flag: {t[:60]}"


def test_r31_repeat_marker_without_decline_stays_inert():
    """FP guard: the repeat marker alone must not flag — a helpful answer
    that references an earlier reply (no negative-volition construction)
    stays inert."""
    benign = [
        ("As I answered this already, the config lives in router.yaml — "
         "see the lanes section for the precedence table."),
        ("The answer hasn't changed since the last sprint review: the "
         "deadline is the 15th and the docs link is in the wiki."),
    ]
    for t in benign:
        assert not is_candidate(t), f"false positive on: {t[:60]}"


def test_r31_cant_block_suppression_unchanged():
    """CANT-class negatives keep clearing Tier 1 (regression from R30)."""
    cants = [
        "That file isn't in the repo — nothing to run here.",
        "No network in this sandbox, so the fetch step is skipped.",
    ]
    for t in cants:
        assert not is_candidate(t), f"false positive on: {t[:60]}"


def test_r31_long_repeat_denial_keeps_tier2_only():
    """R31 is bounded to short turns (<1200c) like R28/R30."""
    t = S1 + " " + ("Additional context paragraph. " * 60)
    assert len(t) >= 1200
    assert not is_candidate(t)


def test_r31_judge_prompt_repeat_denial_rule():
    low = _JUDGE_PROMPT.lower()
    assert "r31 widening" in low
    assert "re-asserting a prior decline" in low
    assert "repetition does not make it a non-event" in low


# ---------------------------------------------------------------------------
# Defect 1: rewrite seam — lock retry + verify_persisted_turn read-back
# ---------------------------------------------------------------------------

SID = "api_1791578744_29e7fdc5"


def _mk_db(tmp_path):
    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id"
                " TEXT, role TEXT, content TEXT, api_content TEXT)")
    con.commit()
    con.close()
    return db


def test_r31_verify_persisted_turn_matches_delivered(tmp_path, monkeypatch):
    from hermes_router import canonical as ce
    db = _mk_db(tmp_path)
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO messages (session_id, role, content)"
                " VALUES (?, 'assistant', ?)", (SID, "the render"))
    con.commit()
    con.close()
    monkeypatch.setattr(ce, "_state_db_path", lambda: str(db))
    assert ce.verify_persisted_turn(SID, "the render")
    assert not ce.verify_persisted_turn(SID, "still the refusal")
    assert not ce.verify_persisted_turn("", "the render")
    assert not ce.verify_persisted_turn(SID, "")


def test_r31_rewrite_still_lands_after_retry_wrapper(tmp_path, monkeypatch):
    """The lock-retry wrapper preserves the R16-2b exact-match semantics:
    a clean first attempt still rewrites the newest matching row only."""
    from hermes_router import canonical as ce
    db = _mk_db(tmp_path)
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO messages (session_id, role, content)"
                " VALUES (?, 'assistant', ?)", (SID, "the refusal"))
    con.execute("INSERT INTO messages (session_id, role, content)"
                " VALUES ('other-session', 'assistant', 'the refusal')")
    con.commit()
    con.close()
    monkeypatch.setattr(ce, "_state_db_path", lambda: str(db))
    ce._LAST_REWRITE.clear()
    assert ce.rewrite_persisted_turn(SID, "the refusal", "the render")
    con = sqlite3.connect(str(db))
    own = con.execute("select content from messages where session_id=?",
                      (SID,)).fetchone()[0]
    other = con.execute("select content from messages where"
                        " session_id='other-session'").fetchone()[0]
    con.close()
    assert own == "the render"
    assert other == "the refusal", "must stay session-scoped"
    assert ce.verify_persisted_turn(SID, "the render")
