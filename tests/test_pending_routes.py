"""Pending-routes tests (spec §10 row 8 + §6.1).

Pre-router stashes the original user message, post-router pops it. Covers
round-trip, consume-on-read, TTL eviction, hard-cap eviction, and concurrent
access via the lock.
"""
import os
import sys
import threading
import time

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import state


@pytest.fixture(autouse=True)
def _reset():
    state.clear()
    yield
    state.clear()


def test_round_trip():
    state.stash_pending("s1", "m1", "original message", "rendered")
    assert state.pop_pending("s1", "m1", ttl_seconds=300) == "original message"


def test_consume_on_read():
    state.stash_pending("s1", "m1", "original message", "rendered")
    assert state.pop_pending("s1", "m1", ttl_seconds=300) == "original message"
    assert state.pop_pending("s1", "m1", ttl_seconds=300) is None


def test_ttl_expiry():
    state.stash_pending("s1", "m1", "original message", "rendered")
    # manipulate created_at directly to simulate age
    with state._PENDING_LOCK:
        for _, entry in state._PENDING:
            entry["created_at"] -= 400  # older than 300s TTL
    assert state.pop_pending("s1", "m1", ttl_seconds=300) is None


def test_ttl_boundary_fresh_entry_wins():
    state.stash_pending("s1", "m1", "stale", "r1")
    state.stash_pending("s1", "m1", "fresh", "r2")
    with state._PENDING_LOCK:
        # age only the first entry
        first_key, first_entry = state._PENDING[0]
        first_entry["created_at"] -= 400
    assert state.pop_pending("s1", "m1", ttl_seconds=300) == "fresh"


def test_key_isolation():
    state.stash_pending("s1", "m1", "msg-A", "r")
    state.stash_pending("s2", "m1", "msg-B", "r")
    state.stash_pending("s1", "m2", "msg-C", "r")
    assert state.pop_pending("s1", "m1", ttl_seconds=300) == "msg-A"
    assert state.pop_pending("s2", "m1", ttl_seconds=300) == "msg-B"
    assert state.pop_pending("s1", "m2", ttl_seconds=300) == "msg-C"
    assert state.pop_pending("s1", "m1", ttl_seconds=300) is None


def test_cap_eviction_fifo():
    for i in range(state.PENDING_MAX + 1):  # 33 entries
        state.stash_pending("s", f"m{i}", f"msg-{i}", "r")
    # oldest (m0) was evicted by the cap
    assert state.pop_pending("s", "m0", ttl_seconds=300) is None
    assert state.pop_pending("s", "m1", ttl_seconds=300) == "msg-1"
    assert state.pop_pending("s", f"m{state.PENDING_MAX}", ttl_seconds=300) == f"msg-{state.PENDING_MAX}"


def test_concurrent_access_lock_guarded():
    """Hammer stash/pop from multiple threads — no exceptions, no lost updates
    beyond the documented cap semantics."""
    errors = []

    def stasher(n):
        try:
            for i in range(50):
                state.stash_pending("conc", "m", f"msg-{n}-{i}", "r")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def popper():
        try:
            for _ in range(50):
                state.pop_pending("conc", "m", ttl_seconds=300)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=stasher, args=(n,)) for n in range(4)]
    threads += [threading.Thread(target=popper) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []


def test_rendered_hash_recorded():
    state.stash_pending("s1", "m1", "original", "rendered-content")
    with state._PENDING_LOCK:
        entry = state._PENDING[0][1]
    assert entry["rendered_content_hash"] == state.hash_text("rendered-content")


def test_empty_session_key_tolerated():
    state.stash_pending("", "m1", "msg", "r")
    assert state.pop_pending("", "m1", ttl_seconds=300) == "msg"