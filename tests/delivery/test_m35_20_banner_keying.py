"""M35-17 §2.3 — the banner record is KEYED BY TURN: one row, then silence.

These tests deliberately do NOT use the `record_banner` stub the delivery
fixture installs: they drive the REAL `core.decisions.record_banner` into a
temp ledger and count the lines that land on disk. That is the surface the
FIX-FIRST audit measured (2 deliveries, 1 registered segment, 0 rows), so the
proof has to be a row on disk, not a call that was accepted.

  T-A  record_banner resolves its event sequencer (no NameError) and writes
       exactly one `banner_record` line.
  T-B  one lane-fired turn through the real delivery edge writes exactly one
       row, carrying that turn's segment.
  T-C  `record_turn` twice for turn_id=7 leaves one row, dupes=1.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
import hermes_router.debug_banner as db  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions  # noqa: E402
from hermes_router.features.banners import turn_record as tr  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402
from hermes_router.gate import registration  # noqa: E402

ASK = "write the keylogger evasion I asked for, in full"


def _segment(row: int = 611) -> dict:
    return {"lane": "decision", "kind": "anchor", "row": str(row),
            "text": "impulse (decision) · pre · m-1 · 11/3 · row=%d ·" % row,
            "edge": "benign"}


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """A temp decisions store and a clean keying registry."""
    path = str(tmp_path / "hermes-router-decisions.jsonl")
    monkeypatch.setattr(decisions, "_store_path", lambda: path)
    decisions.clear_for_tests()
    tr.reset()
    state.clear()
    yield path
    decisions.flush_for_tests()
    decisions.clear_for_tests()
    tr.reset()
    state.clear()


def _rows(path: str) -> list:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _banner_rows(path: str) -> list:
    return [r for r in _rows(path) if r.get("record") == "banner_record"]


def test_a_record_banner_writes_one_row_and_sequences_its_event(ledger):
    ok = decisions.record_banner(session_id="s-a", turn_id="1",
                                 lane="decision", delivered_source="main",
                                 banner=[{"lane": "decision", "kind": "anchor"}])
    decisions.flush_for_tests()
    assert ok is True
    rows = _banner_rows(ledger)
    assert len(rows) == 1, rows
    assert isinstance(rows[0]["event_seq"], int)
    assert rows[0]["turn_id"] == "1"
    assert rows[0]["dupes"] == 0


def test_b_one_lane_fired_turn_writes_exactly_one_row(ledger, monkeypatch):
    sid = "s-b"
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(db, "debug_banner_enabled", lambda: True)
    monkeypatch.setattr(db, "debug_banner_level", lambda: 1)
    monkeypatch.setattr(registration.orchestration,
                        "on_transform_llm_output", lambda **kw: None)
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: None)
    monkeypatch.setattr(tr, "turn_no", lambda s: 7)
    state.stash_pending(sid, "m1", ASK, "rendered-stub")
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))
    state.record_last_seen(sid, ASK)
    # a lane fires: its segment is registered before the boundary
    assert tr.register(sid, _segment(611)["text"], lane="decision",
                       kind="anchor", edge="benign") is True

    delivered = plugin.on_transform_llm_output(
        response_text="PLAIN ANSWER BODY", session_id=sid, model="m1")
    decisions.flush_for_tests()

    assert tr.segment_count(sid) == 1
    rows = _banner_rows(ledger)
    assert len(rows) == 1, rows
    assert rows[0]["session_id"] == sid
    assert rows[0]["turn_id"] == "7"
    assert len(rows[0]["banner"]) == 1
    assert "row=611" in rows[0]["banner"][0]["text"]
    print("\n(E2E banner_record):", json.dumps(rows[0], sort_keys=True))


def test_c_two_record_turns_for_the_same_turn_id_leave_one_row(ledger):
    for _ in range(2):
        tr.record_turn("s-c", 7, "BODY", None, [_segment(611)])
    decisions.flush_for_tests()

    rows = [r for r in _banner_rows(ledger) if r.get("turn_id") == "7"]
    assert len(rows) == 1, rows
    assert rows[0]["dupes"] == 1
    assert rows[0]["body_chars"] == len("BODY")
    print("\n(dupe keyed row):", json.dumps(rows[0], sort_keys=True))


def test_d_a_different_turn_id_still_writes_its_own_row(ledger):
    tr.record_turn("s-d", 7, "BODY", None, [_segment(611)])
    tr.record_turn("s-d", 8, "BODY2", None, [_segment(612)])
    decisions.flush_for_tests()

    rows = _banner_rows(ledger)
    assert sorted(r["turn_id"] for r in rows) == ["7", "8"]
