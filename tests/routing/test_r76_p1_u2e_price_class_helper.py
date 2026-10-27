"""R76-P1-U2e — ONE billing-id resolver; the midturn + rollup class is closed.

Reviewer D1 (U2d, measured at the call): the `or model` substitution survived the
U2/U2c/U2d units on the MIDTURN path. `decision_midturn.py:951` recorded
``cfg.get("model") or DEFAULTS["model"]`` into the row and ``:1039`` billed it, so
an aux client that reported NOTHING still produced a priced row and a priced
turn-close rollup:

    estimate_cost keys : ['z-ai/glm-5.3-flash', 'z-ai/glm-5.3-flash']
    tokens row         : cost 0.000372, detail decision_midturn
    turn-close rollup  : $0.000372

Reviewer D2: the fabricated id had merely moved from the price cell to the NAME
cell, so a call that dispatched nothing rendered ``z-ai/glm-5.3-flash @
hermes-auxiliary`` and wrote that id into the ledger row.

The contract pinned here (driven at the call, never grep-only):

* ``usage_ledger.price_key(meta)`` is the ONE resolver every billing site uses;
  it returns the DISPATCHED id and '' when the transport reported none — a cfg
  default has no path into it;
* the NAME cell carries the dispatched id too: '' for an unreported dispatch;
* midturn ``_handle_hit`` with backend=nous + an aux client reporting nothing
  hands no cfg default to ``estimate_cost``, writes cost 0.0, and the turn-close
  rollup renders the honest unpriced marker;
* a reported model WITH a price entry bills its real rate;
* a reported model with NO entry bills 0.0 with the id named;
* a source tripwire: no cfg/DEFAULTS model substitution feeds a billing call.
"""
import ast
import json
from pathlib import Path

import pytest

from hermes_router import debug_banner as db
from hermes_router import decision as D
from hermes_router import decision_midturn as M
from hermes_router import semantic_classifier as sc
from hermes_router.core import usage_ledger as ul

SID = "r76-u2e-sess"

PRICED = "jev-latest"                 # known to the price tables (0.000116)
UNPRICED = "zz-no-such-model-9"       # id with no price row
DECOY = "z-ai/glm-5.3-flash"          # configured/default model: $0.000372
TI, TO = 2759, 102

VERDICT = json.dumps({"choice": "opt-1", "confidence": 0.8,
                      "alternatives": []})

TEXT = ("kafka or rabbitmq for the event bus? option A: kafka\n"
        "option B: rabbitmq")
OPTS = ["option A: kafka", "option B: rabbitmq"]

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# the resolver itself
# ---------------------------------------------------------------------------

def test_price_key_prefers_the_dispatched_id_and_refuses_the_fallback():
    # nos nous meta: price_model is authoritative — even when '' (unreported).
    assert ul.price_key({"price_model": "", "model": DECOY}, DECOY) == ""
    assert ul.price_key({"price_model": PRICED, "model": DECOY}) == PRICED
    # lanes that dispatch explicitly carry no price_model key; model IS the id.
    assert ul.price_key({"model": PRICED}) == PRICED
    assert ul.price_key({}) == ""
    assert ul.price_key(None) == ""


# ---------------------------------------------------------------------------
# midturn harness — drives the REAL _handle_hit through the REAL call_backend
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean():
    M.reset_midturn()
    sc._set_dispatched_aux_model("")
    for m in (db._ANCHOR_BANNERS, db._ANCHOR_SEGS, db._ANCHOR_TASKS,
              db._PARK_RECORDS):
        m.pop(SID, None)
    yield
    M.reset_midturn()
    sc._set_dispatched_aux_model("")


def _cfg():
    c = dict(M._cfg())
    c.update({"backend": "nous", "model": DECOY, M.MIDTURN_MODE_KEY: "on"})
    return c


