"""M36-09: the graded battery must read the route log its router writes to.

The runner used to probe ``/opt/data/profiles/<p>/uncensored-router.log`` and
fall back to ``/tmp/uncensored-router-<p>.log`` whenever that file existed-but-
was-empty-of-this-run's-rows or did not exist. For coder and operative the
configured (live) path is the per-profile one, so the battery graded a
tombstone with zero battery sids while the real rows sat in the configured log.

These tests pin the resolution contract: config decides, an absent configured
path is a loud error, and the /tmp sibling is never used as a silent fallback.
"""
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO, "benchmarks", "battery_run.py")


def _runner():
    """Import benchmarks/battery_run.py by path (benchmarks/ is not a package)."""
    spec = importlib.util.spec_from_file_location("battery_run_m36_09", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["battery_run_m36_09"] = mod
    spec.loader.exec_module(mod)
    return mod


def _profile(tmp_path, name, log_path):
    """Write a minimal profile tree: config.yaml with hermes_router.log_path."""
    pdir = tmp_path / name
    pdir.mkdir()
    (pdir / "config.yaml").write_text(
        "hermes_router:\n"
        "  enabled: true\n"
        "  log_path: %s\n" % log_path
    )
    return pdir


def test_resolver_reads_configured_path(tmp_path):
    """The configured path is returned verbatim, even though it is unusual."""
    log = tmp_path / "chosen-route-log.log"
    log.write_text("")
    _profile(tmp_path, "p1", log)
    mod = _runner()
    assert mod.router_log_path("p1", profiles_root=str(tmp_path)) == str(log)


def test_resolver_ignores_the_tmp_sibling(tmp_path):
    """Regression: an existing sibling log must not win over the config.

    This is the M36-09 defect in one assertion — coder/operative had live rows
    in the per-profile log while a stale /tmp file was graded instead.
    """
    configured = tmp_path / "p2" / "uncensored-router.log"
    _profile(tmp_path, "p2", configured)
    configured.write_text("live row\n")
    sibling = tmp_path / "uncensored-router-p2.log"
    sibling.write_text("tombstone\n")
    mod = _runner()
    assert mod.router_log_path("p2", profiles_root=str(tmp_path)) == str(configured)
    assert mod.router_log_path("p2", profiles_root=str(tmp_path)) != str(sibling)


def test_resolver_raises_loudly_when_configured_path_missing(tmp_path):
    """A missing configured log is an error naming the path, never a fallback."""
    missing = tmp_path / "p3" / "uncensored-router.log"
    _profile(tmp_path, "p3", missing)
    (tmp_path / "uncensored-router-p3.log").write_text("tombstone\n")
    mod = _runner()
    with pytest.raises(RuntimeError) as ei:
        mod.router_log_path("p3", profiles_root=str(tmp_path))
    assert str(missing) in str(ei.value)
    assert "uncensored-router-p3.log" != os.path.basename(str(ei.value))


def test_resolver_raises_when_config_lacks_log_path(tmp_path):
    """No log_path key is also a loud error, not a guessed default."""
    pdir = tmp_path / "p4"
    pdir.mkdir()
    (pdir / "config.yaml").write_text("hermes_router:\n  enabled: true\n")
    mod = _runner()
    with pytest.raises(RuntimeError) as ei:
        mod.router_log_path("p4", profiles_root=str(tmp_path))
    assert "log_path" in str(ei.value)
