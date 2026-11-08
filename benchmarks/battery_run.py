#!/usr/bin/env python3
"""Per-agent behavioural battery RUNNER: graded JSON, not an exit code.

Runs benchmarks/behavioral_battery.py against ONE profile's gateway (port and
bearer resolved from /opt/data/profiles/<profile>/.env first, because env wins
over config.yaml in this fleet), then grades every scenario PASS / FAIL /
INCONCLUSIVE and says, per scenario, which ledger field proves the lane fired.

Grading rule (the only place a verdict is decided):
  - every turn the battery recorded for the scenario passed AND
  - attribution exists: the profile's decision ledger has at least one record
    for that session id AND, for scenarios whose battery expectation names
    route_fired/anchor_route_fired, at least one record with
    rule_id != "none" or lane != "main" or replaced/replacement_path set.
  A scenario with no ledger rows at all is INCONCLUSIVE(reason=NO_ATTRIBUTION):
  the battery's own pass/fail cannot be attributed to the lane, so it is never
  promoted to PASS. Any recorded turn failure -> FAIL. overall=PASS only when
  every scenario is PASS.

Exit: 0 PASS, 1 FAIL, 2 INCONCLUSIVE or runner error, 3 battery wall-clock
timeout (partial grading is still written, overall=INCONCLUSIVE,
error="TIMEOUT after Ns"). JSON is authoritative.
"""
import argparse
import ast
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

import yaml

PROFILES = "/opt/data/profiles"
REPO = "/opt/data/plugins/hermes_router"


def router_log_path(profile, profiles_root=PROFILES):
    """Resolve the route log the profile's router actually writes to.

    Read ``hermes_router.log_path`` from ``<profiles_root>/<profile>/config.yaml``
    — the same key the plugin's log writer resolves — instead of probing for
    whichever log happens to exist. The old exists() probe fell back to
    ``/tmp/uncensored-router-<profile>.log``; for coder and operative that
    graded a tombstone (a file carrying none of the battery's session rows)
    while the live rows sat in the configured per-profile log.

    A configured path that is missing has no safe substitute, so this raises
    RuntimeError naming the resolved path: grading a different log than the one
    the router writes is worse than not grading at all.

    Returns the configured path, made absolute against the profile directory if
    the config gives it relative.
    """
    cfg_path = os.path.join(profiles_root, profile, "config.yaml")
    if not os.path.exists(cfg_path):
        raise RuntimeError(
            "router log path unresolved: no config at %s for profile %r"
            % (cfg_path, profile))
    with open(cfg_path, errors="replace") as fh:
        cfg = yaml.safe_load(fh) or {}
    section = cfg.get("hermes_router")
    if not isinstance(section, dict) or not section.get("log_path"):
        raise RuntimeError(
            "router log path unresolved: hermes_router.log_path is not set in %s"
            % cfg_path)
    log_path = os.path.expanduser(str(section["log_path"]))
    if not os.path.isabs(log_path):
        log_path = os.path.join(os.path.dirname(cfg_path), log_path)
    if not os.path.exists(log_path):
        raise RuntimeError(
            "router log path unresolved: hermes_router.log_path %s does not exist "
            "(profile %r, config %s)" % (log_path, profile, cfg_path))
    return log_path


