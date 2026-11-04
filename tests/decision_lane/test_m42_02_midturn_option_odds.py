"""M42-02 F1 — a trigger=midturn row persists a probability per option.

Live shape this pins: every other decision surface serialized its option
board through `decision.ledger_options_json`, which attaches the verdict's
measured distribution; the midturn seam (`_ledger_base`) built its board with
a local comprehension and wrote `id`+`label` only. Midturn is the strongest
outcome-capture surface — the fork lands after tool results, before the agent
commits — so it was also the one surface with no odds to calibrate against.

The assertion is on the PERSISTED row, not on the helper: the helper already
had a passing test (`test_m42_02_option_probabilities`), so a test of the
helper alone would pass before the fix and prove nothing.
"""
import json
import sqlite3

import pytest

from hermes_router import decision, decision_midturn as dmt
from hermes_router import on_transform_terminal_output

SID = "s-m42-02-mt"

TOOL_FORK = ("harness verdict — which one should we pick?\n"
             "a) redis\nb) memcached\nc) sqlite — weigh the tradeoffs")


def _cfg():
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    return cfg


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()

    path = str(tmp_path / "plugin.db")
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": sqlite3.connect(path))
    monkeypatch.setattr("hermes_router.core.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr("hermes_router._log_route",
                        lambda e, **f: None, raising=False)
    yield path
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM decision_ledger ORDER BY id")]
    conn.close()
    return rows


def test_midturn_row_persists_probability_per_option(_env, monkeypatch):
    """A verdict that answers with a distribution -> the midturn ROW carries
    one probability per option, keyed to the same envelope ids `choice` uses."""
    seen = {}

    def _backend(envelope, cfg):
        ids = [o["id"] for o in (envelope.get("options") or [])
               if isinstance(o, dict)]
        assert len(ids) >= 2, ids
        probs = {oid: 1.0 / len(ids) for oid in ids}
        body = {"choice": ids[0], "confidence": 0.7,
                "alternatives": ids[1:], "probabilities": probs}
        seen["ids"] = ids
        meta = {"model": str(cfg.get("model") or "m"), "endpoint": "e",
                "tokens_in": 10, "tokens_out": 5, "latency_s": 0.1}
        return (json.dumps(body), meta, "ok")

    monkeypatch.setattr(decision, "call_backend", _backend)
    monkeypatch.setattr(dmt, "_cfg", lambda: _cfg())
    monkeypatch.setattr(dmt, "_mode", lambda cfg: "on")

    on_transform_terminal_output(command="ls", output=TOOL_FORK,
                                 session_id=SID)

    rows = [r for r in _rows(_env) if r.get("trigger") == "midturn"]
    assert rows, "a trigger=midturn row must exist"
    row = rows[-1]
    board = json.loads(row["options_json"] or "[]")
    assert len(board) >= 2, row["options_json"]

    # the ids are the envelope ids, not a renumbering
    assert [o["id"] for o in board] == seen["ids"]
    assert row["choice"] in [o["id"] for o in board]

    # every option carries a probability, and the distribution is real
    for o in board:
        assert "probability" in o, board
        assert isinstance(o["probability"], float)
    assert abs(sum(o["probability"] for o in board) - 1.0) < 0.01
