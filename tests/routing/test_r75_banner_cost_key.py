"""R75 — the banner billed $0.00000* because it priced a LABEL, not a model.

Live line (Goran 2026-10-27):

    · router · impulse (decision) | 1 lead | jev @ typesafe | 2759/102 tok | $0.00000* | row 2

Tokens were real, the host was right, and the cost was structurally dead:
`reflex_lane.py` called `usage_ledger.estimate_cost("hermes-auxiliary", ...)`.
"hermes-auxiliary" is the auxiliary lane's DISPLAY name — no price table (anchor
chain, provider /models, measured fallback) keys on it — so estimate_cost
honestly returned 0.0 and the renderer honestly drew the unpriced cell.

Proved deterministically: estimate_cost("hermes-auxiliary", 2759, 102) == 0.0
vs estimate_cost("jev-latest", 2759, 102) == 0.000116.

Same literal sat in decision.py and semantic_classifier.py, so these pins cover
all three sites and fix the CONTRACT, not one line:

* the price key is always a resolved model id — never the lane label;
* a priced model with real tokens renders a non-zero cost and NO asterisk;
* an unknown model still renders $0.00000* (fail-open is not regressed — the
  asterisk is honest and a fabricated rate is worse than a zero);
* banner shape/budget/aggregation (R74) is untouched.
"""
import json
import pathlib
import re

import pytest

from hermes_router import config_access as ca
from hermes_router import debug_banner as db
from hermes_router import reflex_lane as rl
from hermes_router import semantic_classifier as sc
from hermes_router.core import usage_ledger as ul

SID = "r75-sess"

PARSED = {"options": [{"p": 0.61, "label": "run the check", "kind": "test",
                       "move": "run_check", "target": ""},
                      {"p": 0.44, "label": "read the file", "kind": "read",
                       "move": "read_file", "target": ""}],
          "margin": 0.17}


def _usage(ti, to):
    return {"usage": {"prompt_tokens": ti, "completion_tokens": to}}


def _wipe(sid):
    rl.clear(sid)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(sid, None)


@pytest.fixture(autouse=True)
def clean():
    _wipe(SID)
    yield
    _wipe(SID)


def _cost_cell(banner):
    cells = [c.strip() for c in str(banner or "").split(" | ")]
    return next((c for c in cells if c.startswith("$")), "")


# ---------------------------------------------------------------------------
# Pin 1 — the display label is never a price key, anywhere in the plugin.
# ---------------------------------------------------------------------------

def test_no_estimate_cost_call_anywhere_prices_the_lane_label():
    root = pathlib.Path(rl.__file__).parent
    pat = re.compile(r"estimate_cost\(\s*[\"']hermes-auxiliary[\"']")
    offenders = sorted(str(p.relative_to(root))
                       for p in root.rglob("*.py")
                       if "tests" not in p.parts and pat.search(p.read_text(encoding="utf-8")))
    assert offenders == [], (
        "estimate_cost() must be called with a resolved MODEL ID, never the "
        "'hermes-auxiliary' display label (no price table keys on it -> every "
        "such banner renders the unpriced cell $0.00000*): %s" % offenders)


def test_the_three_known_sites_pass_a_model_not_the_label(monkeypatch):
    """Each of the three sites feeds estimate_cost a value that can be priced.

    Asserted at the CALL, not in the source: a source grep cannot tell a
    variable holding the label from one holding the model.
    """
    import hermes_router.decision as decision

    seen = []

    def _est(model, it, ot):
        seen.append(model)
        return 0.000116

    monkeypatch.setattr(ul, "estimate_cost", _est)

    # (a) reflex_lane._receipt
    rl._receipt(SID, PARSED, _usage(2759, 102), "every_call")

    # (b) semantic_classifier.aux_raw_call
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: json.dumps(
        {"choices": [{"message": {"content": "benign"}}],
         "usage": {"prompt_tokens": 2759, "completion_tokens": 102}}))
    monkeypatch.setattr(ul, "record_tokens", lambda *a, **k: True)
    sc.reset_limits()
    assert sc.aux_raw_call("judge this", session_id=SID) is not None

    # (c) decision.score's aux tap — the live "impulse (decision)" path
    monkeypatch.setattr(ca, "agent_model", lambda: "")  # deterministic key
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: json.dumps(
        {"choices": [{"message": {"content": json.dumps(
            {"decision": "escalate", "confidence": 0.9, "precedents": []})}}],
         "usage": {"prompt_tokens": 2759, "completion_tokens": 102}}))
    frame = {"precedents": [{"id": "1", "ts": "2026-01-01", "same_repo": True,
                             "recent": True, "snippet": "buffer sizing"}],
             "evol": []}
    verdict, reason = decision.score(
        frame, "which approach?",
        {"calls_per_hour": 20, "score_timeout_seconds": 8,
         "confidence_threshold": 0.6, "max_precedent_age_days": 90})
    assert reason == "ok" and verdict is not None, (verdict, reason)

    assert seen, "estimate_cost was never reached"
    assert "hermes-auxiliary" not in seen, seen
    assert all(str(m).strip() for m in seen), seen
    assert seen.count("jev-latest") >= 2, seen


