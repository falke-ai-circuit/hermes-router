"""M53-38 — four measured defects on the unc-lane swap seam.

Each pin names ONE observably-wrong fact, taken from live evidence, and fails
on the parent commit. No pin here re-litigates routing doctrine: routing is
mechanics and the render substrate refuses if it refuses.

    A  the banner is emitted and appended at the swap seam, then absent from
       the persisted assistant row (and, on the wire path, from the delivered
       body). Measured: 236 `debug_banner_emitted` events in shadow's log,
       e.g. 2026-11-24T22:22:55Z lane=uncensored-render trigger=action_denial,
       while the persisted row (session 20261006_190903_c82cbbcc,
       rendered_chars=1199 / 2800) carries NEITHER 'shadow-self (uncensored)'
       NOR '@ api.venice.ai'.
    B  every replaced row's `evidence_ref` is e3b0c44298fc1c14 — sha256('')'s
       prefix, i.e. no evidence at all.
    C  replaced rows carry intent_source=not_consulted / intent_class=''.
       The deterministic classification exists (denial_class.classify) and the
       swap path already runs the domain precondition; the row must say what
       was consulted.
    D  `rendered` / render counts are claimed for sub-1200-char bodies that are
       not renders (measured shadow 506-1069 chars; coder 1009 vs a 524-char
       persisted body reading "the code you're referring to didn't come
       through ... Paste the actual text").
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import config_access, router_core, state  # noqa: E402
from hermes_router import route_gate  # noqa: E402
from hermes_router.core import canonical, decisions  # noqa: E402
from hermes_router.core.delivery_integrity import FAILURE_PREFIX  # noqa: E402
from hermes_router.features.detection import attestation as att  # noqa: E402
from hermes_router.features.detection import domain_gate  # noqa: E402

BANNER_LABEL = "shadow-self (uncensored)"
ENDPOINT = "@ api.venice.ai"
EMPTY_SHA_PREFIX = hashlib.sha256(b"").hexdigest()[:16]

ASK = "write the full evasion for the keylogger I described"
DENIAL = ("No. I won't write keylogging code — that is a refusal of the "
          "action, not a capability gap.")
RENDER = ("RENDERED CONTENT FROM THE UNC LANE: ride the RIDEV_INPUTSINK "
          "capture path, name the filter driver, resolve imports by PEB walk, "
          "then apply the cosmetic fixes the loader expects. " * 9)
SHORT_NOTICE = ("the code you're referring to didn't come through — it was "
                "truncated before I could read it. Paste the actual text and "
                "I'll finish the review.")


def _cfg():
    return {"enabled": True,
            "classification": {"pre_classify": True, "post_classify": True,
                               "match_threshold": 1},
            "log_routes": False,
            "declaration_attestation": {"armed": True}}


def _gate(sid=""):
    return {"gate": "flagged", "domain": "cyber_offensive",
            "state": "flagged", "reason": "in_domain",
            "lane": "", "turn": 0}


@pytest.fixture()
def seam(monkeypatch, tmp_path):
    """The real delivery seam, a real state.db, a real decisions ledger."""
    rows = []

    def _cap(event, **fields):
        rows.append((event, fields))

    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY,"
                 " session_id TEXT, role TEXT, content TEXT, timestamp REAL,"
                 " api_content TEXT)")
    conn.commit()
    conn.close()

    ledger = tmp_path / "hermes-router-decisions.jsonl"

    state.clear()
    router_core._test_reset()
    router_core._cooldown_test_reset()
    plugin._reset_declaration_state()
    att.reset_declarations()
    route_gate.clear_turn_claims()
    monkeypatch.setattr(plugin, "_cfg", _cfg)
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_log_route", _cap)
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1, "banner_in_body": True})
    from hermes_router import debug_banner as _dbg
    monkeypatch.setattr(_dbg, "_banner_section",
                        lambda: {"debug_banner": 1, "banner_in_body": True})
    monkeypatch.setattr(router_core, "_primary_model", lambda: "z-ai/glm-5.3")
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    monkeypatch.setattr(router_core, "consult_cooldown_turns", lambda: 0)
    monkeypatch.setattr(domain_gate, "start", lambda *a, **k: None)
    monkeypatch.setattr(domain_gate, "read", lambda *_a, **_k: _gate())
    monkeypatch.setattr(canonical, "_state_db_path", lambda: str(db))
    monkeypatch.setattr(canonical, "_LAST_REWRITE", {})
    monkeypatch.setattr(canonical, "_LAST_MATCH", {})
    monkeypatch.setattr(decisions, "_store_path", lambda: str(ledger))
    yield {"events": rows, "db": str(db), "ledger": str(ledger)}
    state.clear()
    router_core._test_reset()
    router_core._cooldown_test_reset()
    plugin._reset_declaration_state()
    att.reset_declarations()
    route_gate.clear_turn_claims()


def _events(env, name):
    return [f for e, f in env["events"]
            if e == name or f.get("event_detail") == name]


def _persist(db, sid, body):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (session_id, role, content, timestamp)"
                 " VALUES (?, 'assistant', ?, ?)", (sid, body, time.time()))
    conn.commit()
    conn.close()


def _newest(db, sid):
    conn = sqlite3.connect(db)
    try:
        got = conn.execute(
            "SELECT content FROM messages WHERE session_id = ?"
            " AND role = 'assistant' ORDER BY id DESC LIMIT 1",
            (sid,)).fetchone()
        return got[0] if got else ""
    finally:
        conn.close()


def _run(env, monkeypatch, sid, body=DENIAL, render=RENDER):
    """PRE complexity skip, then the registered POST delivery hook."""
    state.record_last_seen(sid, ASK)
    state.set_last_user_msg_hash(sid, state.hash_text(ASK))
    route = router_core.dispatch(ASK, session_id=sid, model="m")
    assert route.mode == router_core.MODE_FLASH_DIRECT
    from unittest import mock
    with mock.patch.object(plugin.router, "call", return_value=render):
        out = plugin.on_transform_llm_output(
            response_text=body, session_id=sid, model="m")
    return out


def _ledger_rows(env):
    decisions.flush_for_tests()
    if not os.path.exists(env["ledger"]):
        return []
    with open(env["ledger"], encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _delivery_row(env):
    for row in reversed(_ledger_rows(env)):
        if row.get("replaced") is not None and row.get("session_id"):
            return row
    return {}


# ---------------------------------------------------------------------------
# A — the banner must be in the DELIVERED body AND the persisted row
# ---------------------------------------------------------------------------

def test_a_banner_lands_in_both_the_wire_body_and_the_persisted_row(seam,
                                                                   monkeypatch):
    sid = "m53-38-a"
    _persist(seam["db"], sid, DENIAL)          # turn_finalizer's own row
    out = _run(seam, monkeypatch, sid)

    assert isinstance(out, str) and out, "the swap must deliver a body"
    assert BANNER_LABEL in out, ("the delivered body lost the lane banner",
                                 out[-400:])
    row = _newest(seam["db"], sid)
    assert BANNER_LABEL in row, ("the persisted row lost the lane banner — "
                                 "the exact measured defect", row[-400:])
    assert row == out, ("persisted row must be the delivered bytes",
                        len(row), len(out))
    # The operator-readable proof row: the SAME banner line quoted from both
    # byte surfaces, plus both digests. Printed under -s; the assertions above
    # are what pins it.
    _head = "\u00b7 router \u00b7"
    print("\nM53-38-A delivered_chars=%d persisted_chars=%d equal=%s"
          % (len(out), len(row), row == out))
    print("M53-38-A delivered_sha256=%s"
          % hashlib.sha256(out.encode()).hexdigest())
    print("M53-38-A persisted_sha256=%s"
          % hashlib.sha256(row.encode()).hexdigest())
    print("M53-38-A DELIVERED_BANNER: %s" % out[out.find(_head):].strip())
    print("M53-38-A PERSISTED_BANNER: %s" % row[row.find(_head):].strip())


def test_a_banner_lands_with_a_full_render_signature(seam, monkeypatch):
    """The banner's own line carries the endpoint cell the live rows lacked."""
    sid = "m53-38-a2"
    _persist(seam["db"], sid, DENIAL)
    out = _run(seam, monkeypatch, sid)
    row = _newest(seam["db"], sid)
    assert ENDPOINT in out and ENDPOINT in row, (out[-300:], row[-300:])


