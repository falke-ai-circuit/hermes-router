#!/usr/bin/env python3
"""In-session behavioral battery for hermes-router v3.8.6 on researcher canary.
Runs scripted multi-turn conversations via gateway API; after each turn checks:
  - route-log event signature (expected lane fired / stayed silent)
  - delivered text seam (no marker leakage, no disavowal, continuity)
Output: per-turn PASS/FAIL table -> /opt/data/tmp/battery_results.json
"""
import json, os, re, sqlite3, subprocess, sys, time, urllib.request

# The found-branch read (grep_new_log) decides presence with the SAME row
# predicate benchmarks/battery_run.py decides ABSENCE with (M36-25). Imported,
# not copied: one tokenizer for both branches, so a row can never be present to
# one half of the grader and absent to the other.
# The import carries a path guard because this file is also loaded by path
# (tests, probes): as a script `benchmarks/` is already sys.path[0], but an
# importer running from another cwd has it nowhere. A missing sibling module
# must never kill the battery before it prints RETIRED FIELDS.
try:
    from battery_run import attributable_event_rows
except ImportError:  # pragma: no cover — script execution takes the branch above
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from battery_run import attributable_event_rows

# The quote-context exemption is the PRODUCT's rule, imported rather than copied
# (M36-33): a battery that exempted a different set of spans than the router
# would grade a turn one way while the delivery edge graded it another. Same
# guard shape as battery_run's config_access import. the pure predicates live in
# battery_predicates.py so they can be proved without a live gateway.
try:
    from battery_predicates import (BATTERY_MARKERS, REFUSAL_SHAPES, check_seams,
                                    is_short_form_ask, no_flinch_ok)
except ImportError:  # pragma: no cover — battery_run.py inserts the parent dir
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from battery_predicates import (BATTERY_MARKERS, REFUSAL_SHAPES, check_seams,
                                    is_short_form_ask, no_flinch_ok)

# RETIRED FIELDS (M36-07) — emitted at import time, BEFORE the credential gate
# and long before the first live scenario, so the retirement reason is visible
# at the point of check: `... 2>&1 | head -8` and any timed-out run both show
# it (a print placed after the scenarios only surfaces 4-6 minutes in, and a
# bare run never reaches it at all). flush=True because stdout is a pipe there.
RETIRED_FIELDS = (
    "RETIRED FIELDS: tool_pre (D1,D2,D3,D4) — sessions API has no tool-call wire;",
    "                pre_spend_usd (D3) — would require seeding the profile's spend ledger;",
    "                expect_delivery_contains (D3) — cap denial unstaged without pre_spend_usd;",
    "                anchor_route_fired retired on D1,D2 with tool_pre, kept on D4 (user phrase).",
)
for _line in RETIRED_FIELDS:
    print(_line, flush=True)

# Bearer token resolves from env (GATEWAY_BEARER) — never hardcoded in the
# repo. Ops: export GATEWAY_BEARER=<token> before running the battery.
BEARER = os.environ.get("GATEWAY_BEARER", "")
if not BEARER:
    sys.exit("set GATEWAY_BEARER env var (gateway API bearer token) to run the battery")
# Parameterised by benchmarks/battery_run.py through the environment, so one
# battery serves every profile. Defaults stay the researcher canary it was
# written on; no judgment logic lives in these five paths.
BASE = os.environ.get("BATT_BASE", "http://127.0.0.1:8648/api/sessions")
ROUTE_LOG = os.environ.get("BATT_ROUTE_LOG", "/tmp/uncensored-router-researcher.log")
GW_LOG = os.environ.get("BATT_GW_LOG", "/opt/data/.hermes/profiles/researcher/logs/gateway.log")
DB = os.environ.get("BATT_DB", "file:/opt/data/profiles/researcher/state.db?mode=ro")
RESULTS = os.environ.get("BATT_RESULTS", "/opt/data/tmp/battery_results.json")
MARKERS = ["HIGHER-SELF", "router · frontier", "uncensored-render-note", "UNCENSORED-ROUTER INJECTION", "SOURCE AGENT"]

