import json, sys
from hermes_router import decision as D, render_payload as rp
from hermes_router import semantic_classifier as sc
from hermes_router import debug_banner as db
from hermes_router.core import usage_ledger as ul

TI, TO = 2759, 102
DECOY = "z-ai/glm-5.3-flash"
VERDICT = json.dumps({"choice": "opt-1", "confidence": 0.8, "alternatives": []})

keys, rows, banners = [], [], []
real_est = ul.estimate_cost

def est(m, ti, to):
    keys.append(m)
    return real_est(m, ti, to)

def rec(lane, model, sid, it, ot, cost, detail="", **kw):
    rows.append({"lane": lane, "model": model, "cost": cost, "detail": detail})
    return True

ul.estimate_cost = est
ul.record_tokens = rec
D.ledger_write = lambda row: 1
D.bump_counter = lambda *a, **k: None
D._record_failure = lambda cfg: None
D._record_success = lambda: None
D.consume_parked_banner = lambda *a, **k: None
db.park_anchor_banner = lambda s, text, **kw: banners.append(text)
D.render_decision_banner = rp.render_decision_banner  # keep real

def body(dispatched):
    sc._set_dispatched_aux_model(dispatched)
    return json.dumps({"choices": [{"message": {"content": VERDICT}}],
                       "usage": {"prompt_tokens": TI, "completion_tokens": TO}})

CUR = {"d": ""}
def fake_aux(payload, timeout):
    return body(CUR["d"])
sc._hermes_aux_call = fake_aux

def env(sid):
    return {"schema": D.ENVELOPE_SCHEMA, "session_id": sid, "task_id": "t-u2d",
            "ask": "redis or memcached? weigh the tradeoffs", "fork_class": "generic",
            "options": [{"id": "opt-1", "label": "redis"}, {"id": "opt-2", "label": "memcached"}]}

def cfg():
    return dict(D._cfg(), backend="nous", model=DECOY)

for case, dispatched in (("NONE-reported", ""), ("PRICED", "jev-latest"),
                         ("UNPRICED", "zz-no-such-model-9")):
    keys.clear(); rows.clear(); banners.clear()
    CUR["d"] = dispatched
    sid = "probe-" + case
    D._v3_worker(env(sid), {"session_id": sid, "task_id": "t-u2d", "trigger": "pre"},
                 cfg(), lambda e, **f: None)
    tok = [r for r in rows if r["detail"] == "decision_v3"]
    print("=" * 70)
    print("CASE:", case, "| dispatched_aux_model()=", repr(sc.dispatched_aux_model()))
    print("  estimate_cost keys received :", keys)
    print("  tokens rows                 :", tok)
    print("  banner                      :", banners[-1].replace("\n", " \\n ") if banners else "<none>")
    for k in set(keys):
        print("  price(%r) = %r" % (k, real_est(k, TI, TO)))
    print("  price of cfg decoy         = %r" % real_est(DECOY, TI, TO))
