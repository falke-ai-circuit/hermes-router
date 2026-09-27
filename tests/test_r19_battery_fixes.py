"""v4.11.4 battery-findings fixes (conductor live-verified):

FIX 1 — extract_options enumeration coverage: named-style markers
('Approach 1:', 'Option 2:', 'Path 3:', 'Variant 4:') must enumerate;
1147-char 4-approach fixture previously yielded 0 options.
FIX 2 — ledger materialization: fresh sqlite store + one parked verdict
-> tables + rows exist. (Root cause of the 'never materializes' finding:
the ledger lives in hermes_home/hermes_router_state.db, NOT the profile
state.db the operator inspected — 45 rows were present in analyst.)
FIX 3 — session-key sanitization: invalid/stale keys never become ledger
state; fallback logged at debug with raw key.
"""
import json
import sqlite3

import pytest

from hermes_router import decision, decision_midturn as dmt

SID = "s-r19mt4"


def _v3_cfg(**over):
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg.update(over)
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
    monkeypatch.setattr("hermes_router.usage_ledger.estimate_cost",
                        lambda *a, **k: 0.0, raising=True)
    monkeypatch.setattr("hermes_router._log_route",
                        lambda e, **f: None, raising=False)
    yield path
    dmt.reset_midturn()
    decision.reset_limits()
    decision.reset_v3_limits()


# ------------------------------------------------------------------
# FIX 1 — named-style enumeration
# ------------------------------------------------------------------

def _approach_fixture() -> str:
    """'Approach 1..4' fixture, >=600 chars (battery finding shape)."""
    body = (
        "Migration plan review — pick the rollout strategy.\n"
        "Approach 1: Big-bang cutover this weekend. Lowest running cost, "
        "but the blast radius is the whole fleet and rollback means "
        "restoring every profile from cold backups we have not rehearsed. "
        "Requires a 6-hour maintenance window and all hands.\n"
        "Approach 2: Per-profile rolling migration. Each profile moves on "
        "its own night with a canary check afterwards; total calendar time "
        "is two weeks but every step is independently reversible and the "
        "blast radius is one profile at a time.\n"
        "Approach 3: Parallel-run with a shadow writer. Zero downtime, "
        "highest cost while both stacks run, and the reconciliation job "
        "adds a new failure surface we would have to monitor.\n"
        "Approach 4: Freeze new features and migrate incrementally inside "
        "normal releases. Slowest to finish but zero dedicated windows and "
        "every change rides the normal review and rollback process.\n"
        "Which approach should we take — weigh the tradeoffs."
    )
    assert len(body) >= 600
    return body


def test_named_enumeration_extract_options(_env):
    opts = decision.extract_options(_approach_fixture())
    assert len(opts) >= 4
    low = [o.lower() for o in opts]
    assert any("big-bang" in o for o in low)
    assert any("rolling" in o for o in low)


def test_named_enumeration_enum_workflow_fires(_env):
    cfg = _v3_cfg()
    hit = decision.detect_v3(_approach_fixture(), 2, cfg=cfg)
    assert hit and hit.get("trigger") not in ("skip", "provenance_skip")
    assert "enum_workflow" in (hit.get("families") or []) or hit.get("options")


def test_named_enumeration_variants(_env):
    text = ("Option 1: keep it simple and safe.\n"
            "Path 2: take the scenic route with more moving parts.\n"
            "Variant 3: try the exotic architecture nobody has run here.\n"
            "Choice 4: defer the decision to next quarter.\n")
    opts = decision.extract_options(text)
    assert len(opts) >= 4


def test_named_enumeration_below_floor_still_quiet(_env):
    """Short text with named markers must NOT trip the structural gate."""
    short = "Approach 1: do X.\nApproach 2: do Y.\n"
    cfg = _v3_cfg()
    assert not decision._enum_hit(short, cfg)


# ------------------------------------------------------------------
# FIX 2 — ledger materialization
# ------------------------------------------------------------------

def test_ledger_materializes_on_fresh_store(tmp_path, monkeypatch):
    """Creation+write: a FRESH sqlite file -> one parked verdict ->
    decision_ledger + decision_counters exist and a row landed."""
    path = str(tmp_path / "fresh_state.db")
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _fresh_connect(path))
    rid = decision.ledger_write({
        "session_id": SID, "task_id": "midturn", "trigger": "midturn",
        "trigger_kind": "midturn_hook", "fork_class": "generic",
        "options_hash": "abc123def456", "model": "m",
        "choice": "opt-1", "confidence": 0.9,
        "verdict_json": json.dumps({"choice": "opt-1"}),
        "delta_source": "tool_result", "fork_signature": "abc123def456",
        "midturn_mode": "on", "seam": "terminal",
    })
    assert rid
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "decision_ledger" in tables
    assert "decision_counters" in tables
    rows = list(conn.execute("SELECT session_id, choice, seam"
                             " FROM decision_ledger"))
    conn.close()
    assert rows and rows[0][0] == SID and rows[0][1] == "opt-1"
    assert rows[0][2] == "terminal"


def _fresh_connect(path):
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.executescript(decision._LEDGER_SCHEMA)
    conn.commit()
    return conn


def test_ledger_counters_bump_on_fresh_store(tmp_path, monkeypatch):
    path = str(tmp_path / "fresh2.db")
    monkeypatch.setattr(decision, "_ledger_connect",
                        lambda db_path="": _fresh_connect(path))
    assert decision.bump_counter("malformed", path) == 1
    assert decision.get_counter("malformed", path) == 1
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "decision_counters" in tables


# ------------------------------------------------------------------
# FIX 3 — session-key sanitization
# ------------------------------------------------------------------

def test_sanitize_session_key_accepts_platform_shapes():
    assert dmt._sanitize_session_key("sess-abc123") == "sess-abc123"
    assert dmt._sanitize_session_key("cli:2026-09-27T10:00:00") \
        == "cli:2026-09-27T10:00:00"
    assert dmt._sanitize_session_key("  spaced-id  ") == "spaced-id"


def test_sanitize_session_key_rejects_foreign_shapes():
    assert dmt._sanitize_session_key("") == ""
    assert dmt._sanitize_session_key(None) == ""
    assert dmt._sanitize_session_key("has space") == ""
    assert dmt._sanitize_session_key("repr({'a': 1})") == ""
    assert dmt._sanitize_session_key("x" * 200) == ""
    assert dmt._sanitize_session_key("bad\nnewline") == ""


def test_flush_and_scan_sanitizes_foreign_key(_env, monkeypatch, caplog):
    import logging
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    with caplog.at_level(logging.DEBUG, logger="hermes_router."
                                               "decision_midturn"):
        # stale/foreign key like the observed 'probe-sess' bleed: sanitized
        # into the shared bucket, raw key logged at debug, no crash
        out = dmt.flush_and_scan("bad key with spaces",
                                 {"messages": [{"role": "user",
                                                "content": "go"}]})
    assert out == []
    assert any("fallback" in r.message.lower()
               for r in caplog.records)


def test_flush_and_scan_valid_key_untouched(_env, monkeypatch):
    cfg = _v3_cfg(midturn="on")
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    dmt.flush_and_scan(SID, {"messages": [{"role": "user", "content": "go"}]})
    # the valid key's own bucket exists (no fallback bleed)
    assert SID in dmt.runs_state()
    assert "active-session" not in dmt.runs_state()
