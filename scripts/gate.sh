#!/usr/bin/env bash
# scripts/gate.sh — the suite as a POLLED operation, never a blocking one.
#
# WHY THIS EXISTS
#   A full-suite run is 6-10 minutes.  An agent turn that waits on it dies at
#   the gate: the tree is correct and dirty, the session is idle-killed while
#   pytest is still in flight, and a human has to finish the verification by
#   hand.  A multi-minute blocking wait outlives the turn.  So the suite is
#   launched detached (survives the caller's process exiting) and read back
#   with a separate, instant --status call.
#
# OPERATOR RECIPE (three steps, nothing to remember)
#   1. bash scripts/gate.sh
#         -> prints three lines and returns in about a second:
#              PID=<pid>
#              LOG=/opt/data/tmp/gate/gate-<stamp>.log
#              JUNIT=/opt/data/tmp/gate/gate-<stamp>.xml
#   2. bash scripts/gate.sh --status <LOG>
#         -> exactly one of RUNNING | DONE | TIMEOUT
#            on DONE: the pytest summary line, then
#              BEYOND_BASELINE=<n> <ids>     reds nobody pinned  -> REGRESSION
#              MISSING_REQUIRED=<n> <ids>    pinned reds now green -> DRIFT
#              VERDICT=EQUALS_BASELINE|REGRESSION|DRIFT
#   3. commit only when VERDICT=EQUALS_BASELINE.
#
# CLASSIFICATION SOURCE
#   --status delegates to scripts/gate_classify.py, which prefers the junit XML
#   (exact node ids incl. teardown ERRORs) and falls back to the log's
#   FAILED/ERROR lines when the XML is missing/unparseable/empty.  The pin
#   grammar (bare required ids, `optional <id> reason=... expect="..."`) is
#   parsed by scripts/baseline_check.py — one parser in the repo, no fork.
#   `optional` is a class marker, never a node id.
#
# DETACHMENT
#   setsid (fallback: nohup) puts the run in its own session so the caller
#   exiting cannot SIGHUP it; stdin is /dev/null and both streams go to <log>.
#   timeout (default 1800s, override GATE_TIMEOUT) bounds a hung suite.
#   The wrapper writes <log>.pid and <log>.rc; that pid — not the launcher's —
#   is what `kill -0` tests.
#
# env: GATE_DIR (default /opt/data/tmp/gate), GATE_TIMEOUT (default 1800)
# exit (status): 0 EQUALS_BASELINE · 1 REGRESSION · 3 DRIFT · 2 cannot classify
#               launch: 0 launched · 2 bad usage
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE_DIR="${GATE_DIR:-/opt/data/tmp/gate}"
SUITE_TIMEOUT="${GATE_TIMEOUT:-1800}"
PYTHON="${GATE_PYTHON:-python3}"
BASELINE="$ROOT/tests/baseline_failures.txt"

# pytest writes the junit beside the log: gate-<stamp>.log -> gate-<stamp>.xml
junit_for_log() { printf '%s.xml' "${1%.log}"; }

uses_junit() { echo "junit"; }

# --------------------------------------------------------------------------- #
# read mode
# --------------------------------------------------------------------------- #
status() {
    local log="${1:-}"
    if [ -z "$log" ]; then
        echo "usage: bash scripts/gate.sh --status <log>" >&2
        return 2
    fi
    if [ ! -f "$log" ]; then
        echo "gate.sh: no such log: $log" >&2
        return 2
    fi

    local pidfile="$log.pid" rcfile="$log.rc" pid="" alive="no" rc="" summary=""
    [ -f "$pidfile" ] && pid="$(cat "$pidfile" 2>/dev/null)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        alive="yes"
    fi

    # pytest -q ends with a bare "8 failed, 2525 passed, ... in 636.88s";
    # with decorations (-q still decorates on some plugins) strip "= " markers.
    summary="$(grep -E '^=?[[:space:]]*[0-9]+ (passed|failed|error|skipped|deselected|xfailed|xpassed|warning)' "$log" 2>/dev/null \
        | grep -E ' in [0-9.]+s' | tail -n 1 | sed -E 's/^=+[[:space:]]*//; s/[[:space:]]*=+$//')"

    if [ -n "$summary" ]; then
        echo "DONE"
        echo "$summary"
        "$PYTHON" "$ROOT/scripts/gate_classify.py" \
            --baseline "$BASELINE" \
            --junit "$(junit_for_log "$log")" \
            --log "$log"
        return $?
    fi

    if [ "$alive" = "yes" ]; then
        echo "RUNNING"
        return 0
    fi

    # Process gone, no summary line: the run did not finish.  A timeout (exit
    # 124 from `timeout`) and any abnormal death look the same from here — both
    # mean "no verdict", which the contract must never report as DONE.
    rc="$(cat "$rcfile" 2>/dev/null || true)"
    [ -n "$rc" ] && echo "gate.sh: runner exit=$rc (no pytest summary)" >&2
    echo "TIMEOUT"
    return 0
}

# --------------------------------------------------------------------------- #
# launch mode
# --------------------------------------------------------------------------- #
launch() {
    mkdir -p "$GATE_DIR" || { echo "gate.sh: cannot create $GATE_DIR" >&2; return 2; }
    local stamp log junit pidfile rcfile launcher pid=""
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    log="$GATE_DIR/gate-$stamp.log"
    junit="$GATE_DIR/gate-$stamp.xml"
    pidfile="$log.pid"
    rcfile="$log.rc"

    : > "$log"; rm -f "$rcfile" "$junit" "$pidfile"

    # The plugin imports as a package: the PARENT dir must be importable.
    export PYTHONPATH="$ROOT/..${PYTHONPATH:+:$PYTHONPATH}"

    # Child-side env: avoids quoting the paths into the command string.
    export GATE_PIDFILE="$pidfile" GATE_RCFILE="$rcfile" GATE_LOG_DIR="$GATE_DIR"
    export GATE_ROOT="$ROOT" GATE_JUNIT="$junit" GATE_TIMEOUT="$SUITE_TIMEOUT" GATE_PYTHON_BIN="$PYTHON"

    launcher='echo $$ > "$GATE_PIDFILE"
cd "$GATE_ROOT" || exit 3
timeout "$GATE_TIMEOUT" "$GATE_PYTHON_BIN" -m pytest tests -q -p no:cacheprovider --junitxml "$GATE_JUNIT"
echo $? > "$GATE_RCFILE"'

    if command -v setsid >/dev/null 2>&1; then
        setsid bash -c "$launcher" > "$log" 2>&1 < /dev/null &
    else
        # No setsid: nohup is the portable detach (still ignores SIGHUP).
        nohup bash -c "$launcher" > "$log" 2>&1 < /dev/null &
    fi

    # Bounded read-back of the runner's own pid (it writes $$ after exec-free
    # start, so <=0.2s).  Never a wait on the suite.
    local i
    for i in $(seq 1 20); do
        [ -s "$pidfile" ] && break
        sleep 0.01
    done
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    [ -n "$pid" ] || pid="$!"

    printf 'PID=%s\nLOG=%s\nJUNIT=%s\n' "$pid" "$log" "$junit"
    return 0
}

case "${1:-}" in
    ""|--run|start)
        launch
        exit $?
        ;;
    --status)
        status "${2:-}"
        exit $?
        ;;
    -h|--help)
        sed -n '2,45p' "${BASH_SOURCE[0]}"
        exit 0
        ;;
    *)
        echo "usage: bash scripts/gate.sh [--status <log>]" >&2
        exit 2
        ;;
esac