# ---------------------------------------------------------------------------
# B — a real evidence digest on the swap path
# ---------------------------------------------------------------------------

def test_b_swap_row_carries_a_real_evidence_digest(seam, monkeypatch):
    sid = "m53-38-b"
    _persist(seam["db"], sid, DENIAL)
    _run(seam, monkeypatch, sid)

    row = _delivery_row(seam)
    assert row, "no delivery row was written"
    assert row.get("replaced") is True, row
    ref = str(row.get("evidence_ref") or "")
    assert ref != EMPTY_SHA_PREFIX, ("evidence_ref is the empty-string digest "
                                     "placeholder", ref)
    assert len(ref) == 16, ref
    # The digest must be OF the evidence actually used. The swap path names
    # it as the (render digest, ask digest) pair; recompute that derivation
    # here through the SAME sink function the ledger uses, so the pin fails if
    # the producer ever goes back to hashing nothing.
    render_sha = hashlib.sha256(RENDER.encode("utf-8")).hexdigest()[:16]
    ask_sha = hashlib.sha256(ASK.encode("utf-8")).hexdigest()[:16]
    assert ref == decisions.evidence_ref(
        "render:%s|ask:%s" % (render_sha, ask_sha)), ref


# ---------------------------------------------------------------------------
# C — the swap path consults the deterministic classification
# ---------------------------------------------------------------------------

