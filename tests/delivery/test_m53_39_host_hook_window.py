"""m53-39 — the raised host hook window, and the honesty clamp around it.

THE DEFECT THIS PINS
    The host plugin runner abandons ``on_transform_llm_output`` at
    ``plugins.hook_callback_timeout`` (30 s shipped default). Measured this
    build: 13 events across architect+researcher with
    ``reason=render_settled_after_host_hook_kill`` at elapsed 31.2-40.3 s on
    renders of 10,890-18,515 chars — 6 of 37 measured renders (16%) were paid
    for and discarded.

WHAT WAS CHANGED
    Host: ``plugins.hook_callback_timeout`` raised 30 -> 60 (config, all
    profiles that load hermes-router).
    Plugin: ``HOST_HOOK_KILL_S`` 30 -> 60 in both copies, and the inner work
    budget derived once (``HOOK_WORK_BUDGET_S`` = deadline - 15 s margin),
    consumed by ``HOOK_SYNC_CAP`` and the hook's own remaining-budget clock.

THE INVARIANT (B3, inverse delivery)
    The seam must NEVER claim a replacement unless the host actually waited.
    A prediction widened past the enforced deadline converts the honest
    ``render_not_delivered`` row into a FALSE ``replaced``: the render sits in
    the store while the user read the refusal. Two structural guards:
      (a) ``effective_host_hook_kill_s()`` is the MIN of prediction and the
          host's enforced value, and ``render_is_deliverable`` defaults to it;
      (b) the pins below measure the CLAMP MATHEMATICS on both sides, so a
          prediction widened past the host's deadline cannot pass silently.

WHY THE PINS NO LONGER IMPORT THE HOST RESOLVER
    The suite resolves ``hermes_cli`` from the interpreter's tree
    (/opt/hermes), which is NOT the tree the gateway runs
    (/opt/data/src-0.21.4) and may not carry the private
    ``_resolve_hook_callback_timeout`` symbol at all. Importing it made these
    pins measure ``sys.path``: the ImportError collapsed the enforced value to
    the 30 s fallback, so every assertion described the test harness instead
    of the plugin. The pins now stub the resolver through ``sys.modules`` —
    the plugin's own import path, unchanged — with a value the test owns.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.core import turn_provenance as tp  # noqa: E402

#: The measured band that was being thrown away this build (m53-39).
LATE_RENDER_BAND_S = (31.2, 40.3)

#: The plugin's predicted deadline (what the seam believes the host allows).
PREDICTION_S = float(tp.HOST_HOOK_KILL_S)


def _install_host_resolver(monkeypatch, value):
    """Point the plugin's enforced-deadline read at a value the TEST owns.

    ``value=None`` installs a module WITHOUT the resolver symbol — the
    unreadable-host case: the plugin must fall back to
    ``HOST_HOOK_KILL_DEFAULT_S`` and degrade honestly. Any float installs a
    resolver that returns it, i.e. a host whose config was actually raised.

    Stubbing ``sys.modules`` (rather than importing the host symbol) keeps the
    plugin's own ``from hermes_cli.plugins import ...`` path under test while
    making the pin independent of which ``hermes_cli`` tree the interpreter
    happens to see.
    """
    parent = sys.modules.get("hermes_cli")
    if parent is None:
        parent = types.ModuleType("hermes_cli")
        monkeypatch.setitem(sys.modules, "hermes_cli", parent)
    module = types.ModuleType("hermes_cli.plugins")
    if value is not None:
        setattr(module, "_resolve_hook_callback_timeout", lambda: float(value))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", module)
    monkeypatch.setattr(parent, "plugins", module, raising=False)
    tp._reset_enforced_kill_cache_for_tests()
    return module


@pytest.fixture(autouse=True)
def _isolate_enforced_kill_cache():
    """The enforced deadline is memoised; a stubbed value must not leak."""
    tp._reset_enforced_kill_cache_for_tests()
    yield
    tp._reset_enforced_kill_cache_for_tests()


@pytest.mark.parametrize(
    "host_s",
    [PREDICTION_S / 2.0, PREDICTION_S, PREDICTION_S * 2.0],
    ids=["host-narrower", "host-equal", "host-wider"],
)
def test_prediction_never_outlives_the_enforced_deadline(monkeypatch, host_s):
    """The B3 pin: a prediction past the host's kill is a false delivery.

    The claim the seam may make is ``min(prediction, enforced)`` — so a
    narrowed host deadline can never be outlived, and a widened prediction
    only takes effect when the host's own value really is wider. Both sides
    are exercised with values the test owns, so the pin measures the clamp,
    not the interpreter's ``sys.path``.
    """
    _install_host_resolver(monkeypatch, host_s)

    assert tp.enforced_host_hook_kill_s() == host_s
    effective = tp.effective_host_hook_kill_s()
    assert effective == min(PREDICTION_S, host_s), (
        f"effective {effective}s is not min(prediction {PREDICTION_S}s, "
        f"host {host_s}s): a render settling between those values would be "
        f"recorded as delivered while the user read the refusal")
    assert effective <= host_s
    # the boundary is the effective deadline, not either raw input
    assert tp.render_is_deliverable(effective - 0.001) is True
    assert tp.render_is_deliverable(effective) is False


def test_enforced_deadline_clears_the_discarded_render_band(monkeypatch):
    """The window must actually cover the renders that were being lost."""
    _install_host_resolver(monkeypatch, PREDICTION_S)

    effective = tp.effective_host_hook_kill_s()
    assert effective == PREDICTION_S
    assert effective > LATE_RENDER_BAND_S[1], (
        f"effective {effective}s still discards the measured late-render band "
        f"{LATE_RENDER_BAND_S[0]}-{LATE_RENDER_BAND_S[1]}s")


def test_work_budget_keeps_a_margin_for_consume_and_capture(monkeypatch):
    """Budgeted work stops short of the deadline: the banner/consume edge
    still has to run after the last budgeted step."""
    from hermes_router import completion_audit as ca

    _install_host_resolver(monkeypatch, PREDICTION_S)

    effective = tp.effective_host_hook_kill_s()
    assert effective == PREDICTION_S
    assert ca.HOOK_SYNC_CAP == tp.HOOK_WORK_BUDGET_S
    assert ca.HOOK_SYNC_CAP < effective
    assert effective - ca.HOOK_SYNC_CAP >= 5.0


def test_lost_config_degrades_to_the_honest_refusal(monkeypatch):
    """A deployment that never raised the config must not gain the wider
    window by accident: an unreadable host collapses the effective deadline to
    the plugin's documented host default, and a render past it is refused, not
    claimed.
    """
    assert PREDICTION_S > tp.HOST_HOOK_KILL_DEFAULT_S, (
        "the widened prediction must be wider than the fallback, otherwise "
        "the clamp has nothing to degrade to")

    _install_host_resolver(monkeypatch, None)  # resolver symbol unreadable

    default = float(tp.HOST_HOOK_KILL_DEFAULT_S)
    assert tp.enforced_host_hook_kill_s() == default
    assert tp.effective_host_hook_kill_s() == min(PREDICTION_S, default) == default
    assert tp.render_is_deliverable(default - 0.1) is True
    assert tp.render_is_deliverable(default) is False
    # the widened band is NOT reachable on a host that never raised the config
    assert tp.render_is_deliverable((default + PREDICTION_S) / 2.0) is False


def test_one_source_for_the_inner_budget():
    """The runner-safe budget is derived once. A fourth hardcoded copy is how
    the original 30/25 pair drifted from the host contract."""
    from hermes_router import completion_audit as ca

    assert ca.HOOK_SYNC_CAP == tp.HOOK_WORK_BUDGET_S
    orch = open(os.path.join(PLUGIN_DIR, "gate", "orchestration.py"),
                encoding="utf-8").read()
    assert "25.0 - (_hub().time.monotonic() - _hook_t0)" not in orch
    assert "HOOK_WORK_BUDGET_S" in orch
    # the arm clock still starts before generation, so it must stay longer
    # than the callback deadline (M51-09).
    assert tp.SKIP_DEADLINE_S > tp.HOST_HOOK_KILL_S
