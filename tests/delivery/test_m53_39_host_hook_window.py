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
      (b) the first test below fails the suite when the prediction outlives
          the enforced deadline, so the config raise cannot be forgotten.
"""
from __future__ import annotations

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router.core import turn_provenance as tp  # noqa: E402

#: The measured band that was being thrown away this build (m53-39).
LATE_RENDER_BAND_S = (31.2, 40.3)


def _enforced_host_deadline() -> float:
    """The host's ACTUAL kill, resolved from the runner's own config path."""
    from hermes_cli.plugins import _resolve_hook_callback_timeout
    return float(_resolve_hook_callback_timeout())


def test_prediction_never_outlives_the_enforced_deadline():
    """The B3 pin: a prediction past the host's kill is a false delivery.

    Fails when ``HOST_HOOK_KILL_S`` was widened without raising
    ``plugins.hook_callback_timeout`` — exactly the change that would make the
    seam claim a replacement the wire never carried.
    """
    enforced = _enforced_host_deadline()
    assert tp.HOST_HOOK_KILL_S <= enforced, (
        f"prediction {tp.HOST_HOOK_KILL_S}s exceeds the host's enforced "
        f"{enforced}s hook kill: a render settling between those values "
        f"would be recorded as delivered while the user read the refusal")
    assert tp.effective_host_hook_kill_s() <= enforced


def test_enforced_deadline_clears_the_discarded_render_band():
    """The window must actually cover the renders that were being lost."""
    enforced = _enforced_host_deadline()
    assert enforced > LATE_RENDER_BAND_S[1], (
        f"enforced {enforced}s still discards the measured late-render band "
        f"{LATE_RENDER_BAND_S[0]}-{LATE_RENDER_BAND_S[1]}s")


def test_work_budget_keeps_a_margin_for_consume_and_capture():
    """Budgeted work stops short of the deadline: the banner/consume edge
    still has to run after the last budgeted step."""
    from hermes_router import completion_audit as ca

    assert ca.HOOK_SYNC_CAP == tp.HOOK_WORK_BUDGET_S
    assert ca.HOOK_SYNC_CAP < tp.effective_host_hook_kill_s()
    assert tp.effective_host_hook_kill_s() - ca.HOOK_SYNC_CAP >= 5.0


def test_lost_config_degrades_to_the_honest_refusal(monkeypatch):
    """A deployment that never raised the config must not gain the wider
    window by accident: the effective deadline collapses to the enforced 30 s
    and a 31 s render is refused, not claimed."""
    monkeypatch.setattr(tp, "enforced_host_hook_kill_s",
                        lambda default=tp.HOST_HOOK_KILL_DEFAULT_S: 30.0)
    assert tp.effective_host_hook_kill_s() == 30.0
    assert tp.render_is_deliverable(29.9) is True
    assert tp.render_is_deliverable(31.0) is False


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
