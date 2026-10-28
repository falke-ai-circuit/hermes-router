"""Differential driver: drives the live reflex/weights path and dumps every emitted byte.

Run with PYTHONPATH=<worktree parent dir> so `hermes_router` resolves to that tree.
No network: _transport is stubbed. The telemetry seam is captured, not written to disk
(so `log_route` rows are compared exactly as the lane hands them over).
"""
import hashlib, json, sys, os

import hermes_router
from hermes_router import reflex_weights as rw
from hermes_router import reflex_lane as rl
from hermes_router.core import telemetry as tel

ROWS = []
tel.log_route_orig = tel.log_route
def _cap(event, **kw):
    ROWS.append({"event": event, "kw": kw})
tel.log_route = _cap

BODY = ("Traceback (most recent call last):\n  File \"/opt/data/app/src/render.py\", line 12\n"
        "ImportError: cannot import name 'cache' from /opt/data/app/src/cache.py\n"
        "while building the template cache for the report page")
DIST = {"answers": {"action": {"probabilities": {"a1": 0.8, "a2": 0.12, "a3": 0.05, "a4": 0.03},
                             "confidence": 0.8},
                    "scope": {"score": 1}, "rigour": {"score": 4}}}

out = {"tree": hermes_router.__file__, "checks": {}}

# 1. retained API: cfg_for over a config matrix
CFGS = [
    None, {}, {"hermes_router": {}},
    {"hermes_router": {"reflex_weights": {"enabled": True}}},
    {"hermes_router": {"reflex_weights": {"enabled": True, "min_top": 0.6, "min_margin": 0.2,
                                          "every_call": False, "agree_silent": False,
                                          "integrate": False, "block_chars": 700,
                                          "register": "instinct", "shape": "evidence",
                                          "reflex_timeout_seconds": 5}}},
    {"uncensored_router": {"reflex_weights": {"enabled": True, "max_options": 5}}},
]
out["checks"]["cfg_for"] = [rw.cfg_for(c) for c in CFGS]
out["checks"]["cfg_for_has_min_gap"] = ["min_gap" in (rw.cfg_for(c) or {}) for c in CFGS]

# 2. retained API: _note
rw._note(reason="probe", top=0.8)
out["checks"]["note_LAST"] = dict(rw.LAST)

# 3. lane cfg (the min_gap setdefault site)
out["checks"]["lane_cfg"] = [rl._cfg(c) for c in CFGS]
out["checks"]["lane_cfg_has_min_gap"] = ["min_gap" in rl._cfg(c) for c in CFGS]

# 4. drive the midturn seam: fire() -> parked block -> inject_pass() -> request bytes
rl._transport = lambda *a, **k: DIST
rl.clear()
block = rl.fire("probe-s1", "terminal", BODY, CFGS[3], ask="fix the renderer")
out["checks"]["fire_block"] = block
out["checks"]["fire_block_sha"] = hashlib.sha256(block.encode()).hexdigest()
req = {"messages": [{"role": "user", "content": "hi"}]}
inc = rl.inject_pass(req, "probe-s1")
out["checks"]["inject_ok"] = inc
out["checks"]["request_json"] = json.dumps(req, sort_keys=True)
out["checks"]["request_sha"] = hashlib.sha256(
    json.dumps(req, sort_keys=True).encode()).hexdigest()
out["checks"]["inject_twice"] = rl.inject_pass(req, "probe-s1")

# 5. every-call seam on a live-looking request
rl.clear()
req2 = {"messages": [{"role": "user", "content": "please fix the template cache in render.py"},
                     {"role": "assistant", "content": "I will read /opt/data/app/src/render.py first"},
                     {"role": "tool", "content": BODY},
                     {"role": "user", "content": "continue"}]}
ran = rl.run_on_request(req2, "probe-s2", CFGS[3])
out["checks"]["run_on_request"] = ran
out["checks"]["request2_json"] = json.dumps(req2, sort_keys=True)
out["checks"]["request2_sha"] = hashlib.sha256(
    json.dumps(req2, sort_keys=True).encode()).hexdigest()

# 6. telemetry rows as emitted
def scrub(o):
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in sorted(o.items()) if k not in ("session_id",)}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o
out["checks"]["telemetry_rows"] = [scrub(r) for r in ROWS]

# 7. symbol presence census at this tree
out["checks"]["has_symbols"] = {s: hasattr(rw, s) for s in
                                ("parse", "render", "build_request", "extract",
                                 "summarise", "_as_prob", "_clip", "_note", "cfg_for")}
print(json.dumps(out, indent=1, sort_keys=True, default=str))
