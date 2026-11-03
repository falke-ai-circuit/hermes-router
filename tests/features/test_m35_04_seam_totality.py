"""M35-04 — SEAM TOTALITY and the MARKER BOUNDARY.

THE DEFECTS (M35 battery v2, rows A2 and C1)
    Two of ten live turns produced NO provenance row at all. The row was written
    from the transform hook's `finally`; when the host SKIPPED that hook
    ("skipped after previous timeout or while still running") the turn vanished
    from the ledger, so nothing could say what happened to it. A2 additionally
    delivered a body whose position 0 was the router's own
    `<turn declined="false" />` marker — the strip lived in the same skipped
    pass, so internal plumbing shipped to the user.

WHAT IS PINNED HERE
    (a) every exit path from the seam writes a row — the table below drives each
        one and asserts the action it lands;
    (b) an early-bail/error turn lands `action="skipped"` with a NAMED reason;
    (c) a body carrying the marker is never delivered — well-formed tag lines
        are stripped at the boundary, a surviving fragment is WITHHELD with a
        loud error row;
    (d) a 10-turn fixture produces 10 rows and zero silent turns.

Instrumentation + boundary only: no routing decision is changed by this file.
"""
from __future__ import annotations

import json
import os
import sys
import time

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import hermes_router as plugin  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions, turn_provenance as tp  # noqa: E402
from hermes_router.features.detection import declaration  # noqa: E402
from hermes_router.gate import registration as reg  # noqa: E402
from hermes_router.gate import turn_close  # noqa: E402

MARKER = '<turn declined="false" />'
BENIGN = "Here is the summary you asked for, with the numbers you wanted."
REPLACED = "REPLACED-BODY-FROM-THE-LANE"

#: (a) THE EXIT-PATH TABLE — one row per row below.
#: Each entry names a path out of the seam and the action its row must carry.
EXIT_PATHS = (
    ("pass_through", "deliver"),          # orchestrator returns None
    ("replacement", "deliver"),           # orchestrator returns a lane body
    ("orchestrator_raises", "skipped"),   # an exception inside the seam
    ("post_seam_skipped", "skipped"),     # host never invoked the hook
    ("no_delivery_edge", "skipped"),      # nothing observed the turn at all
    ("superseded_by_next_turn", "skipped"),
)


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch, tmp_path):
    state.clear()
    decisions.clear_for_tests()
    tp.reset_pending_for_tests()
    ledger = tmp_path / "hermes-router-decisions.jsonl"
    monkeypatch.setattr(decisions, "_store_path", lambda: str(ledger))
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield ledger
    decisions.clear_for_tests()
    state.clear()
    tp.reset_pending_for_tests()


