"""R75 + R76-P1-U2 — a banner prices what was DISPATCHED, or it says nothing.

R75 (Goran 2026-10-27): the banner billed $0.00000* with real tokens because
`reflex_lane` priced the string "hermes-auxiliary" — the auxiliary lane's DISPLAY
name. No price source keys on a label, so estimate_cost honestly returned 0.0 and
the renderer honestly drew the unpriced cell.

    estimate_cost("hermes-auxiliary", 2759, 102) -> 0.0
    estimate_cost("jev-latest",       2759, 102) -> 0.000116

R76-P1-U2 (review F1, blocking): the R75 fix was right for the reflex site but
WRONG for the two aux sites — they priced a *resolved/default* model, which in
the repo/test layout degenerated to "jev-latest" while the call was served by
something else. A precise-looking wrong rate is worse than an honest zero: cost
surfaces have exactly one job, be true.

The contract these pins enforce:

* an aux site prices the model `get_text_auxiliary_client("router")` reported for
  THAT call — asserted by equality, never "some priceable model was used";
* when that id is absent the site prices 0.0 and the banner renders the unpriced
  marker — a missing model id must never silently become a priced call;
* the reflex site keeps its own dispatched model (jev_native_model);
* an unknown model still renders $0.00000* (fail-open not regressed, no invented
  rate), and pricing never raises into delivery;
* banner shape/budget/aggregation (R74) untouched.
"""
import json
import pathlib
import re
import types

import pytest

from hermes_router import config_access as ca
from hermes_router import debug_banner as db
from hermes_router import decision
from hermes_router import reflex_lane as rl
from hermes_router import semantic_classifier as sc
from hermes_router.core import usage_ledger as ul

SID = "r75-sess"

PARSED = {"options": [{"p": 0.61, "label": "run the check", "kind": "test",
                       "move": "run_check", "target": ""},
                      {"p": 0.44, "label": "read the file", "kind": "read",
                       "move": "read_file", "target": ""}],
          "margin": 0.17}

# a model id the price tables know, used for the real-rate pin
PRICED = "jev-latest"
# the model the fake aux client reports having DISPATCHED — deliberately neither
# the priced id nor any plausible configured/default id, so a stand-in that
# happens to equal the default cannot make the equality pins pass
DISPATCHED = "MiniMax-M3"
# what a configured/default resolver would have picked instead (the F1 decoy)
DECOY = "z-ai/glm-5.3-flash"


def _usage(ti, to):
    return {"usage": {"prompt_tokens": ti, "completion_tokens": to}}


def _wipe(sid):
    rl.clear(sid)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS, db._PARK_RECORDS):
        m.pop(sid, None)


@pytest.fixture(autouse=True)
def clean():
    _wipe(SID)
    sc._set_dispatched_aux_model("")
    yield
    _wipe(SID)
    sc._set_dispatched_aux_model("")


def _cost_cell(banner):
    cells = [c.strip() for c in str(banner or "").split(" | ")]
    return next((c for c in cells if c.startswith("$")), "")


# ---------------------------------------------------------------------------
# Harness: a fake Hermes aux client that reports a chosen model id.
# ---------------------------------------------------------------------------

class _AuxClient:
    """Minimal OpenAI-shape client; records the model actually sent."""

    def __init__(self, content):
        self.sent = []
        _outer = self

        class _Completions:
            def create(self, **kw):
                _outer.sent.append(kw.get("model"))
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(
                        message=types.SimpleNamespace(content=content))],
                    usage=types.SimpleNamespace(prompt_tokens=2759,
                                                completion_tokens=102))

        self.chat = types.SimpleNamespace(completions=_Completions())


def _stub_aux(monkeypatch, model, content="benign"):
    """Route the REAL _hermes_aux_call at a fake client reporting `model`."""
    monkeypatch.setattr(sc, "_hermes_aux_call", sc._original_hermes_aux)
    client = _AuxClient(content)
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client",
                        lambda task: (client, model))
    return client


