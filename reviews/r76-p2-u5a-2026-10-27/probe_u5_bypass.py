import sys, json
sys.path.insert(0, "/opt/data/plugins")
sys.path.insert(0, "/opt/hermes")
from hermes_router import debug_banner as db

TRIG = "consult failed | empty response"
def r(lane="frontier-anchor", trig=TRIG, model="test/model-a", ep="", ti=0, to=0, cost=0.0):
    return db.format_banner(lane, trig, model, ep, ti, to, cost, 0.0, metadata_only=True)

out = {}
for lane in ("frontier-anchor", "not-a-lane", "aux", "flash", "uncensored-render", "shadow", "decision", "", "consult-pre"):
    out["lane:" + lane] = r(lane=lane)
out["trigger:consult"] = r(trig="consult")
out["trigger:completion_audit"] = r(trig="completion_audit")
out["endpoint:url"] = r(ep="openrouter://api.secret-host.example/v1/very-secret?key=abc")
out["endpoint:empty"] = r(ep="")
out["model:url"] = r(model="provider://host.example.com/v1/secret-path?key=abc")
out["unpriced_tokens"] = r(ti=120, to=40, cost=0.0)
out["normal_default_path"] = db.format_banner("frontier-anchor", "midturn", "m1", "openrouter://api.host.example/v1", 10, 5, 0.000001, 0.4, level=1)
for k, v in out.items():
    print("%-28s %r" % (k, v))
print("url_ep_equals_empty:", out["endpoint:url"] == out["endpoint:empty"])
