"""U5 byte-identity probe: drive the REAL empty-consult failure path and capture
the text handed to park_anchor_banner. Run with sys.path[0] = the tree to test."""
import sys, json

TREE = sys.argv[1]
sys.path.insert(0, TREE)
sys.path.insert(0, "/opt/hermes")

import hermes_router
assert hermes_router.__file__.startswith(TREE), hermes_router.__file__

import hermes_router.anchor_exec as ax
from hermes_router import anchor_chain, debug_banner, router_core
from hermes_router import decision as _dec

MODEL = sys.argv[2] if len(sys.argv) > 2 else "test/model-a"


class EPObj:
    def __init__(self, model):
        self.model = model
        self.endpoint = "openrouter"


def drive(model=MODEL):
    parked = {}
    rec = {"task_id": "t-u5", "lane": "complexity", "mode": router_core.MODE_CONSULT,
           "endpoint": EPObj(model), "route_id": "r-u5", "orientation": True,
           "reason": "complexity_orientation"}
    router_core.pending_model_swap = lambda sid: rec
    router_core.clear_anchor_backoff = lambda *a, **k: None
    router_core.store_consult_result = lambda *a, **k: None
    chain = anchor_chain.load_anchor_chain()
    anchor_chain.load_anchor_chain = lambda *a, **k: chain
    anchor_chain.cap_check = lambda *a, **k: (True, 0.0, 0.01)
    anchor_chain.estimate_call_cost = lambda *a, **k: 0.01
    anchor_chain.record_spend = lambda *a, **k: None
    _dec.ledger_write = lambda *a, **k: 1
    ax.anchored_call = lambda endpoint, payload: (None, None, None, None)
    ax._retry_anchored_async = lambda *a, **k: None
    debug_banner.park_anchor_banner = lambda sid, text, **kw: parked.setdefault("text", text)
    ax.maybe_execute_anchored(
        "api_u5", {"messages": [{"role": "user",
                                 "content": "Analyse how the router resolves a model alias"}],
                   "max_tokens": 200})
    return parked


for model in (MODEL, "", "provider://host.example.com/v1/secret-path?key=abc"):
    p = drive(model)
    txt = p.get("text")
    print(json.dumps({"model": model, "parked": txt,
                      "sha256": __import__("hashlib").sha256((txt or "").encode()).hexdigest(),
                      "len": len(txt or "")}))
