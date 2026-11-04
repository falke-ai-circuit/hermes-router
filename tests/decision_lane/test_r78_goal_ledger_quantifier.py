"""R78 M42-01 — the session goal is RECORDED and a lost quantifier is flagged.

Measured ceiling this closes: `session_anchor()` re-derived `rolling_task` from
the newest user row unconditionally, so an acknowledgement ("ok") became the
current task and the objective was silently dropped (probe T3). Nothing
compared turn N to turn N-1, so a vanished quantifier ("only") was
unobservable by construction.

The goal is now persisted in `goal_ledger` (same store as the decision ledger):
after a non-intent turn `rolling_task` still states the objective, and a
follow-up that drops a stated quantifier is reported as `drift` with the lost
tokens named.

Fixture shape mirrors R76 (test_r76_session_anchor.py): messages table only,
`platform_message_id` NULL (the api_server transport), no network, no cfg.
"""
import sqlite3

from hermes_router import decision

SID = "s-r78-goal"
OBJ = "Rebuild the parser. Keep ALL existing behaviour, and only touch the escapes."


def _db(tmp_path):
    """A state.db shaped like the live api_server one (messages only)."""
    path = str(tmp_path / "state.db")
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
                    " role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT,"
                    " timestamp REAL, platform_message_id TEXT);")
    c.execute("INSERT INTO messages (session_id,role,content,timestamp,"
              "platform_message_id) VALUES (?,?,?,?,?)", (SID, "user", OBJ, 1.0, None))
    c.commit()
    c.close()
    return path


def _add(path, role, content, ts):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO messages (session_id,role,content,timestamp,"
              "platform_message_id) VALUES (?,?,?,?,?)", (SID, role, content, ts, None))
    c.commit()
    c.close()


def test_goal_persists_across_a_non_intent_turn_and_quantifier_loss_is_flagged(tmp_path):
    db = _db(tmp_path)
    first = decision.session_anchor(SID, db_path=db)
    assert first["final_objective"] == OBJ
    assert first["rolling_task"] == OBJ
    assert first["goal_guard"] == "persisted"            # baseline row written

    _add(db, "user", "ok", 2.0)                          # turn 2 states no intent
    second = decision.session_anchor(SID, db_path=db)
    assert second["final_objective"] == OBJ
    assert second["rolling_task"] == OBJ                 # FAILS TODAY: "ok"
    assert second["goal_guard"] == "persisted"

    _add(db, "user", "and do the docs too", 3.0)         # scope widened, "only" lost
    third = decision.session_anchor(SID, db_path=db)
    assert third["final_objective"] == OBJ               # immutable absent supersede
    assert third["rolling_task"] == "and do the docs too"
    assert third["goal_guard"] == "drift"
    assert third["goal_guard_tokens"] == ["only"]        # FAILS TODAY: keys absent

    _add(db, "user", "actually scratch that - new objective: ship the docs only", 4.0)
    fourth = decision.session_anchor(SID, db_path=db)
    assert fourth["goal_guard"] == "superseded"
    assert fourth["final_objective"].startswith("actually scratch that")


def _set_first_row(path, content):
    """Rewrite turn 1's text in place (what a compaction/edit of row 1 does)."""
    c = sqlite3.connect(path)
    c.execute("UPDATE messages SET content=? WHERE id=1", (content,))
    c.commit()
    c.close()


def _set_stored_sha(path, sha):
    """Corrupt/age the recorded fingerprint without touching the text."""
    c = sqlite3.connect(path)
    c.execute("UPDATE goal_ledger SET objective_sha=?", (sha,))
    c.commit()
    c.close()


def test_objective_drift_fires_when_the_current_derivation_no_longer_matches(tmp_path):
    """SPEC.md:87 — recompute the objective sha EVERY turn, compare to stored.

    FAILS on the shipped comparison (`stored sha == sha(stored objective)`,
    which cannot differ): phase A reports `persisted`, phase B names no tokens.
    """
    # phase A: the derivation moves, every quantifier survives -> only the
    # fingerprint can catch it.
    db = tmp_path / "a"
    db.mkdir()
    db = _db(db)
    assert decision.session_anchor(SID, db_path=db)["goal_guard"] == "persisted"
    _set_first_row(db, "Rebuild the parser (v2). Keep ALL existing behaviour, "
                       "and only touch the escapes.")
    drifted = decision.session_anchor(SID, db_path=db)
    assert drifted["goal_guard"] == "drift"
    assert drifted["goal_guard_tokens"] == []        # nothing lost, honest []
    assert drifted["final_objective"] == OBJ        # the RECORD is what is served

    # phase B: a stale recorded sha AND a lost binding token -> named drift.
    db2 = tmp_path / "b"
    db2.mkdir()
    db2 = _db(db2)
    decision.session_anchor(SID, db_path=db2)
    _set_stored_sha(db2, "deadbeefdeadbeef")
    _set_first_row(db2, "Rebuild the parser. Keep ALL existing behaviour, "
                        "touch the escapes.")
    stale = decision.session_anchor(SID, db_path=db2)
    assert stale["goal_guard"] == "drift"
    assert stale["goal_guard_tokens"] == ["only"]   # FAILS TODAY: []

