"""M37-06 — guards for the five M37-05 falsification defects (D1, D3, D4).

Each test here fails against the pre-M37-06 tree:

  D1  `_PRE_LANE_HANDLERS` accepted ANY key, so a `router_core.LANE_DECISION`
      row fired at `_pre_lane_dispatch` BEFORE the frozen decision branch and
      made that lane unreachable silently. The table is now validated at
      import; these tests exercise the guard directly (no module reload).
  D3  `LaneSpec.config_section` was `None` on every lane with zero readers.
      The field is gone; a re-introduction fails here.
  D4  Lane-name drift: `debug_banner.lane_label` was a hardcoded map with a
      `split("-", 1)[0]` fallback, `completion_audit` logged a literal
      `lane="higher-post"`, and dropping a lane silently shortened the
      re-routing marker union. The three tests below fail on each of those.

Registry rows mutated by the marker test are restored in a finally block.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from hermes_router import completion_audit, debug_banner, router_core
from hermes_router.lanes import builtins as lane_builtins
from hermes_router.lanes import registry as lane_registry
from hermes_router.passes import dispatcher_pre

REPO_ROOT = Path(__file__).resolve().parents[2]

# Lanes the registry ships, with the display labels they rendered BEFORE
# M37-06 — byte-identical output is the requirement of D4(iii).
EXPECTED_LABELS = {
    "shadow": "shadow-self (uncensored)",
    "higher-pre": "higher",
    "higher-post": "higher",
    "decision": "impulse (decision)",
}

# Non-registry spellings the banner/log code emits, and the registered lane
# each one stands for.
EXPECTED_ALIASES = {
    "frontier": "higher-pre",
    "frontier-anchor": "higher-pre",
    "uncensored": "shadow",
    "uncensored-render": "shadow",
    "reflex": "decision",
}


# ---------------------------------------------------------------------------
# D1 — the PRE handler table may only key a lane the PRE seam owns
# ---------------------------------------------------------------------------

def test_pre_lane_guard_rejects_decision_key():
    """The exact hazard: a decision key would pre-empt the frozen branch."""
    table = dict(dispatcher_pre._PRE_LANE_HANDLERS)
    table[router_core.LANE_DECISION] = lambda *a: True
    with pytest.raises(ValueError) as excinfo:
        dispatcher_pre._validate_pre_lane_handlers(table)
    message = str(excinfo.value)
    assert "'decision'" in message or "decision" in message
    assert repr(router_core.LANE_COMPLEXITY) in message


def test_pre_lane_guard_rejects_unregistered_key():
    """A lane router_core never emits is a claim on unreachable behaviour."""
    table = dict(dispatcher_pre._PRE_LANE_HANDLERS)
    table["shadow"] = lambda *a: True
    with pytest.raises(ValueError) as excinfo:
        dispatcher_pre._validate_pre_lane_handlers(table)
    assert "'shadow'" in str(excinfo.value)


def test_shipped_table_passes_its_own_guard():
    """The guard must not be vacuous: today's table is accepted."""
    assert dispatcher_pre._validate_pre_lane_handlers(
        dispatcher_pre._PRE_LANE_HANDLERS) is None
    assert set(dispatcher_pre._PRE_LANE_HANDLERS) == {router_core.LANE_COMPLEXITY}


def test_guard_runs_at_import():
    """Import-time validation, not an opt-in helper nobody calls."""
    source = (REPO_ROOT / "passes" / "dispatcher_pre.py").read_text(
        encoding="utf-8")
    assert re.search(r"^_validate_pre_lane_handlers\(_PRE_LANE_HANDLERS\)$",
                     source, re.MULTILINE)


# ---------------------------------------------------------------------------
# D3 — the dead field stays deleted
# ---------------------------------------------------------------------------

def test_lanespec_carries_no_config_section_field():
    names = {f.name for f in dataclasses.fields(lane_registry.LaneSpec)}
    assert "config_section" not in names
    assert not hasattr(lane_registry.lane("shadow"), "config_section")


def test_no_config_section_reader_survived_in_the_lane_tree():
    """The grep evidence, executable: the field has no attribute/keyword
    reader left (prose mentions in comments and docs are not readers)."""
    pattern = re.compile(r"\.config_section\b|config_section\s*=")
    hits = []
    for path in REPO_ROOT.rglob("*.py"):
        if ".git" in path.parts or "reviews" in path.parts:
            continue
        if path.name == Path(__file__).name:
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#") or "`" in line:
                continue
            if "_config_section" in line or "config_writer" in line:
                continue
            if pattern.search(line):
                hits.append("%s:%d: %s" % (
                    path.relative_to(REPO_ROOT), lineno, line.strip()))
    assert hits == [], "config_section readers remain: %s" % hits