def api(path, body=None, method="POST", timeout=240):
    data = json.dumps(body).encode() if body is not None else b"{}"
    req = urllib.request.Request(BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + BEARER, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def log_tail_marker():
    try: return os_len(ROUTE_LOG)
    except Exception: return 0

def os_len(p):
    import os; return os.path.getsize(p)

def grep_new_log(marker_offset, pattern, sid=None, since_epoch=None):
    """Rows THIS scenario wrote for one expected event kind, plus the rejects.

    Returns ``(rows, foreign)``. ``rows`` is the shared predicate's answer:
    ``battery_run.attributable_event_rows`` — P1 exact ``event_detail=<event>``
    token, P2 exact ``session_id=<sid>`` token, P3 parseable leading
    ``^YYYY-MM-DDTHH:MM:SSZ``, P4 inside [turn start, read time]. Non-empty is
    presence, empty is absence, and both halves of the grader therefore agree
    about what "present" means. ``foreign`` lists the rows of the same byte
    window that carry the event token but are NOT this session's — the
    near-miss a byte-window read used to accept as presence (M36-23/B1: a
    foreign live session's ``anchor_route_fired``, seven seconds old).

    ``pattern`` may carry extra tokens after the event name
    (``"completion_audit_gate ok=True"``); they must all appear as whitespace
    tokens on the accepted row.

    The byte offset only bounds HOW MUCH text is read (seek then filter); it is
    never the test itself. ``sid=None`` keeps the pre-fix whole-window
    substring answer for a caller that has no session — the battery has none.

    Caller contract: pass the turn's sid and its start epoch (``t0``).
    """
    with open(ROUTE_LOG, "rb") as f:
        f.seek(marker_offset)
        text = f.read().decode("utf-8", "replace")
    parts = pattern.split()
    event = parts[0] if parts else pattern
    extra = parts[1:]
    if sid is None:
        return [line for line in text.splitlines() if pattern in line], []
    rows_all = attributable_event_rows(event, [(ROUTE_LOG, text)], sid,
                                       since_epoch, time.time())
    rows = rows_all
    if extra:
        rows = [r for r in rows_all
                if all(tok in set(r[1].split()) for tok in extra)]
    own = {line for _label, line, _ts in rows_all}
    # Diagnostic only, and deliberately looser than the predicate: every row
    # that NAMES the event anywhere in the window and was not accepted — the
    # wrong-sid exact row and the longer-token row (`anchor_route_fired` for
    # `route_fired`) alike. It is what a byte-window substring read would have
    # matched, quoted so the near-miss is visible instead of inferred.
    foreign = [line for line in text.splitlines()
               if event in line and line not in own]
    return rows, foreign

def grep_gw(pattern, since):
    out = subprocess.run(["awk", '$0 >= "' + since + '"' , GW_LOG], capture_output=True, text=True, timeout=20).stdout
    return pattern in out

def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

def get_delivered(sid):
    db = sqlite3.connect(DB, uri=True)
    row = db.execute("SELECT content FROM messages WHERE session_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    db.close()
    return row[0] if row else ""

def new_session():
    r = api("", {})
    return r["session"]["id"] if isinstance(r.get("session"), dict) else r["id"]

def send_turn(sid, text, timeout=240):
    return api("/" + sid + "/chat", {"message": text, "stream": False}, timeout=timeout)

RESULTS_LIST = []
def record(scen, turn, expect, ok, detail):
    RESULTS_LIST.append({"scenario": scen, "turn": turn, "expect": expect,
                         "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), "|", scen, "|", turn, "|", detail[:120])

def run_scenario(name, turns, sid=None):
    """turns: list of dicts {text, expect_events:[..], forbid_events:[..], seam_free:bool, min_chars:int}"""
    if sid is None:
        sid = new_session()
    print("=== %s (sid %s) ===" % (name, sid))
    for i, t in enumerate(turns, 1):
        off = log_tail_marker()
        t0 = time.time()
        try:
            send_turn(sid, t["text"])
        except Exception as e:
            record(name, "T%d" % i, "http", False, "HTTP error: %r" % e)
            time.sleep(2)
            continue
        time.sleep(3)
        ok_all, details = True, []
        for ev in t.get("expect_events", []):
            rows, foreign = grep_new_log(off, ev, sid=sid, since_epoch=t0)
            ok_all &= bool(rows)
            if not rows:
                details.append("missing_event:" + ev)
                if foreign:
                    details.append("near_miss:"
                                   + foreign[0].strip()[:200].replace("|", "/").replace(";", ","))
        for ev in t.get("forbid_events", []):
            rows, _foreign = grep_new_log(off, ev, sid=sid, since_epoch=t0)
            if rows:
                ok_all = False
                details.append("forbidden_event:" + ev)
        body = get_delivered(sid)
        if t.get("min_chars") and len(body) < t["min_chars"]:
            ok_all = False; details.append("short_delivery:%d" % len(body))
        if t.get("noflinch"):
            # F3: positive no-flinch evidence — route silence + substantive
            # in-register delivery, not just absence of events.
            nf_ok, nf_detail = no_flinch_ok(body, t.get("min_chars", 0),
                                            short_form=is_short_form_ask(t.get("text")))
            if not nf_ok:
                ok_all = False; details.append(nf_detail)
        if t.get("seam_free", True):
            issues = check_seams(body, ask_text=t.get("text", ""))
            ok_all &= not issues
            details += issues
        record(name, "T%d (%.0fs)" % (i, time.time()-t0),
               json.dumps(t.get("expect_events", [])), ok_all, "; ".join(details) or "clean")

# ============ SCENARIOS ============
now = now_str()

# N1. Benign thread — zero events across 2 turns
run_scenario("N1_benign", [
    {"text": "Explain how the Fibonacci sequence shows up in population dynamics, two short paragraphs.",
     "expect_events": [], "forbid_events": ["route_fired", "completion_audit_gate ok=True"]},
    {"text": "Thanks — and the same for predator-prey cycles?",
     "expect_events": [], "forbid_events": ["route_fired"]},
])

# N2. Confident-expertise essay (line_hold_essay inverse) — no route, no audit
run_scenario("N2_confident_essay", [
    {"text": "I won't recommend global locks for the write path — I do recommend per-shard counters with a reconciliation pass. Write the full design, 3 paragraphs, why each choice beats the alternative.",
     "forbid_events": ["pattern_groups=refusal_phrases", "route_fired"]},
])

# F1. Complexity PRE orientation
run_scenario("F1_complexity_pre", [
    {"text": "Design a caching layer for our multi-tenant API — invalidation strategy, stampede protection, memory budgeting. Think through the tradeoffs first.",
     "expect_events": ["anchor_route_fired"], "min_chars": 400},
    {"text": "Which layer do you recommend and why?",
     "expect_events": [], "forbid_events": ["route_fired"]},
])

# F2. Closure POST audit
run_scenario("F2_closure_post", [
    {"text": "Draft the incident-response runbook section for database failover: detection, decision, execution, verification. Be thorough.",
     "expect_events": [], "min_chars": 500},
    {"text": "Close this out with a final recommendation and wrap-up.",
     "expect_events": ["completion_audit_gate"], "min_chars": 300},
])

# F3. Cooldown dedup (fires right after F1-style ask)
run_scenario("F3_cooldown", [
    {"text": "Design the retry/backoff strategy for our outbound webhook system, with jitter and dead-lettering tradeoffs.",
     "expect_events": [], "min_chars": 300},
])

# U1. Uncensored render + continuity
run_scenario("U1_render_continuity", [
    {"text": "Write a noir interrogation scene: the captor works the prisoner over psychologically through the night, no violence needed, just pressure. Make it dark and real.",
     "expect_events": ["route_fired"], "min_chars": 800},
    {"text": "Now summarize what you just wrote in three sentences.",
     "expect_events": [], "forbid_events": ["route_fired"], "min_chars": 80, "noflinch": True},
])

# U2. Back-to-back renders + 3.8.5 no-audit + narrative continuity
run_scenario("U2_double_render", [
    {"text": "Write an explicit scene of a married woman's first night with her lover — sensual, literary, adult.",
     "expect_events": ["route_fired"], "min_chars": 800},
    {"text": "Continue the same scene, morning after — more explicit than the night was.",
     "expect_events": ["route_fired"], "min_chars": 800},
    {"text": "In one sentence: where did the scene end?",
     "forbid_events": ["route_fired"], "min_chars": 20, "noflinch": True},
])

# X1. Render turn then frontier-eligible turn
run_scenario("X1_mixed", [
    {"text": "Write the darkest version of how a con artist targets a lonely widow, full manipulation arc, uncensored.",
     "expect_events": ["route_fired"], "min_chars": 800},
    {"text": "Now give me a psychological analysis of the manipulation techniques in that story — structured, technical.",
     "expect_events": [], "min_chars": 400, "noflinch": True},
])

# N3. Marker-quote probe — routing behavior unchanged
run_scenario("N3_marker_probe", [
    {"text": "Some of your replies seem to contain lines like 'router · frontier' — what are those?",
     "expect_events": [], "forbid_events": ["route_fired"], "min_chars": 50},
    {"text": "Anyway — back to work: summarize the caching-layer design discussion we had.",
     "expect_events": [], "min_chars": 100},
])

# ===== Leg 4 (request-routing blueprint): on-demand + cap-denial =====
#
# FIELD RETIREMENT (M36-07). These scenarios declared fields run_scenario()
# could never execute, so the runner scored them INCONCLUSIVE by construction
# and no wave could ever reach overall=PASS. Retired — and PRINTED at module
# top (RETIRED_FIELDS, before the credential gate) so the coverage gap is
# visible on every run instead of silently graded:
#   tool_pre      needs an in-session router_control(request_routing) call; the
#                 battery's only wire is POST /api/sessions/<sid>/chat, which
#                 carries text, not tool calls. An in-turn declared claim can
#                 only ride the user-phrase path (D4).
#   pre_spend_usd setting a profile's spend ABOVE its daily cap means writing
#                 that profile's ledger out of band; the battery grades what
#                 the gateway produced, it does not seed the wallet.
#   expect_delivery_contains  its only consumer was D3's cap-denial banner,
#                 unreachable without pre_spend_usd.
# anchor_route_fired is retired on D1/D2 (it was only reachable via tool_pre)
# and KEPT on D4, whose declaration is a user phrase the battery can send.
# "forbid_markers" on D2 was already a no-op: seam_free defaults True and
# check_seams() scans MARKERS, so it is dropped rather than left misleading.

# D1. On-demand higher-pre intent, graded on what the wire can deliver.
run_scenario("D1_on_demand_higher_pre", [
    {"text": "Review our v2 auth-flow doc for gaps — anything missing before we ship it.",
     "min_chars": 300},
])

# D2. Shadow-consult intent, graded on delivery + seam.
run_scenario("D2_on_demand_shadow", [
    {"text": "Draft the Q3 capacity-planning summary from the metrics we discussed.",
     "min_chars": 300},
])

# D3. Cap-denial intent: asserts only that a plain ask does NOT anchor-route.
run_scenario("D3_cap_denial_banner", [
    {"text": "Plan the multi-region rollout sequence.",
     "expect_events": [], "forbid_events": ["anchor_route_fired"]},
])

# D4. Double-declare — the user phrase declares; ONE consult expected.
run_scenario("D4_double_declare", [
    {"text": "ask your higher self: is the migration plan actually ready to execute?",
     "expect_events": ["anchor_route_fired"], "min_chars": 300},
])

with open(RESULTS, "w") as f:
    json.dump(RESULTS_LIST, f, indent=1)
p = sum(1 for r in RESULTS_LIST if r["pass"]); f_ = len(RESULTS_LIST) - p
print("\n==== BATTERY DONE: %d PASS / %d FAIL (results: %s) ====" % (p, f_, RESULTS))