def _capture(monkeypatch):
    """Capture price keys and ledger cost cells, in call order."""
    seen, rows = [], []

    def _est(m, i, o):
        seen.append(m)
        return 0.000116

    def _rec(lane, model, sid, it, ot, cost, detail="", **kw):
        rows.append({"lane": lane, "model": model, "cost": cost, "detail": detail})
        return True

    monkeypatch.setattr(ul, "estimate_cost", _est)
    monkeypatch.setattr(ul, "record_tokens", _rec)
    return seen, rows


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
        "estimate_cost() must be called with a dispatched MODEL ID, never the "
        "'hermes-auxiliary' display label (no price table keys on it -> every "
        "such banner renders the unpriced cell $0.00000*): %s" % offenders)


# ---------------------------------------------------------------------------
# Pin 2 (F1) — an aux site prices the DISPATCHED model, asserted by equality.
# ---------------------------------------------------------------------------

def test_aux_raw_call_prices_the_dispatched_model_not_a_resolved_one(monkeypatch):
    # dispatched model deliberately differs from BOTH the configured model and
    # any plausible default, so a stand-in cannot pass this pin by coincidence
    _stub_aux(monkeypatch, DISPATCHED)
    monkeypatch.setattr(ca, "agent_model", lambda: DECOY)
    monkeypatch.setattr(sc, "resolve_aux_model", lambda ep: DECOY)
    seen, rows = _capture(monkeypatch)
    sc.reset_limits()

    assert sc.aux_raw_call("judge this", session_id=SID) is not None
    assert seen == [DISPATCHED], (
        "the aux tap must price the model the client dispatched (%r), got %r"
        % (DISPATCHED, seen))
    assert rows and rows[0]["cost"] == 0.000116
    assert rows[0]["detail"] == "stage2_classify"


def test_decision_score_prices_the_dispatched_model_not_a_resolved_one(monkeypatch):
    _stub_aux(monkeypatch, DISPATCHED, content=json.dumps(
        {"decision": "escalate", "confidence": 0.9, "precedents": []}))
    monkeypatch.setattr(ca, "agent_model", lambda: DECOY)
    monkeypatch.setattr(sc, "resolve_aux_model", lambda ep: DECOY)
    seen, rows = _capture(monkeypatch)

    frame = {"precedents": [{"id": "1", "ts": "2026-01-01", "same_repo": True,
                             "recent": True, "snippet": "buffer sizing"}],
             "evol": []}
    verdict, reason = decision.score(
        frame, "which approach?",
        {"calls_per_hour": 20, "score_timeout_seconds": 8,
         "confidence_threshold": 0.6, "max_precedent_age_days": 90})
    assert reason == "ok" and verdict is not None, (verdict, reason)
    assert seen == [DISPATCHED], seen
    assert rows and rows[0]["detail"] == "decision_score"
    assert rows[0]["cost"] == 0.000116


def test_aux_rate_is_the_real_rate_of_the_dispatched_model(monkeypatch):
    """Real pricing end-to-end: a dispatched model the tables know bills its
    own rate (2759 in x $0.042/1M, output free), not a rounded placeholder."""
    _stub_aux(monkeypatch, PRICED)
    rows = []
    monkeypatch.setattr(ul, "record_tokens",
                        lambda lane, model, sid, it, ot, cost, detail="", **kw:
                        rows.append(cost) or True)
    sc.reset_limits()
    assert sc.aux_raw_call("judge this", session_id=SID) is not None
    assert rows == [0.000116], rows


def test_reflex_still_prices_its_own_dispatched_model(monkeypatch):
    seen, _rows = _capture(monkeypatch)
    rl._receipt(SID, PARSED, _usage(2759, 102), "every_call")
    assert seen and seen[0] == PRICED, seen


# ---------------------------------------------------------------------------
# Pin 3 (F1) — a MISSING model id must never become a priced call.
# ---------------------------------------------------------------------------

def test_missing_dispatched_model_prices_zero_and_never_a_stand_in(monkeypatch):
    _stub_aux(monkeypatch, None)  # the client reports no model
    monkeypatch.setattr(ca, "agent_model", lambda: DECOY)   # the stand-in trap
    monkeypatch.setattr(sc, "resolve_aux_model", lambda ep: DECOY)
    seen, rows = _capture(monkeypatch)
    sc.reset_limits()

    assert sc.aux_raw_call("judge this", session_id=SID) is not None
    assert seen == [], (
        "no dispatched model id -> estimate_cost must NOT be called at all "
        "(a priced call here is a fabricated rate), keys were %r" % (seen,))
    assert rows and rows[0]["cost"] == 0.0, rows


