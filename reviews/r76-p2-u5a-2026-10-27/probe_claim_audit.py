"""U5a claim audit: test every assertion the U5a commit added against driven behaviour.

Not a diff read. Each numbered block below corresponds to a sentence in the commit's
added text (anchor_exec.py comment, debug_banner.py docstring/branch comment, test
module docstring, commit body) and reports MATCH / MISMATCH with the measurement.
"""
import sys, hashlib, json
sys.path.insert(0, "/opt/data/plugins")
sys.path.insert(0, "/opt/hermes")

from hermes_router import debug_banner as db

TRIG = "consult failed | empty response"
SECRET = "openrouter://api.secret-host.example/v1/very-secret?key=abc"


def r(**kw):
    base = dict(lane="frontier-anchor", trigger=TRIG, model="test/model-a",
                endpoint="", tokens_in=0, tokens_out=0, est_cost=0.0, latency_s=0.0)
    base.update(kw)
    return db.format_banner(base.pop("lane"), base.pop("trigger"), base.pop("model"),
                            base.pop("endpoint"), base.pop("tokens_in"),
                            base.pop("tokens_out"), base.pop("est_cost"),
                            base.pop("latency_s"), metadata_only=True)


out = {}

# ---- 1. the exact measurement the brief asks for -------------------------------
a = r(endpoint=SECRET)
b = r(endpoint="")
out["1a_bytes_secret_ep"] = a
out["1b_bytes_empty_ep"] = b
out["1c_byte_identical"] = a == b
out["1d_sha_secret"] = hashlib.sha256(a.encode()).hexdigest()
out["1e_sha_empty"] = hashlib.sha256(b.encode()).hexdigest()

# ---- 2. "lane VALIDATION applies (FORBIDDEN_LANES or unknown lane returns '')" --
out["2a_invalid_lane"] = repr(r(lane="not-a-lane"))
out["2b_forbidden_aux"] = repr(r(lane="aux"))
out["2c_forbidden_flash"] = repr(r(lane="flash"))
out["2d_valid_frontier"] = repr(r(lane="frontier-anchor"))

# ---- 3. "the lane label comes from lane_label()" ------------------------------
out["3a_label_shadow"] = repr(r(lane="uncensored-render"))
out["3b_label_decision"] = repr(r(lane="decision"))

# ---- 4. "the trigger literal is not an _END_LABELS key, so that map is a no-op" -
out["4a_trigger_literal_verbatim"] = (TRIG in a)
out["4b_mapped_trigger_still_maps"] = repr(r(trigger="consult"))

# ---- 5. MECHANISM CLAIM: "metadata_only returns BEFORE the endpoint is read" ---
# If true, the endpoint argument is never touched on this path. Instrument it.
class Probe:
    def __init__(self, tag, store):
        self.tag, self.store = tag, store
    def __str__(self):
        self.store.append(self.tag)
        return self.tag

reads = []
ep_probe = Probe("openrouter://api.secret-host.example/v1", reads)
_cost_reads = []

class CostProbe(float):
    def __float__(self):
        _cost_reads.append("est_cost")
        return 0.0

r(endpoint=ep_probe, est_cost=CostProbe(0.0), tokens_in=120, tokens_out=40)
out["5a_endpoint_str_called_on_metadata_only"] = reads
out["5b_est_cost_read_on_metadata_only"] = _cost_reads

# ---- 6. "endpoint REDACTION does NOT apply": does redaction CODE still run? ----
# ep_s is host-redacted at line 181-182 before the metadata_only return. Prove the
# redaction ran by capturing what the renderer would have had if ep were used:
src = open("/opt/data/plugins/hermes_router/debug_banner.py").read()
i_val = src.index("if lane in FORBIDDEN_LANES")
i_ep = src.index('ep_s = str(endpoint or "?")')
i_red = src.index('if "://" in ep_s:')
i_mo = src.index("if metadata_only:")
out["6_line_order_lane_validation"] = i_val
out["6_line_order_ep_read"] = i_ep
out["6_line_order_ep_redaction"] = i_red
out["6_line_order_metadata_only_branch"] = i_mo
out["6_ep_read_before_mo_branch"] = i_ep < i_mo
out["6_redaction_before_mo_branch"] = i_red < i_mo

# ---- 7. "no unpriced-model marker can appear" ---------------------------------
out["7_unpriced_tokens_no_cost_cell"] = "$0.0000*" not in r(tokens_in=120, tokens_out=40)
out["7b_cost_cell_absent"] = ("tok " not in a)

# ---- 8. documented boundary: docstring sentence the pin asserts on ------------
doc = db.format_banner.__doc__ or ""
out["8a_doc_has_exact_sentence"] = "Endpoint REDACTION does NOT apply" in doc
out["8b_doc_says_returns_before_endpoint_read"] = "returns before the endpoint is ever read" in doc

# ---- 9. "A caller that needs host redaction must use the normal shape" ---------
out["9_normal_shape_redacts_host"] = db.format_banner(
    "frontier-anchor", "midturn", "m1", SECRET, 10, 5, 0.000001, 0.4, level=1)

for k, v in out.items():
    print("%-46s %r" % (k, v))
