#!/usr/bin/env python3
"""R76-P4-S1b mutation battery for scripts/baseline_check.py.

Runs the gate with --no-run against synthetic junit XML, so every outcome below
is caused by the pin/comparison logic and nothing else.

    python3 reviews/r76-p4-s1b-2026-10-27/probes/optional_mutation_battery.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(REPO, "scripts", "baseline_check.py")
M1 = "tests/decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stance"

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
{body}
</testsuite></testsuites>
"""


def case(file, name, failure=None, text=""):
    inner = ""
    if failure:
        inner = f'<failure message="{failure}">{text or failure}</failure>'
    return (
        f'<testcase classname="{file[:-3].replace("/", ".")}" name="{name}" '
        f'file="{file}" line="1">{inner}</testcase>'
    )


def junit(tmp, cases):
    path = os.path.join(tmp, "junit.xml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(JUNIT.format(body="\n".join(cases)))
    return path


def baseline(tmp, lines):
    path = os.path.join(tmp, "baseline.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent("\n".join(lines)) + "\n")
    return path


def run(tmp, base_lines, cases, label):
    jp = junit(tmp, cases)
    bp = baseline(tmp, base_lines)
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--no-run", "--junit", jp, "--baseline", bp],
        cwd=REPO, text=True, capture_output=True,
    )
    print(f"=== {label} -> EXIT {proc.returncode}")
    print(proc.stdout.rstrip())
    print("")
    return proc.returncode


def main() -> int:
    rc = 0
    with tempfile.TemporaryDirectory() as tmp:
        # control: declared exemption, red for the declared reason -> exit 0
        rc |= run(
            tmp,
            [f'optional {M1}  reason=state-dependent  expect="assert None is not None"'],
            [case(M1.split("::")[0], M1.split("::")[1],
                  failure="assert None is not None",
                  text=">       assert env[\"mindset\"][\"scope\"] is not None\nE       assert None is not None")],
            "PROBE c  legitimate state-dependent red, reason recorded",
        ) != 0

        # a) optional id that never ran -> must FAIL
        rc |= run(
            tmp,
            [f'optional {M1}  reason=state-dependent  expect="assert None is not None"'],
            [case("tests/decision_lane/test_r53_midturn_mindset.py",
                  "test_m1_unreadable_drive_is_null_never_invented")],
            "PROBE a  optional id absent from the run (stale/mistyped)",
        ) != 1

        # a2) typo of a real id -> same rule
        rc |= run(
            tmp,
            ['optional tests/decision_lane/test_r53_midturn_mindset.py::test_m1_envelope_carries_the_current_stanc  reason=state-dependent  expect="assert None is not None"'],
            [case("tests/decision_lane/test_r53_midturn_mindset.py",
                  "test_m1_envelope_carries_the_current_stance",
                  failure="assert None is not None")],
            "PROBE a2 one-character typo of a live id",
        ) != 1

        # b) SAME node failing with a DIFFERENT cause -> must FAIL
        rc |= run(
            tmp,
            [f'optional {M1}  reason=state-dependent  expect="assert None is not None"'],
            [case("tests/decision_lane/test_r53_midturn_mindset.py",
                  M1.split("::")[1],
                  failure="KeyError: 'mindset'",
                  text="E       KeyError: 'mindset'")],
            "PROBE b  same node, different failure cause (regression)",
        ) != 1

        # b2) key-set change (the reviewer's named regression) -> must FAIL
        rc |= run(
            tmp,
            [f'optional {M1}  reason=state-dependent  expect="assert None is not None"'],
            [case("tests/decision_lane/test_r53_midturn_mindset.py",
                  M1.split("::")[1],
                  failure="assert {'scope', 'rigour', 'mood'} == {'scope', 'rigour'}",
                  text="E       AssertionError: assert {'scope', 'rigour', 'mood'} == {'scope', 'rigour'}")],
            "PROBE b2 envelope key set changed",
        ) != 1

        # refusal: optional with no reason -> pin refused before running
        rc |= run(
            tmp,
            [f'optional {M1}  expect="assert None is not None"'],
            [case("tests/decision_lane/test_r53_midturn_mindset.py", M1.split("::")[1])],
            "PROBE d  optional entry with no reason class",
        ) != 1

        # refusal: optional with no signature -> pin refused
        rc |= run(
            tmp,
            [f"optional {M1}  reason=state-dependent"],
            [case("tests/decision_lane/test_r53_midturn_mindset.py", M1.split("::")[1])],
            "PROBE e  optional entry with no failure signature",
        ) != 1

        # green-but-present stays allowed (the class doing no work)
        rc |= run(
            tmp,
            [f'optional {M1}  reason=state-dependent  expect="assert None is not None"'],
            [case("tests/decision_lane/test_r53_midturn_mindset.py", M1.split("::")[1])],
            "PROBE f  optional entry green this run (still allowed, noted)",
        ) != 0

    print("BATTERY:", "PASS" if rc == 0 else "FAIL")
    return rc


if __name__ == "__main__":
    sys.exit(main())
