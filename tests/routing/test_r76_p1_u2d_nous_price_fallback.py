"""R76-P1-U2d — the nous banner prices the model the transport DISPATCHED.

Reviewer D1 (U2 + U2c, both measured at the call): `decision.call_backend(...,
backend="nous")` lets the aux client pick the model, but the nous branch recorded
`meta["model"] = dispatched or cfg_model_or_DEFAULT`, and BOTH pricing sites
(tokens row `:4080`, banner `render_decision_banner`) read that same cell. An aux
client that reported nothing therefore billed the CONFIGURED model:

    model sent on the wire : [None]
    billed id              : z-ai/glm-5.3-flash
    price                  : 0.000372        <- nothing was dispatched

The contract pinned here:

* the ledger/banner MODEL cell is the dispatched id — '' when the transport
  reported none (R76-P1-U2e closes the D2 gap: no cfg/default substitute);
* the PRICE KEY is `meta["price_model"]` — the dispatched id, '' when the
  transport reported none — and never the cfg/default label;
* unpriced dispatch -> 0.0 and the honest `$0.0000*` marker, with the id named;
* priced dispatch -> that model's real rate;
* a price-path exception cannot block or alter the banner (fail-open);
* the three sites already correct (reflex_lane._receipt, decision.score,
  semantic_classifier.aux_raw_call) keep pricing their own dispatched id.

Assertions are behavioural: `call_backend` / `_v3_worker` are driven with a fake
aux client, and the captured keys are the ones `estimate_cost` actually received.
"""
import json
import types

import pytest

from hermes_router import debug_banner as db
from hermes_router import decision as D
from hermes_router import reflex_lane as rl
from hermes_router import render_payload as rp
from hermes_router import semantic_classifier as sc
from hermes_router.core import usage_ledger as ul

SID = "r76-u2d-sess"
SID2 = "r76-u2d-sess2"

PRICED = "jev-latest"                 # known to the price tables (0.000116)
UNPRICED = "zz-no-such-model-9"       # id with no price row
DECOY = "z-ai/glm-5.3-flash"          # configured/default model: $0.000372
TI, TO = 2759, 102

VERDICT = json.dumps({"choice": "opt-1", "confidence": 0.8,
                      "alternatives": []})


def _body(model_dispatched):
    """Fake aux transport: reports `model_dispatched` ('' = reported none) and
    returns a valid verdict + usage. This is the wire-shape `_hermes_aux_call`
    produces, including the '' case (client returned no model)."""
    sc._set_dispatched_aux_model(model_dispatched)
    return json.dumps({"choices": [{"message": {"content": VERDICT}}],
                       "usage": {"prompt_tokens": TI,
                                 "completion_tokens": TO}})


@pytest.fixture(autouse=True)
def clean():
    rl.clear(SID)
    rl.clear(SID2)
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS,
              db._PARK_RECORDS):
        m.pop(SID, None)
    sc._set_dispatched_aux_model("")
    yield
    rl.clear(SID)
    rl.clear(SID2)
    sc._set_dispatched_aux_model("")


def _envelope(sid=SID):
    return {"schema": D.ENVELOPE_SCHEMA, "session_id": sid, "task_id": "t-u2d",
            "ask": "redis or memcached? weigh the tradeoffs", "fork_class": "generic",
            "options": [{"id": "opt-1", "label": "redis"},
                        {"id": "opt-2", "label": "memcached"}]}


def _cfg():
    return dict(D._cfg(), backend="nous", model=DECOY)


def _capture(monkeypatch):
    """Record every price key handed to estimate_cost + every ledger row."""
    keys, rows = [], []
    real_est = ul.estimate_cost

    def _est(model, ti, to):
        keys.append(model)
        return real_est(model, ti, to)

    def _rec(lane, model, sid, it, ot, cost, detail="", **kw):
        rows.append({"lane": lane, "model": model, "cost": cost,
                     "detail": detail})
        return True

    monkeypatch.setattr(ul, "estimate_cost", _est)
    monkeypatch.setattr(ul, "record_tokens", _rec)
    return keys, rows