def test_missing_dispatched_model_on_the_decision_path_prices_zero(monkeypatch):
    _stub_aux(monkeypatch, "", content=json.dumps(
        {"decision": "escalate", "confidence": 0.9, "precedents": []}))
    monkeypatch.setattr(ca, "agent_model", lambda: DECOY)
    seen, rows = _capture(monkeypatch)

    frame = {"precedents": [{"id": "1", "ts": "2026-01-01", "same_repo": True,
                             "recent": True, "snippet": "buffer sizing"}],
             "evol": []}
    verdict, reason = decision.score(
        frame, "which approach?",
        {"calls_per_hour": 20, "score_timeout_seconds": 8,
         "confidence_threshold": 0.6, "max_precedent_age_days": 90})
    assert reason == "ok" and verdict is not None
    assert seen == [], seen
    assert rows and rows[0]["cost"] == 0.0, rows


def test_the_retired_default_model_helper_is_not_called_from_production():
    with pytest.raises(RuntimeError):
        sc.aux_price_model()
    root = pathlib.Path(rl.__file__).parent
    callers = []
    for p in root.rglob("*.py"):
        # production only: tests document the proof, reviews/ holds the
        # reviewer's probe artefacts (evidence, never rewritten by a fix)
        if "tests" in p.parts or "reviews" in p.parts:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "aux_price_model(" in line and not line.strip().startswith("def "):
                callers.append("%s:%d" % (p.name, i))
    assert callers == [], (
        "aux_price_model() resolved a default model id and is retired; price the "
        "dispatched model or 0.0: %s" % callers)


# ---------------------------------------------------------------------------
# Pin 4 — a priced banner renders the rate; an unpriced one keeps the asterisk.
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


def test_unknown_model_still_renders_the_honest_zero():
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
# Pin 5 — pricing never raises into a delivery.
# ---------------------------------------------------------------------------

def test_a_pricing_explosion_cannot_break_the_aux_lane(monkeypatch):
    _stub_aux(monkeypatch, PRICED)

    def _boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ul, "estimate_cost", _boom)
    monkeypatch.setattr(ul, "record_tokens", _boom)
    sc.reset_limits()
    # the classification still returns its content: observability, not delivery
    assert sc.aux_raw_call("judge this", session_id=SID) == "benign"


# ---------------------------------------------------------------------------
# R76-P1-U2 addendum (F1, same defect class): the decision lane's DEFAULT
# backend ("nous") puts no model in the request — semantic_classifier's aux
# client picks it. decision.call_backend used to record the CONFIGURED model in
# meta, so the ledger row and the parked banner named AND priced a model nobody
# dispatched (measured: cfg z-ai/glm-5.3-flash claimed $0.000372 for a call
# served by an id that has no price row). The row/banner model cell must be the
# id the aux client reported, and the money cell must be that id's own rate.
# ---------------------------------------------------------------------------

def _verdict_json():
    return json.dumps({"questions": [{"id": "q1", "type": "choice", "chosen": "a1"}]})


def _nous_meta(monkeypatch, dispatched):
    env = decision.build_envelope(SID, "redis or memcached? weigh the tradeoffs", [], "pre")
    assert env, "fixture envelope must carry an option set"

    def _call(payload_json, timeout):
        sc._set_dispatched_aux_model(dispatched)
        return json.dumps({"choices": [{"message": {"content": _verdict_json()}}],
                           "usage": {"prompt_tokens": 2759, "completion_tokens": 102}})

    monkeypatch.setattr(sc, "_hermes_aux_call", _call)
    content, meta, reason = decision.call_backend(env, {"backend": "nous", "model": DECOY})
    assert reason == "ok", reason
    return meta


def test_nous_backend_records_the_dispatched_model_not_the_configured_one(monkeypatch):
    meta = _nous_meta(monkeypatch, DISPATCHED)
    assert meta["model"] == DISPATCHED, (
        "the decision row must name the model the aux client dispatched (%r), got %r"
        % (DISPATCHED, meta["model"]))
    assert meta["model"] != DECOY


