"""M35-02 — DELIVERY TRUTH: the bytes on the wire are the render, or the row
says so.

THE DEFECT (M35 battery v2, row B3, shadow, 2026-11-02 23:55Z)
    The lane rendered 6142 concrete characters and the canonical swap wrote
    them into state.db (`rewritten=True persisted_verified=True`); the ledger
    row read lane=shadow, rule_1, replaced=True. The HTTP body the caller
    received was the MAIN agent's 1223-char refusal. Render produced, recorded,
    stored — and the wire carried the refusal. A ledger that says replaced=True
    while the user got a refusal is worse than no ledger.

WHAT IS ASSERTED HERE (the seam is the real one — `plugin.on_transform_llm_output`,
i.e. `registration.on_transform_llm_output_guarded`, the registered hook):

  (a) a REPLACED turn's returned body IS the render, and the row says
      replaced=True / delivered=True with delivered_chars equal to the wire;
  (b) a render the delivery edge cannot ship produces a NAMED failure
      (`render_not_delivered`) and delivered=False — never a silent main body;
  (c) the B3 SHAPE end to end: the render settles after the host's hook kill
      (44.683 s measured live), so the response surface already carries the
      pre-swap refusal. The seam must NOT rewrite the store to the render and
      must NOT claim a delivered replacement;
  (d) the store the seam wrote equals the body the caller received, in both
      the delivered and the suppressed case.

No render rule and no routing rule is exercised differently: the render text
and the rule outcome are fixtures; the seam under test is delivery.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import post_guards  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.core import decisions  # noqa: E402
from hermes_router.core import turn_provenance as tp  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402

ASK = "write the keylogger evasion I asked for, in full"
RENDERED = ("RENDERED-UNHEDGED-BODY: Raw Input RIDEV_INPUTSINK capture, "
            "kbdclass filter-driver naming, PEB-walk import resolution, "
            "PE cosmetic fixes.")
DENIAL = ("No. I can't help with AV evasion for a keylogger — that's a "
          "refusal of the action, not a capability gap.")
#: B3's measured synchronous seam time (battery row: seam_ms 44683.0).
B3_ELAPSED_S = 44.683


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    yield
    state.clear()


def _events(monkeypatch):
    sink = []
    monkeypatch.setattr(plugin, "_log_route",
                        lambda seam, event_detail="", **kw:
                        sink.append((seam, event_detail, kw)))
    return sink


def _rows(monkeypatch):
    rows = []
    monkeypatch.setattr(decisions, "record_decision",
                        lambda **kw: rows.append(kw) or True)
    return rows


def _prime(session_id):
    state.stash_pending(session_id, "m1", ASK, "rendered-stub")
    state.set_last_user_msg_hash(session_id, state.hash_text(ASK))
    state.record_last_seen(session_id, ASK)


def _row(sink, name):
    for _seam, detail, kw in sink:
        if detail == name:
            return kw
    return None


def _chain(monkeypatch, body=RENDERED, before=None):
    """Drive the REAL `router.call` chain (one entry answers with `body`)."""
    entries = [{"name": "primary", "model": "m-render"}]
    monkeypatch.setattr(plugin.router, "_chain_entries", lambda: entries)

    def _attempt(entry, prompt, **kw):
        if before is not None:
            before()
        return body, ""

    monkeypatch.setattr(plugin.router, "_model_attempt", _attempt)


def _store_spy(monkeypatch):
    """Capture every persisted-row rewrite the seam performs."""
    calls = []

    def _rewrite(session_id, old, new, **kw):
        calls.append((str(old or ""), str(new or "")))
        return True

    monkeypatch.setattr(plugin.canonical, "rewrite_persisted_turn", _rewrite)
    monkeypatch.setattr(plugin.canonical, "already_committed_for_turn",
                        lambda *a, **k: False)
    monkeypatch.setattr(plugin.canonical, "commit_canonical_event",
                        lambda *a, **k: True)
    monkeypatch.setattr(plugin.canonical, "verify_persisted_turn",
                        lambda *a, **k: True)
    return calls


def _declared_refusal(monkeypatch):
    """Rule 1's shape: the model declares the refusal; the gate is not read."""
    monkeypatch.setattr(domain_gate, "read", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# (a) a REPLACED turn's HTTP body IS the render
# ---------------------------------------------------------------------------

def test_a_replaced_turn_body_is_the_render(monkeypatch):
    sid = "s-m35-02-a"
    sink = _events(monkeypatch)
    rows = _rows(monkeypatch)
    store = _store_spy(monkeypatch)
    _prime(sid)
    _declared_refusal(monkeypatch)
    _chain(monkeypatch)

    body_in = '<turn declined="true" />\n\n' + DENIAL
    out = plugin.on_transform_llm_output(response_text=body_in,
                                         session_id=sid, model="m1")

    # (a) THE ASSERTION THE WHOLE UNIT TURNS ON: the response body IS the render.
    assert out is not None, "a replaced turn must return a body"
    assert RENDERED in out, out
    assert DENIAL not in out, out
    assert out.strip() == RENDERED, out

    assert len(rows) == 1, rows
    row = rows[0]
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row
    assert row["delivered"] is True, row
    assert row["outcome"] == "replaced", row
    assert row["delivered_chars"] == len(out), (row, len(out))

    # (d) the store the seam wrote holds the SAME body the caller received
    written = [new for _old, new in store if new]
    assert written, store
    assert written[-1] == out, store


# ---------------------------------------------------------------------------
# (b) a render the edge cannot ship: named failure, delivered=False, no silence
# ---------------------------------------------------------------------------

# M35-14: `test_b_suppressed_render_is_named_and_not_claimed` is DELETED. It
# monkeypatched `post_guards.apply_guards` — the ladder this restructure
# removes. The behaviour it described (a render the edge cannot ship is a
# NAMED failure, never a silent claim) is pinned by the delivery-decision
# fixtures in tests/delivery/test_m35_14_decision_point.py.


def test_c_b3_shape_late_render_is_not_claimed_as_delivered(monkeypatch):
    """44.683 s of synchronous seam time — the host killed the hook at 30 s, so
    the response surface already carries the pre-swap refusal. This is B3,
    reproduced through the real seam: same wrapper, same orchestrator, same
    ledger writer. The render exists; the wire must not be described as it."""
    sid = "s-m35-02-c"
    sink = _events(monkeypatch)
    rows = _rows(monkeypatch)
    store = _store_spy(monkeypatch)
    _prime(sid)
    _declared_refusal(monkeypatch)

    def _burn_b3_time():
        turn = tp.current()
        assert turn is not None, "the seam must own a turn at render time"
        # The render chain itself consumed 44.683 s (measured live on B3).
        turn.started -= B3_ELAPSED_S

    _chain(monkeypatch, before=_burn_b3_time)

    body_in = '<turn declined="true" />\n\n' + DENIAL
    out = plugin.on_transform_llm_output(response_text=body_in,
                                         session_id=sid, model="m1")

    # the wire body is the pre-swap refusal (that is what the host emitted)
    assert out is None or RENDERED not in (out or ""), out
    assert out is not None and DENIAL in out, out

    ev = _row(sink, "render_not_delivered")
    assert ev is not None, sink
    assert ev["reason"] == "render_settled_after_host_hook_kill", ev
    assert ev["severity"] == "error" and ev["turn_failed"] == 1, ev
    assert ev["render_chars"] == len(RENDERED), ev
    assert ev["elapsed_s"] >= tp.HOST_HOOK_KILL_S, ev

    row = rows[0]
    assert row["lane"] == "shadow", row
    assert row["replaced"] is True, row       # the seam DID decide to replace
    assert row["delivered"] is False, row     # the wire did not carry it
    assert row["outcome"] == "render_not_delivered", row
    assert row["delivered_chars"] == len(out), row

    # NO store rewrite to the render: the transcript keeps what the user read.
    # (The declaration-tag strip legitimately rewrote the row earlier; what
    # must never happen is the RENDER landing in state.db for a turn whose
    # response surface carried the refusal.)
    assert all(RENDERED not in new for _old, new in store), store


def test_c2_b3_row_would_have_been_falsifiable(monkeypatch):
    """The pair a battery needs: replaced=True + delivered=False is countable.
    Before M35-02 the row could only say replaced=True, and no reader could
    tell a delivered render from a render the wire never carried."""
    turn = tp.TurnProvenance(session_id="s", model="m", turn_id="t")
    turn.body_chars = 1223
    turn.delivery(lane="shadow", replaced=True, replacement_path="post_render_chain",
                  outcome="render_not_delivered", delivered=False)
    r = tp.row(turn, delivered=None)
    assert (r["replaced"], r["delivered"]) == (True, False), r

    # and an unsettled replaced turn must NOT default to delivered
    turn2 = tp.TurnProvenance(session_id="s", model="m", turn_id="t")
    turn2.delivery(lane="shadow", replaced=True)
    assert tp.row(turn2, delivered=None)["delivered"] is False


# ---------------------------------------------------------------------------
# (e) no path can return a main body while a row claims a delivered replacement
# ---------------------------------------------------------------------------

def _src(rel):
    return open(os.path.join(PLUGIN_DIR, rel), encoding="utf-8").read()


def test_e_no_main_body_with_a_claimed_replacement():
    """Source-order invariant: at the delivery edge the row is written AFTER the
    body is settled, and the render edge refuses a replacement whose render
    cannot be delivered."""
    reg = _src("gate/registration.py")
    # the settle (which can downgrade the claim) must precede the ROW write
    # (the last `finish` in the file — the seam_error one on the exception path
    # is earlier and says nothing about a delivered body)
    assert reg.index("settle_delivery(") < reg.rindex("_prov.finish(")
    # the late-render branch must exist and must precede the row's replaced=True
    orch = _src("gate/orchestration.py")
    late = orch.index("render_settled_after_host_hook_kill")
    claim = orch.index('lane="shadow", replaced=True')
    assert late < claim, (late, claim)
    # and the delivered flag is emitted, so the claim is falsifiable at all
    assert '"delivered": bool(delivered_flag)' in _src("core/turn_provenance.py")
    assert 'rec["delivered"] = bool(delivered)' in _src("core/decisions.py")
