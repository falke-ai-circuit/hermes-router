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
import json
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
    """Row attributable to THIS run elsewhere -> grader_path_drift.

    M36-17: the class needs the row to be this run's own — exact event token,
    exact scenario sid, and a leading timestamp inside the run window. The
    file merely containing the event name is not evidence (that was B1/F1).
    """
    graded = F1_WINDOW
    configured = F1_WINDOW + (
        "%s POST event_detail=completion_audit_gate ok=True "
        "reason=ok turn=2 of=3 closure=True session_id=SID-1\n" % _stamp(RUN_START + 30)
    )
    mod = _runner()
    attributed = mod.attributable_event_rows(
        "completion_audit_gate", [("coder/uncensored-router.log", configured)],
        "SID-1", RUN_START, RUN_START + 60)
    assert len(attributed) == 1
    cls, evidence = mod.classify_absent_event(
        "completion_audit_gate", graded, attributed, sid="SID-1",
        run_start=RUN_START)
    assert cls == "grader_path_drift"
    assert "coder/uncensored-router.log" in evidence
    assert "this run's own row" in evidence


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


# --- REVIEW P2-1: the stand-down slice is the turn's own session -----------

SID_A = "api_1793962551_74da86fd"
SID_B = "api_1793999999_deadbeef"


def test_stand_down_row_from_another_session_does_not_label_this_one():
    """A borrowed skip row must not convert this session's absence into a class."""
    graded = (
        "2026-11-06T11:00:03Z PRE session_id=%s event_detail=complexity_skip "
        "reason=pre_no_impact\n" % SID_A
    )
    mod = _runner()
    # Without the window the tail does support the class (the P2-1 defect shape).
    borrowed, _ = mod.classify_absent_event("anchor_route_fired", graded, [])
    assert borrowed == "not_instrumented_path_not_taken"
    # With the turn's own session slice there is no row at all -> no class.
    cls, _ = mod.classify_absent_event(
        "anchor_route_fired", graded, [],
        window_text=mod.session_window(graded, SID_B))
    assert cls is None


def test_session_window_keeps_only_the_named_sessions_rows():
    graded = (
        "2026-11-06T11:00:03Z PRE session_id=%s event_detail=complexity_skip "
        "reason=pre_no_impact\n" % SID_A
        + "2026-11-06T11:00:09Z PRE session_id=%s event_detail=complexity_skip "
          "reason=pre_no_impact\n" % SID_B
    )
    mod = _runner()
    window = mod.session_window(graded, SID_B)
    assert SID_B in window
    assert SID_A not in window
    cls, _ = mod.classify_absent_event(
        "anchor_route_fired", graded, [], window_text=window)
    assert cls == "not_instrumented_path_not_taken"


# --- M36-11 item 2: the runner delegates resolution, it does not re-derive ---

def test_resolver_uses_the_writers_key_order(tmp_path):
    """paths.log_path (grouped) must win over legacy log_path, as in the writer."""
    grouped = tmp_path / "grouped.log"
    legacy = tmp_path / "legacy.log"
    grouped.write_text("row\n")
    legacy.write_text("row\n")
    pdir = tmp_path / "p6"
    pdir.mkdir()
    (pdir / "config.yaml").write_text(
        "hermes_router:\n"
        "  log_path: %s\n"
        "  paths:\n"
        "    log_path: %s\n" % (legacy, grouped)
    )
    mod = _runner()
    assert mod.router_log_path("p6", profiles_root=str(tmp_path)) == str(grouped)


def test_resolver_delegates_to_config_access(tmp_path):
    """The resolver's answer is config_access.route_log_path's answer, not a copy."""
    log = tmp_path / "delegated.log"
    log.write_text("row\n")
    _profile(tmp_path, "p7", log)
    mod = _runner()
    access = mod._config_access()
    section = access.section_at(str(tmp_path / "p7" / "config.yaml"))
    assert mod.router_log_path("p7", profiles_root=str(tmp_path)) == \
        access.route_log_path(section)


# --- M36-11 item 1: a stale graded log is INCONCLUSIVE, never a FAIL ---------

RUN_START = 1794000000.0  # fixed epoch: rows below are stamped around it


def _stamp(epoch):
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(epoch))


