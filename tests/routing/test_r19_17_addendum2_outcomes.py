"""R19.17 ADDENDUM 2 (Goran-approved; outcome-labeling defects from the
fleet-wide auto-labeling run — /opt/data/tmp/r19_auto_labeler.py, 251 rows):

1) OPTION-SPAN: a choice matching NO envelope option (e.g. reviewer row 78
   'No such file' lifted from a log dump) is recorded as choice='unmapped'
   + outcome='invalid_fork' — never free text in the choice column. Label
   hits map to the option id.
2) DELTA PERSISTENCE: verdict_json carries the source delta excerpt (first
   500 chars of the scanned content) — choice-in-source checkable without
   re-reading sessions.
3) RESCAN DEDUPE: the same fork signature re-swept within a session skips
   at detection (no second verdict).
4) EVOL MIGRATION: per-column fail-open drift migration — one failing
   ALTER no longer aborts the rest (evol's pre-R19.2 ledger lacking
   fork_signature gets it on next connect).
"""
import json
import time
import sqlite3

import pytest

from hermes_router import decision as D


@pytest.fixture()
def _reset_lane():
    """Isolate shared lane state (breaker/caps/rescan registry) per test —
    other tests in the same pytest process leak module globals."""
    D.reset_limits()
    D.reset_v3_limits()
    D._RESCAN_SIGS.clear()
    yield
    D.reset_limits()
    D._RESCAN_SIGS.clear()


def _env():
    return {"fork_class": "deploy",
            "options": [{"id": "opt-1", "label": "Keep the parser"},
                        {"id": "opt-2", "label": "Rewrite the parser"}]}


# --- 1: option-span anchoring ---------------------------------------------------

def test_choice_by_id_ok():
    v, r = D.validate_verdict('{"choice": "opt-1", "confidence": 0.9}',
                              _env())
    assert r == "ok" and v["choice"] == "opt-1"


def test_choice_by_label_maps_to_id():
    v, r = D.validate_verdict('{"choice": "Keep the parser", "confidence": 0.9}',
                              _env())
    assert r == "ok" and v["choice"] == "opt-1"


def test_choice_label_case_insensitive():
    v, r = D.validate_verdict('{"choice": "rewrite THE parser", "confidence": 0.8}',
                              _env())
    assert r == "ok" and v["choice"] == "opt-2"


def test_unmapped_choice_is_invalid_fork_never_free_text():
    """PIN (reviewer row 78): choice text from an error dump maps to NO
    option -> choice='unmapped', reason=invalid_fork — never the free
    text."""
    v, r = D.validate_verdict('{"choice": "No such file", "confidence": 0.95}',
                              _env())
    assert r == D.REASON_INVALID_FORK
    assert v["choice"] == "unmapped"
    assert v["alternatives"] == []


def test_non_string_choice_still_malformed():
    v, r = D.validate_verdict('{"choice": 5, "confidence": 0.9}', _env())
    assert v is None and r == D.REASON_MALFORMED


# --- 2: delta persistence --------------------------------------------------------

def test_verdict_json_carries_delta_excerpt(tmp_path):
    """The row helper truncates the scanned content to 500 chars; top-level
    verdict keys are preserved."""
    body = "y" * 800
    data = json.loads(D.verdict_row_json(
        {"choice": "opt-1", "confidence": 0.9, "alternatives": []}, body))
    assert data["choice"] == "opt-1"  # top-level keys preserved
    assert len(data["delta_source_excerpt"]) == 500  # exactly 500 chars
    # fail-open: garbage inputs never raise
    assert json.loads(D.verdict_row_json(None, None))["delta_source_excerpt"] == ""


# --- 3: rescan dedupe -------------------------------------------------------------

