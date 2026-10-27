"""R76-P1-U1 review probe — are the three call sites really passing a MODEL ID?

Independent of the shipped pin file: monkeypatch usage_ledger.estimate_cost and
capture the first argument at each of the three paths the commit touched.

Run: PYTHONPATH=/opt/data/plugins python -m pytest reviews/r76-p1-u1/probe_sites.py -q -s
"""
import json

import pytest

from hermes_router import config_access as ca
from hermes_router import debug_banner as db
from hermes_router import decision
from hermes_router import reflex_lane as rl
from hermes_router import semantic_classifier as sc
from hermes_router.core import usage_ledger as ul

SID = "r76-probe"
PARSED = {"options": [{"p": 0.61, "label": "a", "kind": "test", "move": "m1", "target": ""},
                      {"p": 0.44, "label": "b", "kind": "read", "move": "m2", "target": ""}],
          "margin": 0.17}


def _wipe(sid):
    rl.clear(sid)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(sid, None)


@pytest.fixture(autouse=True)
def clean():
    _wipe(SID)
    yield
    _wipe(SID)


def test_probe_sites_capture(monkeypatch):
    seen = []

    def _est(model, it, ot):
        seen.append((model, it, ot))
        return 0.000116

    monkeypatch.setattr(ul, "estimate_cost", _est)
    monkeypatch.setattr(ul, "record_tokens", lambda *a, **k: True)

    # 1. reflex_lane._receipt
    rl._receipt(SID, PARSED, {"usage": {"prompt_tokens": 2759, "completion_tokens": 102}},
                "every_call")

    # 2. semantic_classifier.aux_raw_call
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: json.dumps(
        {"choices": [{"message": {"content": "benign"}}],
         "usage": {"prompt_tokens": 2759, "completion_tokens": 102}}))
    sc.reset_limits()
    sc.aux_raw_call("judge", session_id=SID)

    # 3. decision.score
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: json.dumps(
        {"choices": [{"message": {"content": json.dumps(
            {"decision": "escalate", "confidence": 0.9, "precedents": []})}}],
         "usage": {"prompt_tokens": 2759, "completion_tokens": 102}}))
    frame = {"precedents": [{"id": "1", "ts": "2026-01-01", "same_repo": True,
                             "recent": True, "snippet": "x"}], "evol": []}
    decision.score(frame, "which?", {"calls_per_hour": 20, "score_timeout_seconds": 8,
                                     "confidence_threshold": 0.6,
                                     "max_precedent_age_days": 90})

    print("\nPROBE estimate_cost keys:", seen)
    print("PROBE agent_model:", repr(ca.agent_model()))
    print("PROBE aux_price_model:", repr(sc.aux_price_model()))
    assert len(seen) == 3, seen
    assert all(m and m != "hermes-auxiliary" for m, _, _ in seen), seen
