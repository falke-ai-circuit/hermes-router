"""R78U M46-07 — the consult knobs must be ALIVE, and both legs must be reachable.

Defects this pins:
  D1  DEAD KNOBS. `consult_budget._knob_int` read ONLY the top-level router
      section, while the fleet carries the `consult_*` dials in the section's
      `complexity` sub-block. Every knob therefore returned its default: a
      configured `consult_min_turns: 2` (all 11 profile configs) behaved as 1.
      MEASURED at HEAD 0bb0f2d against the live coder profile config:
      `min_turns() == 1` while `complexity.consult_min_turns == 2`.
      (The `from . import config_access` line does NOT raise in package
      context — `hermes_router/__init__.py` re-exports that module — so the
      sub-block miss, not the import, is the measured cause. The explicit
      `.core` import is kept because it is correct in either layout.)
  D2  PRE GATE. The floor must NEVER gate a first PRE: `prior == 0` for that
      leg -> allowed on task turn 1, on any ask, whenever the trigger fired.
  D3  POST GATE. The floor counted prior consults across ALL legs, so a task
      whose PRE spent its slot could not reach its POST on the turn its own
      age floor allowed (the PRE->POST gap). Prior is now PER LEG, and the POST
      has its own age floor `consult_post_min_turns` (default 3 — M46-08 absent
      at build time), counted from the ORIGINAL ask. The POST must land in the
      delivered body of the task's response: asserted on the composed body, and
      refused `after_delivery` on the async seam that ships before it consults.
  D4  the 20-turn test accepts `below_min_turns` as a CORRECT refusal while
      every cap assertion stays (tests/test_m45_25_consult_budget.py).
"""
from __future__ import annotations

import copy

import pytest

pytest_plugins = ("tests.conftest",)


@pytest.fixture(autouse=True)
def _config_cache_neutral():
    """Leave `config_access._cache` exactly as this module found it.

    `router_section()` warms a process-level cache with whatever section it
    last resolved. A test module that reads the REAL profile config would
    otherwise hand that section to every later test in the same process —
    measured: three banner tests downstream turned red only in a full-suite run,
    purely from a cache this module warmed.
    """
    from hermes_router import config_access

    saved = copy.deepcopy(getattr(config_access, "_cache", None))
    yield
    cache = getattr(config_access, "_cache", None)
    if isinstance(cache, dict) and isinstance(saved, dict):
        cache.clear()
        cache.update(saved)


@pytest.fixture()
def budget(monkeypatch):
    from hermes_router import advisory_consumption as ac, consult_budget as cb

    cb.reset()
    monkeypatch.setattr(ac, "_TURN_NO", {})
    yield cb
    cb.reset()


def _set_turn(session_id: str, n: int) -> None:
    from hermes_router import advisory_consumption as ac

    ac._TURN_NO[session_id] = n


def _knobs(monkeypatch, pre_floor: int = 1, post_floor: int = 3, cap: int = 1) -> None:
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"complexity": {"consult_min_turns": pre_floor,
                                                "consult_post_min_turns": post_floor,
                                                "consult_max_per_task": cap}})


# --------------------------------------------------------------------- D1 ----

def test_reader_returns_configured_knob_from_a_temp_config(budget, monkeypatch,
                                                           tmp_path):
    """D1: a knob set in a temp config is RETURNED by the reader.

    The temp config carries the knobs where the fleet actually does — inside
    `hermes_router.complexity` — so a reader that only looks at the top-level
    section returns the default and FAILS here. Measured red at HEAD 0bb0f2d:
    the configured 2 read back as 1.
    """
    from hermes_router import config_access

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "hermes_router:\n"
        "  complexity:\n"
        "    consult_min_turns: 2\n"
        "    consult_post_min_turns: 4\n",
        encoding="utf-8",
    )
    # Make the co-located-yaml leg of the canonical reader resolve THIS file,
    # and stop the process-level read from short-circuiting it.
    import hermes_cli.config as hc

    monkeypatch.setattr(config_access, "_coLocatedPath", lambda: str(cfg))
    monkeypatch.setattr(hc, "load_config", lambda: {}, raising=True)
    config_access.reset_cache()

    assert config_access.router_section()["complexity"]["consult_min_turns"] == 2
    assert budget.min_turns() == 2
    assert budget.post_min_turns() == 4