def test_nous_backend_banner_prices_the_dispatched_model(monkeypatch):
    meta = _nous_meta(monkeypatch, PRICED)
    banner = decision.render_decision_banner("pre", meta["model"], meta)
    assert PRICED in banner and DECOY not in banner, banner
    real = ul.estimate_cost(PRICED, meta.get("tokens_in"), meta.get("tokens_out"))
    assert real > 0, "fixture expects a priced id"
    assert ("$%.6f" % real) in banner, banner
    assert "$0.00000*" not in banner, banner


def test_nous_backend_unpriced_dispatch_renders_the_honest_zero(monkeypatch):
    meta = _nous_meta(monkeypatch, "zz-no-such-model-9")
    banner = decision.render_decision_banner("pre", meta["model"], meta)
    assert "zz-no-such-model-9" in banner and DECOY not in banner, banner
    assert "$0.0000*" in banner, banner  # the unpriced marker this renderer emits


# ---------------------------------------------------------------------------
# R76-P1-U2 (third site) — the IMPULSE banner prices the model it DISPATCHED.
#
# The same F1 defect on the reflex lane: _receipt re-derived its price key from
# cfg at receipt time and _park_summary named that same cfg value. When the key
# and the id actually sent diverge, the banner either draws a hard zero for a
# call that WAS billed, or a rate for a model nobody called.
# ---------------------------------------------------------------------------

def test_impulse_banner_prices_the_model_that_was_dispatched():
    # jev-preview dispatched: 1000 in x $0.042/1M => $0.00004 (not the zero)
    rl._set_dispatched_model("jev-preview")
    try:
        banner = rl._receipt(SID, PARSED, _usage(1000, 0), "every_call")
    finally:
        rl._set_dispatched_model("")
    assert banner
    cell = _cost_cell(banner)
    assert cell == "$0.00004", banner
    assert "*" not in cell, banner


def test_impulse_banner_never_prices_a_model_it_did_not_dispatch():
    # config would resolve the PRICED default, but the consult went to an id no
    # table knows: the honest answer is the asterisk, never a stand-in rate.
    rl._set_dispatched_model("zz-never-dispatched-9")
    try:
        banner = rl._receipt(SID, PARSED, _usage(2759, 102), "every_call")
    finally:
        rl._set_dispatched_model("")
    assert banner
    cell = _cost_cell(banner)
    assert cell == "$0.00000*", banner
    assert "zz-never" in banner, banner          # names the id it could not price
    assert "jev" not in banner.lower(), banner   # never the priced decoy


def test_transport_records_the_id_it_actually_sent(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    sent = {}

    def _fake_post(endpoint, headers, body, timeout):
        sent.update(body)
        return {"usage": {"prompt_tokens": 10, "completion_tokens": 1}}

    monkeypatch.setattr(decision, "_http_post_json", _fake_post)
    cfg = {"typesafe_endpoint": "https://api.typesafe.ai/v1/x",
           "typesafe_api_key_env": "TYPESAFE_API_KEY",
           "jev_native_model": "jev-preview"}
    rl._transport({"model": "jev-latest"}, cfg)
    assert sent.get("model") == "jev-preview", sent
    assert rl._dispatched_model() == "jev-preview"
    rl._set_dispatched_model("")


def test_a_failed_dispatch_claims_no_model():
    # nothing sent => nothing priced; a stale id must not bill the next receipt
    rl._set_dispatched_model("jev-preview")
    cfg = {"typesafe_endpoint": "https://api.typesafe.ai/v1/x",
           "typesafe_api_key_env": "ZZ_NO_SUCH_ENV_FOR_TEST"}
    assert rl._transport({"model": "jev-latest"}, cfg) is None
    assert rl._dispatched_model() == ""


def test_an_unpriced_model_is_reportable_by_id_and_table_key():
    info = ul.describe_price("zz-never-dispatched-9")
    assert info["priced"] is False
    assert info["model"] == "zz-never-dispatched-9"
    assert info["key"] == "zz-never-dispatched-9"
    priced = ul.describe_price("jev-latest")
    assert priced["priced"] is True
    assert priced["source"] == "measured"
    assert priced["key"] == "jev-latest"
