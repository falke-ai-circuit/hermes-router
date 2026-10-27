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

Exit codes: 0 = failure set equals the pinned baseline; 1 = drift (including a
stale or unexplained exemption); 2 = the junit run itself could not be parsed
(infrastructure problem, never a "pass").

Pinned-file format (``tests/baseline_failures.txt``)
---------------------------------------------------
One node id per line, ``path/to/test_module.py::test_name``.  ``#`` starts a
comment.  Two kinds of line:

    tests/foo/test_bar.py::test_baz            required — must be red, always
    optional tests/foo/test_bar.py::test_qux  reason=state-dependent  expect="assert None is not None"
                                               optional — may be red, but only
                                               when both attributes are given

The ``optional`` class exists for exactly one reason: a test whose result is a
function of the machine's live state rather than of the slice under review
(state/order-dependent).  Pinning such a test as required would make a full-suite
run fail for reasons that have nothing to do with the slice; pinning it as absent
would do the reverse.  So the class is declared here, in the file, and in
``docs/r76-p4-baseline-triage.md`` — never silently swallowed.

An exemption must explain itself, and the explanation must be machine-checkable:

* ``reason=<class>`` — one of ``state-dependent``, ``env``, ``order``.  Printed
  whenever the red occurs, so a human reading the output sees WHY it is
  tolerated, not merely that it was.  A missing or unknown class is refused
  before the suite runs.
* ``expect="<substring>"`` — a signature lifted from the recorded failure.  A red
  at that node is tolerated only when its junit message/traceback still contains
  this substring.  A different cause is a regression and fails the gate, which is
  the point: colour alone is not a reason.

An ``optional`` entry must also APPEAR in the run (red or green).  A node id that
never ran — typo, renamed test, deselected, file removed — fails the gate as
DRIFT, because an exemption that matches nothing is a lie about the code (the
same rule S1 applies to ``.importlinter``).  Green-but-present is allowed and
noted: it means the class is doing no work this run, which the triage doc, not
this script, must re-decide.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASELINE = os.path.join(REPO_ROOT, "tests", "baseline_failures.txt")

ALLOWED_REASONS = ("state-dependent", "env", "order")


class OptionalSpec(NamedTuple):
    """A declared exemption: what may be red, why, and with which signature."""

    nodeid: str
    reason: str
    expect: str


