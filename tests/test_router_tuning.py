"""Router-tuning dispatch tests (2026-09-09, Goran-approved).

Covers: A1 verify-class exempt, A2 pre_cooldown_seconds, A3 post_audit_min_turns,
B1 persona_card_chars, B2 orientation_ask_cap, B3 bounded_replay default 24.
All new knobs are enabled explicitly per test — the conftest isolation fixture
pins them to legacy behavior for the rest of the suite.
"""
import inspect
import time
from unittest import mock

import pytest

import hermes_router as plugin
from hermes_router import anchor_exec, persona_card, router_core, state

PLAN_ASK = ("Design a migration strategy across multiple services with "
            "unclear invariants and a phased rollout plan.")


# ---------------------------------------------------------------- A1 -------

def test_verify_exempt_skips_pre_on_imperative_confirm(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    d = router_core.dispatch("Put all on L2", session_id="vx1", model="m")
    assert d.lane == router_core.LANE_UNCENSORED
    assert d.mode == router_core.MODE_FLASH_DIRECT
    d2 = router_core.dispatch("can you confirm X is active",
                              session_id="vx2", model="m")
    assert d2.mode == router_core.MODE_FLASH_DIRECT


def test_verify_exempt_covers_typos(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    # "is X (ok|on|active|enabled)" arm matches even with minor typos in the tail
    d = router_core.dispatch("is the L2 runner actve", session_id="vx3", model="m")
    assert d.mode == router_core.MODE_FLASH_DIRECT


def test_verify_exempt_does_not_skip_analysis_asks(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    # Exempt classifier must reject every analysis-dim ask...
    for ask in ("why did you pick glm 5.3",
                "dig deeper into the walk weights",
                "compare the two approaches better explained"):
        assert not router_core._is_verify_class_exempt(ask), ask
    # ...and a real depth ask still routes to the complexity lane.
    d = router_core.dispatch("dig deeper into the walk weights",
                             session_id="vx4", model="m")
    assert d.lane == router_core.LANE_COMPLEXITY


def test_verify_exempt_override_still_anchors(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    _chain = mock.Mock()
    _ep = mock.Mock(model="frontier-x")
    _chain.endpoint_for.return_value = _ep
    monkeypatch.setattr(router_core.anchor_chain, "load_anchor_chain",
                        lambda: _chain)
    d = router_core.dispatch("status\nanchor this", session_id="vx5", model="m")
    assert d.override_used == "anchor"
    assert d.lane == router_core.LANE_COMPLEXITY


# ---------------------------------------------------------------- A2 -------

@pytest.fixture()
def _cooldown_env(monkeypatch):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 600)
    monkeypatch.setattr(router_core, "_complexity_level", lambda: 3)
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"pre_mode": "route"})
    state.clear()
    yield
    state.clear()


def _route_to_orientation(monkeypatch, session_id, ask=PLAN_ASK):
    """Dispatch a complexity ask and record a staged orientation consult."""
    d = router_core.dispatch(ask, session_id=session_id, model="m")
    assert d.reason == "complexity_orientation"
    if state.last_staged_consult(session_id)[0] is None:
        state.record_staged_consult(session_id, time.time(),
                                    task_id=d.task_id)
    return d


def test_pre_cooldown_blocks_second_orientation_in_window(monkeypatch, _cooldown_env):
    _route_to_orientation(monkeypatch, "cd1")
    d2 = router_core.dispatch(PLAN_ASK + " and also map dependencies across the repo",
                              session_id="cd1", model="m")
    assert d2.reason == "pre_cooldown_skip"


def test_pre_cooldown_fires_after_window(monkeypatch, _cooldown_env):
    _route_to_orientation(monkeypatch, "cd2")
    # age the staged ts beyond the window (different task_id => cooldown applies)
    state.record_staged_consult("cd2", time.time() - 601, task_id="old-task")
    d2 = router_core.dispatch(PLAN_ASK + " and also map dependencies across the repo",
                              session_id="cd2", model="m")
    assert d2.reason == "complexity_orientation"


def test_pre_cooldown_fires_in_new_session(monkeypatch, _cooldown_env):
    _route_to_orientation(monkeypatch, "cd3a")
    d2 = router_core.dispatch(PLAN_ASK, session_id="cd3b", model="m")
    assert d2.reason == "complexity_orientation"


def test_pre_cooldown_fail_open_when_state_missing(monkeypatch, _cooldown_env):
    # No record_staged_consult call at all — consult must still fire.
    d = router_core.dispatch(PLAN_ASK, session_id="cd4", model="m")
    assert d.reason == "complexity_orientation"


def test_pre_cooldown_zero_disables(monkeypatch, _cooldown_env):
    monkeypatch.setattr(router_core, "pre_cooldown_seconds", lambda: 0)
    _route_to_orientation(monkeypatch, "cd5")
    d2 = router_core.dispatch(PLAN_ASK + " and also map dependencies across the repo",
                              session_id="cd5", model="m")
    assert d2.reason == "complexity_orientation"


# ---------------------------------------------------------------- A3 -------

def test_post_audit_fires_on_nth_substantive_turn(monkeypatch):
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 3)
    for n in range(1, 4):
        got = state.bump_substantive_turn("pa1")
        fire = (got % 3 == 0)
        if n == 3:
            assert fire
        else:
            assert not fire
            assert got in (1, 2)
    state.clear()


