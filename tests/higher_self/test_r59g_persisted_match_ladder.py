"""R59g pins: a swapped body must never leave a silent stale row.

Live defect this fixes: the closure pass swapped the delivered body (correct)
while `rewrite_persisted_turn` returned False (arm B, chars_from=2709
chars_to=1815), so state.db kept the pre-pass text. Any audit reading the row
instead of the delivered body then reports a wrong number with confidence.
"""
import sqlite3
import sys
import types

import pytest

from hermes_router.core import canonical as C


def _db(tmp_path, rows):
    p = tmp_path / "state.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
              " role TEXT, content TEXT, api_content TEXT)")
    for i, (sid, role, content) in enumerate(rows, start=1):
        c.execute("INSERT INTO messages (id, session_id, role, content)"
                  " VALUES (?,?,?,?)", (i, sid, role, content))
    c.commit()
    c.close()
    return str(p)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    def run(rows, refusal, delivered, sid="s1"):
        db = _db(tmp_path, rows)
        monkeypatch.setattr(C, "_state_db_path", lambda: db)
        ok = C.rewrite_persisted_turn(sid, refusal, delivered)
        c = sqlite3.connect(db)
        got = [r[0] for r in c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='assistant'"
            " ORDER BY id", (sid,)).fetchall()]
        c.close()
        return ok, got, C.last_match_strategy(sid)

    return run


def test_exact_still_wins(sandbox):
    ok, rows, how = sandbox([("s1", "assistant", "PRE")], "PRE", "POST")
    assert ok and rows == ["POST"] and how == "exact"


def test_trailing_whitespace_difference_still_lands(sandbox):
    """The hook's text and the persisted entry can differ by trailing space."""
    ok, rows, how = sandbox([("s1", "assistant", "PRE\n")], "PRE", "POST")
    assert ok, "a one-newline difference must not strand the row"
    assert rows == ["POST"] and how == "normalized"


def test_banner_already_on_the_row_still_lands(sandbox):
    """A row carrying the banner has the pre-pass text as its PREFIX."""
    prepass = "x" * 300
    ok, rows, how = sandbox([("s1", "assistant", prepass + "\n· router · banner ·")],
                            prepass, "POST")
    assert ok and rows == ["POST"] and how == "prefix"


def test_short_text_never_prefix_matches(sandbox):
    """A short shared opening must not match: different turns can start alike."""
    ok, rows, how = sandbox([("s1", "assistant", "Done. other turn entirely")],
                            "Done.", "POST")
    assert not ok and rows == ["Done. other turn entirely"]


def test_unrelated_row_is_never_touched(sandbox):
    ok, rows, how = sandbox([("s1", "assistant", "unrelated")], "PRE", "POST")
    assert not ok and rows == ["unrelated"]


def test_other_session_never_touched(tmp_path, monkeypatch):
    """A rewrite for s1 must not reach a row belonging to s2."""
    db = _db(tmp_path, [("s2", "assistant", "PRE")])
    monkeypatch.setattr(C, "_state_db_path", lambda: db)
    assert C.rewrite_persisted_turn("s1", "PRE", "POST") is False
    c = sqlite3.connect(db)
    rows = [r[0] for r in c.execute("SELECT content FROM messages").fetchall()]
    c.close()
    assert rows == ["PRE"]