def test_reader_returns_top_level_knobs_too(budget, monkeypatch):
    """D1: the legacy/compat shape (top-level key) still reads."""
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 5,
                                 "consult_post_min_turns": 2,
                                 "consult_max_per_task": 2})
    assert budget.min_turns() == 5
    assert budget.post_min_turns() == 2
    assert budget.max_per_task() == 2


def test_top_level_wins_over_the_complexity_sub_block(budget, monkeypatch):
    """D1: explicit precedence, never a silent mix of the two shapes."""
    from hermes_router import config_access

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"consult_min_turns": 4,
                                 "complexity": {"consult_min_turns": 2}})
    assert budget.min_turns() == 4


def test_unparseable_knob_falls_back_to_the_default(budget, monkeypatch):
    """D1: a bad value must not raise and must not invent a number."""
    from hermes_router import config_access, consult_budget as cb

    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"complexity": {"consult_min_turns": "not-an-int",
                                                "consult_post_min_turns": None}})
    assert budget.min_turns() == cb.DEFAULT_MIN_TURNS
    assert budget.post_min_turns() == cb.DEFAULT_POST_MIN_TURNS


def test_live_config_value_is_what_the_reader_returns(budget):
    """D1 anti-dead-knob pin against the REAL process config."""
    from hermes_router import config_access

    sec = config_access.router_section() or {}
    comp = sec.get("complexity") if isinstance(sec.get("complexity"), dict) else {}
    declared = sec.get("consult_min_turns", comp.get("consult_min_turns"))
    if declared is None:
        pytest.skip("this config declares no consult_min_turns")
    assert budget.min_turns() == int(declared), (
        "configured consult_min_turns=%r but the reader returned %r"
        % (declared, budget.min_turns()))


# --------------------------------------------------------------------- D2 ----

def test_first_turn_trigger_eligible_ask_gets_its_pre(budget, monkeypatch):
    """D2: `prior == 0` on the PRE leg -> allowed, whatever the floors say.

    Session turn 42 (a task minted mid-session) with a PRE floor of 5 must
    still produce the ask's first PRE: the triggers decided, not the floor.
    """
    _knobs(monkeypatch, pre_floor=5, post_floor=3)
    session, task = "s-m4607-d2", "t-m4607-d2"
    _set_turn(session, 42)

    gate = budget.check(session, task, leg="pre")
    assert gate["allow"] is True, gate
    assert gate["reason"] == "", gate
    assert gate["task_turn"] == 1, gate
    assert budget.counts(session, task) == {"pre": 1}


# --------------------------------------------------------------------- D3 ----

def test_pre_then_post_on_the_same_task_both_fire(budget, monkeypatch):
    """D3 THE GAP: a task whose PRE spent its slot must still reach its POST.

    PRE floor 3 (irrelevant to the POST once prior is PER LEG) and POST age
    floor 2: the PRE fires on task turn 1, the POST on task turn 2. At HEAD
    0bb0f2d the floor summed both legs (prior=1) and refused this POST with
    `below_min_turns` — the exact defect this unit closes.
    """
    _knobs(monkeypatch, pre_floor=3, post_floor=2)
    session, task = "s-m4607-d3", "t-m4607-d3"

    _set_turn(session, 7)
    assert budget.check(session, task, leg="pre")["allow"] is True

    _set_turn(session, 8)  # task turn 2
    post = budget.check(session, task, leg="post")
    assert post["allow"] is True, post
    assert post["task_turn"] == 2, post
    assert budget.counts(session, task) == {"pre": 1, "post": 1}


