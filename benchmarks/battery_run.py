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
import calendar
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

PROFILES = "/opt/data/profiles"
REPO = "/opt/data/plugins/hermes_router"


def _config_access():
    """The plugin's single config accessor, imported for THIS script.

    ``benchmarks/`` is not a package, so the script puts the plugin's parent on
    sys.path and imports the same module the writer uses. Delegating the whole
    resolution — section reader and key order — is the point (M36-11 item 2):
    the runner must not own a second copy of the rule the writer applies.
    """
    parent = os.path.dirname(REPO)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    from hermes_router.core import config_access  # noqa: PLC0415

    return config_access


def router_log_path(profile, profiles_root=PROFILES):
    """Resolve the route log the profile's router actually writes to.

    Resolution is DELEGATED, not re-implemented: the profile's ``config.yaml``
    is read with ``config_access.section_at`` (the writer's own yaml reader,
    mtime-keyed) and the path is taken with ``config_access.route_log_path``,
    which applies the writer's key order — ``paths.log_path`` then the deployed
    legacy ``log_path`` — makes a RELATIVE value absolute against the profile
    directory, and answers an UNSET key with the fleet convention
    ``<profile>/uncensored-router.log`` (M36-13 item 2: one behaviour for all
    four consumers, never a battery-only fallback).

    Kept on top of the delegation: the existence check. A path that the config
    never named and that does not exist yet cannot be graded; the error names
    the same path every other consumer resolved, so the disagreement is about
    the file's presence, never about which file.

    Returns the absolute route-log path.
    """
    cfg_path = os.path.join(profiles_root, profile, "config.yaml")
    if not os.path.exists(cfg_path):
        raise RuntimeError(
            "router log path unresolved: no config at %s for profile %r"
            % (cfg_path, profile))
    access = _config_access()
    section = access.section_at(cfg_path)
    profile_dir = os.path.dirname(cfg_path)
    named = access.route_log_path(section)
    log_path = access.route_log_path(section, profile_dir=profile_dir)
    if not named:
        if not log_path or not os.path.exists(log_path):
            raise RuntimeError(
                "router log path unresolved: hermes_router.log_path is not set "
                "in %s and the convention path %s does not exist (profile %r)"
                % (cfg_path, log_path, profile))
        return log_path
    if not os.path.exists(log_path):
        raise RuntimeError(
            "router log path unresolved: hermes_router.log_path %s does not exist "
            "(profile %r, config %s)" % (log_path, profile, cfg_path))
    return log_path


# A row timestamp, or the file's own mtime, must land no earlier than this many
# seconds before the run started. Rows are the evidence grading consumes; the
# mtime window only absorbs clock rounding and a write that happened while the
# runner was starting up.
FRESHNESS_WINDOW_SEC = 300
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)")


def _row_epoch(line):
    """Epoch seconds of a route-log row's leading ISO timestamp, else None."""
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def grade_freshness(text, size, mtime, started_epoch,
                    window=FRESHNESS_WINDOW_SEC):
    """None when the graded log proves it was written during this run, else
    the INCONCLUSIVE reason.

    M36-11 item 1: a configured log that no writer touches resolves cleanly,
    contributes zero rows, and every missing_event becomes a false FAIL charged
    to the router. Freshness is decided before grading: a non-empty file with
    either a row timestamp at/after the run start or an mtime inside the window
    is gradable; an empty file is never gradable, whatever its mtime (the
    reviewer's probe: a 0-byte file touched now).
    """
    if not size:
        return "GRADED_LOG_STALE"
    cutoff = started_epoch - window
    for line in str(text or "").splitlines():
        ts = _row_epoch(line)
        if ts is not None and ts >= cutoff:
            return None
    if mtime is not None and mtime >= cutoff:
        return None
    return "GRADED_LOG_STALE"


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

# M36-15/B1: the third answer, and the only honest one when the graded log
# carries none of this run's rows. A row in a sibling log then proves nothing
# about where THIS run's row went — the sibling may be an old tombstone — so
# asserting ABSENT_DRIFT would be a wrong cause on a real FAIL.
ABSENT_STALE_UNATTRIB = "unattributable_stale_log"

# Stand-down rows that prove an emit arm was unreachable, per event kind. Names
# are the ones M36-08 found in the live logs for the unreached turns.
UNREACHED_MARKERS = {
    "anchor_route_fired": ("pre_no_impact", "complexity_skip"),
    "route_fired": ("not_a_denial", "lane_decision_deferred", "gate_abstain",
                    "main_body_kept", "route_fired_no_stash"),
    "completion_audit_gate": ("audit_gate_skip", "cadence"),
}