def test_empty_configured_log_is_stale_not_gradable():
    """The reviewer's probe: a 0-byte configured file, mtime now."""
    mod = _runner()
    assert mod.grade_freshness("", 0, RUN_START, RUN_START) == "GRADED_LOG_STALE"
    assert mod.grade_freshness("", 0, RUN_START + 5, RUN_START) == "GRADED_LOG_STALE"


def test_rows_from_before_the_run_are_stale():
    mod = _runner()
    old = "%s PRE event_detail=route_fired\n" % _stamp(RUN_START - 7200)
    assert mod.grade_freshness(old, len(old), RUN_START - 7200, RUN_START) == \
        "GRADED_LOG_STALE"


def test_row_during_the_run_is_gradable_even_with_an_old_mtime():
    mod = _runner()
    fresh = ("%s PRE event_detail=route_fired\n" % _stamp(RUN_START - 7200)
             + "%s POST event_detail=main_body_kept\n" % _stamp(RUN_START + 30))
    assert mod.grade_freshness(fresh, len(fresh), RUN_START - 7200, RUN_START) is None


def test_mtime_inside_the_window_is_gradable_for_unparsed_rows():
    mod = _runner()
    text = "no timestamp here\n"
    assert mod.grade_freshness(text, len(text), RUN_START + 10, RUN_START) is None


def test_runner_writes_inconclusive_and_exits_2_on_a_stale_graded_log(tmp_path, monkeypatch):
    """Item 1, end to end at the runner's verdict boundary.

    The gateway is stubbed (probe + battery subprocess): what is pinned here is
    the runner's contract — a configured log carrying no row at/after run start
    produces overall=INCONCLUSIVE, reason=GRADED_LOG_STALE, zero graded
    scenarios and exit 2, never a FAIL charged to the router.
    """
    import json

    mod = _runner()
    stale = tmp_path / "stale-empty.log"
    stale.write_text("")
    monkeypatch.setattr(mod, "env_of",
                        lambda p: {"API_SERVER_KEY": "k", "API_SERVER_PORT": "1"})
    monkeypatch.setattr(mod, "probe", lambda base, key: (True, "ok"))
    monkeypatch.setattr(mod, "router_log_path", lambda p: str(stale))
    monkeypatch.setattr(mod, "head_sha", lambda: "deadbeef")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0,
                                                       "stdout": "", "stderr": ""})())
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv",
                        ["battery_run.py", "--profile", "pz", "--out", str(out)])

    rc = mod.main()
    doc = json.loads(out.read_text())
    assert rc == 2
    assert doc["overall"] == "INCONCLUSIVE"
    assert doc["reason"] == "GRADED_LOG_STALE"
    assert doc["scenarios"] == []
    assert doc["totals"]["fail"] == 0
    assert "ledger" in doc["error"]


# --- M36-13 item 1: freshness must not gate evidence it does not cover ------
#
# The M36-11 guard was evaluated before grading and consulted only the log, so
# a run with recorded-and-failed turns emitted INCONCLUSIVE with scenarios=[]
# and the turn failure vanished; a ledger-proven PASS was suppressed the same
# way. Order at HEAD: turns -> ledger -> freshness, and GRADED_LOG_STALE only
# when neither the log nor the ledger carries the run.

CONDUCTOR_ROUTE_LOG = "/opt/data/profiles/conductor/scripts/route_log.py"


def _main_with_evidence(tmp_path, monkeypatch, sids, turns, rows,
                        log_text=""):
    """Run main() with the gateway, battery stdout and ledger stubbed.

    ``log_text`` is written to the graded log: "" is the reviewer's probe (the
    configured file exists, 0 bytes, so grade_freshness says STALE).
    """
    import json

    mod = _runner()
    stale = tmp_path / "graded.log"
    stale.write_text(log_text)
    monkeypatch.setattr(mod, "env_of",
                        lambda p: {"API_SERVER_KEY": "k", "API_SERVER_PORT": "1"})
    monkeypatch.setattr(mod, "probe", lambda base, key: (True, "ok"))
    monkeypatch.setattr(mod, "router_log_path", lambda p: str(stale))
    monkeypatch.setattr(mod, "head_sha", lambda: "deadbeef")
    monkeypatch.setattr(mod, "parse_stdout", lambda raw: (sids, turns))
    monkeypatch.setattr(mod, "read_ledger",
                        lambda p, start: (str(tmp_path / "decisions.jsonl"), rows))
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0,
                                                       "stdout": "", "stderr": ""})())
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv",
                        ["battery_run.py", "--profile", "pz", "--out", str(out)])
    rc = mod.main()
    return rc, json.loads(out.read_text())