def test_post_on_task_turn_one_is_refused_below_min_turns_and_recorded(
        budget, monkeypatch):
    """D3: the POST's own age floor (default 3) refuses and RECORDS turn-1 POST."""
    rows = []
    from hermes_router import decision as dm

    monkeypatch.setattr(dm, "ledger_write", lambda row, db_path="": rows.append(row))
    _knobs(monkeypatch, pre_floor=1, post_floor=3)
    session, task = "s-m4607-d3t1", "t-m4607-d3t1"
    _set_turn(session, 5)

    assert budget.check(session, task, leg="pre")["allow"] is True
    post = budget.check(session, task, leg="post")
    assert post["allow"] is False, post
    assert post["reason"] == "below_min_turns", post
    assert post["task_turn"] == 1, post
    assert budget.counts(session, task) == {"pre": 1}

    capped = [r for r in rows if r.get("trigger") == "consult_capped"]
    assert len(capped) == 1, rows
    assert capped[0].get("choice") == "refused"
    assert "below_min_turns" in str(capped[0].get("verdict_json")), capped[0]


def test_post_lands_in_the_delivered_body_of_the_final_response(
        budget, monkeypatch, tmp_path):
    """D3(c) THE BODY PROOF: an allowed POST is visible in the delivered bytes.

    Asserts on the composed body the user receives (not on a return value):
    the gate allows the aged POST, its banner parks exactly as
    completion_audit parks it, and the delivery seam composes it into THIS
    response. Same task, same session as the PRE that fired earlier.
    """
    import sys

    _P = "/opt/data/plugins"
    if _P not in sys.path:
        sys.path.insert(0, _P)
    import hermes_router as plugin
    from hermes_router import (completion_audit, debug_banner, render_inbox,
                               route_gate, router_core, state, usage_ledger)
    from hermes_router.features.banners import turn_record

    sid, task = "s-m4607-body", "t-m4607-body"
    router_core._test_reset()
    plugin.state.clear()
    state.reset_turn_identity(sid)
    route_gate.clear_declared(sid)
    route_gate.clear_turn_claims(sid)
    turn_record.reset()
    turn_record.reset(sid)
    debug_banner._ANCHOR_BANNERS.clear()
    debug_banner._ANCHOR_TASKS.clear()
    debug_banner._ANCHOR_SEGS.clear()
    debug_banner._PARK_RECORDS.clear()
    monkeypatch.setattr(plugin, "_log_route", lambda e, **f: None)
    monkeypatch.setattr(usage_ledger, "_store_path",
                        lambda: str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr(render_inbox, "_inbox_path",
                        lambda: str(tmp_path / "renders.jsonl"))
    monkeypatch.setattr(debug_banner, "_banner_section",
                        lambda: {"debug_banner": 1}, raising=True)
    monkeypatch.setattr(completion_audit, "audit_enabled", lambda: False)
    # banner knob rides the same section as the consult dials
    from hermes_router import config_access
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"debug_banner": 1,
                                 "complexity": {"consult_min_turns": 1,
                                                "consult_post_min_turns": 3,
                                                "consult_max_per_task": 1}})

    _set_turn(sid, 10)
    assert budget.check(sid, task, leg="pre")["allow"] is True

    _set_turn(sid, 12)  # task turn 3 = the POST age floor
    post = budget.check(sid, task, leg="post")
    assert post["allow"] is True, post

    # Exactly what completion_audit does when the POST fires: park the leg's
    # segment under the task id, then deliver.
    line = debug_banner.format_banner(
        lane="frontier-anchor", trigger="completion_audit", model="glm-5.3",
        endpoint="https://api.nousresearch.com/v1", tokens_in=150,
        tokens_out=40, est_cost=0.06, latency_s=3, task_id=task,
        session_id=sid)
    debug_banner.park_task_banner(sid, task, "post", line)

    out = plugin.on_transform_llm_output(
        response_text="final answer for the task", session_id=sid, model="m")
    body = str(out or "")
    assert body.startswith("final answer for the task"), body[:200]
    assert debug_banner.BANNER_HEAD in body, body[-600:]
    assert "post-check" in body, body[-600:]
    assert "tok 150/40" in body, body[-600:]
    turn_record.reset(sid)
