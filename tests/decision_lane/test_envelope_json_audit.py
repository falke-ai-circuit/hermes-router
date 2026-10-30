"""UNIT 2 pin: the decision envelope is auditable after the fact.

Measured defect: decision_ledger stored envelope_hash / envelope_ids /
envelope_digest only, and the digest is counts-only (<200 chars on every row
across the fleet), so what the lane actually sent could not be verified from
data. envelope_json now carries the bounded body, written next to the digest.
"""
from __future__ import annotations

import json
import sqlite3

from hermes_router import decision as d


KEYS = ("agent_frame", "final_objective", "rolling_task", "session_context",
        "mindset", "options")


def _row(ledger, rid, column):
    conn = sqlite3.connect(ledger)
    try:
        return conn.execute(
            "SELECT %s FROM decision_ledger WHERE id = ?" % column,
            (rid,)).fetchone()
    finally:
        conn.close()


def test_built_envelope_round_trips_through_the_ledger(tmp_path):
    env = d.build_envelope("s-audit", "redis or memcached?",
                           ["redis", "memcached"], "pre", {},
                           surrounding_context="user: pick a cache layer",
                           task="pick a cache layer")
    body = d.envelope_json(env)
    parsed = json.loads(body)          # valid JSON, not a half-written string
    for k in KEYS:
        assert k in parsed, "envelope_json lost top-level key %r" % k
    assert parsed["options"] and "id" in parsed["options"][0]

    ledger = str(tmp_path / "ledger.db")
    rid = d.ledger_write({"session_id": "s-audit", "trigger": "pre",
                          "envelope_digest": '{"sctx_chars": 26}',
                          "envelope_json": body}, db_path=ledger)
    assert rid
    stored = _row(ledger, rid, "envelope_json")[0]
    again = json.loads(stored)
    assert again == parsed
    for k in KEYS:
        assert k in again


def test_oversized_envelope_is_truncated_but_valid_json():
    env = d.build_envelope("s-big", "redis or memcached?",
                           ["redis", "memcached"], "pre", {},
                           surrounding_context="x" * 50000,
                           task="pick a cache layer")
    env["session_context"] = "y" * 50000
    env["options"] = env.get("options") or [{"id": "opt-1", "label": "redis"}]
    for opt in env["options"]:
        opt["cause_effect"] = "z" * 20000

    body = d.envelope_json(env)
    assert len(body) <= d._ENVELOPE_JSON_CAP
    parsed = json.loads(body)           # truncation never emits invalid JSON
    for k in KEYS:
        assert k in parsed
    assert parsed.get("_truncated") is True
    assert parsed["_full_chars"] > d._ENVELOPE_JSON_CAP
    assert any(d._ENVELOPE_JSON_MARK in s for s in
               (parsed.get("session_context") or "",)), (
        "the truncation marker must be visible in the clipped body")


def test_envelope_json_fails_open_on_junk():
    assert d.envelope_json({}) == "{}"
    assert d.envelope_json(None) == "{}"


def test_envelope_json_never_exceeds_cap_even_with_tiny_cap():
    env = {"agent_frame": "a" * 9000, "final_objective": "b" * 9000,
           "rolling_task": "c" * 9000, "session_context": "d" * 9000,
           "mindset": {"scope": 0.2}, "options": [{"id": "opt-1"}]}
    body = d.envelope_json(env, cap=2000)
    assert len(body) <= 2000
    json.loads(body)
