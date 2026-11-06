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

Exit: 0 PASS, 1 FAIL, 2 INCONCLUSIVE or runner error. JSON is authoritative.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

PROFILES = "/opt/data/profiles"
REPO = "/opt/data/plugins/hermes_router"


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


def unimplemented_fields(name, src):
    """Keys declared in this scenario's block that run_scenario() never reads."""
    marker = 'run_scenario("%s"' % name
    at = src.find(marker)
    if at < 0:
        return []
    end = src.find("\n])", at)
    block = src[at:end if end > at else at + 2500]
    body = src[src.find("def run_scenario"):src.find("# ============ SCENARIOS")]
    return [k for k in UNIMPLEMENTED_KEYS if k in block and k not in body]


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

    route_log = os.path.join(PROFILES, a.profile, "uncensored-router.log")
    if not os.path.exists(route_log):
        route_log = "/tmp/uncensored-router-%s.log" % a.profile
    results_tmp = a.out + ".battery.json"
    env = dict(os.environ, GATEWAY_BEARER=key, BATT_BASE="http://127.0.0.1:%s/api/sessions" % port,
               BATT_ROUTE_LOG=route_log,
               BATT_GW_LOG="/opt/data/.hermes/profiles/%s/logs/gateway.log" % a.profile,
               BATT_DB="file:%s/%s/state.db?mode=ro" % (PROFILES, a.profile),
               BATT_RESULTS=results_tmp)
    started = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    proc = subprocess.run([sys.executable, os.path.join(REPO, "benchmarks", "behavioral_battery.py")],
                          env=env, cwd=REPO, capture_output=True, text=True, timeout=a.timeout)
    raw = proc.stdout + "\n" + proc.stderr

    sids, turns = parse_stdout(raw)
    expects_lane = {}
    if os.path.exists(results_tmp):
        try:
            for r in json.load(open(results_tmp)):
                exp = r.get("expect") or "[]"
                expects_lane[r["scenario"]] = ("route_fired" in exp) or ("anchor_route_fired" in exp)
        except Exception:
            pass
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
            entry.update(verdict="FAIL", reason="%d/%d turns failed" % (entry["turns_failed"], len(trs)),
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
    json.dump(doc, open(a.out, "w"), indent=1)
    print("profile=%s port=%s head=%s" % (a.profile, port, doc["head_sha"]))
    for s in scenarios:
        print("%-24s %-12s %s" % (s["id"], s["verdict"], s.get("reason", "")))
    print("TOTALS pass=%d fail=%d inconclusive=%d overall=%s"
          % (totals["pass"], totals["fail"], totals["inconclusive"], overall))
    return 0 if overall == "PASS" else (1 if overall == "FAIL" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
