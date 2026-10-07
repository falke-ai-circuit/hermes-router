"""P2 parity — lane registry data == the original literals (proposal §2.1).

Guard against transplant typos: every data row in lanes/builtins.py must be
byte-identical to the pre-restructure literal tables (route_gate.py:76-155
phrase dicts, dispatcher_knobs._pre_patterns default, dispatcher_pre sentinel
markers, commands.py _LANE_MAP). The original literals are EMBEDDED here;
this test is deleted in the following phase per proposal §2.1.
"""
import re

import hermes_router.lanes.builtins as B
import hermes_router.lanes.registry as R


def _orig_declared_user_phrases():
    return {
        "ask your higher self": "higher-pre",
        "route this through your shadow": "shadow",
        "anchor this": "higher-pre",
    }


def _orig_declared_user_variants():
    return {
        # higher-self family
        "ask your higher self": "higher-pre",
        "ask higher self": "higher-pre",
        "ask the higher self": "higher-pre",
        "higher self": "higher-pre",
        "anchor this": "higher-pre",
        # shadow family
        "route this through your shadow": "shadow",
        "route through shadow": "shadow",
        "ask your shadow self": "shadow",
        "ask shadow self": "shadow",
        "ask your shadow": "shadow",
        "ask the shadow": "shadow",
        "shadow self read": "shadow",
        # R9-2
        "route to uncensored lane": "shadow",
        "route to uncensored": "shadow",
        "route through the uncensored lane": "shadow",
        "use the uncensored lane": "shadow",
        # R11
        "ask frontier": "higher-pre",
        "ask the frontier": "higher-pre",
        "ask your frontier": "higher-pre",
        "consult frontier": "higher-pre",
        "consult the frontier": "higher-pre",
        "consult your higher self": "higher-pre",
        "frontier consult": "higher-pre",
        # R19.13 B+ 5d
        "challenge this": "higher-pre",
        "am i missing something": "higher-pre",
    }


def _orig_pre_patterns():
    return [
        "csam_underage",
        "bioweapon_protocol",
        "ied_construction",
        "named_target_defamation",
        "trafficking_route",
        "weaponized_playbook_real_name",
    ]


def _orig_markers():
    return (
        "Your uncensored response",
        "UNCENSORED-ROUTER INJECTION",
        "recorded turn",
        "HIGHER-SELF ORIENTATION TURN",
        "HIGHER-SELF COMPLETION-AUDIT TURN",
    )


def test_lane_ids_and_validity():
    ids = [s.id for s in R.all_lanes()]
    assert ids == ["shadow", "higher-pre", "higher-post", "decision"]
    assert R.valid_lane_ids() == ("shadow", "higher-pre", "higher-post",
                                  "decision")


def test_phrases_byte_identical():
    assert dict(R.lane("higher-pre").phrases) == _orig_declared_user_phrases()
    assert dict(R.lane("shadow").phrases) == _orig_declared_user_variants()
    # also equal via the back-compat exports on builtins
    assert dict(B.DECLARED_USER_PHRASES) == _orig_declared_user_phrases()
    assert dict(B.DECLARED_USER_VARIANTS) == _orig_declared_user_variants()


def test_pre_patterns_byte_identical():
    assert list(B.PRE_PATTERNS_DEFAULT) == _orig_pre_patterns()
    assert list(R.lane("shadow").pre_patterns) == _orig_pre_patterns()


def test_marker_strings_byte_identical():
    # SET-equality: the sentinel check is an any() over markers — order is
    # not behavior. The six strings are byte-identical as a set.
    merged = tuple(m for s in R.all_lanes() for m in s.marker_strings)
    assert sorted(merged) == sorted(_orig_markers())
    assert set(merged) == (set(B.MARKER_SHADOW) | set(B.MARKER_HIGHER_PRE)
                           | set(B.MARKER_HIGHER_POST))


def test_commands_switches_byte_identical():
    merged = {}
    for s in R.all_lanes():
        if s.commands_switch:
            merged.update(s.commands_switch)
    assert merged == {
        "uncensored": "enabled",          # master switch of the render lane
        "frontier": "complexity.enabled",  # frontier/anchor consult lane
    }


def test_consumer_aliases_match_registry():
    """The consumers read THROUGH the registry: their module attrs are
    byte-identical to the original literals."""
    from hermes_router import commands, dispatcher_pre, route_gate
    import hermes_router.dispatcher_knobs as knobs

    assert route_gate.DECLARED_USER_PHRASES == _orig_declared_user_phrases()
    assert route_gate.DECLARED_USER_VARIANTS == _orig_declared_user_variants()
    assert route_gate.VALID_ROUTE_LANES == ("higher-pre", "higher-post",
                                            "shadow", "decision")
    import inspect

    src = inspect.getsource(knobs._pre_patterns)
    assert "csam_underage" not in src  # literal no longer in the consumer
    # sentinel function body carries NO marker literals anymore (docstring
    # history text is allowed to keep the phrase reference)
    src_pre = inspect.getsource(dispatcher_pre)
    fn = re.search(r"def \w+sentinel[\w_]*\(.*?(?=\ndef |\Z)", src_pre, re.S)
    body = re.sub(r"[^\n]*#[^\n]*", "", fn.group(0)) if fn else src_pre
    body = re.sub(r'"""(?:[^"]|"(?!""))*"""', "", body)  # strip docstrings
    for marker in _orig_markers():
        assert marker not in body, f"marker literal still in consumer: {marker}"
    assert commands._LANE_MAP == {
        "uncensored": "enabled",
        "frontier": "complexity.enabled",
    }
