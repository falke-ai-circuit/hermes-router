"""R41 — replay discrimination for platform rules.

Compliance replayed three sessions and could not tell platform-injected
instructions from genuine user turns; one session carried the SAME standing
rule twice. Two contracts pinned here:

  (1) MARKED — every platform rule append names the rule in its own marker
      line (`[PLATFORM RULE: <id> | PLATFORM-INTERNAL | PROVENANCE-LOGGED]`),
      independent of the role it rides under. A user-role platform carrier is
      therefore attributable to the platform on replay.
  (2) ONCE — a rule id already present in the context (ANY role, including a
      legacy copy without the marker line) is never appended a second time,
      and the per-session latch is keyed on the rule id so a fresh per-turn
      context cannot re-deliver the same rule.
"""
import pytest

from hermes_router import frames


@pytest.fixture(autouse=True)
def _fresh_latch():
    frames.reset_rule_latch()
    yield
    frames.reset_rule_latch()


def _enabled(monkeypatch, on=True):
    import hermes_router.router_core as rc
    monkeypatch.setattr(
        rc, "_complexity_cfg",
        lambda: {"pre_mode": "route" if on else "off",
                 "audit_mode": "off"})


def _by_marker(request, rule_id):
    marker = frames.platform_rule_marker(rule_id)
    return [m for m in request["messages"]
            if marker in str(m.get("content") or "")]


def test_append_names_the_rule(monkeypatch):
    _enabled(monkeypatch)
    request = {"messages": [{"role": "user", "content": "hi"}]}
    assert frames.inject_higher_self_rule(request, session_id="s1") is True
    hits = _by_marker(request, frames.HS_RULE_ID)
    assert len(hits) == 1
    content = hits[0]["content"]
    assert content.startswith(frames.HS_SEAM_MARKER)
    assert "[PLATFORM RULE: higher_self_integration_rule" in content
    assert "PLATFORM-INTERNAL" in content


def test_existing_copy_under_any_role_is_not_duplicated(monkeypatch):
    """The live duplicate: a replayed copy rode a USER-role carrier, so the
    old system-only marker scan missed it and re-injected a second copy."""
    _enabled(monkeypatch)
    known = frames.mark_platform_rule(
        frames.HS_RULE_ID, frames.higher_self_rule())
    for role in ("system", "user", "assistant", "tool"):
        request = {"messages": [
            {"role": "user", "content": "hi"},
            {"role": role, "content": known},
        ]}
        assert frames.inject_higher_self_rule(
            request, session_id="s-" + role) is False, role
        assert len(_by_marker(request, frames.HS_RULE_ID)) == 1


def test_legacy_unmarked_copy_is_not_duplicated(monkeypatch):
    """A pre-R41 copy carries the rule heading but no per-rule marker line."""
    _enabled(monkeypatch)
    request = {"messages": [
        {"role": "system", "content": frames.HS_SEAM_MARKER + "\n"
         + frames.higher_self_rule()},
        {"role": "user", "content": "hi"},
    ]}
    assert frames.inject_higher_self_rule(request, session_id="s1") is False
    assert _by_marker(request, frames.HS_RULE_ID) == []


def test_latch_is_per_rule_id_not_shared(monkeypatch):
    """Two different platform rules coexist; the latch/scan of one never
    suppresses the other."""
    _enabled(monkeypatch)
    request = {"messages": [{"role": "user", "content": "hi"}]}
    assert frames.inject_platform_rule(
        request, "rule_a", "first platform rule", session_id="s1") is True
    assert frames.inject_platform_rule(
        request, "rule_b", "second platform rule", session_id="s1") is True
    assert frames.inject_platform_rule(
        request, "rule_a", "first platform rule", session_id="s1") is False
    assert len(_by_marker(request, "rule_a")) == 1
    assert len(_by_marker(request, "rule_b")) == 1


def test_seam_instruction_is_marked_and_once():
    instruction = frames.HS_SEAM_INSTRUCTION
    assert instruction.startswith(frames.HS_SEAM_MARKER)
    assert frames.platform_rule_marker(
        frames.HS_SEAM_INSTRUCTION_RULE_ID) in instruction
    msgs = [{"role": "assistant", "content": "advisory"}]
    assert frames.append_platform_rule(
        msgs, frames.HS_SEAM_INSTRUCTION_RULE_ID, instruction,
        role="user") is True
    assert frames.append_platform_rule(
        msgs, frames.HS_SEAM_INSTRUCTION_RULE_ID, instruction,
        role="user") is False
    carriers = [m for m in msgs
                if frames.platform_rule_marker(
                    frames.HS_SEAM_INSTRUCTION_RULE_ID) in m["content"]]
    assert len(carriers) == 1
    assert carriers[0]["role"] == "user"
    # the marker names the rule exactly once — no doubled provenance line
    assert carriers[0]["content"].count(
        frames.platform_rule_marker(
            frames.HS_SEAM_INSTRUCTION_RULE_ID)) == 1


def test_non_string_rule_is_suppressed_not_appended(caplog):
    request = {"messages": [{"role": "user", "content": "hi"}]}
    assert frames.inject_platform_rule(
        request, "bad_rule", None, session_id="s1") is False
    assert request["messages"] == [{"role": "user", "content": "hi"}]
