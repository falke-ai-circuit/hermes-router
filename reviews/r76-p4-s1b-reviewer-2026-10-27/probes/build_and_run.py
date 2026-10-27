#!/usr/bin/env python3
"""Reviewer's own S1b probes — built from scratch at HEAD 1f4a2f0.

Synthetic repo: scripts/baseline_check.py copied verbatim from the commit,
tests/test_pinned.py carrying a required red, a legit state-dependent red
(signature "assert None is not None"), and a red with a DIFFERENT cause.
No coder artifacts are used.
"""
import os, shutil, subprocess, sys, textwrap

SCR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(SCR, "repo")
SRC = "/opt/data/plugins/hermes_router"

shutil.rmtree(REPO, ignore_errors=True)
os.makedirs(os.path.join(REPO, "scripts"))
os.makedirs(os.path.join(REPO, "tests"))

# baseline_check.py exactly as committed at HEAD
subprocess.run(["git", "-C", SRC, "show", "1f4a2f0:scripts/baseline_check.py"],
               check=True, stdout=open(os.path.join(REPO, "scripts", "baseline_check.py"), "w"))

open(os.path.join(REPO, "tests", "test_pinned.py"), "w").write(textwrap.dedent('''
    def test_pinned_red():
        assert 0, "pinned red"

    def test_green():
        assert True

    def test_opt():
        # state-dependent red, the recorded signature
        assert None is not None

    def test_opt_other():
        # real failure inside an optional node, DIFFERENT cause
        assert 1 == 2, "different cause: key set changed"

    def test_opt_third():
        msg = "envelope lost the mindset key"
        assert not msg
'''))

P = "tests/test_pinned.py"
SYN = "assert None is not None"

cases = {
 # C: optional id that never ran / never existed
 "C_never_exists": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_never_exists_anywhere  reason=state-dependent  expect=\"{SYN}\"\n",
   []),
 "C2_deselected": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_opt  reason=state-dependent  expect=\"{SYN}\"\n",
   ["--pytest-arg=--deselect", f"--pytest-arg={P}::test_opt"]),
 # D: real failure inside an optional, different cause than the pin declares
 "D_other_cause": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_opt_other  reason=state-dependent  expect=\"{SYN}\"\n",
   []),
 "D2_third_cause": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_opt_third  reason=state-dependent  expect=\"{SYN}\"\n",
   []),
 # legit: the state-dependent red, signature matches -> tolerated, reason printed
 "LEGIT_matching_signature": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_opt  reason=state-dependent  expect=\"{SYN}\"\n",
   []),
 # unexplained exemption: no reason / no expect
 "U_no_reason": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_opt  expect=\"{SYN}\"\n", []),
 "U_no_expect": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_opt  reason=state-dependent\n", []),
 "U_bad_reason": (
   f"tests/test_pinned.py::test_pinned_red\n"
   f"optional {P}::test_opt  reason=flaky  expect=\"{SYN}\"\n", []),
}

out = []
for name, (pin, extra) in cases.items():
    pinpath = os.path.join(SCR, f"pin_{name}.txt")
    open(pinpath, "w").write(pin)
    cmd = [sys.executable, os.path.join(REPO, "scripts", "baseline_check.py"),
           "--baseline", pinpath, "--junit", os.path.join(SCR, f"junit_{name}.xml")] + extra
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": REPO})
    body = (p.stdout + p.stderr).strip()
    out.append(f"===== {name}  EXIT {p.returncode} =====\n{body}\n")

report = "\n".join(out)
open(os.path.join(SCR, "probe_results.txt"), "w").write(report)
print(report)
