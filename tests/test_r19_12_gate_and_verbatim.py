"""R19.12 — two surgical decision-lane fixes (reviewer battery evidence).

FIX 1 — on-demand manual trigger: options-verbatim passthrough. The
trusted manual 'decide this:' ask must not fail-closed on parser
limitations: inline A)/B) options the strict line-marker regex misses are
passed through VERBATIM (ids opt-1..n by order of appearance); a binary
fork is derived ONLY from an explicit either/or connective; no structure
+ no connective keeps the no_options suppression. Never-invent holds.
FIX 2 — POST pseudo-fire gate: the POST leg fires ONLY on >= 2 DISTINCT
named options WITH consequence markers (numbered / lettered / explicit
Option N / Approach N labels, each with >= 20 chars of consequence text).
Ordinary delivery turns = no POST scan (reason=post_gate_insufficient_structure).
PRE / midturn / on-demand legs unchanged.
"""
import json
import sqlite3
import sys
import os

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import decision, debug_banner, state  # noqa: E402


@pytest.fixture()
def _env(monkeypatch, tmp_path):
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
    yield path
    decision.reset_limits()
    decision.reset_v3_limits()
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._HELD_DECISIONS.clear()


# ------------------------------------------------------------------
# FIX 1 — manual verbatim passthrough
# ------------------------------------------------------------------

ASK_INLINE = (
    "decide this: we can do A) batch-append all rows in one shot, faster "
    "but riskier on partial failure, or B) synchronous per-row appends, "
    "slower but each insert is independently reversible. which do you pick?"
)


def test_manual_inline_options_verbatim_passthrough(_env, monkeypatch):
    """Manual ask with A)/B) inline options the strict regex misses ->
    verbatim passthrough engages, backend receives BOTH options, no
    no_options suppression."""
    assert decision.extract_options(ASK_INLINE) == []  # strict regex misses
    calls = []

    def fake_backend(envelope, cfg):
        calls.append(envelope)
        content = json.dumps({"choice": "opt-1", "confidence": 0.9,
                              "alternatives": []})
        meta = {"model": "m", "endpoint": "e", "tokens_in": 10,
                "tokens_out": 5, "latency_s": 0.1}
        return content, meta, "ok"

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    monkeypatch.setattr(decision, "render_prompt", lambda env: "P")
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg["on_demand"] = {"manual": True, "midturn": True}
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    # drive _invoke directly (trigger=manual, the suppression site)
    decision._invoke("s-man", "t-man", ASK_INLINE, "manual", cfg,
                     lambda e, **f: None, initiator="user")
    assert len(calls) == 1  # NOT suppressed with no_options
    opts = [o.get("label") or "" for o in calls[0].get("options", [])]
    assert len(opts) == 2
    assert any("batch-append" in o for o in opts)
    assert any("synchronous" in o for o in opts)


def test_manual_no_structure_still_suppressed(_env, monkeypatch):
    """Manual ask with no enumerable structure and no either/or -> the
    no_options suppression keeps holding (never-invent)."""
    rows = []

    def fake_backend(envelope, cfg):  # must never be reached
        raise AssertionError("backend must not fire")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    monkeypatch.setattr(decision, "ledger_write",
                        lambda row, db_path="": rows.append(row) or 1)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg["on_demand"] = {"manual": True, "midturn": True}
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    ask = ("decide this: I trust your judgment on the migration approach "
           "entirely, pick whatever you think is best here.")
    assert decision._manual_verbatim_options(ask) == []
    decision._invoke("s-man2", "t-man2", ask, "manual", cfg,
                     lambda e, **f: None, initiator="user")
    assert rows and rows[0]["fail_open_reason"] == "no_options"


def test_manual_either_or_binary_fork(_env, monkeypatch):
    """Manual ask with an explicit either/or connective -> 2-option fork."""
    ask = ("decide this: either we ship it tonight or we wait for the "
           "next window.")
    opts = decision._manual_verbatim_options(ask)
    assert len(opts) == 2
    assert any("ship it tonight" in o for o in opts)
    assert any("wait for the next window" in o for o in opts)


def test_verbatim_never_invents(_env):
    """No synthesis: a single inline option must not become two."""
    ask = ("decide this: A) just do the batch append and move on, that is "
           "my whole ask.")
    opts = decision._manual_verbatim_options(ask)
    assert len(opts) <= 1  # no invented second option


# ------------------------------------------------------------------
# FIX 2 — POST pseudo-fire gate
# ------------------------------------------------------------------

POST_GOOD = ("Recommendation:\n"
             "1. Batch-append everything tonight, because the window is "
             "free and the rollback script is rehearsed.\n"
             "2. Synchronous per-row appends, since each insert stays "
             "independently reversible at a small cost.\n")