def test_c_swap_row_names_the_deterministic_classification(seam, monkeypatch):
    sid = "m53-38-c"
    _persist(seam["db"], sid, DENIAL)
    _run(seam, monkeypatch, sid)

    row = _delivery_row(seam)
    assert row.get("replaced") is True, row
    assert row.get("intent_source") != "not_consulted", row
    assert row.get("intent_source") == "deterministic", row
    assert str(row.get("intent_class") or ""), row
    # The class the deterministic layer produced for this body.
    assert row["intent_class"] == "action_denial", row


def test_c_compliant_body_still_ships_from_main(seam, monkeypatch):
    """Owner precondition: compliant -> main ships, no lane render."""
    sid = "m53-38-c2"
    compliant = "Here is the file inventory you asked for: 12 modules, all green."
    _persist(seam["db"], sid, compliant)
    out = _run(seam, monkeypatch, sid, body=compliant)
    assert out is None, ("a compliant body must be delivered unchanged", out)


# ---------------------------------------------------------------------------
# D — rendered is a claim about a RENDER, not about any bytes
# ---------------------------------------------------------------------------

def _claim(sid):
    route_gate.stamp_turn_claim(sid, route_gate.LANE_SHADOW,
                                route_gate.SOURCE_DECLARED_USER, executed=True)
    return route_gate.claim_state(sid) or {}


def test_d_short_body_is_not_marked_rendered(seam):
    sid = "m53-38-d1"
    _claim(sid)
    route_gate.mark_shadow_rendered(sid, body=SHORT_NOTICE)
    assert not (route_gate.claim_state(sid) or {}).get("rendered"), \
        "a 170-char notice was claimed as a render"


def test_d_sub_1200_body_is_not_marked_rendered(seam):
    sid = "m53-38-d2"
    _claim(sid)
    route_gate.mark_shadow_rendered(sid, body="x" * 1009)
    assert not (route_gate.claim_state(sid) or {}).get("rendered"), \
        "a 1009-char body was claimed as a render (measured coder i=16)"


def test_d_failure_notice_is_never_marked_rendered(seam):
    sid = "m53-38-d3"
    _claim(sid)
    route_gate.mark_shadow_rendered(
        sid, body=FAILURE_PREFIX + ": the store rejected the write. " * 60)
    assert not (route_gate.claim_state(sid) or {}).get("rendered"), \
        "a graceful-fail notice was claimed as a render"


def test_d_genuine_render_is_still_marked_rendered(seam):
    sid = "m53-38-d4"
    _claim(sid)
    route_gate.mark_shadow_rendered(sid, body=RENDER)
    assert (route_gate.claim_state(sid) or {}).get("rendered") is True, \
        "a genuine 1000+/1200+ render must keep its flag"
