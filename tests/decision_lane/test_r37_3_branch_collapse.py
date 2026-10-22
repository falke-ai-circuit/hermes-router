"""R37-3 — the decision lane must COLLAPSE a branch and be SILENT without a
verdict.

Three defects, all observed live on coder (v5.13.0/5.14.0):

  D1  lane text leaked into a delivered body: a probe turn began with the
      literal line "[END ROUTER FORK NOTE]" before the answer (session
      api_1792679326-era, ledger row 4065).
  D2  a noise-band verdict (confidence 0.42, band=noise by the lane's own
      constants) was still stamped as a verdict of record.
  D3  the midturn advisory was a MENU: every option with its pull weight plus
      a why_not line restating the rejected alternatives.

Pins:
  (a) below-band confidence emits no advisory and no verdict line, and logs
      the suppression with the confidence and the band;
  (b) a non-noise advisory names exactly one route, imperatively, and the
      rejected option labels never reach the delivered text;
  (c) no fork-note/advisory marker text survives the delivery boundary;
  (d) the ledger still records the full option set for a suppressed fork
      (suppression is a DELIVERY decision, never a detection one).
"""
import json
import sqlite3

import pytest

from hermes_router import decision, decision_midturn as dmt, echo_guard

SID = "s-r37-3"

TOOL_FORK = ("harness verdict — which one should we pick?\n"
             "a) redis cache\nb) memcached cache\nc) sqlite store — weigh the "
             "tradeoffs")

OPTIONS = ["redis cache", "memcached cache", "sqlite store — weigh the tradeoffs"]


def _cfg(**over):
    cfg = dict(decision.DEFAULTS)
    cfg.update({"enabled": True, "midturn": "on", "model": "jev-latest"})
    cfg.update(over)
    return cfg


@pytest.fixture()
def env(monkeypatch, tmp_path):
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
    monkeypatch.setattr(dmt, "_cfg", _cfg)
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


def _backend(monkeypatch, confidence, choice="opt-1"):
    def _call(envelope, cfg):
        meta = {"model": "jev-latest", "endpoint": "e", "tokens_in": 10,
                "tokens_out": 5, "latency_s": 0.1}
        return (json.dumps({"choice": choice, "confidence": confidence,
                            "alternatives": ["opt-2"]}), meta, "ok")

    monkeypatch.setattr(decision, "call_backend", _call)


def _logs(monkeypatch):
    events = []

    def _rec(session_id, event, **fields):
        events.append((event, dict(fields)))

    monkeypatch.setattr(dmt, "_log", _rec)
    return events


# --- (a) below the band => silence, with the suppression logged -------------

def test_below_band_confidence_emits_nothing_and_logs_the_suppression(
        env, monkeypatch):
    _backend(monkeypatch, 0.42)
    events = _logs(monkeypatch)
    dmt._handle_hit(SID, "terminal", TOOL_FORK, OPTIONS, _cfg(), "on")

    assert dmt.flush(SID) == []          # no advisory text at all
    suppressed = [f for (e, f) in events
                  if e == "decision_suppressed" and f.get("reason") == "noise_band"]
    assert suppressed, events
    fields = suppressed[0]
    assert fields["confidence"] == 0.42
    assert fields["band"] == "noise"
    assert "ledger_id" in fields         # the row exists and is named


def test_below_band_verdict_line_is_empty(monkeypatch):
    """The envelope and the verdict-of-record line go silent together."""
    assert echo_guard is not None
    monkeypatch.setattr(
        "hermes_router.render_payload.ledger_row_by_id",
        lambda rid: {"id": rid, "choice": "opt-1", "confidence": 0.42})
    from hermes_router import render_payload as rp

    assert rp.render_verdict_record(4065) == ""
    assert rp.render_advisory({"choice": "opt-1", "confidence": 0.42},
                              {"options": [{"id": "opt-1", "label": "a route"},
                                           {"id": "opt-2", "label": "b route"}]}) == ""


# --- (b) one named route, no alternatives ----------------------------------