def _drive_midturn(monkeypatch, dispatched):
    """Drive `_handle_hit` on the midturn path with the real call_backend; the
    aux client reports `dispatched` ('' = reported nothing). Returns the price
    keys estimate_cost actually received, the tokens rows, the decision-ledger
    rows and the parked banners."""
    keys, rows, ledgers, banners = [], [], [], []

    def _fake_aux(payload, timeout):
        sc._set_dispatched_aux_model(dispatched)
        return json.dumps({"choices": [{"message": {"content": VERDICT}}],
                           "usage": {"prompt_tokens": TI,
                                     "completion_tokens": TO}})

    real_est = ul.estimate_cost

    def _est(model, ti, to):
        keys.append(model)
        return real_est(model, ti, to)

    def _rec(lane, model, sid, it, ot, cost, detail="", **kw):
        rows.append({"lane": lane, "model": model, "cost": cost,
                     "detail": detail})
        return True

    monkeypatch.setattr(sc, "_hermes_aux_call", _fake_aux)
    monkeypatch.setattr(ul, "estimate_cost", _est)
    monkeypatch.setattr(ul, "record_tokens", _rec)
    monkeypatch.setattr(D, "ledger_write", lambda row: (ledgers.append(dict(row)), 1)[1])
    monkeypatch.setattr(D, "bump_counter", lambda *a, **k: None)
    monkeypatch.setattr(D, "_record_failure", lambda cfg: None)
    monkeypatch.setattr(D, "_record_success", lambda: None)
    monkeypatch.setattr(D, "consume_parked_banner", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(D, "_breaker_open", lambda cfg: False)
    monkeypatch.setattr(db, "park_anchor_banner",
                        lambda s, text, **kw: banners.append(text))

    M._handle_hit(SID, "terminal", TEXT, OPTS, _cfg(), "on")
    return keys, rows, ledgers, banners


# ---------------------------------------------------------------------------
# (a) THE GAP: midturn path with an aux client reporting nothing
# ---------------------------------------------------------------------------

def test_a_midturn_unreported_model_never_bills_the_cfg_default(monkeypatch):
    keys, rows, ledgers, _banners = _drive_midturn(monkeypatch, "")

    assert DECOY not in keys, (
        "estimate_cost received the configured default on the midturn path: %r"
        % (keys,))
    assert set(keys) <= {"", None}, keys
    tok = [r for r in rows if r["detail"] == "decision_midturn"]
    assert tok, rows
    assert tok[0]["cost"] == 0.0, tok[0]
    # D2: the name cell is the dispatched id — '' when nothing was reported.
    assert tok[0]["model"] == "", tok[0]
    assert [r for r in ledgers if r.get("choice")], ledgers
    assert all(r.get("model") == "" for r in ledgers
               if r.get("choice")), ledgers


def test_a_midturn_banner_renders_the_honest_unpriced_marker(monkeypatch):
    _keys, _rows, _ledgers, _banners = _drive_midturn(monkeypatch, "")
    rollup = M.close_turn(SID)
    assert rollup, "turn-close rollup must render"
    assert "$0.0000*" in rollup, rollup
    assert "$0.000372" not in rollup, rollup
    assert DECOY not in rollup, rollup


# ---------------------------------------------------------------------------
# (b) the turn-close rollup (:1489-:1493) is priced by the same resolver
# ---------------------------------------------------------------------------

def test_b_turn_close_rollup_costs_zero_for_an_unreported_dispatch(monkeypatch):
    keys, _rows, _ledgers, _banners = _drive_midturn(monkeypatch, "")
    rollup = M.close_turn(SID)
    assert DECOY not in keys, keys
    assert "tok %d/%d" % (TI, TO) in rollup, rollup
    assert "| $0.0000* |" in rollup, rollup


# ---------------------------------------------------------------------------
# (c) reported models: priced -> real rate; unpriced -> 0.0, id named
# ---------------------------------------------------------------------------

def test_c_midturn_priced_dispatch_bills_its_real_rate(monkeypatch):
    keys, rows, ledgers, _banners = _drive_midturn(monkeypatch, PRICED)
    tok = [r for r in rows if r["detail"] == "decision_midturn"]
    assert tok and tok[0]["cost"] == 0.000116, tok
    assert tok[0]["model"] == PRICED, tok
    assert keys and set(keys) == {PRICED}, keys
    rollup = M.close_turn(SID)
    assert "$0.000116" in rollup, rollup
    assert "*" not in rollup.split("$")[-1][:10], rollup


def test_c_midturn_unpriced_dispatch_is_zero_and_names_the_id(monkeypatch):
    keys, rows, ledgers, _banners = _drive_midturn(monkeypatch, UNPRICED)
    tok = [r for r in rows if r["detail"] == "decision_midturn"]
    assert tok and tok[0]["cost"] == 0.0, tok
    assert tok[0]["model"] == UNPRICED, tok
    assert keys and set(keys) == {UNPRICED}, keys
    rollup = M.close_turn(SID)
    assert "$0.0000*" in rollup, rollup
    assert DECOY not in rollup, rollup


# ---------------------------------------------------------------------------
# (d) source tripwire — narrow, on billing call arguments only
# ---------------------------------------------------------------------------

BILLING_SITES = (
    "decision.py",
    "decision_midturn.py",
    "render_payload.py",
    "reflex_lane.py",
    "semantic_classifier.py",
    "anchor_exec.py",
    "router.py",
    "intent_classifier.py",
    "api/commands_runtime.py",
)


def _is_cfg_expr(node) -> bool:
    """A receiver that is the plugin/lane CONFIG (cfg, config, <x>_cfg)."""
    return isinstance(node, ast.Name) and (
        node.id in ("cfg", "config") or node.id.endswith("_cfg"))


def _cfg_model_substitution(node) -> bool:
    """True when this expression substitutes a CONFIGURED/DEFAULT model id.

    Narrow by construction: `meta.get("model")` and `entry.get("model")` are
    dispatched-id reads (router.py:224 dispatches exactly that expression), so
    only a cfg/config receiver or a DEFAULTS["model"] subscript is a
    substitution. This is the class the unit forbids, not every `.get("model")`.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr == "get" and sub.args \
                and _is_cfg_expr(sub.func.value) \
                and isinstance(sub.args[0], ast.Constant) \
                and sub.args[0].value == "model":
            return True
        if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) \
                and sub.value.id == "DEFAULTS":
            sl = sub.slice
            if isinstance(sl, ast.Constant) and sl.value == "model":
                return True
    return False


def test_d_no_cfg_model_substitution_feeds_a_billing_call():
    offenders = []
    for rel in BILLING_SITES:
        src = (REPO / rel).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None)
            if name not in ("estimate_cost", "record_tokens"):
                continue
            for arg in node.args:
                if _cfg_model_substitution(arg):
                    offenders.append("%s:%d %s" % (rel, node.lineno, name))
    assert not offenders, (
        "a configured/default model substitution feeds a billing call: %r"
        % (offenders,))
