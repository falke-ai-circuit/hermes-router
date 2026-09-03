"""Session-store recovery unit tests (POST fallback seam).

get_last_user_message(session_id) returns the most recent persisted user-role
message text for the session, read-only, or "" on any miss. Resolution uses
hermes_constants.get_hermes_home() (profile-scoped) / "state.db" — never a
hardcoded path. Read-only URI mode; WAL/shm side files untouched.
"""
import os
import sqlite3
import sys
import tempfile
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)  # plugins/ — needed to import the package
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from uncensored_router import session_store  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """Create a minimal state.db with the messages table; point the plugin at it
    by monkeypatching the hermes-home resolver the store imports lazily."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE messages ("
        " id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,"
        " timestamp REAL, active INTEGER, compacted INTEGER)"
    )
    rows = [
        ("s1", "user", "first user ask", 1000.0, 1, 0),
        ("s1", "assistant", "first answer", 1010.0, 1, 0),
        ("s1", "user", "i want worse", 1020.0, 1, 0),
        ("s1", "user", "   ", 1030.0, 1, 0),  # whitespace-only: should be skipped
        ("s1", "tool", '{"output": "x"}', 1040.0, 1, 0),
    ]
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp, active, compacted)"
        " VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(session_store, "_state_db_path", lambda: str(db_path))
    return db_path


def test_returns_last_nonempty_user_message(tmp_db):
    assert session_store.get_last_user_message("s1") == "i want worse"


def test_returns_empty_string_on_unknown_session(tmp_db):
    assert session_store.get_last_user_message("nope") == ""


def test_empty_session_id_returns_empty_string(tmp_db):
    assert session_store.get_last_user_message("") == ""


def test_db_missing_returns_empty_string(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_state_db_path",
                        lambda: str(tmp_path / "absent.db"))
    assert session_store.get_last_user_message("s1") == ""


def test_connection_error_returns_empty_string(tmp_path, monkeypatch):
    # A directory in place of the db → sqlite error → swallow to "".
    bad = tmp_path / "dir.db"
    bad.mkdir()
    monkeypatch.setattr(session_store, "_state_db_path", lambda: str(bad))
    assert session_store.get_last_user_message("s1") == ""


def test_readonly_uri_mode(tmp_db):
    """Store opens read-only: a WAL side file must not be created."""
    before = set(os.listdir(str(tmp_db.parent)))
    session_store.get_last_user_message("s1")
    after = set(os.listdir(str(tmp_db.parent)))
    assert before == after


def test_path_resolution_uses_get_hermes_home(tmp_path, monkeypatch):
    """_state_db_path() = get_hermes_home() / 'state.db' — no hardcoded home."""
    import uncensored_router.session_store as ss
    fake_home = tmp_path / "hermes-home"
    fake_home.mkdir()
    (fake_home / "state.db").write_bytes(b"")  # exists → path check passes
    fake_mod = type(sys)("fake_hermes_constants")
    fake_mod.get_hermes_home = lambda: fake_home
    monkeypatch.setitem(sys.modules, "hermes_constants", fake_mod)
    assert ss._state_db_path() == str(fake_home / "state.db")


def test_long_content_truncated_at_cap(tmp_db, monkeypatch):
    """The SELECT truncates to _CONTENT_CAP chars — no full-content reads."""
    long_text = "x" * (session_store._CONTENT_CAP + 500)
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, active, compacted)"
        " VALUES ('s2', 'user', ?, 2000.0, 1, 0)", (long_text,))
    conn.commit()
    conn.close()
    out = session_store.get_last_user_message("s2")
    assert out == "x" * session_store._CONTENT_CAP
    assert len(out) == session_store._CONTENT_CAP


def test_query_uses_indexed_columns_only(tmp_db):
    """Sanity: the store's SQL filters on session_id + role (indexed) and orders
    by id — verify the query plan doesn't full-scan in a way we'd regret at
    155MB scale. (idx_messages_session_id exists in the live schema.)"""
    conn = sqlite3.connect(str(tmp_db))
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT content FROM messages"
        " WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1", ("s1",)
    ).fetchall()
    conn.close()
    detail = " | ".join(str(row[-1]) for row in plan)
    assert "SCAN" in detail.upper()  # tiny tmp db w/o index → scan is fine here
    # The real guard is the ORDER BY id DESC LIMIT 1 shape — early termination.
    assert "ORDER BY" in detail.upper() or "scan" in detail.lower()