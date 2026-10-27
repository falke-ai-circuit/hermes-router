
import json, os, sys
from hermes_router import decision as D, render_payload as rp
from hermes_router import semantic_classifier as sc
from hermes_router import debug_banner as db
from hermes_router import decision_midturn as M
from hermes_router.core import usage_ledger as ul

TI, TO = 2759, 102
DECOY = "z-ai/glm-5.3-flash"
VERDICT = json.dumps({"choice": "opt-1", "confidence": 0.8, "alternatives": []})

keys, rows, ledgers, banners = [], [], [], []
real_est = ul.estimate_cost

def est(m, ti, to):
    keys.append(m)
    return real_est(m, ti, to)

def rec(lane, model, sid, it, ot, cost, detail="", **kw):
    rows.append({"lane": lane, "model": model, "cost": cost, "detail": detail})
    return True

ul.estimate_cost = est
ul.record_tokens = rec
D.ledger_write = lambda row: (ledgers.append(dict(row)), 1)[1]
D.bump_counter = lambda *a, **k: None
D._record_failure = lambda cfg: None
D._record_success = lambda: None
D.consume_parked_banner = lambda *a, **k: None
D._breaker_open = lambda cfg: False
db.park_anchor_banner = lambda s, text, **kw: banners.append(text)
M._dec.ledger_write = D.ledger_write
M._dec._record_success = D._record_success
M._dec._breaker_open = lambda cfg: False

CUR = {"d": ""}
def fake_aux(payload, timeout):
    sc._set_dispatched_aux_model(CUR["d"])
    return json.dumps({"choices": [{"message": {"content": VERDICT}}],
                       "usage": {"prompt_tokens": TI, "completion_tokens": TO}})

sc._hermes_aux_call = fake_aux

def cfg():
    c = M._cfg()
    c.update({"backend": "nous", "model": DECOY, M.MIDTURN_MODE_KEY: "on"})
    return c

TEXT = ("kafka or rabbitmq for the event bus? option A: kafka\n"
        "option B: rabbitmq")

for case, dispatched in (("NONE-reported", ""), ("PRICED", "jev-latest"),
                         ("UNPRICED", "zz-no-such-model-9")):
    keys.clear(); rows.clear(); banners.clear(); ledgers.clear()
    CUR["d"] = dispatched
    sc._set_dispatched_aux_model(dispatched)
    M.reset_midturn()
    M._handle_hit("sess-" + case, "terminal", TEXT,
                  ["option A: kafka", "option B: rabbitmq"], cfg(), "on")
    print("=" * 70)
    print("MIDTURN CASE:", case, "| dispatched_aux_model()=", repr(sc.dispatched_aux_model()))
    print("  estimate_cost keys received :", keys)
    print("  tokens rows                 :", rows)
    print("  ledger row model            :", [r.get("model") for r in ledgers])
    print("  parked banner               :", banners[-1].replace("\n", " \\n ") if banners else "<none>")
    closed = M.close_turn("sess-" + case)
    print("  TURN-CLOSE ROLLUP           :", (closed or "<none>").replace("\n", " \\n "))
