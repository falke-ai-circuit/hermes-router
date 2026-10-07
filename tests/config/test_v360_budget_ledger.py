"""v3.6.0 Phase-0 budget-ledger tests (suggestions.py, §2.5 + P0.4).

Pins: commit-on-terminal, abandon-on-interrupt, no-double-charge on
crash-before-commit, corrupt-line skip. budget.jsonl events flow (the shadow
data). canonical.py discipline: append-only, chmod 0600, corrupt line skipped.
"""
import json
import os

import pytest

from hermes_router import suggestions


@pytest.fixture(autouse=True)
def _budget_isolated(tmp_path, monkeypatch):
    """Point the budget ledger at a test-scoped path; reset in-memory state."""
    ledger = tmp_path / "budget-home" / "hermes-router-budget.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(suggestions, "_store_path", lambda: str(ledger))
    suggestions.clear_for_tests()
    yield ledger
    suggestions.clear_for_tests()


def test_commit_on_terminal():
    """Delivered suggestion commits on turn terminal; budget increments once."""
    seq = suggestions.record_sugg_fired("t1", "post")
    assert seq is not None
    assert suggestions.record_sugg_delivered("t1", "post", seq) is True
    committed = suggestions.commit_turn("t1")
    assert committed == [seq]
    assert suggestions.committed_count("t1") == 1
    # events flowed to the ledger (the shadow data)
    events = suggestions.replay_events()
    kinds = [e.get("event") for e in events]
    assert kinds == ["sugg_fired", "sugg_delivered", "turn_committed"]


def test_commit_without_delivery_is_empty():
    """Fired-but-undelivered stays pending: terminal commit flips nothing
    (only DELIVERED VALID suggestions count — §2.5)."""
    seq = suggestions.record_sugg_fired("t2", "mid")
    assert seq is not None
    assert suggestions.commit_turn("t2") == []
    assert suggestions.committed_count("t2") == 0


def test_abandon_on_interrupt():
    """Interrupted turn: fired-without-terminal -> abandoned on the next
    dispatch/replay — budget-exempt, tokens-spent (§2.5 Rule 2)."""
    seq = suggestions.record_sugg_fired("t3", "pre")
    assert seq is not None
    abandoned = suggestions.abandon_turn("t3")
    assert abandoned == [seq]
    assert suggestions.committed_count("t3") == 0  # budget NOT consumed
    assert suggestions.task_summary("t3")["abandoned"] == [seq]


def test_no_double_charge_crash_before_commit():
    """Crash between sugg_fired and turn_committed: replay sees
    fired-without-terminal -> abandoned (never double-charged). Simulate by
    dropping in-memory state (process death) and replaying the ledger."""
    seq = suggestions.record_sugg_fired("t4", "post")
    assert seq is not None
    suggestions.record_sugg_delivered("t4", "post", seq)
    suggestions.commit_turn("t4")  # the delivered terminal commits seq 1
    # "crash": in-memory state dies; ledger replay reconstructs
    suggestions.clear_for_tests()
    # replay: the commit survived; a second commit call re-charges NOTHING
    assert suggestions.committed_count("t4") == 1
    assert suggestions.commit_turn("t4") == []  # idempotent — no re-charge
    assert suggestions.committed_count("t4") == 1
    # and the crash-before-commit case: a fired-only suggestion is abandoned,
    # never double-charged
    seq2 = suggestions.record_sugg_fired("t4b", "pre")
    suggestions.clear_for_tests()
    assert suggestions.abandon_turn("t4b") == [seq2]  # fired-only -> abandoned
    assert suggestions.committed_count("t4b") == 0  # budget intact


def test_corrupt_line_skipped(tmp_path, monkeypatch):
    """Corrupt/torn ledger line is skipped on replay; parseable events still
    fold (fail-open toward budget availability, never double-charging)."""
    ledger = tmp_path / "budget-home" / "hermes-router-budget.jsonl"
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write('{"event": "sugg_fired", "task_id": "t5", "gate": "pre", "seq": 1, "ts": 1}\n')
        fh.write('{"event": "sugg_delivered", "task_id": "t5", "gate": "pre", "seq": 1\n')  # torn
        fh.write("not json at all\n")
        fh.write('{"event": "turn_committed", "task_id": "t5", "delivered": [1], "ts": 2}\n')
    # fresh read (in-memory empty) replays: seq1 fired; torn line skipped;
    # turn_committed folds -> delivered count 1
    assert suggestions.committed_count("t5") == 1
    assert suggestions.task_summary("t5")["fired"] == []  # folded into commit


def test_chmod_0600_on_ledger():
    """canonical.py discipline: ledger file lands 0600."""
    seq = suggestions.record_sugg_fired("t6", "mid")
    assert seq is not None
    import stat as _stat

    path = suggestions._store_path()
    mode = _stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_budget_cap_would_spend():
    """Budget invariant: sum(committed) < 3 gates the next fire (§2.5 Rule 1)."""
    task = "t7"
    seqs = []
    for _ in range(3):
        s = suggestions.record_sugg_fired(task, "post")
        assert s is not None
        suggestions.record_sugg_delivered(task, "post", s)
        assert suggestions.commit_turn(task) != []
        seqs.append(s)
    assert suggestions.committed_count(task) == 3
    ok, reason = suggestions.would_spend(task, "post")
    assert ok is False and reason == "budget_exhausted"


def test_invalid_gate_rejected():
    """Gate names are enum-checked — no invented gates."""
    assert suggestions.record_sugg_fired("t8", "sideways") is None
    assert suggestions.would_spend("t8", "sideways") == (False, "invalid_gate")