def test_rescan_dedupe_same_signature_skips(monkeypatch, _reset_lane):
    """PIN: the same fork signature re-swept within a session fires ONCE;
    the second sweep logs decision_rescan_dedupe and dispatches nothing."""
    logged = []
    cfg = dict(D.DEFAULTS)
    cfg["level"] = 3  # aggressive: heuristic fires on the same ask twice
    ask = ("which one should we pick: redis or memcached? weigh the tradeoffs")

    def _lr(event, **fields):
        logged.append((event, fields))
        if event == "decision_suppressed":
            print("SUPPRESS:", fields.get("reason"))

    # re-scan = the SAME task re-swept (same session + task + signature)
    D.handle_decision_v3(session_id="s-dedupe", task_id="t1",
                         task_text=ask, log_route=_lr, cfg=cfg)
    D.handle_decision_v3(session_id="s-dedupe", task_id="t1",
                         task_text=ask, log_route=_lr, cfg=cfg)
    print("SIGS:", dict(D._RESCAN_SIGS))
    events = [e for e, _ in logged]
    assert events.count("decision_rescan_dedupe") >= 1
    assert events.count("decision_v3_dispatched") == 1  # re-sweep skipped
    # a different task re-using the same option shape is a NEW fork
    D.handle_decision_v3(session_id="s-dedupe", task_id="t2",
                         task_text=ask, log_route=_lr, cfg=cfg)
    # the third dispatch is sync, but prior workers may interleave log
    # appends — poll briefly rather than race the shared list
    deadline = time.time() + 2.0
    while time.time() < deadline and \
            [e for e, _ in logged].count("decision_v3_dispatched") < 2:
        time.sleep(0.02)
    assert [e for e, _ in logged].count("decision_v3_dispatched") == 2
    # and a genuine re-scan of THAT task dedupes too
    D.handle_decision_v3(session_id="s-dedupe", task_id="t2",
                         task_text=ask, log_route=_lr, cfg=cfg)
    assert any(e == "decision_rescan_dedupe" for e, _ in logged)
    D._RESCAN_SIGS.pop("s-dedupe", None)


def test_rescan_dedupe_ttl_expiry(monkeypatch, _reset_lane):
    """Old signatures expire (TTL) — a later re-sweep can fire again."""
    D._RESCAN_SIGS["s-ttl"] = {"deadbeef": 1.0}  # ancient ts
    cfg = dict(D.DEFAULTS)
    cfg["level"] = 3
    logged = []
    D.handle_decision_v3(
        session_id="s-ttl", task_id="t9",
        task_text="which one: redis or memcached?",
        log_route=lambda e, **f: logged.append((e, f)), cfg=cfg)
    assert not any(e == "decision_rescan_dedupe" for e, _ in logged)
    D._RESCAN_SIGS.pop("s-ttl", None)


# --- 4: evol migration (per-column fail-open) --------------------------------------

def test_migration_adds_fork_signature_to_old_schema(tmp_path):
    """A pre-R19.2 ledger (no fork_signature) gains ALL R19.2 columns on
    next connect — one failing ALTER cannot abort the rest."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE decision_ledger (id INTEGER PRIMARY KEY,"
                 " ts REAL, session_id TEXT, task_id TEXT, trigger TEXT)")
    conn.commit()
    conn.close()
    c = D._ledger_connect(db)
    cols = {r[1] for r in c.execute("PRAGMA table_info(decision_ledger)")}
    c.close()
    assert {"delta_source", "fork_signature", "midturn_mode",
            "envelope_ids", "tool_name", "seam",
            "trigger_kind"} <= cols


def test_migration_idempotent_no_raise(tmp_path):
    """Fail-open per column: reconnecting twice over the same old schema
    adds everything once, then no-ops — never raises."""
    db = str(tmp_path / "idem.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE decision_ledger (id INTEGER PRIMARY KEY,"
                 " ts REAL, session_id TEXT, task_id TEXT, trigger TEXT,"
                 " delta_source TEXT NOT NULL DEFAULT '')")
    conn.commit()
    conn.close()
    c1 = D._ledger_connect(db)
    c2 = D._ledger_connect(db)
    cols = {r[1] for r in c2.execute("PRAGMA table_info(decision_ledger)")}
    c1.close()
    c2.close()
    assert "fork_signature" in cols and "sense_check" in cols \
        and "p_failure" in cols
