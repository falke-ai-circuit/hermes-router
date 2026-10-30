#!/usr/bin/env python3
"""gate_classify.py — set-equality classifier behind ``gate.sh --status``.

Pure function ``classify(required, optional, reds)`` plus two readers, so the
same logic is exercised by ``tests/test_gate_classify.py`` without needing a
real suite run.

Verdict (three-way, exactly as the gate contract states it):

    BEYOND_BASELINE = reds - required - optional      (new red -> REGRESSION)
    MISSING_REQUIRED = required - reds                (pin stale -> DRIFT)
    EQUALS_BASELINE  = both empty

Grammar is ``tests/baseline_failures.txt``'s REAL grammar, delegated to
``scripts/baseline_check.py`` so there is exactly one parser in the repo:

    tests/a.py::test_b           required — a bare node id
    optional tests/a.py::test_b  reason=...  expect="..."   exempt

The ``optional`` keyword is a CLASS MARKER, not a node id.  Treating it as one
is the bug that produced a phantom regression in real use: the pinned node
itself then looked "unpinned" while the literal token ``optional`` was reported
as a red that does not exist.  ``read_baseline`` consumes the token before
taking the id; ``tests/test_gate_classify.py`` pins that.

Red-set source, in order of preference:

1. the junit XML — exact node ids, and it records teardown ``ERROR`` cases
   (which the ``-q`` log prints as ``ERROR <node>`` too, but a killed run can
   leave the log summary half-written while the XML still parses as a
   document);
2. the log's ``FAILED``/``ERROR`` lines — the fallback for a missing,
   unparseable, or empty junit file (e.g. pytest died before writing it).

Exit codes: 0 EQUALS_BASELINE, 1 REGRESSION, 3 DRIFT, 2 cannot classify
(unreadable pin/red source — never silently a pass).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable, List, Sequence, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from baseline_check import parse_junit, read_baseline  # noqa: E402

REPO_ROOT = os.path.dirname(_HERE)
DEFAULT_BASELINE = os.path.join(REPO_ROOT, "tests", "baseline_failures.txt")


# --------------------------------------------------------------------------- #
# the classifier
# --------------------------------------------------------------------------- #
def classify(
    required: Iterable[str],
    optional: Iterable[str],
    reds: Iterable[str],
) -> Tuple[List[str], List[str], str]:
    """Return ``(beyond, missing, verdict)`` — sorted id lists.

    ``beyond`` = reds nobody pinned (REGRESSION).  ``missing`` = pinned-required
    ids that are not red this run (DRIFT: the pin no longer describes the tree —
    either the red was fixed or the test stopped running; both are decisions,
    not a pass).  A declared-optional id is red-tolerated by membership only;
    its reason=/expect= signature stays ``baseline_check.py``'s job.
    """
    req: Set[str] = set(required)
    opt: Set[str] = set(optional)
    actual: Set[str] = set(reds)

    beyond = sorted(actual - req - opt)
    missing = sorted(req - actual)

    if beyond:
        verdict = "REGRESSION"
    elif missing:
        verdict = "DRIFT"
    else:
        verdict = "EQUALS_BASELINE"
    return beyond, missing, verdict


def read_pin(path: str) -> Tuple[List[str], List[str]]:
    """``(required, optional)`` from a baseline file; refuses an invalid pin."""
    required, optional, errors = read_baseline(path)
    if errors:
        raise ValueError("baseline pin invalid: " + "; ".join(errors))
    return required, sorted(optional)


# --------------------------------------------------------------------------- #
# red-set readers
# --------------------------------------------------------------------------- #
def reds_from_junit(path: str) -> Set[str]:
    """Failing/erroring node ids from a junit XML, or empty when uneusable."""
    bad, _present = parse_junit(path)
    return set(bad)


def reds_from_log(path: str) -> Set[str]:
    """``FAILED``/``ERROR`` node ids from a ``pytest -q`` log."""
    reds: Set[str] = set()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            marker = None
            if line.startswith("FAILED "):
                marker = "FAILED "
            elif line.startswith("ERROR "):
                marker = "ERROR "
            if marker is None:
                continue
            # "FAILED tests/x.py::test_y - AssertionError: ..." — the id ends at
            # the space-dash-space pytest separator; ids can contain spaces
            # inside [...] parametrisation, so never split on the first space.
            rest = line[len(marker) :]
            nodeid = rest.split(" - ", 1)[0].strip()
            if nodeid:
                reds.add(nodeid)
    return reds


def collect_reds(junit_path: str, log_path: str) -> Tuple[Set[str], str]:
    """Return ``(reds, source)``; junit preferred, log as fallback."""
    if junit_path and os.path.exists(junit_path):
        try:
            reds = reds_from_junit(junit_path)
            # A zero-case document means the writer died; do not call that a
            # clean run.  Fall through to the log instead.
            _bad, present = parse_junit(junit_path)
            if present:
                return reds, "junit"
        except Exception:  # noqa: BLE001 — unparseable XML is not a verdict
            pass
    if log_path and os.path.exists(log_path):
        return reds_from_log(log_path), "log"
    raise ValueError(f"no usable red source (junit={junit_path or '-'} log={log_path or '-'})")


def render(
    beyond: Sequence[str],
    missing: Sequence[str],
    verdict: str,
) -> str:
    """The three machine-readable lines, exactly."""
    def line(name: str, ids: Sequence[str]) -> str:
        return f"{name}={len(ids)}" + (" " + " ".join(ids) if ids else "")

    return "\n".join(
        [line("BEYOND_BASELINE", beyond), line("MISSING_REQUIRED", missing), f"VERDICT={verdict}"]
    )


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="classify a gate run against the pinned baseline")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--junit", default="", help="junit XML written by the run")
    ap.add_argument("--log", default="", help="pytest -q log (fallback red source)")
    ap.add_argument(
        "--reds",
        default="",
        metavar="FILE",
        help="synthetic red list, one node id per line (testing/verification)",
    )
    args = ap.parse_args(argv)

    try:
        required, optional = read_pin(args.baseline)
    except (OSError, ValueError) as exc:
        # An unreadable pin is DRIFT, never a pass: print the three-line shape
        # so callers keep parsing one format.  No ids are known in this branch.
        print(f"gate_classify: {exc}", file=sys.stderr)
        print(render([], [], "DRIFT"))
        return 3

    try:
        if args.reds:
            with open(args.reds, "r", encoding="utf-8") as fh:
                reds = {ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")}
            source = "reds-file"
        else:
            reds, source = collect_reds(args.junit, args.log)
    except (OSError, ValueError) as exc:
        print(f"gate_classify: {exc}", file=sys.stderr)
        return 2

    beyond, missing, verdict = classify(required, optional, reds)
    print(render(beyond, missing, verdict))
    if os.environ.get("GATE_CLASSIFY_DEBUG"):
        print(f"gate_classify: red source={source} reds={len(reds)}", file=sys.stderr)
    return {"EQUALS_BASELINE": 0, "REGRESSION": 1, "DRIFT": 3}[verdict]


if __name__ == "__main__":
    sys.exit(main())
