"""M29 · F4 — install shape.

One route: `scripts/install.sh` copies a checkout into
`<hermes_home>/plugins/hermes_router`, and INSTALL.md documents that same
route. These tests pin the properties the audit found broken at 1e9dab3:

  * no rsync dependency              (`which rsync` -> not installed)
  * no destructive mirror            (`--delete` over a live plugin dir)
  * no fleet-literal target          (`/opt/data/.hermes/profiles/<p>/plugins`)
  * no s6-only instructions          (`/command/s6-svc`, `/run/service`)
  * target parameterised             (HERMES_HOME / --hermes-home / --plugins-root)
  * dry-run first, and it writes nothing
  * idempotent + deletes nothing
  * exactly ONE documented route
"""
import os
import re
import shutil
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTALL_SH = os.path.join(REPO, "scripts", "install.sh")
INSTALL_MD = os.path.join(REPO, "INSTALL.md")

pytestmark = [
    pytest.mark.local_exec,
    pytest.mark.skipif(
        shutil.which("bash") is None or shutil.which("tar") is None,
        reason="install.sh is a bash+tar script",
    ),
]


def _script_lines():
    with open(INSTALL_SH, encoding="utf-8") as fh:
        return fh.read().splitlines()


def _executable_text():
    """Script text with comment lines removed — a comment may NAME rsync when
    explaining that it is not used; only real code can depend on it."""
    return "\n".join(
        ln for ln in _script_lines()
        if not ln.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# Static properties of the script
# ---------------------------------------------------------------------------

def test_no_rsync_and_no_delete_flag():
    code = _executable_text()
    # command position only: prose ("needs no rsync") may name the tool it
    # deliberately does not use; a real dependency is `rsync ...` at the start
    # of a command, after a pipe, or after a shell separator.
    assert not re.search(r"(?:^|[|&;(]\s*)rsync\b", code, re.M), \
        "install.sh still shells out to rsync"
    assert "--delete" not in code, "install.sh still mirrors destructively"


def test_no_fleet_literal_target_and_no_s6_instructions():
    text = "\n".join(_script_lines())
    assert "/opt/data" not in text, "install.sh bakes a fleet path"
    for fleet_only in ("s6-svc", "s6-svstat", "/run/service"):
        assert fleet_only not in text, "install.sh tells a stranger to run %s" % fleet_only


def test_target_is_a_parameter():
    text = "\n".join(_script_lines())
    for knob in ("--hermes-home", "--plugins-root", "HERMES_HOME", "HERMES_PROFILE"):
        assert knob in text, "install.sh does not honour %s" % knob


def test_script_is_valid_bash():
    proc = subprocess.run(["bash", "-n", INSTALL_SH], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# Dry run — the first thing a user is told to run, and it must not touch disk
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_PROFILE="dry-agent")
    env.pop("HERMES_PROFILE_HOME", None)
    proc = subprocess.run(["bash", INSTALL_SH, "--dry-run"], capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "dry-run" in out
    assert str(home / "plugins" / "hermes_router") in out
    assert "profile:  dry-agent" in out
    assert not home.exists(), "a dry run created the target home"


# ---------------------------------------------------------------------------
# Real run on a fixture checkout — idempotent, excludes, deletes nothing
# ---------------------------------------------------------------------------

@pytest.fixture()
def fixture_checkout(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "scripts").mkdir(parents=True)
    (repo / "core").mkdir()
    shutil.copy2(INSTALL_SH, repo / "scripts" / "install.sh")
    (repo / "plugin.yaml").write_text("name: hermes-router\n", encoding="utf-8")
    (repo / "__init__.py").write_text("", encoding="utf-8")
    (repo / "core" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    # excluded families (must not be shipped to a profile)
    for d in ("tests", "benchmarks", ".git"):
        (repo / d).mkdir()
        (repo / d / "junk.txt").write_text("junk\n", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "mod.cpython-313.pyc").write_text("x", encoding="utf-8")
    return repo


def _run(repo, home, *args):
    env = dict(os.environ, HERMES_HOME=str(home))
    env.pop("HERMES_PROFILE_HOME", None)
    return subprocess.run(["bash", str(repo / "scripts" / "install.sh"), *args],
                          capture_output=True, text=True, env=env)


def _manifest(root):
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if "__pycache__" in rel or rel.endswith(".pyc"):
                continue
            with open(full, "rb") as fh:
                out[rel] = fh.read()
    return out


def test_install_copies_only_shipping_files(fixture_checkout, tmp_path):
    home = tmp_path / "home"
    proc = _run(fixture_checkout, home)
    assert proc.returncode == 0, proc.stderr
    dst = home / "plugins" / "hermes_router"
    assert (dst / "plugin.yaml").is_file()
    assert (dst / "__init__.py").is_file()
    assert (dst / "core" / "mod.py").is_file()
    for excluded in ("tests", "benchmarks", ".git", "__pycache__"):
        assert not (dst / excluded).exists(), "%s was shipped" % excluded


def test_install_is_idempotent_and_deletes_nothing(fixture_checkout, tmp_path):
    home = tmp_path / "home"
    assert _run(fixture_checkout, home).returncode == 0
    dst = home / "plugins" / "hermes_router"
    first = _manifest(dst)

    # an operator file that the checkout does not ship
    (dst / "operator_note.txt").write_text("keep me\n", encoding="utf-8")

    proc = _run(fixture_checkout, home)
    assert proc.returncode == 0, proc.stderr
    second = _manifest(dst)

    assert {k: v for k, v in second.items() if k != "operator_note.txt"} == first, \
        "re-running install.sh changed the installed tree"
    assert (dst / "operator_note.txt").read_text(encoding="utf-8") == "keep me\n", \
        "install.sh deleted a file it does not ship"
    assert "operator_note.txt" in proc.stdout, "stale file was not reported"


def test_plugins_root_overrides_hermes_home(fixture_checkout, tmp_path):
    root = tmp_path / "custom-plugins"
    proc = _run(fixture_checkout, tmp_path / "unused", "--plugins-root", str(root))
    assert proc.returncode == 0, proc.stderr
    assert (root / "hermes_router" / "plugin.yaml").is_file()
    assert not (tmp_path / "unused" / "plugins").exists()


# ---------------------------------------------------------------------------
# Documentation — one route, not two
# ---------------------------------------------------------------------------

def test_install_md_documents_the_script_route_only():
    with open(INSTALL_MD, encoding="utf-8") as fh:
        doc = fh.read()
    assert "scripts/install.sh" in doc, "INSTALL.md does not document the shipped script"
    assert "--dry-run" in doc, "INSTALL.md does not tell the user to dry-run first"
    assert "hermes plugins install" not in doc, \
        "INSTALL.md still documents the second (deleted) install route"
