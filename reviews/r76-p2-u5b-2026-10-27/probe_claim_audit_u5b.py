"""U5b claim audit — drive the metadata_only shape and recompute every clause.

Usage: python3 probe_claim_audit_u5b.py <plugins-parent-dir> <label>
The first arg is the directory CONTAINING hermes_router/ (a worktree parent), so the
package under test is pinned and asserted by __file__.
Not a diff read: the endpoint and usage args are instrumented objects whose
__str__ / __float__ / split record invocation.
"""
import sys, json, hashlib

PARENT = sys.argv[1]
LABEL = sys.argv[2]
sys.path.insert(0, PARENT)
sys.path.insert(0, "/opt/hermes")

import hermes_router
assert hermes_router.__file__.startswith(PARENT), hermes_router.__file__

from hermes_router import debug_banner as db

TRIG = "consult failed | empty response"
SECRET = "openrouter://api.secret-host.example/v1/very-secret?key=abc"
out = {"label": LABEL, "package": hermes_router.__file__}


def r(endpoint="", ti=0, to=0, cost=0.0, lane="frontier-anchor", model="test/model-a"):
    return db.format_banner(lane, TRIG, model, endpoint, ti, to, cost, 0.0,
                            metadata_only=True)


# --- 1. endpoint read: __str__ invocation recorded -----------------------------
events = []


class S(str):
    def split(self, *a, **k):
        events.append(("split", a, k))
        return super().split(*a, **k)


class EPObj:
    def __str__(self):
        events.append("str")
        return S(SECRET)


base = r()
line = r(endpoint=EPObj())
out["1a_endpoint_str_invoked"] = "str" in events
out["1b_redaction_split_invoked"] = any(e[0] == "split" for e in events
                                        if isinstance(e, tuple))
out["1c_events"] = [e if isinstance(e, str) else e[0] for e in events]
out["1d_line_with_secret_ep"] = line
out["1e_line_with_empty_ep"] = base
out["1f_byte_identical"] = line == base
out["1g_sha_secret_ep"] = hashlib.sha256(line.encode()).hexdigest()
out["1h_sha_empty_ep"] = hashlib.sha256(base.encode()).hexdigest()
out["1i_line_has_endpoint_cell"] = ("@" in line) or ("secret-host" in line)
out["1j_line_has_usage_cell"] = ("tok " in line) or ("$" in line)


# --- 2. usage args converted before the branch --------------------------------
reads = []


class NumIn(int):
    def __int__(self):
        reads.append("tokens_in")
        return int.__int__(self)


class NumOut(int):
    def __int__(self):
        reads.append("tokens_out")
        return int.__int__(self)


class Cost(float):
    def __float__(self):
        reads.append("est_cost")
        return float.__float__(self)


out["2_pre_conversion"] = sorted(reads)
r(ti=NumIn(120), to=NumOut(40), cost=Cost(0.5))
out["2a_usage_args_read_on_metadata_only"] = sorted(reads)
out["2b_unpriced_marker_present"] = "$" in r(ti=120, to=40, cost=0.0)


# --- 3. the remaining clauses of the corrected sentence -----------------------
out["3a_invalid_lane"] = repr(r(lane="not-a-lane"))
out["3b_forbidden_aux"] = repr(r(lane="aux"))
out["3c_label_shadow"] = r(lane="uncensored-render")
out["3d_normal_shape_redacts"] = db.format_banner(
    "decision", "midturn", "jev-latest", SECRET, 10, 5, 0.000001, 0.3, level=1)


# --- 4. what the shipped docstring actually says ------------------------------
doc = " ".join((db.format_banner.__doc__ or "").split())
out["4_docstring_flat"] = doc
out["4a_claims_value_read_then_dropped"] = "values are read, then dropped, not skipped" in doc
out["4b_claims_old_mechanism"] = "before the endpoint is ever read" in doc

# pointer audit: do the docstring's own line citations resolve in THIS tree?
src = open(hermes_router.__file__.replace("__init__.py", "debug_banner.py")).read()
lines = src.splitlines()


def at(n):
    return lines[n - 1].strip() if 0 < n <= len(lines) else "<out of range>"


out["5_pointers"] = {
    "ep_s_actual": next((i for i, l in enumerate(lines, 1)
                         if l.strip() == 'ep_s = str(endpoint or "?")'), None),
    "conversion_actual": next((i for i, l in enumerate(lines, 1)
                               if l.strip().startswith("ti = int(tokens_in)")), None),
    "branch_actual": next((i for i, l in enumerate(lines, 1)
                           if l.strip() == "if metadata_only:"), None),
    "cited_180": at(180), "cited_181": at(181), "cited_183": at(183),
    "cited_170": at(170), "cited_172": at(172),
    "cited_228": at(228), "cited_224": at(224),
}

print(json.dumps(out, indent=1, ensure_ascii=False))