# ---------------------------------------------------------------------------
# D4(i) — every lane literal in the banner/log code resolves via the registry
# ---------------------------------------------------------------------------

_LANE_LITERAL_RE = re.compile(r"""lane\s*=\s*["']([^"']+)["']""")


def _lane_literals(*module_paths):
    literals = set()
    for rel in module_paths:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        literals.update(_LANE_LITERAL_RE.findall(text))
    return literals


def _assert_literals_resolve(literals):
    """Each literal is a registered id, or a documented alias of one."""
    unresolved = []
    for literal in sorted(literals):
        try:
            lane_registry.lane(literal)
            continue
        except KeyError:
            pass
        target = EXPECTED_ALIASES.get(literal)
        if target is None:
            unresolved.append(literal)
            continue
        lane_registry.lane(target)  # alias target must still be registered
    if unresolved:
        raise ValueError(
            "lane literal(s) not resolvable via lanes.registry: %s"
            % ", ".join(repr(v) for v in unresolved))


def test_banner_and_audit_lane_literals_resolve_via_registry():
    literals = _lane_literals("debug_banner.py", "completion_audit.py")
    _assert_literals_resolve(literals)
    # the audited literal is registry-derived now, not pasted into the call
    assert "higher-post" not in literals
    assert completion_audit._POST_AUDIT_LANE_ID == lane_builtins.LANE_HIGHER_POST


def test_audit_spend_call_does_not_paste_the_lane_literal():
    source = (REPO_ROOT / "completion_audit.py").read_text(encoding="utf-8")
    assert 'lane="higher-post"' not in source
    assert "lane=_POST_AUDIT_LANE_ID" in source


def test_a_hardcoded_non_registered_lane_name_fails_the_guard():
    """The guard is not tautological: an unregistered name is rejected."""
    with pytest.raises(ValueError) as excinfo:
        _assert_literals_resolve({"higher-pots"})
    assert "higher-pots" in str(excinfo.value)


def test_lane_label_output_is_byte_identical_for_registered_lanes():
    for lane_id, expected in EXPECTED_LABELS.items():
        assert debug_banner.lane_label(lane_id) == expected
    for alias, target in EXPECTED_ALIASES.items():
        lane_registry.lane(target)
        assert debug_banner.lane_label(alias) == debug_banner.lane_label(
            target) or debug_banner.lane_label(alias) in (
                "higher-self (frontier)", "shadow-self (uncensored)",
                "impulse (decision)")


def test_lane_label_table_covers_exactly_the_registered_lanes():
    assert {row[0] for row in debug_banner._LANE_LABEL_ROWS} == set(
        lane_registry.all_ids())


# ---------------------------------------------------------------------------
# D4(ii) — dropping a lane must not silently disarm its marker firewall
# ---------------------------------------------------------------------------

def test_firewall_union_carries_every_declared_marker():
    dispatcher_pre._validate_sentinel_coverage()  # no raise
    markers = set(dispatcher_pre._frame_sentinel_markers())
    for constant in (lane_builtins.MARKER_SHADOW,
                     lane_builtins.MARKER_HIGHER_PRE,
                     lane_builtins.MARKER_HIGHER_POST):
        assert set(constant) <= markers


def test_dropping_a_registered_lane_fails_the_firewall_guard():
    saved_order = list(lane_registry._IDS.items())
    try:
        lane_registry._IDS.pop(lane_builtins.LANE_HIGHER_POST)
        with pytest.raises(ValueError) as excinfo:
            dispatcher_pre._validate_sentinel_coverage()
        assert "HIGHER-SELF COMPLETION-AUDIT TURN" in str(excinfo.value)
    finally:
        lane_registry._IDS.clear()
        lane_registry._IDS.update(saved_order)


# ---------------------------------------------------------------------------
# D4(iii) — the audited literal now comes from the registry
# ---------------------------------------------------------------------------

def test_audit_spend_lane_id_is_registry_derived():
    assert completion_audit._POST_AUDIT_LANE_ID == lane_builtins.LANE_HIGHER_POST
    assert lane_registry.lane(completion_audit._POST_AUDIT_LANE_ID).id == (
        lane_builtins.LANE_HIGHER_POST)