def _call_backend(monkeypatch, dispatched):
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: _body(dispatched))
    content, meta, reason = D.call_backend(_envelope(), _cfg())
    assert reason == "ok", reason
    return meta


def _drive_worker(monkeypatch, dispatched, sid=SID):
    """Drive the real v3 worker (the site that writes the priced tokens row and
    parks the banner) through the real call_backend + fake aux transport."""
    rows, banners, logs = [], [], []
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: _body(dispatched))
    monkeypatch.setattr(D, "ledger_write", lambda row: 1)
    monkeypatch.setattr(D, "bump_counter", lambda *a, **k: None)
    monkeypatch.setattr(D, "_record_failure", lambda cfg: None)
    monkeypatch.setattr(D, "_record_success", lambda: None)
    monkeypatch.setattr(D, "consume_parked_banner", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(db, "park_anchor_banner",
                        lambda s, text, **kw: banners.append(text))
    D._v3_worker(_envelope(sid),
                 {"session_id": sid, "task_id": "t-u2d", "trigger": "pre"},
                 _cfg(), lambda event, **fields: logs.append((event, fields)))
    return rows, banners, logs


# ---------------------------------------------------------------------------
# a) aux client reports NOTHING -> price 0.0, honest marker, never the cfg id
# ---------------------------------------------------------------------------

def test_a_nous_unreported_model_prices_zero_never_the_configured_default(monkeypatch):
    keys, rows = _capture(monkeypatch)
    meta = _call_backend(monkeypatch, "")

    assert meta["price_model"] == "", meta.get("price_model")
    # R76-P1-U2e (D2): the NAME cell is the dispatched id too — '' here.
    assert meta["model"] == "", meta.get("model")
    banner = rp.render_decision_banner("pre", meta["model"], meta)
    assert banner, "banner must render"
    assert "$0.0000*" in banner, banner
    assert "$0.000372" not in banner, banner
    assert DECOY not in banner, banner
    assert DECOY not in keys, (
        "estimate_cost received the configured default (%r): %r" % (DECOY, keys))
    assert "" in keys, keys


def test_a_nous_unreported_model_writes_a_zero_cost_token_row(monkeypatch):
    keys, rows = _capture(monkeypatch)
    _rows, banners, _logs = _drive_worker(monkeypatch, "")
    assert banners, "banner must park"
    tok = [r for r in rows if r["detail"] == "decision_v3"]
    assert tok, rows
    assert tok[0]["cost"] == 0.0, tok[0]
    assert DECOY not in keys, keys
    assert "$0.0000*" in banners[-1], banners[-1]
    assert "$0.000372" not in banners[-1], banners[-1]


# ---------------------------------------------------------------------------
# b) aux client reports a model WITH a price table entry -> its real rate
# ---------------------------------------------------------------------------

def test_b_nous_priced_dispatch_bills_that_models_real_rate(monkeypatch):
    keys, rows = _capture(monkeypatch)
    _rows, banners, _logs = _drive_worker(monkeypatch, PRICED)
    tok = [r for r in rows if r["detail"] == "decision_v3"]
    assert tok, rows
    assert tok[0]["cost"] == 0.000116, tok[0]
    # two priced surfaces ran: the tokens row and the banner — both the id sent
    assert keys and set(keys) == {PRICED}, keys
    assert "$0.000116" in banners[-1], banners[-1]
    assert "*" not in banners[-1].split("$")[-1][:11], banners[-1]


# ---------------------------------------------------------------------------
# c) aux client reports a model with NO price entry -> 0.0, marker, id named
# ---------------------------------------------------------------------------

def test_c_nous_unpriced_dispatch_is_zero_and_names_the_id(monkeypatch):
    keys, rows = _capture(monkeypatch)
    _rows, banners, _logs = _drive_worker(monkeypatch, UNPRICED)
    tok = [r for r in rows if r["detail"] == "decision_v3"]
    assert tok, rows
    assert tok[0]["cost"] == 0.0, tok[0]
    assert keys and set(keys) == {UNPRICED}, keys
    assert "$0.0000*" in banners[-1], banners[-1]
    assert UNPRICED in banners[-1], banners[-1]
    assert "$0.000372" not in banners[-1], banners[-1]


def test_c2_banner_prices_the_dispatched_id_not_the_provenance_cell(monkeypatch):
    keys, _rows = _capture(monkeypatch)
    meta = _call_backend(monkeypatch, UNPRICED)
    assert meta["model"] == UNPRICED
    banner = rp.render_decision_banner("pre", meta["model"], meta)
    assert UNPRICED in banner and "$0.0000*" in banner, banner
    assert keys == [UNPRICED], keys


# ---------------------------------------------------------------------------
# Fail-open: a pricing explosion cannot block or alter the banner
# ---------------------------------------------------------------------------

def test_pricing_explosion_cannot_break_the_nous_banner(monkeypatch):
    monkeypatch.setattr(sc, "_hermes_aux_call", lambda p, t: _body(PRICED))
    content, meta, reason = D.call_backend(_envelope(), _cfg())
    assert reason == "ok"

    def _boom(*a, **k):
        raise RuntimeError("price table down")

    monkeypatch.setattr(ul, "estimate_cost", _boom)
    banner = rp.render_decision_banner("pre", meta["model"], meta)
    assert banner, "a price-path exception must not swallow the banner"
    assert "tok 2759/102" in banner, banner


# ---------------------------------------------------------------------------
# d) the three sites already correct keep pricing their own dispatched id
# ---------------------------------------------------------------------------

class _AuxClient:
    def __init__(self, content):
        _outer = self
        self.sent = []

        class _Completions:
            def create(self, **kw):
                _outer.sent.append(kw.get("model"))
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(
                        message=types.SimpleNamespace(content=content))],
                    usage=types.SimpleNamespace(prompt_tokens=TI,
                                                completion_tokens=TO))

        self.chat = types.SimpleNamespace(completions=_Completions())