def _ledger_row(sid, line=7):
    return {"session_id": sid, "ts": RUN_START + 1, "rule_id": "r1",
            "lane": "main", "event_seq": 1, "replaced": None,
            "replacement_path": None, "delivered_chars": 12, "_line": line}


def test_empty_log_with_failed_turns_is_a_FAIL_not_INCONCLUSIVE(tmp_path, monkeypatch):
    """The reviewer's probe, fixed: empty log + 1 recorded failed turn -> FAIL.

    The ledger carries the run's row, so the run is attributable; the turn
    failure is the verdict and must survive a stale log.
    """
    rc, doc = _main_with_evidence(
        tmp_path, monkeypatch,
        sids={"s1": "SID-1"},
        turns={"s1": [{"ok": False, "detail": "missing_event:route_fired"}]},
        rows=[_ledger_row("SID-1")])
    assert rc == 1
    assert doc["overall"] == "FAIL"
    assert doc["totals"]["fail"] == 1
    assert len(doc["scenarios"]) == 1
    assert doc["scenarios"][0]["verdict"] == "FAIL"
    assert doc["scenarios"][0]["turns_failed"] == 1
    assert doc["log_freshness"] == "GRADED_LOG_STALE"
    assert "stale_log_note" in doc


def test_ledger_proven_PASS_survives_a_stale_log(tmp_path, monkeypatch):
    """The reverse damage: a lane-attributing ledger row keeps its PASS.

    The log carries no row at/after run start; the verdict is not discarded —
    it is graded normally and the stale log becomes a note.
    """
    rc, doc = _main_with_evidence(
        tmp_path, monkeypatch,
        sids={"s1": "SID-1"},
        turns={"s1": [{"ok": True, "detail": "ok"}]},
        rows=[_ledger_row("SID-1")])
    assert rc == 0
    assert doc["overall"] == "PASS"
    assert doc["scenarios"][0]["verdict"] == "PASS"
    assert doc["log_freshness"] == "GRADED_LOG_STALE"
    assert "stale_log_note" in doc


def test_absent_run_is_still_GRADED_LOG_STALE(tmp_path, monkeypatch):
    """Neither the log nor the ledger carries the run: the guard still fires."""
    rc, doc = _main_with_evidence(
        tmp_path, monkeypatch, sids={}, turns={}, rows=[])
    assert rc == 2
    assert doc["overall"] == "INCONCLUSIVE"
    assert doc["reason"] == "GRADED_LOG_STALE"
    assert doc["scenarios"] == []


# --- M36-13 item 2: ONE resolution rule across the four consumers -----------
#
# Behaviour chosen: the fleet CONVENTION, not a loud raise. The writer
# (core.telemetry._log_path) must always have a destination — a profile that
# loses the key would otherwise lose its telemetry — and the convention is the
# path it already wrote. With one rule, the four consumers can differ only on
# whether the file exists, never on which file it is.

def test_unset_log_path_resolves_to_one_convention_everywhere(tmp_path, monkeypatch):
    pdir = tmp_path / "p8"
    pdir.mkdir()
    (pdir / "config.yaml").write_text("hermes_router:\n  enabled: true\n")
    convention = pdir / "uncensored-router.log"
    convention.write_text("row\n")
    mod = _runner()
    access = mod._config_access()
    section = access.section_at(str(pdir / "config.yaml"))
    # 1. the battery grader
    assert mod.router_log_path("p8", profiles_root=str(tmp_path)) == str(convention)
    # 2. the shared accessor
    assert access.route_log_path(section, profile_dir=str(pdir)) == str(convention)
    # 3. the writer (core.telemetry), same rule with the live patch surface empty
    from hermes_router.core import telemetry
    monkeypatch.setattr(telemetry, "_cfg_scalar", lambda n, l: telemetry._MISSING_CFG)
    monkeypatch.setattr(access, "router_section", lambda: {})
    monkeypatch.setattr(access, "config_path", lambda: str(pdir / "config.yaml"))
    assert telemetry._log_path() == str(convention)