def _iso(epoch):
    """ISO-8601 second stamp for evidence strings; 'unknown' when unset."""
    if epoch is None:
        return "unknown"
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _attributable_rows(other_texts, sid, run_start, read_epoch, event=None):
    """Rows of a sibling log that are evidence THIS RUN wrote them (M36-17).

    A row counts only when, ON THE SAME LINE:
      P1 EVENT  ``event_detail=<event>`` is a whitespace-delimited token and its
                value equals ``event`` exactly (no substring);
      P2 SID    ``session_id=<sid>`` is a whitespace-delimited token whose value
                equals the scenario sid exactly (no substring, no prefix);
      P3 TS     the leading ``^YYYY-MM-DDTHH:MM:SSZ`` parses;
      P4 WINDOW floor(run_start) <= ts <= read_epoch + 1 (second granularity).
    ``event=None`` drops P1 (used for the stale NOTE: this run wrote *something*
    here) and returns every in-window row of the session.
    """
    if not sid:
        return []
    lo = int(run_start) if run_start is not None else None
    hi = None if read_epoch is None else int(read_epoch) + 1
    event_tok = None if event is None else "event_detail=" + event
    sid_tok = "session_id=" + sid
    out = []
    for label, text in other_texts or ():
        for line in (text or "").splitlines():
            tokens = set(line.split())
            if event_tok is not None and event_tok not in tokens:
                continue
            if sid_tok not in tokens:
                continue
            ts = _row_epoch(line)
            if ts is None:
                continue
            if lo is not None and ts < lo:
                continue
            if hi is not None and ts > hi:
                continue
            out.append((label, line, ts))
    return out


def attributable_event_rows(event, other_texts, sid, run_start, read_epoch):
    """The P(row) predicate as a row list: siblings holding THIS run's event.

    The single source of ABSENT_DRIFT. File-level ``event in text`` is retired:
    a whole-file hit (a tombstone, another session's row) proves nothing about
    where THIS run's row was written. Empty when no row is attributable.
    """
    return _attributable_rows(other_texts, sid, run_start, read_epoch, event=event)


def attributable_session_rows(other_texts, sid, run_start, read_epoch):
    """In-window sibling rows of this run's own session, any event kind.

    Used only for the stale NOTE: it says this run did write somewhere the
    grader did not read, without claiming the expected row exists there.
    """
    return _attributable_rows(other_texts, sid, run_start, read_epoch)