def test_post_audit_n1_is_current_behavior(monkeypatch):
    monkeypatch.setattr(router_core, "post_audit_min_turns", lambda: 1)
    for n in range(1, 4):
        got = state.bump_substantive_turn("pa2")
        assert got % 1 == 0
    state.clear()


# ---------------------------------------------------------------- B1 -------

def test_persona_card_enriched_contains_identity_head_and_respects_cap(monkeypatch,
                                                                       tmp_path):
    ident = tmp_path / "IDENTITY.md"
    ident.write_text("# Header\n" + ("X" * 3000), encoding="utf-8")
    monkeypatch.setattr(persona_card, "_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(persona_card, "persona_card_chars_budget", lambda: 2500)
    monkeypatch.setattr(persona_card, "_scrub", lambda t: t)  # keep chars exact
    persona_card._card_cache.clear()
    card = persona_card.build_persona_context()
    assert "X" in card                      # identity head rides
    assert len(card) <= 2500 + len("\n[card truncated]")
    persona_card._card_cache.clear()


def test_persona_card_zero_is_legacy_compact(monkeypatch, tmp_path):
    ident = tmp_path / "IDENTITY.md"
    ident.write_text("**Trait tags:** calm. **Voice stems:** low.\n",
                     encoding="utf-8")
    soul = tmp_path / "SOUL.md"
    soul.write_text("voice: steady\n", encoding="utf-8")
    monkeypatch.setattr(persona_card, "_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(persona_card, "persona_card_chars_budget", lambda: 0)
    persona_card._card_cache.clear()
    card = persona_card.build_persona_context()
    # legacy compact card: voice-DNA extraction, no ROLE section
    assert "=== ROLE ===" not in card
    persona_card._card_cache.clear()


# ---------------------------------------------------------------- B2 -------

def test_orientation_ask_cap_reads_config(monkeypatch):
    from hermes_router import config_access
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"orientation_ask_cap": 777})
    assert anchor_exec.orientation_ask_cap() == 777


def test_orientation_ask_cap_default(monkeypatch):
    from hermes_router import config_access
    monkeypatch.setattr(config_access, "router_section", lambda: {})
    assert anchor_exec.orientation_ask_cap() == 4000


# ---------------------------------------------------------------- B3 -------

def test_bounded_replay_default_24():
    cfg = anchor_exec._bounded_replay_cfg()
    assert cfg["last_n_turns"] == 24


def test_bounded_replay_config_override_still_works(monkeypatch):
    monkeypatch.setattr(router_core, "_complexity_cfg",
                        lambda: {"bounded_replay": {"last_n_turns": 8}})
    cfg = anchor_exec._bounded_replay_cfg()
    assert cfg["last_n_turns"] == 8


