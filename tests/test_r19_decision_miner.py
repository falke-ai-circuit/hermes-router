"""R19 step 2 — decision_miner + POST leg test battery (spec §10).

Covers: miner extraction from a fixture history DB, evol.jsonl mining,
resume cursor, provenance exclusion (echo-loop guard at miner level),
POST-leg action detection on a synthetic autonomous run, post_audit
record write, parked advisory on precedent contradiction, dark-default
no-op, store-cap bound, fail-open.
"""
import json
import sqlite3
import time

import pytest

import hermes_router as plugin
from hermes_router import debug_banner, decision, decision_miner

SID = "s-r19m"

LOGGED = []


@pytest.fixture()
def _reset(monkeypatch, tmp_path):
    plugin.state.clear()
    LOGGED.clear()
    debug_banner._ANCHOR_BANNERS.clear()
    decision.reset_limits()
    monkeypatch.setattr(plugin, "_log_route",
                        lambda e, **f: LOGGED.append((e, dict(f))))
    # miner seams: isolated plugin DB + fixture history DB + evol file
    plug_db = tmp_path / "hermes_router_state.db"
    hist_db = tmp_path / "state.db"
    evol = tmp_path / "evol.jsonl"
    monkeypatch.setattr(decision_miner, "plugin_db_path", lambda: str(plug_db))
    monkeypatch.setattr(decision_miner, "_history_db_path", lambda: str(hist_db))
    monkeypatch.setattr(decision_miner, "_evol_db_path", lambda: str(evol))
    cfg = dict(decision.DEFAULTS)
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    yield {"plug_db": str(plug_db), "hist_db": str(hist_db), "evol": str(evol),
           "cfg": cfg}
    decision.reset_limits()
    debug_banner._ANCHOR_BANNERS.clear()


def _mk_hist(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (session_key TEXT, git_repo_root TEXT);"
        "CREATE TABLE messages (content TEXT, timestamp REAL, session_id TEXT);"
        "CREATE VIRTUAL TABLE messages_fts USING fts5(content);")
    for content, ts, sid in rows:
        conn.execute("INSERT INTO messages VALUES (?,?,?)", (content, ts, sid))
        conn.execute("INSERT INTO messages_fts(rowid, content) SELECT rowid,"
                     " content FROM messages WHERE rowid = (SELECT max(rowid)"
                     " FROM messages)")
    conn.commit()
    conn.close()


def _logged(event):
    return [f for e, f in LOGGED if e == event]


# --------------------------------------------------------------------------
# 1. Miner extraction from a fixture history DB
# --------------------------------------------------------------------------

def test_miner_extracts_decision_episodes(_reset):
    r = _reset
    now = time.time()
    _mk_hist(r["hist_db"], [
        ("we need to pick between approach A and approach B for the retry "
         "layer — the tradeoffs are real", now - 200, SID),
        ("just a status note, nothing decided here", now - 190, SID),
        ("went with approach B; rollback available if it breaks",
         now - 180, SID),
    ])
    out = decision_miner.run_miner()
    assert out["added"] >= 2 and out.get("reason") is None
    assert decision_miner.record_count() >= 2
    # retrieval finds the mined precedent, tagged mined
    hits = decision_miner.retrieve("approach B retry layer tradeoffs")
    assert hits and all(h["tag"] == "mined" for h in hits)
    assert any("approach B" in h["situation"] for h in hits)


def test_miner_non_decision_messages_skipped(_reset):
    r = _reset
    now = time.time()
    _mk_hist(r["hist_db"], [
        ("please run the test suite and report the count", now - 100, SID),
        ("ok", now - 90, SID),
    ])
    out = decision_miner.run_miner()
    assert out["added"] == 0
    assert decision_miner.record_count() == 0


def test_miner_evol_ledger(_reset):
    r = _reset
    now = time.time()
    with open(r["evol"], "w") as fh:
        fh.write(json.dumps({"id": "ev1", "lane": "complexity",
                             "action": "flash_direct",
                             "basis": "choosing between option A and "
                                      "option B — classic tradeoffs",
                             "timestamp": now - 60}) + "\n")
        fh.write("not json at all\n")
    out = decision_miner.run_miner()
    assert out["added"] == 1
    hits = decision_miner.retrieve("option A option B tradeoffs")
    assert hits and hits[0]["source"].startswith("evol:ev1")