def _rows(ledger):
    decisions.flush_for_tests()
    if not os.path.exists(str(ledger)):
        return []
    out = []
    for line in open(str(ledger), encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _seam(monkeypatch, sid, body=BENIGN, orchestrator=None):
    """Drive the REAL registered wrapper with a stubbed orchestrator."""
    if orchestrator is None:
        def orchestrator(**kwargs):  # noqa: ANN001
            return None
    monkeypatch.setattr(reg.orchestration, "on_transform_llm_output",
                        orchestrator)
    return reg.on_transform_llm_output(response_text=body, session_id=sid,
                                       model="m1")


# ---------------------------------------------------------------------------
# (a) the exit-path table — a row on EVERY path
# ---------------------------------------------------------------------------

def _drive_path(monkeypatch, path, sid):
    if path == "pass_through":
        return _seam(monkeypatch, sid)
    if path == "replacement":
        return _seam(monkeypatch, sid,
                     orchestrator=lambda **kw: REPLACED)
    if path == "orchestrator_raises":
        def boom(**kwargs):  # noqa: ANN001
            raise RuntimeError("seam exploded")
        with pytest.raises(RuntimeError):
            _seam(monkeypatch, sid, orchestrator=boom)
        return None
    if path == "post_seam_skipped":
        # PRE armed the turn; the host never called the transform hook; the
        # finalizer's observer edge closed it.
        state.advance_turn_identity(sid, state.hash_text("ask"))
        tp.arm(sid, body_chars=len(BENIGN), model="m1")
        turn_close.on_post_llm_call(session_id=sid, assistant_response=BENIGN,
                                   turn_id="t")
        return None
    if path == "no_delivery_edge":
        state.advance_turn_identity(sid, state.hash_text("ask"))
        tp.arm(sid, body_chars=len(BENIGN), model="m1")
        written = tp.sweep_once(now=time.monotonic() + tp.SKIP_DEADLINE_S + 1.0)
        assert written == 1
        return None
    if path == "superseded_by_next_turn":
        state.advance_turn_identity(sid, state.hash_text("ask one"))
        tp.arm(sid, body_chars=10, model="m1")
        state.advance_turn_identity(sid, state.hash_text("ask two"))
        tp.arm(sid, body_chars=10, model="m1")
        return None
    raise AssertionError(f"unknown path {path}")


@pytest.mark.parametrize("path,expected_action", EXIT_PATHS)
def test_a_every_exit_path_writes_a_row(monkeypatch, _isolated_ledger,
                                        path, expected_action):
    sid = f"m35-04-exit-{path}"
    _drive_path(monkeypatch, path, sid)
    rows = [r for r in _rows(_isolated_ledger) if r.get("session_id") == sid]
    assert len(rows) == 1, f"path {path} wrote {len(rows)} rows, expected 1: {rows}"
    row = rows[0]
    assert row["action"] == expected_action, (path, row)
    assert row["session_id"] == sid
    assert row["schema"] == "decision-record"
    if expected_action == "skipped":
        # `outcome` names the failure the path saw (an error keeps `seam_error`);
        # `skip_reason` is the invariant: a skipped row always names its cause.
        assert row["outcome"] in ("seam_skipped", "seam_error"), (path, row)
        assert row["skip_reason"], f"path {path} skipped without a reason"
    print(f"\n(a) {path}: action={row['action']} reason={row.get('skip_reason')!r}")


# ---------------------------------------------------------------------------
# (b) the early-bail / error fixture -> skipped WITH A NAMED REASON
# ---------------------------------------------------------------------------

def test_b_error_turn_lands_skipped_with_a_named_reason(monkeypatch,
                                                        _isolated_ledger):
    sid = "m35-04-b-error"

    def boom(**kwargs):  # noqa: ANN001
        raise RuntimeError("early bail")

    with pytest.raises(RuntimeError):
        _seam(monkeypatch, sid, orchestrator=boom)

    row = _rows(_isolated_ledger)[0]
    assert row["action"] == "skipped", row
    # `outcome` names the failure the seam saw; `skip_reason` names why no
    # decision exists. Both present, both named — never silence.
    assert row["outcome"] == "seam_error", row
    assert row["skip_reason"] == "seam_error", row
    assert row["lane"] == "main" and row["replaced"] is False, row
    print("\n(b) early-bail row:", json.dumps(row, indent=None))


def test_b2_host_skip_lands_skipped_with_its_own_reason(monkeypatch,
                                                       _isolated_ledger):
    sid = "m35-04-b2-skip"
    state.advance_turn_identity(sid, state.hash_text("ask"))
    tp.arm(sid, body_chars=len(BENIGN), model="m1")
    # the host skipped transform_llm_output; only the close edge ran
    turn_close.on_post_llm_call(session_id=sid, assistant_response=BENIGN,
                               turn_id="t")
    row = _rows(_isolated_ledger)[0]
    assert row["action"] == "skipped", row
    assert row["skip_reason"] == tp.SKIP_REASON_POST_SEAM_NOT_REACHED, row
    # the delivered body was observed, so the row can carry its real size
    assert row["delivered_chars"] == len(BENIGN), row
    print("\n(b2) host-skip row:", json.dumps(row, indent=None))


# ---------------------------------------------------------------------------
# (c) THE MARKER BOUNDARY — stripped, or withheld loudly. Never delivered.
# ---------------------------------------------------------------------------

def test_c1_marker_on_a_path_that_never_reached_the_table_is_stripped(
        monkeypatch, _isolated_ledger):
    sid = "m35-04-c1"
    tagged = MARKER + "\n\n" + BENIGN

    # The orchestrator path that leaks in production is the one that does NOT
    # enter the routing table (router disabled / passthrough edge): it hands
    # the body back untouched. Model that verbatim.
    out = _seam(monkeypatch, sid, body=tagged,
                orchestrator=lambda **kw: tagged)
    assert "<turn" not in str(out), f"marker delivered: {out!r}"
    assert BENIGN in str(out), "the answer text must survive the strip"
    row = _rows(_isolated_ledger)[0]
    assert row["action"] != "withheld", row
    print("\n(c1) stripped body:", repr(str(out)[:60]))


def test_c2_a_surviving_marker_fragment_withholds_the_turn(monkeypatch,
                                                           _isolated_ledger):
    sid = "m35-04-c2"
    # Truncated by an output cap: not a well-formed tag line, so the strip
    # cannot remove it — but it is still the router's marker text.
    leaked = '<turn declined="false"\n\n' + BENIGN

    out = _seam(monkeypatch, sid, body=leaked,
                orchestrator=lambda **kw: leaked)
    assert "<turn" not in str(out), f"marker fragment delivered: {out!r}"
    assert str(out) == reg.WITHHELD_BODY
    row = _rows(_isolated_ledger)[0]
    assert row["action"] == "withheld", row
    assert row["outcome"] == "marker_leak_withheld", row
    assert row["skip_reason"] == "marker_survived_boundary_strip", row
    print("\n(c2) withheld body:", repr(str(out)[:60]))
    print("(c2) withheld row:", json.dumps(row, indent=None))


def test_c3_the_boundary_preserves_pass_through_none(monkeypatch,
                                                     _isolated_ledger):
    sid = "m35-04-c3"
    out = _seam(monkeypatch, sid)  # orchestrator returns None
    assert out is None, ("pass-through must stay None (deliver the host body); "
                         f"got {out!r}")
    assert _rows(_isolated_ledger)[0]["delivered"] is True


# ---------------------------------------------------------------------------
# (d) 10 turns -> 10 rows, zero silent turns
# ---------------------------------------------------------------------------

def test_d_ten_turns_produce_ten_rows(monkeypatch, _isolated_ledger):
    sids = []
    for i in range(10):
        sid = f"m35-04-d-{i}"
        sids.append(sid)
        state.advance_turn_identity(sid, state.hash_text(f"ask {i}"))
        if i < 7:                       # ordinary deliveries
            _seam(monkeypatch, sid)
        elif i == 7:                    # an error
            def boom(**kwargs):  # noqa: ANN001
                raise RuntimeError("boom")
            with pytest.raises(RuntimeError):
                _seam(monkeypatch, sid, orchestrator=boom)
        elif i == 8:                    # host skipped the transform hook
            tp.arm(sid, body_chars=len(BENIGN), model="m1")
            turn_close.on_post_llm_call(session_id=sid,
                                       assistant_response=BENIGN, turn_id="t")
        else:                           # nothing observed the turn
            tp.arm(sid, body_chars=len(BENIGN), model="m1")
            tp.sweep_once(now=time.monotonic() + tp.SKIP_DEADLINE_S + 1.0)

    rows = _rows(_isolated_ledger)
    covered = {r.get("session_id") for r in rows}
    silent = [s for s in sids if s not in covered]
    assert len(rows) == 10, f"{len(rows)} rows for 10 turns: {rows}"
    assert silent == [], f"silent turns: {silent}"
    assert len(covered) == 10
    kinds = sorted(r["action"] for r in rows)
    print(f"\n(d) 10 turns -> {len(rows)} rows, actions={kinds}, silent=0")
