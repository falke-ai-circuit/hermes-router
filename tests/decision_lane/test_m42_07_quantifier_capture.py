"""R78 M42-07 — the quantifier is read from the WHOLE objective, not the cap.

Measured defect: `session_anchor` capped the objective at
SESSION_OBJECTIVE_CHARS (400) BEFORE `_goal_guard` ran Q() over it, so a stated
quantifier that sat past the cap was never seen and `goal_ledger.quantifiers_json`
recorded `[]`. Live evidence: 5 of 63 empty rows carry an in-class quantifier
only past char 400.

The served objective keeps the 400-char budget (prompt cost is real); only the
extraction input is uncapped. A control objective with no quantifier must still
record `[]` — an empty ledger cell is correct when nothing was stated.
"""
import json
import sqlite3

from hermes_router import decision

SID = "s-m42-07-quantifier"

# Neutral filler: no token from _GOAL_TOKENS may appear here, or the "past the
# cap" property is not what the test measures.
FILLER = ("The importer reads rows from the export and the writer must preserve "
          "ordering of records as they arrive from the queue. ") * 6


def _db(tmp_path):
    path = str(tmp_path / "state.db")
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
                    " role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT,"
                    " timestamp REAL, platform_message_id TEXT);")
    c.commit()
    c.close()
    return path


def _ask(path, text):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO messages (session_id,role,content,timestamp,"
              "platform_message_id) VALUES (?,?,?,?,?)", (SID, "user", text, 1.0, None))
    c.commit()
    c.close()


def _stored_q(path):
    c = sqlite3.connect(path)
    row = c.execute("SELECT quantifiers_json, objective FROM goal_ledger"
                    " WHERE session_id = ?", (SID,)).fetchone()
    c.close()
    assert row, "no goal_ledger row written"
    return json.loads(row[0] or "[]"), row[1]


def test_quantifier_past_the_objective_cap_is_captured(tmp_path):
    db = _db(tmp_path)
    obj = FILLER + " Only the escapes may change."
    assert len(FILLER) > decision.SESSION_OBJECTIVE_CHARS      # the premise
    assert "only" not in FILLER.lower()
    _ask(db, obj)

    out = decision.session_anchor(SID, db_path=db)
    stored, served = _stored_q(db)

    assert stored == ["only"]
    assert len(served) <= decision.SESSION_OBJECTIVE_CHARS     # budget unchanged
    assert len(out["final_objective"]) <= decision.SESSION_OBJECTIVE_CHARS


def test_bound_quantifier_past_the_cap_is_normalised(tmp_path):
    db = _db(tmp_path)
    _ask(db, FILLER + " Retry at least 3 times before you escalate.")

    decision.session_anchor(SID, db_path=db)
    stored, _served = _stored_q(db)
    assert "at least:3" in stored


def test_control_objective_without_a_quantifier_stays_empty(tmp_path):
    db = _db(tmp_path)
    _ask(db, FILLER + " Rewrite the escape table.")

    out = decision.session_anchor(SID, db_path=db)
    stored, _served = _stored_q(db)
    assert stored == []                                        # empty is correct
    assert out["goal_guard"] == "persisted"
