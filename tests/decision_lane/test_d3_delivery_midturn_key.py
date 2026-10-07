"""D3-DELIVERY FIX — midturn fork-consult park->deliver round-trip.

Live-probed root cause (conductor, 2026-10-02): the platform hands the
transform_terminal_output seam a 'session:'-prefixed session id
('session:api_...') while the llm_execution (flush_and_scan) and POST
(close_turn) edges use the bare sid. _sanitize_session_key passed the
prefixed form through verbatim, so SEAM-1 consults staged pending
advisories and consumed records under a key the delivery edges never
read — the parked advisory AND the aggregate banner never delivered
(5 live fork probes, 0 deliveries; one ledger row carried the leaked
'session:' prefix).

Pins: prefix normalization, the park->deliver round-trip for a
'session:'-prefixed SEAM-1 consult (initiator=agent path), ledger row
written under the bare sid, and the aggregate banner delivered via
close_turn on the same key.
"""
import json
import sqlite3

import pytest

from hermes_router import decision, decision_midturn as dmt
from hermes_router import on_transform_terminal_output

PREFIXED_SID = "session:api_1790938156_a166e833"
BARE_SID = "api_1790938156_a166e833"

TOOL_FORK = ("harness verdict — which one should we pick?\n"
             "a) redis\nb) memcached\nc) sqlite — weigh the tradeoffs")


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


def _verdict_body(conf=0.8):
    return {"choice": "opt-2", "confidence": conf,
            "alternatives": ["opt-1"]}


def _backend(monkeypatch, conf=0.8):
    def _b(envelope, cfg):
        meta = {"model": str(cfg.get("model") or "m"),
                "endpoint": "e", "tokens_in": 10, "tokens_out": 5,
                "latency_s": 0.1}
        return (json.dumps(_verdict_body(conf)), meta, "ok")
    monkeypatch.setattr(decision, "call_backend", _b)


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM decision_ledger ORDER BY id")]
    conn.close()
    return rows


def test_sanitize_strips_session_prefix():
    assert dmt._sanitize_session_key(PREFIXED_SID) == BARE_SID
    assert dmt._sanitize_session_key("sessions:" + BARE_SID) == BARE_SID
    assert dmt._sanitize_session_key(BARE_SID) == BARE_SID
    # the bare sid itself may legitimately contain ':' — only the
    # LEADING 'session:'/'sessions:' prefix is stripped
    assert dmt._sanitize_session_key("sess:abc") == "sess:abc"


def test_prefixed_seam1_consult_roundtrip_flushes(_env, monkeypatch):
    """Park->deliver round-trip: a SEAM-1 (terminal) fork consult staged
    under the prefixed key is delivered by flush_and_scan under the BARE
    sid the platform uses at the llm_execution edge."""
    _backend(monkeypatch)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setattr(dmt, "_mode", lambda cfg: "on")
    on_transform_terminal_output(command="ls", output=TOOL_FORK,
                                 session_id=PREFIXED_SID)
    st = dmt._RUNS.get(BARE_SID) or {}
    assert st.get("pending"), "pending advisory must stage under bare sid"
    assert PREFIXED_SID not in dmt._RUNS, \
        "prefixed key must not fork session state"
    req = {"messages": []}
    out = dmt.flush_and_scan(BARE_SID, req)
    assert out, "parked advisory must flush on the bare-sid edge"
    assert any("decision" in str(a).lower() for a in out)
    # one-shot: second flush returns nothing
    assert dmt.flush(BARE_SID) == []


def test_prefixed_seam1_ledger_row_bare_sid(_env, monkeypatch):
    """The ledger row records the BARE session_id — the 'session:' prefix
    never leaks into the ledger again (probe anomaly pinned)."""
    _backend(monkeypatch)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setattr(dmt, "_mode", lambda cfg: "on")
    on_transform_terminal_output(command="ls", output=TOOL_FORK,
                                 session_id=PREFIXED_SID)
    rows = _rows(_env)
    assert rows, "verdict row must exist"
    assert all(r["session_id"] == BARE_SID for r in rows), rows


def test_prefixed_seam1_consult_close_turn_delivers_banner(
        _env, monkeypatch):
    """The aggregate turn-close banner for a SEAM-1 consult must be
    reachable via close_turn under the BARE sid (this is what the POST
    edge parks for delivery)."""
    _backend(monkeypatch, conf=0.91)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    monkeypatch.setattr(dmt, "_cfg", lambda: cfg)
    monkeypatch.setattr(dmt, "_mode", lambda cfg: "on")
    on_transform_terminal_output(command="ls", output=TOOL_FORK,
                                 session_id=PREFIXED_SID)
    dmt.flush(BARE_SID)  # drain the pending advisory
    banner = dmt.close_turn(BARE_SID)
    assert banner, "aggregate banner must render for the bare-sid close"
    assert "decision" in banner.lower()
    assert dmt.close_turn(BARE_SID) == ""  # one-shot drain
