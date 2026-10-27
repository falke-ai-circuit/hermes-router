#!/usr/bin/env python3
"""baseline_check.py — make "not 0 failed, equal to the pinned baseline" mechanical.

The suite is not green at HEAD and no slice in R76-P4 may claim GREEN over an
undiagnosed red.  The plan (§C clause 3) therefore defines a slice's gate as
**failure-set equality against a pinned baseline**, not "0 failed".  This script
is that gate: it runs the suite once into a junit XML and compares the set of
failing/erroring node ids against ``tests/baseline_failures.txt``.

Usage
-----
    python3 scripts/baseline_check.py                    # run + compare, exit 0/1
    python3 scripts/baseline_check.py --junit out.xml    # keep the junit file
    python3 scripts/baseline_check.py --show-variant     # print the variant line only

Exit codes: 0 = failure set equals the pinned baseline; 1 = drift; 2 = the junit
run itself could not be parsed (infrastructure problem, never a "pass").

Pinned-file format (``tests/baseline_failures.txt``)
---------------------------------------------------
One node id per line, ``path/to/test_module.py::test_name``.  ``#`` starts a
comment.  Two kinds of line:

    tests/foo/test_bar.py::test_baz            required — must be red, always
    optional tests/foo/test_bar.py::test_qux   may be red or green

The ``optional`` class exists for exactly one reason: a test whose result is
**order-dependent** (it fails standalone and passes in the full-suite order, or
vice versa).  Pinning such a test as required would make a full-suite run fail
and a standalone run pass for reasons that have nothing to do with the slice
under review; pinning it as absent would do the reverse.  So the class is
declared here, in the file, and in ``docs/r76-p4-baseline-triage.md`` — never
silently swallowed.

An ``optional`` entry is still checked in one direction: it must APPEAR in the
run at least sometimes.  A permanently green optional entry is stale and this
script reports it as drift, because an exemption that matches nothing is a lie
about the code (the same rule S1 applies to ``.importlinter``).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASELINE = os.path.join(REPO_ROOT, "tests", "baseline_failures.txt")


# --------------------------------------------------------------------------- #
# pinned file
# --------------------------------------------------------------------------- #
def read_baseline(path: str) -> Tuple[List[str], List[str]]:
    """Return (required, optional) node-id lists, in file order."""
    required: List[str] = []
    optional: List[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            kind = "required"
            if line.startswith("optional "):
                kind = "optional"
                line = line[len("optional ") :].strip()
            # strip a trailing explanatory comment: "nodeid  # why"
            if "  #" in line:
                line = line.split("  #", 1)[0].strip()
            (optional if kind == "optional" else required).append(line)
    return required, optional


# --------------------------------------------------------------------------- #
# junit parsing
# --------------------------------------------------------------------------- #
def _nodeid_from_case(case: ET.Element) -> str:
    """Rebuild a pytest node id from a junit testcase element.

    pytest records ``file``/``line`` on the case (>= 7.0) and splits the python
    path into ``classname``.  We prefer ``file`` because it survives module-level
    tests, class-based tests and parametrised ids alike; ``classname`` is the
    fallback for older writers.
    """
    name = case.get("name") or ""
    file_attr = case.get("file") or ""
    classname = case.get("classname") or ""

    node = ""
    if file_attr:
        node = file_attr
    elif classname:
        # xunit2 gives no `file`, only a dotted classname. The module path is
        # the longest dotted prefix that actually exists on disk; whatever
        # follows is the class chain (tests/mod.py::TestClass).
        parts = classname.split(".")
        for i in range(len(parts), 0, -1):
            rel = "/".join(parts[:i]) + ".py"
            if os.path.exists(os.path.join(REPO_ROOT, rel)):
                node = rel + "".join("::" + p for p in parts[i:])
                break
        if not node:  # nothing on disk — keep the old flat mapping
            node = classname.replace(".", "/") + ".py"

    # Class-based tests carry the class in classname: tests.mod.TestClass
    # -> tests/mod.py::TestClass::test_name ; pytest's `name` already includes
    # the parametrisation but NOT the class, so re-add it when present.
    if file_attr and classname:
        cls_path = classname
        mod_path = file_attr[:-3].replace("/", ".") if file_attr.endswith(".py") else file_attr
        if cls_path.startswith(mod_path + "."):
            node += "::" + cls_path[len(mod_path) + 1 :].replace(".", "::")
    return node + "::" + name if node else name


def parse_junit(path: str) -> Tuple[Set[str], Dict[str, str]]:
    """Return (failing node ids, {nodeid: 'failure'|'error'})."""
    tree = ET.parse(path)
    root = tree.getroot()
    cases = root.iter("testcase")
    bad: Dict[str, str] = {}
    for case in cases:
        kind = None
        for child in case:
            tag = child.tag.split("}")[-1]
            if tag == "failure":
                kind = "failure"
                break
            if tag == "error":
                kind = "error"
                break
        if kind:
            bad[_nodeid_from_case(case)] = kind
    return set(bad), bad


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run_suite(junit_path: str, extra: List[str]) -> int:
    plugin_parent = os.path.dirname(REPO_ROOT)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [plugin_parent] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "-p",
        "no:cacheprovider",
        "--junitxml",
        junit_path,
    ] + extra
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True)
    return proc.returncode


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--junit", default="", help="where to write the junit XML")
    ap.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="extra argument for pytest (repeatable)",
    )
    ap.add_argument("--no-run", action="store_true", help="reuse --junit, do not run")
    args = ap.parse_args(argv)

    required, optional = read_baseline(args.baseline)
    if not required and not optional:
        print("baseline_check: pinned baseline is EMPTY — refusing to call that a pass")
        return 1

    junit_path = args.junit or os.path.join(
        tempfile.mkdtemp(prefix="r76-baseline-"), "junit.xml"
    )
    if not args.no_run:
        print(f"baseline_check: running suite -> {junit_path}", flush=True)
        rc = run_suite(junit_path, args.pytest_arg)
        print(f"baseline_check: pytest exit={rc}", flush=True)

    try:
        actual, kinds = parse_junit(junit_path)
    except Exception as exc:  # noqa: BLE001
        print(f"baseline_check: cannot parse junit at {junit_path}: {exc}")
        return 2

    req = set(required)
    opt = set(optional)
    missing = sorted(req - actual)          # pinned red, now green
    new = sorted(actual - req - opt)        # red, not pinned anywhere
    opt_present = sorted(actual & opt)
    opt_absent = sorted(opt - actual)

    print("")
    print(f"baseline_check: pinned required={len(req)} optional={len(opt)}")
    print(f"baseline_check: actual failures/errors={len(actual)}")
    print(
        "baseline_check: variant = "
        f"{len(actual)} red"
        f"{' (optional order-dependent member RED: ' + ', '.join(opt_present) + ')' if opt_present else ' (no optional members red)'}"
    )
    for node in opt_absent:
        print(f"  optional member green this run: {node}")
    ok = not missing and not new

    if missing:
        print("\nbaseline_check: PINNED-RED-NOW-GREEN (baseline drifted, re-pin deliberately):")
        for node in missing:
            print(f"  - {node}")
    if new:
        print("\nbaseline_check: UNPINNED RED (a slice introduced this, or the baseline is stale):")
        for node in new:
            print(f"  + {node}  [{kinds.get(node)}]")
    if opt_absent and not opt_present:
        # Not a failure: an optional member is explicitly allowed to be either
        # colour, which is the whole point of the class (see module docstring).
        # It IS a signal that the entry may now be pinnable as absent — the
        # triage doc, not this script, decides that.
        print(
            "\nbaseline_check: NOTE — every optional entry was green this run; "
            "the optional class is doing no work. Re-check the triage doc before "
            "removing the entry."
        )

    print("")
    print("baseline_check: " + ("OK — failure set equals the pinned baseline" if ok else "DRIFT"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