@pytest.mark.skipif(not os.path.exists(CONDUCTOR_ROUTE_LOG),
                    reason="conductor scripts are not on this host")
def test_conductor_audit_script_resolves_the_same_convention(tmp_path):
    import importlib.util

    pdir = tmp_path / "p8"
    pdir.mkdir()
    (pdir / "config.yaml").write_text("hermes_router:\n  enabled: true\n")
    convention = pdir / "uncensored-router.log"
    convention.write_text("row\n")
    spec = importlib.util.spec_from_file_location("route_log_m36_13", CONDUCTOR_ROUTE_LOG)
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)
    # 4. the audit scripts' resolver
    assert rl.route_log("p8", profiles_root=str(tmp_path)) == str(convention)


def test_relative_log_path_is_absolutised_by_the_shared_rule(tmp_path):
    pdir = tmp_path / "p9"
    (pdir / "logs").mkdir(parents=True)
    (pdir / "logs" / "router.log").write_text("row\n")
    (pdir / "config.yaml").write_text(
        "hermes_router:\n  log_path: logs/router.log\n")
    expected = os.path.join(str(pdir), "logs", "router.log")
    mod = _runner()
    access = mod._config_access()
    section = access.section_at(str(pdir / "config.yaml"))
    assert access.abs_route_log_path("logs/router.log", str(pdir)) == expected
    assert access.route_log_path(section, profile_dir=str(pdir)) == expected
    assert mod.router_log_path("p9", profiles_root=str(tmp_path)) == expected


# --- M36-15/B1: a stale log must not accuse a path drift it cannot see -----
#
# With a stale graded log every expected event is trivially absent, so the old
# classifier matched the first sibling log holding the name (usually the frozen
# /tmp tombstone) and shipped "the grader read a file the router did not write"
# on a FAIL whose real cause was the failed turn. The signal is only valid when
# the graded log is fresh; both directions are pinned here.