def classify_absent_event(event, graded_text, attributed=(), window_text=None,
                          stale_reason=None, sid=None, run_start=None,
                          sibling_sid_rows=()):
    """Classify an expected event kind that has no row in the graded log.

    ``graded_text`` is the log this run actually graded; ``attributed`` is the
    output of :func:`attributable_event_rows` — already-proven rows, never raw
    sibling text, so ``grader_path_drift`` cannot be produced from unattributed
    text by any caller. ``window_text`` narrows the stand-down search — defaults
    to ``graded_text``. ``stale_reason`` is the freshness NOTE, used only as the
    fallback label; it never adds or removes a class.

    Returns ``(cls, evidence)``. ``cls`` is ABSENT_DRIFT, ABSENT_STALE_UNATTRIB,
    ABSENT_UNREACHED, or None when nothing on disk supports a claim.
    """
    window = graded_text if window_text is None else window_text
    if window_text is None and event in graded_text:
        return None, "event present in the graded log; not an absence"
    if event in window:
        return None, "event present in the window; not an absence"
    if attributed:
        label, _line, ts = attributed[0]
        return ABSENT_DRIFT, (
            "%s present in %s at %s with session_id=%s (run start %s) — this "
            "run's own row was written to a file the grader did not read"
            % (event, label, _iso(ts), sid or "", _iso(run_start)))
    if stale_reason:
        evidence = (
            "%s absent, and no sibling row names this run (leading ts >= %s "
            "and session_id=%s) — no path drift is claimed"
            % (event, _iso(run_start), sid or ""))
        if sibling_sid_rows:
            counts = {}
            for label, _line, _ts in sibling_sid_rows:
                counts[label] = counts.get(label, 0) + 1
            evidence += (
                "; NOTE this run's own rows do appear in %s — the graded path "
                "is not the file the writer used, but no row for %s exists "
                "there either"
                % (", ".join("%s (%d row(s))" % (l, n) for l, n in counts.items()),
                   event))
        return ABSENT_STALE_UNATTRIB, evidence
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
    # M36-13 item 1: freshness is COMPUTED here but it no longer gates the
    # evidence. Turn records and ledger rows are independent of the route log —
    # a run whose turns failed, or whose ledger rows attribute the lane, is
    # graded normally with the stale log carried as a note. Only a run that
    # NEITHER the log NOR the ledger carries is GRADED_LOG_STALE.
    try:
        _st = os.stat(route_log)
        log_size, log_mtime = _st.st_size, _st.st_mtime
    except OSError:
        log_size, log_mtime = 0, None
    stale_reason = grade_freshness(graded_text, log_size, log_mtime, started)
    other_logs = []
    for label, path in (
            ("%s/uncensored-router.log" % a.profile,
             os.path.join(PROFILES, a.profile, "uncensored-router.log")),
            ("/tmp/uncensored-router-%s.log" % a.profile,
             "/tmp/uncensored-router-%s.log" % a.profile)):
        if os.path.abspath(path) != os.path.abspath(route_log):
            other_logs.append((label, _read_log_text(path, 400000)))
    # M36-17/P4: the moment the siblings were read. The upper bound only
    # excludes rows that did not exist when they were read.
    read_epoch = time.time()
    ledger_path, rows = read_ledger(a.profile, started)
    by_sid = {}
    for r in rows:
        by_sid.setdefault(r.get("session_id", ""), []).append(r)

    # The guard's real contract: a run the log does not carry AND the ledger
    # does not carry is not gradable — never a FAIL charged to the router.
    # Recorded turns count as carried evidence even when nothing was written
    # to the ledger yet, so they are graded (NO_ATTRIBUTION / FAIL / PASS).
    if stale_reason and not rows and not sids:
        doc = {"profile": a.profile, "port": int(port), "head_sha": head_sha(),
               "started_at": started_at, "route_log": route_log,
               "scenarios": [], "totals": {"pass": 0, "fail": 0, "inconclusive": 0},
               "overall": "INCONCLUSIVE", "reason": stale_reason,
               "error": "graded log %s carries no row at/after run start %s "
                        "(size=%d bytes) and the ledger %s carries no row for "
                        "this run — not graded"
                        % (route_log, started_at, log_size, ledger_path),
               "battery_exit": proc.returncode}
        json.dump(doc, open(a.out, "w"), indent=1)
        print("profile=%s port=%s head=%s" % (a.profile, port, doc["head_sha"]))
        print("INCONCLUSIVE: %s — %s" % (stale_reason, route_log))
        print("TOTALS pass=0 fail=0 inconclusive=0 overall=INCONCLUSIVE (%s)"
              % stale_reason)
        return 2

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
            entry["absent_event"] = absent[0]
            sid_rows = attributable_session_rows(other_logs, sid, started, read_epoch)
            cls, ev = classify_absent_event(
                absent[0], graded_text,
                attributable_event_rows(absent[0], other_logs, sid, started, read_epoch),
                window_text=session_window(graded_text, sid),
                stale_reason=stale_reason, sid=sid, run_start=started,
                sibling_sid_rows=sid_rows)
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
        elif entry["turns_failed"]:
            # M36-13 item 1: a recorded-then-failed turn is EVIDENCE and is
            # graded before attribution can turn it into LANE_NOT_FIRED or
            # NO_ATTRIBUTION. A stale log does not erase it.
            reason = "%d/%d turns failed" % (entry["turns_failed"], len(trs))
            if entry.get("absent_class"):
                reason += "; absent_class=%s" % entry["absent_class"]
            entry.update(verdict="FAIL", reason=reason,
                         evidence="%s: %s" % (results_tmp, [t["detail"] for t in trs if not t["ok"]][:3]))
        elif not recs:
            entry.update(verdict="INCONCLUSIVE", reason="NO_ATTRIBUTION",
                         evidence="%s: no decision row for session_id=%s after %s"
                                  % (ledger_path, sid, started_at))
        elif expects_lane.get(name) and not fired:
            entry.update(verdict="INCONCLUSIVE", reason="LANE_NOT_FIRED",
                         evidence="%s: %d row(s) for %s, none with rule_id!=none/lane!=main"
                                  % (ledger_path, len(recs), sid))
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
           "started_at": started_at, "route_log": route_log, "scenarios": scenarios,
           "totals": totals, "overall": overall, "battery_exit": proc.returncode,
           "log_freshness": stale_reason or "fresh",
           "output_tail": raw.strip().splitlines()[-6:]}
    if stale_reason:
        # Graded on recorded turns / ledger attribution, but the reader must
        # know the log itself carried none of this run's rows.
        doc["stale_log_note"] = (
            "graded log %s carries no row at/after run start %s (size=%d bytes); "
            "verdicts rest on the battery's turn records and on %d ledger row(s) "
            "for this run" % (route_log, started_at, log_size, len(rows)))
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
    if stale_reason:
        # M36-15/F2: JSON is the artifact, but the console is where the verdict
        # is usually read. One line so a stale grade never looks clean here.
        print("NOTE: graded log stale — verdicts rest on turns + ledger")
    if timed_out:
        print("TIMEOUT after %ds — %d scenario(s) graded from partial run; JSON written: %s"
              % (a.timeout, len(scenarios), a.out))
        return 3
    return 0 if overall == "PASS" else (1 if overall == "FAIL" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