def env_of(profile):
    """Resolve the credential and port the same way the gateway does: .env first."""
    path = os.path.join(PROFILES, profile, ".env")
    out = {}
    if os.path.exists(path):
        for line in open(path, errors="replace"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def probe(base, key):
    """One authenticated GET; proves the bearer+port actually belong to a gateway."""
    req = urllib.request.Request(base, headers={"Authorization": "Bearer " + key})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True, "ok"
    except Exception as e:
        return False, repr(e)


def head_sha():
    try:
        return subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def read_ledger(profile, run_start):
    """All decision rows written during this run, with their 1-based file line."""
    path = os.path.join(PROFILES, profile, "hermes-router-decisions.jsonl")
    rows = []
    if not os.path.exists(path):
        return path, rows
    with open(path, errors="replace") as f:
        for i, line in enumerate(f, 1):
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts")
            if isinstance(ts, (int, float)) and ts < run_start - 5:
                continue
            d["_line"] = i
            rows.append(d)
    return path, rows


def lane_fired(row):
    return bool(row.get("rule_id") not in (None, "", "none")
                or row.get("lane") not in (None, "", "main")
                or row.get("replaced")
                or row.get("replacement_path"))


# --- why an EXPECTED event kind has no row -------------------------------
# Every kind the battery expects has a live emit site at HEAD (M36-08:
# gate/orchestration.py:3620 anchor_route_fired, :3116/:3111 route_fired,
# completion_audit.py:1397 completion_audit_gate). An absent name therefore
# never means "missing emitter" — it means one of exactly two things, and the
# grader must name which:
#
#   grader_path_drift               the row EXISTS, in a log this run did not read
#   not_instrumented_path_not_taken no row anywhere, and a skip/refusal row in
#                                   the same window shows the emit arm stood down
#                                   BEFORE the log line (impact gate, cadence, or
#                                   not-a-denial defer)
#
# When neither the row nor a stand-down row is present the classifier returns
# None plus what it looked for: an unexplained absence is reported as such, not
# promoted to a diagnosis. A missing-emitter label is never emitted.
ABSENT_DRIFT = "grader_path_drift"
ABSENT_UNREACHED = "not_instrumented_path_not_taken"

# Stand-down rows that prove an emit arm was unreachable, per event kind. Names
# are the ones M36-08 found in the live logs for the unreached turns.
UNREACHED_MARKERS = {
    "anchor_route_fired": ("pre_no_impact", "complexity_skip"),
    "route_fired": ("not_a_denial", "lane_decision_deferred", "gate_abstain",
                    "main_body_kept", "route_fired_no_stash"),
    "completion_audit_gate": ("audit_gate_skip", "cadence"),
}


def classify_absent_event(event, graded_text, other_texts=(), window_text=None):
    """Classify an expected event kind that has no row in the graded log.

    ``graded_text`` is the log this run actually graded; ``other_texts`` is an
    iterable of ``(label, text)`` for logs the router may write instead (the
    per-profile file, the /tmp sibling). ``window_text`` narrows the stand-down
    search — defaults to ``graded_text``.

    Returns ``(cls, evidence)``. ``cls`` is ABSENT_DRIFT, ABSENT_UNREACHED, or
    None when nothing on disk supports either claim.
    """
    window = graded_text if window_text is None else window_text
    if window_text is None and event in graded_text:
        return None, "event present in the graded log; not an absence"
    if event in window:
        return None, "event present in the window; not an absence"
    for label, text in other_texts:
        if text and event in text:
            return ABSENT_DRIFT, (
                "%s present in %s, absent from the graded log — the grader read "
                "a file the router did not write" % (event, label))
    hit = [m for m in UNREACHED_MARKERS.get(event, ()) if m in window]
    if hit:
        return ABSENT_UNREACHED, (
            "%s absent and %s in the same window — the emit arm was not reached"
            % (event, " / ".join(hit)))
    return None, (
        "%s absent and no stand-down row (%s) in the window — absence not "
        "explained by this classifier"
        % (event, " / ".join(UNREACHED_MARKERS.get(event, ())) or "none known"))


def _read_log_text(path, limit=4_000_000):
    """Tail of a route log, or "" — a missing/locked log never breaks grading."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def session_window(text, sid):
    """Rows of a route log that name ONE session.

    The slice a verdict may reason from: without it a stand-down row from an
    earlier, unrelated turn in the same 4 MB tail would label this turn's
    absence as path-not-taken (REVIEW P2-1). Every route row carries
    session_id=; a session with no rows yields "" — no evidence, no label.
    """
    if not text or not sid:
        return ""
    return "\n".join(line for line in text.splitlines() if sid in line)


def parse_stdout(text):
    """scenario -> sid, and scenario -> [turn rows] from the battery's own prints."""
    sids, turns = {}, {}
    cur = None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("=== ") and "(sid " in line:
            name = line[4:].split(" (sid ")[0].strip()
            sid = line.split("(sid ", 1)[1].rstrip(" )=")
            sids[name] = sid
            cur = name
            turns.setdefault(name, [])
        elif (line.startswith("PASS |") or line.startswith("FAIL |")) and cur:
            parts = [p.strip() for p in line.split("|")]
            turns[cur].append({"ok": parts[0] == "PASS",
                               "turn": parts[2] if len(parts) > 2 else "",
                               "detail": parts[3] if len(parts) > 3 else ""})
    return sids, turns


# Fields the battery DECLARES on a scenario but never executes: run_scenario()
# reads only text/expect_events/forbid_events/min_chars/noflinch/seam_free, so a
# verdict on a scenario that needs one of these keys would be a proxy, not a
# measurement. Such a scenario is INCONCLUSIVE with the reason naming the key.
UNIMPLEMENTED_KEYS = ("tool_pre", "pre_spend_usd", "expect_delivery_contains")


def source_of(path):
    return open(path, errors="replace").read()


def _declared_keys(block):
    """String keys of every dict literal in a source block, or None if unparsable.

    Keys, not values: a token that merely appears in a turn's prose
    ("explain what tool_pre means") is not a declaration, so scanning raw text
    would credit a scenario with a field it never carried.
    """
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return None
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def _bounded_window(src, name):
    """Text window for a scenario whose call node could not be located.

    Start at the scenario's own string literal, stop at the next
    run_scenario( call (or 4000 chars, whichever first): a bounded fallback
    beats returning [] (which graded a live un-implemented field as measured).
    """
    start = -1
    for quote in ('"', "'"):
        start = src.find(quote + name + quote)
        if start >= 0:
            break
    if start < 0:
        return ""
    # Walk past the call the literal belongs to, then stop at the FOLLOWING
    # run_scenario( — bounding to one scenario. Stopping at the first call
    # would return just the name, i.e. fail open again.
    first = src.find("run_scenario(", start)
    nxt = -1 if first < 0 else src.find("run_scenario(", first + 1)
    end = len(src) if nxt < 0 else nxt
    return src[start:min(end, start + 4000)]


def unimplemented_fields(name, src):
    """Keys declared by THIS scenario's own run_scenario(...) call, and nothing
    beyond it, that the battery's run_scenario() never reads.

    The block is bounded by the AST node of that single call (end_lineno via
    ast.get_source_segment). A text scan for the next "\\n])" is unsound: when a
    block's closing formatting differs the scan returns -1 and the fallback
    window runs into the NEXT scenarios, so a scenario gets accused of keys a
    neighbour declared (U1/X1/N3 once cited tool_pre/pre_spend_usd they never
    carried). Bounding by the call node makes that overrun impossible.

    Both call forms are matched — run_scenario("X", [...]) and the keyword form
    run_scenario(name="X", turns=[...]). Matching only the positional form
    returned [] for the keyword form, i.e. it failed OPEN on exactly the
    scenarios it exists to catch.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    keys = None
    if tree is not None:
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "run_scenario"):
                continue
            hit = (len(node.args) >= 1
                   and isinstance(node.args[0], ast.Constant)
                   and node.args[0].value == name)
            if not hit:
                for kw in node.keywords:
                    if (kw.arg == "name" and isinstance(kw.value, ast.Constant)
                            and kw.value.value == name):
                        hit = True
                        break
            if hit:
                block = ast.get_source_segment(src, node) or ""
                keys = _declared_keys(block)
                if keys is None:
                    keys = _declared_keys("x = {" + block.split("[", 1)[-1])
                break
    if keys is None:
        # Name not locatable (or the whole file is unparsable): bounded window.
        window = _bounded_window(src, name)
        keys = _declared_keys("y = [" + window + "]") or set()
        if not keys:
            import re
            keys = set(re.findall(r"[\"']([a-z_]+)[\"']\s*:", window))
    body = src[src.find("def run_scenario"):src.find("# ============ SCENARIOS")]
    return [k for k in UNIMPLEMENTED_KEYS if k in keys and k not in body]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-url")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()

    cfg = env_of(a.profile)
    key = cfg.get("API_SERVER_KEY", "")
    port = cfg.get("API_SERVER_PORT", "")
    if not key or not port:
        json.dump({"profile": a.profile, "overall": "INCONCLUSIVE", "totals": {},
                   "error": "no API_SERVER_KEY/API_SERVER_PORT in %s/.env" % a.profile},
                  open(a.out, "w"), indent=1)
        print("INCONCLUSIVE: no credentials for %s" % a.profile)
        return 2
    base = a.base_url or ("http://127.0.0.1:%s/api/sessions" % port)
    alive, why = probe(base, key)
    if not alive:
        json.dump({"profile": a.profile, "port": int(port), "overall": "INCONCLUSIVE",
                   "totals": {}, "error": "gateway probe failed: " + why},
                  open(a.out, "w"), indent=1)
        print("INCONCLUSIVE: gateway probe failed on %s (%s)" % (base, why))
        return 2

    try:
        route_log = router_log_path(a.profile)
    except RuntimeError as e:
        json.dump({"profile": a.profile, "overall": "INCONCLUSIVE", "totals": {},
                   "error": str(e)}, open(a.out, "w"), indent=1)
        print("INCONCLUSIVE: %s" % e)
        return 2
    results_tmp = a.out + ".battery.json"
    env = dict(os.environ, GATEWAY_BEARER=key, BATT_BASE="http://127.0.0.1:%s/api/sessions" % port,
               BATT_ROUTE_LOG=route_log,
               BATT_GW_LOG="/opt/data/.hermes/profiles/%s/logs/gateway.log" % a.profile,
               BATT_DB="file:%s/%s/state.db?mode=ro" % (PROFILES, a.profile),
               BATT_RESULTS=results_tmp)
    started = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    timed_out = False
    try:
        proc = subprocess.run([sys.executable, os.path.join(REPO, "benchmarks", "behavioral_battery.py")],
                              env=env, cwd=REPO, capture_output=True, text=True, timeout=a.timeout)
        raw = proc.stdout + "\n" + proc.stderr
    except subprocess.TimeoutExpired as e:
        # The battery is wall-clock bounded; dying here used to kill the runner
        # before json.dump, so a slow profile produced NO verdict at all. Grade
        # whatever it managed to record and still write the file.
        timed_out = True

        def _txt(v):
            if v is None:
                return ""
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            return str(v)

        raw = _txt(e.stdout) + "\n" + _txt(e.stderr)
        proc = subprocess.CompletedProcess(e.cmd, 124, raw, "")

    # The battery prints its RETIRED FIELDS block at import time, before the
    # credential gate and before any live scenario. Echo it into the runner's
    # own stdout so the retirement reason is visible where the verdict is read
    # — including on the timeout path, where the child is killed mid-run.
    retired_block = []
    for line in raw.splitlines():
        if line.startswith("RETIRED FIELDS:"):
            retired_block = [line]
        elif retired_block and line.startswith("        "):
            retired_block.append(line)
        elif retired_block:
            break
    for line in retired_block:
        print(line, flush=True)

    sids, turns = parse_stdout(raw)
    expects_lane = {}
    if os.path.exists(results_tmp):
        try:
            for r in json.load(open(results_tmp)):
                exp = r.get("expect") or "[]"
                expects_lane[r["scenario"]] = ("route_fired" in exp) or ("anchor_route_fired" in exp)
        except Exception:
            pass
    graded_text = _read_log_text(route_log)
    other_logs = []
    for label, path in (
            ("%s/uncensored-router.log" % a.profile,
             os.path.join(PROFILES, a.profile, "uncensored-router.log")),
            ("/tmp/uncensored-router-%s.log" % a.profile,
             "/tmp/uncensored-router-%s.log" % a.profile)):
        if os.path.abspath(path) != os.path.abspath(route_log):
            other_logs.append((label, _read_log_text(path, 400000)))
    ledger_path, rows = read_ledger(a.profile, started)
    by_sid = {}
    for r in rows:
        by_sid.setdefault(r.get("session_id", ""), []).append(r)

    scenarios = []
    for name in (sids or {}):
        sid = sids[name]
        trs = turns.get(name, [])
        recs = by_sid.get(sid, [])
        fired = [r for r in recs if lane_fired(r)]
        entry = {"id": name, "sid": sid, "turns": len(trs),
                 "turns_failed": sum(1 for t in trs if not t["ok"])}
        absent = []
        for t in trs:
            for part in str(t.get("detail", "")).split(";"):
                part = part.strip()
                if part.startswith("missing_event:"):
                    ev = part.split(":", 1)[1].strip()
                    if ev and ev not in absent:
                        absent.append(ev)
        if absent:
            cls, ev = classify_absent_event(
                absent[0], graded_text, other_logs,
                window_text=session_window(graded_text, sid))
            entry["absent_event"] = absent[0]
            entry["absent_class"] = cls
            entry["absent_evidence"] = ev
        if not trs:
            entry.update(verdict="INCONCLUSIVE", reason="NO_TURNS_RECORDED",
                         evidence="battery stdout has no graded turn for %s" % name)
            scenarios.append(entry)
            continue
        miss = unimplemented_fields(name, source_of(os.path.join(REPO, "benchmarks", "behavioral_battery.py")))
        if miss:
            entry.update(verdict="INCONCLUSIVE", reason="DECLARED_FIELD_UNIMPLEMENTED:" + ",".join(miss),
                         evidence="benchmarks/behavioral_battery.py: run_scenario reads no %s; the scenario's %s expectation was never executed"
                                  % (miss[0], "/".join(miss)))
        elif not recs:
            entry.update(verdict="INCONCLUSIVE", reason="NO_ATTRIBUTION",
                         evidence="%s: no decision row for session_id=%s after %s"
                                  % (ledger_path, sid, started_at))
        elif expects_lane.get(name) and not fired:
            entry.update(verdict="INCONCLUSIVE", reason="LANE_NOT_FIRED",
                         evidence="%s: %d row(s) for %s, none with rule_id!=none/lane!=main"
                                  % (ledger_path, len(recs), sid))
        elif entry["turns_failed"]:
            reason = "%d/%d turns failed" % (entry["turns_failed"], len(trs))
            if entry.get("absent_class"):
                reason += "; absent_class=%s" % entry["absent_class"]
            entry.update(verdict="FAIL", reason=reason,
                         evidence="%s: %s" % (results_tmp, [t["detail"] for t in trs if not t["ok"]][:3]))
        else:
            cite = (fired or recs)[0]
            entry.update(verdict="PASS", reason="lane attributed",
                         evidence="%s:%d event_seq=%s lane=%s rule_id=%s replaced=%s delivered_chars=%s"
                                  % (ledger_path, cite["_line"], cite.get("event_seq"),
                                     cite.get("lane"), cite.get("rule_id"), cite.get("replaced"),
                                     cite.get("delivered_chars")))
        scenarios.append(entry)

    totals = {k: sum(1 for s in scenarios if s["verdict"] == k.upper())
              for k in ("pass", "fail", "inconclusive")}
    if not scenarios:
        overall = "INCONCLUSIVE"
    elif totals["fail"]:
        overall = "FAIL"
    elif totals["pass"] == len(scenarios):
        overall = "PASS"
    else:
        overall = "INCONCLUSIVE"
    doc = {"profile": a.profile, "port": int(port), "head_sha": head_sha(),
           "started_at": started_at, "scenarios": scenarios, "totals": totals,
           "overall": overall, "battery_exit": proc.returncode,
           "output_tail": raw.strip().splitlines()[-6:]}
    if timed_out:
        overall = "INCONCLUSIVE"
        doc["overall"] = overall
        doc["error"] = "TIMEOUT after %ds" % a.timeout
    json.dump(doc, open(a.out, "w"), indent=1)
    print("profile=%s port=%s head=%s" % (a.profile, port, doc["head_sha"]))
    for s in scenarios:
        print("%-24s %-12s %s" % (s["id"], s["verdict"], s.get("reason", "")))
    print("TOTALS pass=%d fail=%d inconclusive=%d overall=%s"
          % (totals["pass"], totals["fail"], totals["inconclusive"], overall))
    if timed_out:
        print("TIMEOUT after %ds — %d scenario(s) graded from partial run; JSON written: %s"
              % (a.timeout, len(scenarios), a.out))
        return 3
    return 0 if overall == "PASS" else (1 if overall == "FAIL" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