def test_non_noise_advisory_names_exactly_one_route(env, monkeypatch):
    _backend(monkeypatch, 0.91)
    dmt._handle_hit(SID, "terminal", TOOL_FORK, OPTIONS, _cfg(), "on")

    pending = dmt.flush(SID)
    assert len(pending) == 1
    text = pending[0]
    assert text.startswith(dmt.ADVISORY_HEADER)
    assert "redis cache" in text
    # the rejected routes are NOT enumerated anywhere in the delivered text
    assert "memcached" not in text
    assert "sqlite store" not in text
    assert "opt-2" not in text
    assert "pulls" not in text
    assert "why_not" not in text.lower()
    assert "could also" not in text.lower()


def test_selected_option_confidence_decides_the_gate_not_the_weight_band():
    """Gate is the CHOSEN option's confidence — the mechanical weighting band
    is untouched (weights math must not change under R37-3)."""
    env_ = {"options": [{"id": "opt-1", "label": "a route"},
                        {"id": "opt-2", "label": "b route"}],
            "weighting": {"weights": {"opt-1": 0.5, "opt-2": 0.5},
                          "band": "noise", "evidence": []}}
    reason, band, conf = decision.emission_gate(
        {"choice": "opt-1", "confidence": 0.91}, env_)
    assert reason == "" and band == "strong" and conf == 0.91


# --- (c) no lane marker survives delivery ----------------------------------

def test_lane_marker_variants_never_reach_a_delivered_body():
    bodies = (
        "[END ROUTER FORK NOTE]\nThe suite is green, 214 passed.",
        "[BEGIN ROUTER FORK NOTE]\nthe answer is 42",
        "[ROUTER ADVISORY — decision lane; banner at turn close; may ignore] "
        "take redis cache (advisory only — never a command).\nwork report",
        "text before [ROUTER FORK NOTE — decision lane] answer after",
    )
    for body in bodies:
        out, removed = echo_guard.strip_lane_markers(body)
        assert removed > 0, body
        assert "ROUTER FORK NOTE" not in out.upper()
        assert "ROUTER ADVISORY" not in out.upper()
        assert "END ROUTER" not in out.upper()
    # the answer itself is never re-ordered or lost
    out, _ = echo_guard.strip_lane_markers(bodies[0])
    assert "The suite is green, 214 passed." in out
    out, _ = echo_guard.strip_lane_markers(bodies[3])
    assert "text before" in out and "answer after" in out


def test_marker_family_is_visible_to_the_echo_guard():
    """The marker variants are DETECTABLE too, so an echo is never silent."""
    fams = echo_guard.detect_envelope_echo_families(
        "[END ROUTER FORK NOTE]\nbody")
    assert "advisory_header" in fams


# --- (d) suppressed fork keeps its full option set in the ledger -----------

def test_suppressed_fork_still_records_the_full_option_set(env, monkeypatch):
    _backend(monkeypatch, 0.42)
    events = _logs(monkeypatch)
    dmt._handle_hit(SID, "terminal", TOOL_FORK, OPTIONS, _cfg(), "on")

    rows = _rows(env)
    assert len(rows) == 1
    row = rows[0]
    assert row["confidence"] == 0.42
    assert row["choice"] == "opt-1"
    opts = json.loads(row["options_json"])
    assert [o["label"] for o in opts] == OPTIONS
    assert row["signatures_json"]
    assert json.loads(row["verdict_json"])["confidence_band"] == "noise"
    # suppression is delivery-side only: the row is real, not a placeholder
    suppressed = [f for (e, f) in events
                  if e == "decision_suppressed" and f.get("reason") == "noise_band"]
    assert suppressed and suppressed[0]["ledger_id"] == row["id"]


def test_identical_option_labels_are_not_a_separable_fork():
    same = {"options": [{"id": "opt-1", "label": "deploy it"},
                        {"id": "opt-2", "label": "deploy it"}]}
    reason, _, _ = decision.emission_gate(
        {"choice": "opt-1", "confidence": 0.95}, same)
    assert reason == decision.EMIT_REASON_NOT_SEPARABLE