# --------------------------------------------------------------------------
# 2. Resume cursor: re-run does not duplicate
# --------------------------------------------------------------------------

def test_miner_resume_no_duplicates(_reset):
    r = _reset
    now = time.time()
    _mk_hist(r["hist_db"], [
        ("decide this: which approach for the cache layer? weigh the "
         "tradeoffs", now - 300, SID),
    ])
    first = decision_miner.run_miner()
    assert first["added"] == 1
    second = decision_miner.run_miner()
    assert second["added"] == 0          # cursor advanced past the row
    assert decision_miner.record_count() == 1  # dedupe holds


def test_miner_new_rows_after_cursor_are_picked_up(_reset):
    r = _reset
    now = time.time()
    _mk_hist(r["hist_db"], [
        ("decide this: which approach for the queue? weigh the tradeoffs",
         now - 300, SID),
    ])
    assert decision_miner.run_miner()["added"] == 1
    conn = sqlite3.connect(r["hist_db"])
    conn.execute("INSERT INTO messages VALUES (?,?,?)",
                 ("you decide — option A or option B for the sharder?",
                  now - 50, SID))
    conn.commit()
    conn.close()
    assert decision_miner.run_miner()["added"] == 1
    assert decision_miner.record_count() == 2


# --------------------------------------------------------------------------
# 3. Store cap + bounded walk
# --------------------------------------------------------------------------

def test_miner_store_cap(_reset):
    r = _reset
    r["cfg"]["miner_max_records"] = 1
    now = time.time()
    _mk_hist(r["hist_db"], [
        ("decide this: which approach for the cache? weigh the tradeoffs",
         now - 300, SID),
        ("which approach for the store? the tradeoffs matter — decide this",
         now - 200, SID),
    ])
    out = decision_miner.run_miner()
    assert decision_miner.record_count() <= 1


def test_miner_fail_open_on_bad_history(_reset):
    r = _reset
    with open(r["hist_db"], "w") as fh:
        fh.write("this is not a sqlite database\n")
    out = decision_miner.run_miner()      # must not raise
    assert out["added"] == 0


def test_miner_fail_open_no_dbs(_reset, monkeypatch):
    monkeypatch.setattr(decision_miner, "_history_db_path", lambda: "")
    monkeypatch.setattr(decision_miner, "_evol_db_path", lambda: "")
    out = decision_miner.run_miner()
    assert out["added"] == 0


# --------------------------------------------------------------------------
# 4. Provenance exclusion (echo-loop guard at miner level)
# --------------------------------------------------------------------------

def test_lane_advisory_tag_never_stored(_reset):
    rid = decision_miner.write_record(
        situation_text="echo bait", options=[], chosen="", outcome="",
        outcome_ts=None, source="test", provenance_tag="lane_advisory")
    assert rid is None                    # rejected at write time
    assert decision_miner.record_count() == 0


def test_retrieve_excludes_lane_advisory(_reset):
    decision_miner.write_record(
        situation_text="cache layer tradeoffs", options=[], chosen="",
        outcome="", outcome_ts=None, source="test", provenance_tag="mined")
    # force-row a lane_advisory record behind the writer's back (defense in
    # depth must hold even against a rogue producer)
    conn = sqlite3.connect(_reset["plug_db"])
    conn.execute("INSERT OR REPLACE INTO decision_records VALUES"
                 " ('bait', ?, 'cache layer tradeoffs bait', '[]', '', '',"
                 "  NULL, 'test', 'lane_advisory', ?)",
                 (time.time(), time.time()))
    conn.commit()
    conn.close()
    hits = decision_miner.retrieve("cache layer tradeoffs")
    assert hits and all(h["tag"] != "lane_advisory" for h in hits)
    assert all(h["id"] != "bait" for h in hits)


# --------------------------------------------------------------------------
# 5. POST leg — detection, record write, advisory, dark default
# --------------------------------------------------------------------------