def _stub_aux(monkeypatch, model, content=VERDICT):
    monkeypatch.setattr(sc, "_hermes_aux_call", sc._original_hermes_aux)
    client = _AuxClient(content)
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client",
                        lambda task: (client, model))
    return client


def test_d_aux_raw_call_still_prices_its_dispatched_model(monkeypatch):
    _stub_aux(monkeypatch, PRICED)
    keys, rows = _capture(monkeypatch)
    sc.reset_limits()
    assert sc.aux_raw_call("judge this", session_id=SID) is not None
    assert keys == [PRICED], keys
    assert rows and rows[0]["cost"] == 0.000116, rows


def test_d_decision_score_still_prices_its_dispatched_model(monkeypatch):
    _stub_aux(monkeypatch, PRICED, content=json.dumps(
        {"decision": "escalate", "confidence": 0.9, "precedents": []}))
    keys, rows = _capture(monkeypatch)
    frame = {"precedents": [{"id": "1", "ts": "2026-01-01", "same_repo": True,
                             "recent": True, "snippet": "buffer sizing"}],
             "evol": []}
    verdict, reason = D.score(
        frame, "which approach?",
        {"calls_per_hour": 20, "score_timeout_seconds": 8,
         "confidence_threshold": 0.6, "max_precedent_age_days": 90})
    assert reason == "ok" and verdict is not None, (verdict, reason)
    assert keys == [PRICED], keys
    assert rows and rows[0]["cost"] == 0.000116, rows


def test_d_reflex_receipt_still_prices_its_dispatched_model():
    parsed = {"options": [{"p": 0.61, "label": "run the check", "kind": "test",
                           "move": "run_check", "target": ""},
                          {"p": 0.44, "label": "read the file", "kind": "read",
                           "move": "read_file", "target": ""}],
              "margin": 0.17}
    rl._set_dispatched_model("jev-preview")
    try:
        banner = rl._receipt(SID2, parsed,
                             {"usage": {"prompt_tokens": 1000,
                                        "completion_tokens": 0}},
                             "every_call")
    finally:
        rl._set_dispatched_model("")
    cells = [c.strip() for c in str(banner).split(" | ")]
    assert next((c for c in cells if c.startswith("$")), "") == "$0.00004", banner