# ---------------------------------------------------------------------------
# Pin 2 — priced model + real tokens => non-zero cost, no asterisk.
# ---------------------------------------------------------------------------

def test_priced_model_renders_nonzero_cost_and_no_asterisk(monkeypatch):
    monkeypatch.setattr(db, "debug_banner_enabled", lambda: True)
    banner = rl._receipt(SID, PARSED, _usage(2759, 102), "every_call")
    assert banner, "the reading must still banner"
    cell = _cost_cell(banner)
    assert cell, banner
    assert "*" not in cell, "a priced model must not claim to be unpriced: %r" % cell
    assert float(cell.lstrip("$")) > 0.0, banner
    # 2759 in x $0.042/1M (measured jev rate), output free => $0.00012
    assert cell == "$0.00012", banner


def test_the_call_receives_the_resolved_model_id(monkeypatch):
    seen = []

    def _est(model, it, ot):
        seen.append(model)
        return 0.000116

    monkeypatch.setattr(ul, "estimate_cost", _est)
    rl._receipt(SID, PARSED, _usage(2759, 102), "every_call")
    assert seen == ["jev-latest"], seen


# ---------------------------------------------------------------------------
# Pin 3 — the honest path is NOT regressed: unknown model stays $0.00000*.
# ---------------------------------------------------------------------------

def test_unknown_model_still_prices_zero_and_keeps_the_asterisk():
    assert ul.estimate_cost("zz-no-such-model-9", 2759, 102) == 0.0
    acc = {"fires": 1, "tok_in": 2759, "tok_out": 102, "cost": 0.0,
           "first_row": 2, "last_row": 2, "leads": 1, "no_lead": 0}
    line = rl._park_summary(SID, acc, {"jev_native_model": "zz-no-such-model-9"})
    assert "$0.00000*" in line, line


def test_estimate_cost_still_refuses_to_fabricate_a_rate():
    # the fail-open contract itself: no table row, no invented price
    assert ul.estimate_cost("hermes-auxiliary", 2759, 102) == 0.0
    assert ul.estimate_cost("", 2759, 102) == 0.0


# ---------------------------------------------------------------------------
# The shared resolver the two aux sites use.
# ---------------------------------------------------------------------------

def test_aux_price_model_prefers_the_configured_endpoint_model():
    cfg = {"aux_endpoint": {"model": "z-ai/glm-5.3-flash"}}
    assert sc.aux_price_model(cfg) == "z-ai/glm-5.3-flash"


def test_aux_price_model_falls_back_to_the_agent_model(monkeypatch):
    monkeypatch.setattr(ca, "agent_model", lambda: "z-ai/glm-5.3-flash")
    assert sc.aux_price_model({}) == "z-ai/glm-5.3-flash"


def test_aux_price_model_last_resort_is_jev_latest(monkeypatch):
    monkeypatch.setattr(ca, "agent_model", lambda: "")
    assert sc.aux_price_model({}) == "jev-latest"


def test_aux_price_model_never_returns_the_display_label(monkeypatch):
    monkeypatch.setattr(ca, "agent_model", lambda: "")
    for cfg in ({}, {"aux_endpoint": {}}, {"aux_endpoint": {"model": "auto"}},
                {"aux_endpoint": {"model": "hermes"}}, None):
        got = sc.aux_price_model(cfg)
        assert got and got != "hermes-auxiliary", (cfg, got)


# ---------------------------------------------------------------------------
# The two ledger taps bill the model, not the label.
# ---------------------------------------------------------------------------

def test_aux_raw_call_ledger_row_is_priced_from_the_model(monkeypatch):
    monkeypatch.setattr(ca, "agent_model", lambda: "jev-latest")
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: json.dumps(
        {"choices": [{"message": {"content": "benign"}}],
         "usage": {"prompt_tokens": 2759, "completion_tokens": 102}}))
    rows = []

    def _rec(lane, model, sid, it, ot, cost, detail="", **kw):
        rows.append({"lane": lane, "model": model, "cost": cost, "detail": detail})
        return True

    monkeypatch.setattr(ul, "record_tokens", _rec)
    sc.reset_limits()
    assert sc.aux_raw_call("judge this", session_id=SID) is not None
    assert rows, "the aux tokens-ledger tap must still write"
    assert float(rows[0]["cost"] or 0.0) > 0.0, rows[0]
    assert rows[0]["detail"] == "stage2_classify"