# --------------------------------------------------------------------------- #
# pinned file
# --------------------------------------------------------------------------- #
def read_baseline(path: str) -> Tuple[List[str], Dict[str, OptionalSpec], List[str]]:
    """Return (required, {nodeid: OptionalSpec}, errors), in file order.

    ``errors`` is non-empty when an ``optional`` line omits its reason class or
    its failure signature — an exemption that cannot explain itself is refused
    rather than honoured.
    """
    required: List[str] = []
    optional: Dict[str, OptionalSpec] = {}
    errors: List[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # strip a trailing explanatory comment: "nodeid  # why"
            if "  #" in line:
                line = line.split("  #", 1)[0].strip()
            try:
                tokens = shlex.split(line)
            except ValueError as exc:
                errors.append(f"{path}:{lineno}: cannot parse line ({exc}): {raw.rstrip()}")
                continue
            if not tokens:
                continue
            kind = "required"
            if tokens[0] == "optional":
                kind = "optional"
                tokens = tokens[1:]
            if not tokens:
                errors.append(f"{path}:{lineno}: '{kind}' with no node id")
                continue
            nodeid = tokens[0]
            attrs: Dict[str, str] = {}
            bad_attr = False
            for tok in tokens[1:]:
                key, sep, value = tok.partition("=")
                if not sep or not key or not value:
                    errors.append(
                        f"{path}:{lineno}: stray token {tok!r} (expected key=value)"
                    )
                    bad_attr = True
                    break
                attrs[key] = value
            if bad_attr:
                continue
            if kind == "required":
                if attrs:
                    errors.append(
                        f"{path}:{lineno}: required entry takes no attributes: {nodeid}"
                    )
                    continue
                required.append(nodeid)
                continue

            reason = attrs.get("reason", "")
            expect = attrs.get("expect", "")
            unknown = sorted(set(attrs) - {"reason", "expect"})
            if unknown:
                errors.append(f"{path}:{lineno}: unknown attribute(s) {unknown} on {nodeid}")
            if reason not in ALLOWED_REASONS:
                errors.append(
                    f"{path}:{lineno}: optional entry {nodeid} declares reason={reason!r};"
                    f" must be one of {', '.join(ALLOWED_REASONS)}"
                )
            if not expect:
                errors.append(
                    f"{path}:{lineno}: optional entry {nodeid} declares no expect=\"...\""
                    " failure signature — an unexplained exemption is refused"
                )
            if nodeid in optional:
                errors.append(f"{path}:{lineno}: duplicate optional entry {nodeid}")
            optional[nodeid] = OptionalSpec(nodeid, reason, expect)
    return required, optional, errors


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


def _failure_detail(child: ET.Element) -> str:
    """Message plus traceback text of a <failure>/<error> element."""
    msg = child.get("message") or ""
    return (msg + "\n" + (child.text or "")).strip()


def parse_junit(path: str) -> Tuple[Dict[str, Tuple[str, str]], Set[str]]:
    """Return ({nodeid: (kind, detail)} for reds, all node ids that ran)."""
    tree = ET.parse(path)
    root = tree.getroot()
    bad: Dict[str, Tuple[str, str]] = {}
    present: Set[str] = set()
    for case in root.iter("testcase"):
        nodeid = _nodeid_from_case(case)
        present.add(nodeid)
        for child in case:
            tag = child.tag.split("}")[-1]
            if tag in ("failure", "error"):
                bad[nodeid] = (tag, _failure_detail(child))
                break
    return bad, present


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

    required, optional, pin_errors = read_baseline(args.baseline)
    if pin_errors:
        print("baseline_check: PIN FILE INVALID — refusing to run against it:")
        for err in pin_errors:
            print(f"  ! {err}")
        print("\nbaseline_check: DRIFT")
        return 1
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
        bad, present = parse_junit(junit_path)
    except Exception as exc:  # noqa: BLE001
        print(f"baseline_check: cannot parse junit at {junit_path}: {exc}")
        return 2

    actual = set(bad)
    req = set(required)
    opt = set(optional)
    missing = sorted(req - actual)                  # pinned red, now green
    new = sorted(actual - req - opt)                # red, not pinned anywhere

    opt_red: List[OptionalSpec] = []
    opt_mismatch: List[Tuple[OptionalSpec, str, str]] = []
    opt_green: List[str] = []
    opt_absent: List[str] = []
    for nodeid in sorted(opt):
        spec = optional[nodeid]
        if nodeid in actual:
            kind, detail = bad[nodeid]
            if spec.expect in detail:
                opt_red.append(spec)
            else:
                observed = (detail.splitlines() or [""])[-1].strip()
                opt_mismatch.append((spec, kind, observed))
        elif nodeid in present:
            opt_green.append(nodeid)
        else:
            opt_absent.append(nodeid)

    print("")
    print(f"baseline_check: pinned required={len(req)} optional={len(opt)}")
    print(f"baseline_check: actual failures/errors={len(actual)}")
    print(
        "baseline_check: variant = "
        f"{len(actual)} red"
        f"{' (optional order/state-dependent member RED: ' + ', '.join(s.nodeid for s in opt_red) + ')' if opt_red else ' (no optional members red)'}"
    )
    for spec in opt_red:
        print(
            f"  optional member RED (tolerated): {spec.nodeid}\n"
            f"    reason={spec.reason}; failure signature matched expect={spec.expect!r}"
        )
    for nodeid in opt_green:
        print(f"  optional member green this run: {nodeid}")

    ok = not missing and not new and not opt_absent and not opt_mismatch

    if missing:
        print("\nbaseline_check: PINNED-RED-NOW-GREEN (baseline drifted, re-pin deliberately):")
        for node in missing:
            print(f"  - {node}")
    if new:
        print("\nbaseline_check: UNPINNED RED (a slice introduced this, or the baseline is stale):")
        for node in new:
            print(f"  + {node}  [{bad[node][0]}]")
    if opt_absent:
        print(
            "\nbaseline_check: OPTIONAL ID NOT PRESENT IN RUN — the exemption matches"
            " nothing (stale, mistyped, renamed or deselected); that is DRIFT:"
        )
        for node in opt_absent:
            print(f"  ? {node}  (declared reason={optional[node].reason})")
    if opt_mismatch:
        print(
            "\nbaseline_check: OPTIONAL REASON MISMATCH — this red is not the red the"
            " pin declares, so the colour is not the exemption:"
        )
        for spec, kind, observed in opt_mismatch:
            print(
                f"  ! {spec.nodeid}  [{kind}] reason={spec.reason}"
                f" expect={spec.expect!r}\n    observed: {observed}"
            )
    if opt_green and not opt_red:
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