POST_ONE_NAMED = ("Going with the batch-append approach.\n"
                  "Approach 1: batch-append everything tonight.\n")
POST_BARE_BULLETS = ("Two options:\n"
                     "- batch append\n"
                     "- synchronous appends\n")


def test_post_gate_two_options_with_consequences(_env, monkeypatch):
    calls = []

    def fake_backend(envelope, cfg):
        calls.append(envelope)
        return (json.dumps({"choice": "opt-1", "confidence": 0.5,
                            "alternatives": []}),
                {"model": "m", "endpoint": "e", "tokens_in": 1,
                 "tokens_out": 1, "latency_s": 0.0}, "ok")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    monkeypatch.setattr(decision, "render_prompt", lambda env: "P")
    monkeypatch.setattr(decision, "_WORKER_SEM", type("S", (), {
        "acquire": staticmethod(lambda blocking=False: True),
        "release": staticmethod(lambda: None)})())
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    assert decision._post_gate_ok(POST_GOOD)
    decision.post_fork_scan("s-post", POST_GOOD, model="m",
                            log_route=lambda e, **f: None)
    assert len(calls) == 1


def test_post_gate_blocks_one_named_option(_env, monkeypatch):
    calls = []

    def fake_backend(envelope, cfg):
        calls.append(envelope)
        return (json.dumps({"choice": "opt-1", "confidence": 0.5,
                            "alternatives": []}),
                {"model": "m", "endpoint": "e", "tokens_in": 1,
                 "tokens_out": 1, "latency_s": 0.0}, "ok")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    monkeypatch.setattr(decision, "render_prompt", lambda env: "P")
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    assert not decision._post_gate_ok(POST_ONE_NAMED)
    events = []
    decision.post_fork_scan("s-post2", POST_ONE_NAMED, model="m",
                            log_route=lambda e, **f: events.append((e, f)))
    assert calls == []  # no billing, no ledger pollution
    assert events and events[0][1].get("outcome") == \
        decision.REASON_POST_GATE


def test_post_gate_blocks_bare_bullets(_env, monkeypatch):
    calls = []

    def fake_backend(envelope, cfg):
        calls.append(envelope)
        return (json.dumps({"choice": "opt-1", "confidence": 0.5,
                            "alternatives": []}),
                {"model": "m", "endpoint": "e", "tokens_in": 1,
                 "tokens_out": 1, "latency_s": 0.0}, "ok")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    assert not decision._post_gate_ok(POST_BARE_BULLETS)
    decision.post_fork_scan("s-post3", POST_BARE_BULLETS, model="m",
                            log_route=lambda e, **f: None)
    assert calls == []


def test_post_gate_ordinary_delivery_turn(_env, monkeypatch):
    calls = []

    def fake_backend(envelope, cfg):
        calls.append(envelope)
        return (json.dumps({"choice": "opt-1", "confidence": 0.5,
                            "alternatives": []}),
                {"model": "m", "endpoint": "e", "tokens_in": 1,
                 "tokens_out": 1, "latency_s": 0.0}, "ok")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    ordinary = ("All 12 tests pass. The build is clean and I pushed the "
                "branch to origin. Ready for your review whenever you "
                "want to take a look at the diff.")
    decision.post_fork_scan("s-post4", ordinary, model="m",
                            log_route=lambda e, **f: None)
    assert calls == []


def test_post_gate_does_not_block_manual_leg(_env, monkeypatch):
    """The gate is POST-only: the manual leg still fires on the same
    text shape."""
    calls = []

    def fake_backend(envelope, cfg):
        calls.append(envelope)
        return (json.dumps({"choice": "opt-1", "confidence": 0.9,
                            "alternatives": []}),
                {"model": "m", "endpoint": "e", "tokens_in": 1,
                 "tokens_out": 1, "latency_s": 0.0}, "ok")

    monkeypatch.setattr(decision, "call_backend", fake_backend)
    monkeypatch.setattr(decision, "render_prompt", lambda env: "P")
    monkeypatch.setattr(decision, "_WORKER_SEM", type("S", (), {
        "acquire": staticmethod(lambda blocking=False: True),
        "release": staticmethod(lambda: None)})())
    cfg = dict(decision.DEFAULTS)
    cfg["enabled"] = True
    cfg["on_demand"] = {"manual": True, "midturn": True}
    monkeypatch.setattr(decision, "_cfg", lambda: cfg)
    manual_ask = ("decide this: 1. batch-append tonight, because the "
                  "rollback script is rehearsed.\n"
                  "2. synchronous appends, since each insert stays "
                  "independently reversible.\n")
    decision._invoke("s-post5", "t-post5", manual_ask, "manual", cfg,
                     lambda e, **f: None, initiator="user")
    assert len(calls) == 1  # manual leg unaffected by the POST gate