def _aux_body(decision_, confidence, precedents):
    return json.dumps({
        "choices": [{"message": {"content": json.dumps({
            "decision": decision_, "confidence": confidence,
            "precedents": precedents})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


def _mk_frame_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (session_key TEXT, git_repo_root TEXT);"
        "CREATE TABLE messages (content TEXT, timestamp REAL, session_id TEXT);"
        "CREATE VIRTUAL TABLE messages_fts USING fts5(content);")
    conn.execute("INSERT INTO messages VALUES (?,?,?)",
                 ("we chose approach B for buffer sizing", time.time() - 100,
                  SID))
    conn.execute("INSERT INTO messages_fts(rowid, content) SELECT rowid,"
                 " content FROM messages")
    conn.commit()
    conn.close()


def test_post_detect_actions_synthetic_run():
    resp = ("Hit a failure on approach A. Retrying with approach B now. "
            "If this breaks again we are abandoning the direct route.")
    acts = decision_miner.detect_actions(resp)
    fams = {f for a in acts for f in a["families"]}
    assert "retry" in fams and "abort" in fams
    assert all(a["action_text"] for a in acts)


def test_post_detect_actions_none_on_plain_text():
    assert decision_miner.detect_actions("All checks passed. Nothing to do.") == []


def test_post_dark_default_noop(_reset, monkeypatch):
    # decision.enabled False default AND post_audit False default: the scan
    # is a total no-op even on a decision-shaped response.
    r = _reset
    _mk_frame_db(r["plug_db"] + ".frame")
    resp = ("Hit a failure. Retrying with approach B now.")
    decision_miner.post_audit_scan(SID, resp, model="m", log_route=lambda e, **f: LOGGED.append((e, dict(f))))
    time.sleep(0.2)
    assert _logged("decision_post_dispatched") == []
    assert decision_miner.record_count() == 0
    assert debug_banner.consume_parked_banner(SID) == ""


def test_post_enabled_writes_record_and_parks_advisory(_reset, monkeypatch):
    r = _reset
    r["cfg"]["enabled"] = True
    r["cfg"]["post_audit"] = True
    _mk_frame_db(r["plug_db"] + ".frame")
    monkeypatch.setattr(decision, "_db_path",
                        lambda: r["plug_db"] + ".frame")
    monkeypatch.setattr("hermes_router.semantic_classifier._hermes_aux_call",
                        lambda payload, timeout: _aux_body(
                            "apply_precedent", 0.9, ["1"]))
    resp = ("Approach A failed. Retrying with approach B — we went with the "
            "buffer-first order.")
    decision_miner.post_audit_scan(SID, resp, model="m",
                                   log_route=lambda e, **f: LOGGED.append((e, dict(f))))
    assert _logged("decision_post_dispatched")
    deadline = time.time() + 5
    while time.time() < deadline and decision_miner.record_count() < 1:
        time.sleep(0.02)
    assert decision_miner.record_count() >= 1
    hits = decision_miner.retrieve("buffer approach retry")
    assert hits and any(h["tag"] == "post_audit" for h in hits)
    deadline = time.time() + 5
    while time.time() < deadline and not debug_banner._ANCHOR_BANNERS.get(SID):
        time.sleep(0.02)
    banner = debug_banner.consume_parked_banner(SID)
    assert decision.PROVENANCE_TAG in banner  # parked at next delivery boundary


def test_post_records_even_without_frame(_reset, monkeypatch):
    # no frame (empty history): the audited decision is still recorded —
    # memory grows as a side effect of operation
    r = _reset
    r["cfg"]["enabled"] = True
    r["cfg"]["post_audit"] = True
    monkeypatch.setattr(decision, "_db_path", lambda: str(r["hist_db"]))
    resp = "Aborting the direct route, rolling back to the queue."
    decision_miner.post_audit_scan(SID, resp, model="m",
                                   log_route=lambda e, **f: LOGGED.append((e, dict(f))))
    deadline = time.time() + 5
    while time.time() < deadline and decision_miner.record_count() < 1:
        time.sleep(0.02)
    assert decision_miner.record_count() >= 1
    assert _logged("decision_post_recorded")


def test_post_scan_never_raises_on_garbage(_reset):
    r = _reset
    r["cfg"]["enabled"] = True
    r["cfg"]["post_audit"] = True
    decision_miner.post_audit_scan(None, None, model=None, context=None)  # noqa
    decision_miner.post_audit_scan(SID, 12345, model="m")  # type: ignore[arg-type]
