"""Liveness battery for the import guard (.importlinter).

The guard is only worth its runtime if each contract actually fails on a real
violation. A contract whose `ignore_imports` went stale, or a `forbidden` pair
that nests (import-linter silently skips overlapping source/forbidden pairs),
runs green forever and proves nothing.

This battery injects ONE genuine upward direct import per contract into a
throwaway copy of the package tree, re-runs `lint-imports`, and asserts exit 1
with that contract reported BROKEN. The live tree is never mutated: every probe
runs on a copy whose `hermes_router.__file__` is asserted to live under the
temporary directory (otherwise a green run could be the live tree passing).

Skipped cleanly when lint-imports is not installed.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS_FILE = PLUGIN_DIR / ".importlinter"

# tests/conftest.py::_zero_network_guard replaces subprocess.run with a tripwire
# for the whole suite (it exists to stop curl-backed provider lanes from paying
# real egress). This battery must spawn the real `lint-imports` binary, which is
# a local static analyser: it parses source into a grimp graph and opens no
# socket. Capture the genuine callable at import time -- fixtures run after
# collection -- and call that, instead of widening the zero-network guard.
_REAL_RUN = subprocess.run

# (contract display name, file relative to the package root, import to append)
# Each line is a genuine upward direct import for that contract's layer. The
# features/lanes hub probe is spelled with the correct number of dots for the
# module's depth: `from ...` inside lanes/registry.py would be a
# beyond-top-level import (no graph edge, nothing to detect), so the one-deep
# hub spelling is `from .. import`.
PROBES = [
    ("core/ must not import other hermes_router modules (L0)",
     "core/state.py", "from .. import suggestions"),
    ("core/ must not import hermes_cli (except the config_access adapter)",
     "core/state.py", "import hermes_cli"),
    ("gate may not import api (direct)",
     "gate/orchestration.py", "from ..api import routes"),
    ("passes may not import api (direct)",
     "passes/dispatcher_post.py", "from ..api import router_tools"),
    ("features may not import api/gate/passes (direct)",
     "features/patterns/__init__.py", "from ...gate import registration"),
    ("lanes may not import api/gate/passes/features (direct)",
     "lanes/registry.py", "from hermes_router.passes import dispatcher_pre"),
    ("core may not import api/gate/passes/features/lanes (direct)",
     "core/state.py", "from ..lanes import registry"),
    ("features/lanes may not import the root package hermes_router (direct)",
     "features/banners/lifecycle.py", "from ... import router_tools"),
]


def _find_linter():
    found = shutil.which("lint-imports")
    if found:
        return found
    fallback = pathlib.Path.home() / ".local" / "bin" / "lint-imports"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    return None


LINTER = _find_linter()


def _run(tmp_parent: pathlib.Path):
    linter = LINTER
    assert linter is not None  # guarded by the skipif on the test
    env = dict(os.environ, PYTHONPATH=str(tmp_parent))
    proc = _REAL_RUN(
        [linter],
        cwd=tmp_parent / "hermes_router",
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc


def _enumerated_sources():
    """Modules listed under the hub contract's source_modules.

    Scoped to the features-lanes-no-root contract on purpose: core-purity also
    declares `source_modules`, and a whole-file scan would pick up its one-line
    `source_modules = hermes_router.core` and return an empty set.
    """
    out = set()
    lines = CONTRACTS_FILE.read_text().splitlines()
    try:
        start = lines.index("[importlinter:contract:features-lanes-no-root]")
    except ValueError:
        return out
    inside = False
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("[importlinter:contract:"):
            break
        if stripped.startswith("source_modules"):
            rest = stripped[len("source_modules"):].strip().lstrip("=").strip()
            inside = True
            if rest:                 # `source_modules = hermes_router.core`
                out.add(rest)
                inside = False
            continue
        if inside:
            if not line.startswith(("    ", "\t")) or not stripped:
                break
            if "=" in line:          # nested field name, not a value
                break
            out.add(stripped)
    return out


@pytest.mark.skipif(LINTER is None, reason="lint-imports is not installed")
def test_every_contract_fails_on_a_real_violation(tmp_path_factory):
    # 1. The hub contract enumerates its source modules (as_packages=False), so a
    #    new module under features/ or lanes/ would escape the guard silently.
    listed = _enumerated_sources()
    root = str(PLUGIN_DIR).replace("/", ".")
    for sub in ("features", "lanes"):
        for py in sorted((PLUGIN_DIR / sub).rglob("*.py")):
            rel = py.relative_to(PLUGIN_DIR)
            parts = list(rel.parts)
            if parts[-1] == "__init__.py":
                parts = parts[:-1]          # a package, not a `.__init__` module
            module = "hermes_router." + ".".join(
                p.removesuffix(".py") for p in parts)
            assert module in listed, f"{module} missing from the hub contract sources"

    # 2. One copy of the tree, one control run, then one mutation per contract.
    tmp_parent = tmp_path_factory.mktemp("import_guard")
    copy = tmp_parent / "hermes_router"
    shutil.copytree(
        PLUGIN_DIR, copy,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache",
                                      "reviews", "tests", "docs"),
    )

    # The probe must bind the copy, not the live tree -- a live import here would
    # make every BROKEN assertion below meaningless.
    bound = _REAL_RUN(
        [sys.executable, "-c", "import hermes_router; print(hermes_router.__file__)"],
        cwd=copy, env=dict(os.environ, PYTHONPATH=str(tmp_parent)),
        capture_output=True, text=True, timeout=120,
    )
    assert bound.returncode == 0, bound.stderr
    assert bound.stdout.strip().startswith(str(copy)), bound.stdout

    control = _run(tmp_parent)
    assert control.returncode == 0, control.stdout + control.stderr
    assert "0 broken" in control.stdout

    for name, relpath, injected in PROBES:
        target = copy / relpath
        original = target.read_bytes()
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(f"\n{injected}  # liveness probe\n")
            result = _run(tmp_parent)
            combined = result.stdout + result.stderr
        finally:
            target.write_bytes(original)

        assert result.returncode == 1, (
            f"{name}: injecting {injected!r} into {relpath} did not fail the run "
            f"(exit {result.returncode})\n{combined}"
        )
        assert f"{name} BROKEN" in combined, (
            f"{name}: run failed but this contract was not the one broken by "
            f"{injected!r} in {relpath}\n{combined}"
        )