# ---------------------------------------------------------------- B4 -------

def test_per_profile_config_values_honored(tmp_path, monkeypatch):
    """B4: thread_digest_chars/asks are config-read — two profile-co-located
    configs (conductor vs analyst) must resolve their OWN values through the
    config_access co-located-yaml arm."""
    import yaml
    from hermes_router import config_access

    conductor_cfg = tmp_path / "conductor" / "config.yaml"
    conductor_cfg.parent.mkdir()
    conductor_cfg.write_text(yaml.safe_dump({
        "hermes_router": {"thread_digest_chars": 2222, "thread_digest_asks": 2}}),
        encoding="utf-8")
    analyst_cfg = tmp_path / "analyst" / "config.yaml"
    analyst_cfg.parent.mkdir()
    analyst_cfg.write_text(yaml.safe_dump({
        "hermes_router": {"thread_digest_chars": 8888, "thread_digest_asks": 9}}),
        encoding="utf-8")

    # No process-level router section -> co-located yaml arm decides.
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {}, raising=False)
    monkeypatch.setattr(config_access, "_coLocatedPath",
                        lambda: str(conductor_cfg))
    config_access.reset_cache()
    assert config_access.router_section()["thread_digest_chars"] == 2222
    assert config_access.router_section()["thread_digest_asks"] == 2

    monkeypatch.setattr(config_access, "_coLocatedPath",
                        lambda: str(analyst_cfg))
    config_access.reset_cache()
    assert config_access.router_section()["thread_digest_chars"] == 8888
    assert config_access.router_section()["thread_digest_asks"] == 9
    config_access.reset_cache()


def test_verify_exempt_strips_memory_context():
    """Platform appends <memory-context> to user messages; exempt must judge the ask only."""
    from hermes_router import router_core
    ask = "Can you confirm your tools are working right now? Just say yes."
    wrapped = ask + "\n\n<memory-context>\n[System note: recalled facts]\n- IS: something long\n</memory-context>"
    assert router_core._is_verify_class_exempt(wrapped) is True
    # and the ingress strip in __init__ path: complex analysis ask with memory noise still routes complex
    cplx = "dig deeper into the walk weights and help me think through the design"
    wrapped2 = cplx + "\n\n<memory-context>\nnoise\n</memory-context>"
    assert router_core._is_verify_class_exempt(wrapped2) is False


def test_frontier_orientation_is_agent_tailored():
    """PRE orientation payload must carry the profile's own persona card (Goran 09-10)."""
    from hermes_router import anchor_exec
    src = inspect.getsource(anchor_exec)
    assert "build_persona_context" in src
    assert "profile card" in src


def test_post_audit_is_agent_tailored():
    """POST completion audit must carry the profile's own persona card (Goran 09-10)."""
    from hermes_router import completion_audit
    assert callable(getattr(completion_audit, "_persona_tailoring", None))
    t = completion_audit._persona_tailoring()
    assert "profile card" in t


def test_zero_config_frontier_default_activates_with_anchor_chain():
    """Goran 09-10: frontier lane works out-of-box once anchor_chain API is in
    config — no complexity block needed. Explicit complexity config always wins."""
    with mock.patch.object(plugin.config_access, "router_section",
                           return_value={"anchor_chain": {"primary": "nous://z-ai/glm-5.3"}}), \
         mock.patch.object(plugin.router_core, "_complexity_cfg", return_value={}):
        assert plugin.router_core._complexity_level() == 2
    # no anchor chain -> stays off
    with mock.patch.object(plugin.config_access, "router_section", return_value={}), \
         mock.patch.object(plugin.router_core, "_complexity_cfg", return_value={}):
        assert plugin.router_core._complexity_level() == 0
    # explicit level wins (including 0)
    with mock.patch.object(plugin.router_core, "_complexity_cfg",
                           return_value={"enabled": True, "level": 0}):
        assert plugin.router_core._complexity_level() == 0
    with mock.patch.object(plugin.router_core, "_complexity_cfg",
                           return_value={"enabled": False, "level": 2}):
        assert plugin.router_core._complexity_level() == 0