def _main_with_sibling(tmp_path, monkeypatch, profile, log_text, sibling_text,
                       now=None):
    """main() where the graded log is ``log_text`` and a sibling log holds
    ``sibling_text``; returns (rc, doc, stdout).

    ``now`` freezes the runner's clock (M36-17 test-fixture hazard: a sibling
    row stamped with the wall clock can land 0-1s BEFORE ``started`` and flake).
    """
    import io
    import json
    from contextlib import redirect_stdout

    mod = _runner()
    graded = tmp_path / "graded.log"
    graded.write_text(log_text)
    sibdir = tmp_path / profile
    sibdir.mkdir()
    (sibdir / "uncensored-router.log").write_text(sibling_text)
    if now is not None:
        import time as _time
        import types

        monkeypatch.setattr(mod, "time", types.SimpleNamespace(
            time=lambda: now, strftime=_time.strftime, gmtime=_time.gmtime,
            strptime=_time.strptime))
    monkeypatch.setattr(mod, "PROFILES", str(tmp_path))
    monkeypatch.setattr(mod, "env_of",
                        lambda p: {"API_SERVER_KEY": "k", "API_SERVER_PORT": "1"})
    monkeypatch.setattr(mod, "probe", lambda base, key: (True, "ok"))
    monkeypatch.setattr(mod, "router_log_path", lambda p: str(graded))
    monkeypatch.setattr(mod, "head_sha", lambda: "deadbeef")
    monkeypatch.setattr(mod, "parse_stdout",
                        lambda raw: ({"s1": "SID-1"},
                                     {"s1": [{"ok": False,
                                              "detail": "missing_event:route_fired"}]}))
    monkeypatch.setattr(mod, "read_ledger",
                        lambda p, start: (str(tmp_path / "decisions.jsonl"),
                                          [_ledger_row("SID-1")]))
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0,
                                                       "stdout": "", "stderr": ""})())
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv",
                        ["battery_run.py", "--profile", profile, "--out", str(out)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main()
    return rc, json.loads(out.read_text()), buf.getvalue()


TOMBSTONE = ("2026-09-01T00:00:00Z POST session_id=SID-1 "
             "event_detail=route_fired lane=lane\n")


def test_stale_log_does_not_accuse_grader_path_drift(tmp_path, monkeypatch):
    """A frozen tombstone sibling must not become a path-drift claim on a FAIL.

    The graded log is empty (GRADED_LOG_STALE), the turn failed, the ledger
    carries the run: the FAIL ships, the cause stays unattributed.
    """
    rc, doc, stdout = _main_with_sibling(
        tmp_path, monkeypatch, "m3615stale", log_text="", sibling_text=TOMBSTONE)
    entry = doc["scenarios"][0]
    assert rc == 1
    assert doc["overall"] == "FAIL"
    assert doc["totals"]["fail"] == 1
    assert doc["log_freshness"] == "GRADED_LOG_STALE"
    assert entry["verdict"] == "FAIL"
    assert entry["turns_failed"] == 1
    assert entry["absent_class"] == "unattributable_stale_log"
    assert "grader_path_drift" not in entry["reason"]
    assert "grader_path_drift" not in entry["absent_evidence"]
    assert "grader_path_drift" not in stdout
    # F2: the reader of the console summary is told the log was silent.
    assert "NOTE: graded log stale" in stdout


def test_fresh_log_still_reports_grader_path_drift(tmp_path, monkeypatch):
    """Control: the class survives where it IS established — a fresh grade.

    The graded log has this run's rows (fresh), route_fired is genuinely
    missing there, and a sibling log holds a row THIS run wrote (in-window ts,
    matching session_id). Blanket removal would delete a real signal; the row
    must be attributable, and M36-17 re-fixtured this test off the 2026-09-01
    tombstone, which asserted the false cause the ruling forbids.
    """
    fresh = ("%s POST session_id=SID-1 event_detail=other_event lane=main\n"
             % _stamp(RUN_START))
    live = ("%s POST session_id=SID-1 event_detail=route_fired lane=lane\n"
            % _stamp(RUN_START))
    rc, doc, _ = _main_with_sibling(
        tmp_path, monkeypatch, "m3615fresh", log_text=fresh, sibling_text=live,
        now=RUN_START)
    entry = doc["scenarios"][0]
    assert doc["log_freshness"] == "fresh"
    assert rc == 1 and entry["verdict"] == "FAIL"
    assert entry["absent_class"] == "grader_path_drift"
    assert "this run's own row was written to a file the grader did not read" \
        in entry["absent_evidence"]


# --- M36-17: the attribution predicate, driven through the REAL main() -----
#
# P(row) is a ROW-level fact: the same line must carry the exact event token,
# the exact scenario sid, a parseable leading timestamp, and a timestamp inside
# [run start, sibling read]. The file-level "event in text" test is retired —
# it accused a tombstone (M36-14/B1) and it suppressed a provable drift
# (M36-16/F1). Every shape below runs battery_run.main() end to end with the
# runner's clock frozen at RUN_START.

OLD_TOMB = RUN_START - 30 * 24 * 3600
FRESH_GRADED = ("%s POST session_id=SID-1 event_detail=other_event lane=main\n"
                % _stamp(RUN_START))


def _attr_row(ts, sid="SID-1", ev="route_fired"):
    """One route-log row: optional leading ts, exact event token, exact sid."""
    head = ("%s " % _stamp(ts)) if ts is not None else ""
    sidf = (" session_id=%s" % sid) if sid is not None else ""
    return "%sPOST event_detail=%s lane=lane%s\n" % (head, ev, sidf)


def _main_attr(tmp_path, monkeypatch, profile, log_text, sibling_text,
               sid="SID-1", turns=None, rows=None, graded_mtime=None):
    """main() with the clock frozen at RUN_START and one sibling log."""
    import io
    import json
    import time as _time
    import types
    from contextlib import redirect_stdout

    mod = _runner()
    graded = tmp_path / "graded.log"
    graded.write_text(log_text)
    if graded_mtime is not None:
        os.utime(graded, (graded_mtime, graded_mtime))
    sibdir = tmp_path / profile
    sibdir.mkdir()
    if sibling_text is not None:
        (sibdir / "uncensored-router.log").write_text(sibling_text)
    stray = "/tmp/uncensored-router-%s.log" % profile
    if os.path.exists(stray):
        os.remove(stray)
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(
        time=lambda: RUN_START,
        strftime=_time.strftime, gmtime=_time.gmtime, strptime=_time.strptime))
    monkeypatch.setattr(mod, "PROFILES", str(tmp_path))
    monkeypatch.setattr(mod, "env_of",
                        lambda p: {"API_SERVER_KEY": "k", "API_SERVER_PORT": "1"})
    monkeypatch.setattr(mod, "probe", lambda base, key: (True, "ok"))
    monkeypatch.setattr(mod, "router_log_path", lambda p: str(graded))
    monkeypatch.setattr(mod, "head_sha", lambda: "deadbeef")
    if turns is None:
        turns = [{"ok": False, "detail": "missing_event:route_fired"}]
    monkeypatch.setattr(mod, "parse_stdout",
                        lambda raw: ({"s1": sid}, {"s1": turns}))
    ledger = [_ledger_row(sid or "SID-1")] if rows is None else rows
    monkeypatch.setattr(mod, "read_ledger",
                        lambda p, start: (str(tmp_path / "decisions.jsonl"), ledger))
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0,
                                                       "stdout": "", "stderr": ""})())
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv",
                        ["battery_run.py", "--profile", profile, "--out", str(out)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main()
    return rc, json.loads(out.read_text()), buf.getvalue()


# (name, graded, sibling, kwargs, absent_class, verdict, overall, drift?, NOTE?)
M3617_SHAPES = [
    # stale: no sibling row names this run
    ("stale_tombstone_only", "", _attr_row(OLD_TOMB), {},
     "unattributable_stale_log", "FAIL", "FAIL", False, False),
    # stale + attributable row in-window: the F1 case that was suppressed
    ("stale_inwindow_matching_sid", "", _attr_row(RUN_START), {},
     "grader_path_drift", "FAIL", "FAIL", True, False),
    ("stale_inwindow_foreign_sid", "", _attr_row(RUN_START, sid="SID-9"), {},
     "unattributable_stale_log", "FAIL", "FAIL", False, False),
    ("stale_inwindow_foreign_sid_note",
     "", _attr_row(RUN_START, sid="SID-9") + _attr_row(RUN_START, ev="other_event"),
     {}, "unattributable_stale_log", "FAIL", "FAIL", False, True),
    ("stale_inwindow_no_sid", "", _attr_row(RUN_START, sid=None), {},
     "unattributable_stale_log", "FAIL", "FAIL", False, False),
    ("stale_row_before_start", "", _attr_row(RUN_START - 600), {},
     "unattributable_stale_log", "FAIL", "FAIL", False, False),
    ("stale_row_no_ts", "", _attr_row(None), {},
     "unattributable_stale_log", "FAIL", "FAIL", False, False),
    ("stale_different_event_token", "",
     _attr_row(RUN_START, ev="route_fired_no_stash"), {},
     "unattributable_stale_log", "FAIL", "FAIL", False, True),
    ("stale_own_standdown_row", "", _attr_row(RUN_START, ev="not_a_denial"), {},
     "unattributable_stale_log", "FAIL", "FAIL", False, True),
    ("stale_tombstone_plus_live_row",
     "", _attr_row(OLD_TOMB) + _attr_row(RUN_START), {},
     "grader_path_drift", "FAIL", "FAIL", True, False),
    ("stale_two_tokens_one_line", "",
     "%s POST event_detail=other_event event_detail=route_fired session_id=SID-1\n"
     % _stamp(RUN_START), {},
     "grader_path_drift", "FAIL", "FAIL", True, False),
    # stale by AGE (rows + mtime old) — same live sibling as above
    ("stale_aged_inwindow_matching_sid", _attr_row(RUN_START - 7200, ev="other_event"),
     _attr_row(RUN_START), {"graded_mtime": RUN_START - 7200},
     "grader_path_drift", "FAIL", "FAIL", True, False),
    # fresh: the file-level part is now provable too
    ("fresh_inwindow_matching_sid", FRESH_GRADED, _attr_row(RUN_START), {},
     "grader_path_drift", "FAIL", "FAIL", True, False),
    ("fresh_row_before_start", FRESH_GRADED, _attr_row(RUN_START - 600), {},
     None, "FAIL", "FAIL", False, False),
    ("fresh_foreign_sid", FRESH_GRADED, _attr_row(RUN_START, sid="SID-9"), {},
     None, "FAIL", "FAIL", False, False),
    ("fresh_row_no_ts", FRESH_GRADED, _attr_row(None), {},
     None, "FAIL", "FAIL", False, False),
    ("fresh_different_event_token", FRESH_GRADED,
     _attr_row(RUN_START, ev="route_fired_no_stash"), {},
     None, "FAIL", "FAIL", False, False),
    # fresh: stand-down marker in this run's window (unchanged semantics)
    ("fresh_standdown_marker", _attr_row(RUN_START, ev="not_a_denial"), None, {},
     "not_instrumented_path_not_taken", "FAIL", "FAIL", False, False),
    # attribution impossible without a sid
    ("stale_sid_empty", "", _attr_row(RUN_START), {"sid": ""},
     "unattributable_stale_log", "FAIL", "FAIL", False, False),
    ("fresh_sid_empty", FRESH_GRADED, _attr_row(RUN_START), {"sid": ""},
     None, "FAIL", "FAIL", False, False),
    # FAIL / PASS / F2 preservation
    ("fail_survives_no_ledger", "", _attr_row(RUN_START), {"rows": []},
     "grader_path_drift", "FAIL", "FAIL", True, False),
    ("fail_stale_note_no_ledger", "", _attr_row(OLD_TOMB), {"rows": []},
     "unattributable_stale_log", "FAIL", "FAIL", False, False),
    ("pass_survives_stale_log", "", None,
     {"turns": [{"ok": True, "detail": "ok"}]},
     None, "PASS", "PASS", False, False),
]


@pytest.mark.parametrize(
    "graded,sibling,kwargs,cls,verdict,overall,drift,note",
    [s[1:] for s in M3617_SHAPES],
    ids=[s[0] for s in M3617_SHAPES])
def test_m36_17_attribution_matrix(tmp_path, monkeypatch, graded, sibling,
                                   kwargs, cls, verdict, overall, drift, note):
    rc, doc, stdout = _main_attr(tmp_path, monkeypatch, "m3618_" + str(id(kwargs)),
                                 graded, sibling, **kwargs)
    entry = doc["scenarios"][0]
    assert entry.get("absent_class") == cls
    assert entry["verdict"] == verdict
    assert doc["overall"] == overall
    assert ("this run's own row was written to a file the grader did not read"
            in entry.get("absent_evidence", "")) is drift
    assert ("grader_path_drift" in (doc_text := json.dumps(doc) + stdout)) is drift
    assert doc["log_freshness"] != "fresh" or not note
    # the stale NOTE rides the evidence string, never a second class
    assert ("NOTE this run's own rows" in entry.get("absent_evidence", "")) is note
    # F2: one stdout note on stale runs, zero on fresh ones
    assert stdout.count("NOTE: graded log stale") == (
        0 if doc["log_freshness"] == "fresh" else 1)


def test_m36_17_predicate_needs_the_whole_line():
    """P1-P4 hold on ONE line; a near-miss on any term is not attribution."""
    mod = _runner()
    other = [("sib.log", "".join([
        _attr_row(RUN_START, ev="route_fired_no_stash"),   # P1 exact token
        _attr_row(RUN_START, sid="SID-10"),                # P2 exact sid
        _attr_row(None),                                   # P3 parseable ts
        _attr_row(RUN_START - 600),                        # P4 window
        _attr_row(RUN_START),                              # all four: counts
    ]))]
    rows = mod.attributable_event_rows("route_fired", other, "SID-1",
                                       RUN_START, RUN_START)
    assert len(rows) == 1
    assert rows[0][1].endswith("session_id=SID-1")


# --- M36-19 F1/F2: precedence order, stale-path totality, exact window token --
#
# F1(F1a) shipped green because nothing pinned the two edges: the window gate was
# a SUBSTRING test that ran BEFORE attribution (so a different emit site answered
# for `route_fired`, and the stale path returned None — a third value the ruling
# forbids), and P4's upper bound was untested. The three drives below pin them.

STALE_EARLIER = RUN_START - 7200


def test_m36_20_stale_window_foreign_emit_site_still_gets_the_stale_label(
        tmp_path, monkeypatch):
    """Stale + `route_fired_no_stash` in this session's window, no sibling row.

    `route_fired_no_stash` contains `route_fired`, so the retired substring gate
    returned None ("not an absence") on a STALE run: the fallback label was
    deleted, and the verdict lost its absent_class (M36-19 F1a). The stale path
    has exactly two labels; None is not one of them.
    """
    graded = ("%s POST session_id=SID-1 event_detail=route_fired_no_stash "
              "lane=main\n" % _stamp(STALE_EARLIER))
    rc, doc, stdout = _main_attr(tmp_path, monkeypatch, "m3620_no_stash",
                                 graded, None, graded_mtime=STALE_EARLIER)
    entry = doc["scenarios"][0]
    assert doc["log_freshness"] == "GRADED_LOG_STALE"
    assert entry["absent_class"] == "unattributable_stale_log"
    assert "not an absence" not in entry["absent_evidence"]
    assert "no path drift is claimed" in entry["absent_evidence"]
    assert entry["verdict"] == "FAIL" and rc == 1

    # same shape with a FOREIGN sid row next to it: still the stale label.
    rc, doc, _ = _main_attr(tmp_path, monkeypatch, "m3620_foreign",
                            graded, _attr_row(RUN_START, sid="SID-9"),
                            graded_mtime=STALE_EARLIER)
    assert doc["scenarios"][0]["absent_class"] == "unattributable_stale_log"

    # the gate itself is load-bearing: a real `route_fired` row in the window
    # beats a stand-down marker in the same window. With the token gate off the
    # marker labels a turn that did fire -> not_instrumented_path_not_taken.
    present = ("%s POST session_id=SID-1 event_detail=route_fired lane=main\n"
               "%s POST session_id=SID-1 event_detail=route_fired_no_stash\n"
               % (_stamp(RUN_START), _stamp(RUN_START)))
    mod = _runner()
    cls, evidence = mod.classify_absent_event(
        "route_fired", present, [], window_text=mod.session_window(present, "SID-1"))
    assert cls is None
    assert "not an absence" in evidence


def test_m36_20_stale_anchor_emit_does_not_hide_provable_drift(tmp_path, monkeypatch):
    """Stale window holding only `anchor_route_fired` + an attributable sibling.

    `anchor_route_fired` contains `route_fired`; under the substring gate the
    window looked like it already carried the event and a provable drift claim
    was replaced by "not an absence" (M36-19 F1b, ruling §PRECEDENCE 1).
    """
    graded = ("%s POST session_id=SID-1 event_detail=anchor_route_fired "
              "lane=main\n" % _stamp(STALE_EARLIER))
    rc, doc, stdout = _main_attr(tmp_path, monkeypatch, "m3620_anchor",
                                 graded, _attr_row(RUN_START),
                                 graded_mtime=STALE_EARLIER)
    entry = doc["scenarios"][0]
    assert doc["log_freshness"] == "GRADED_LOG_STALE"
    assert entry["absent_class"] == "grader_path_drift"
    assert "this run's own row was written to a file the grader did not read" \
        in entry["absent_evidence"]
    assert "not an absence" not in entry["absent_evidence"]
    assert entry["verdict"] == "FAIL" and rc == 1


def test_m36_20_p4_upper_bound_is_the_sibling_read_time(tmp_path, monkeypatch):
    """ts == read_epoch+1 is attributable; read_epoch+2 is not (P4 upper bound)."""
    mod = _runner()
    for ts, expected in ((RUN_START + 61, 1), (RUN_START + 62, 0)):
        rows = mod.attributable_event_rows(
            "route_fired", [("sib.log", _attr_row(ts))], "SID-1",
            RUN_START, RUN_START + 60)
        assert len(rows) == expected, (ts, rows)

    # end to end: main() freezes its clock at RUN_START, so the bound is +1s
    rc, doc, _ = _main_attr(tmp_path, monkeypatch, "m3620_hi_edge",
                            FRESH_GRADED, _attr_row(RUN_START + 1))
    assert doc["scenarios"][0]["absent_class"] == "grader_path_drift"
    rc, doc, _ = _main_attr(tmp_path, monkeypatch, "m3620_hi_plus_one",
                            FRESH_GRADED, _attr_row(RUN_START + 2))
    assert doc["scenarios"][0]["absent_class"] is None
