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


# --- class-aware absence labels -------------------------------------------
# Rows below are copied from the M36-08 live-log evidence, one fixture per
# class, so the label is pinned against a real observed window rather than a
# shape invented for the test.

F1_WINDOW = (
    "2026-11-06T11:00:03Z PRE event_detail=complexity_skip "
    "reason=pre_no_impact stage1=clear_complex score=1\n"
)
U1_WINDOW = (
    "2026-11-06T10:57:35Z PRE event_detail=denial_class cls=not_a_denial\n"
    "2026-11-06T10:57:35Z PRE event_detail=rule_table_decision rule=3 "
    "predicate=gate_abstain action=defer\n"
    "2026-11-06T10:57:35Z POST event_detail=main_body_kept\n"
)
F2_CADENCE = (
    "2026-11-05T18:13:00Z POST event_detail=audit_gate_skip reason=cadence "
    "turn=2 of=3\n"
)


def test_class_drift_when_the_row_exists_in_a_log_not_graded():
    """Row present elsewhere -> grader_path_drift, by its exact class string."""
    graded = F1_WINDOW
    configured = F1_WINDOW + (
        "2026-11-06T10:57:35Z POST event_detail=completion_audit_gate ok=True "
        "reason=ok turn=2 of=3 closure=True\n"
    )
    mod = _runner()
    cls, evidence = mod.classify_absent_event(
        "completion_audit_gate", graded, [("coder/uncensored-router.log", configured)])
    assert cls == "grader_path_drift"
    assert "coder/uncensored-router.log" in evidence


def test_class_unreached_when_the_impact_gate_stood_down():
    """F1: the tradeoff ask never staged a swap -> path not taken, not missing."""
    mod = _runner()
    cls, evidence = mod.classify_absent_event("anchor_route_fired", F1_WINDOW, [])
    assert cls == "not_instrumented_path_not_taken"
    assert "pre_no_impact" in evidence


def test_class_unreached_when_the_render_was_not_a_denial():
    """U1: refusal class not_a_denial + rule 3 defer leaves :3116 unreachable."""
    mod = _runner()
    cls, evidence = mod.classify_absent_event("route_fired", U1_WINDOW, [])
    assert cls == "not_instrumented_path_not_taken"
    assert "not_a_denial" in evidence


def test_class_unreached_when_the_audit_gate_skipped_on_cadence():
    """F2 shadow: cadence skip means the gate row is never written."""
    mod = _runner()
    cls, evidence = mod.classify_absent_event("completion_audit_gate", F2_CADENCE, [])
    assert cls == "not_instrumented_path_not_taken"
    assert "audit_gate_skip" in evidence


def test_absence_is_never_labelled_missing_emitter():
    """No evidence -> no class, and the string never appears in the runner."""
    mod = _runner()
    cls, evidence = mod.classify_absent_event("anchor_route_fired", "benign turn\n", [])
    assert cls is None
    assert "missing_emitter" not in evidence
    assert "missing_emitter" not in open(RUNNER, errors="replace").read()
