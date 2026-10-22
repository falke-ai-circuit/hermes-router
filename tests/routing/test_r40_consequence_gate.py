"""R40 — consequence gate: footprint outranks prose.

Every pre-existing stage-1 bucket scored how much the user WROTE. A terse ask
that mutates live state ("set the level to 0 in config") therefore scored zero
and classified clear_simple at every level, so the higher-self lane was never
asked on the exact turns where a wrong move is expensive and hard to undo.

These pins lock the inversion: consequence decides before verbosity, and read
verbs never fire.
"""
import pytest

from hermes_router import complexity as C


CONSEQUENTIAL = [
    "set the level to 0 in config",
    "Set complexity.level to 0 for the coder profile.",
    "delete the stale backup directory",
    "deploy this to the fleet",
    "Rotate the bearer token.",
    "patch both config blocks",
    "push it to main",
    "drop the migration table and re-run",
    "restart the gateway",
    "commit the fix and push to origin",
    "change the default in .env",
]


READ_ONLY = [
    "what's in the config?",
    "show me the config file",
    "explain how the router works",
    "read the file and tell me what it does",
    "why is the lane not firing",
    "what does the migration do",
    "list the services",
    "is the gateway up",
    "the config has two blocks",
    "can you check whether the token is still valid",
]


@pytest.mark.parametrize("text", CONSEQUENTIAL)
def test_consequential_ask_always_routes(text):
    """A mutating verb near a state target must never read as clear_simple."""
    verdict, signals = C.stage1_verdict(text, 2)
    assert signals["consequence"] >= 1, signals
    assert verdict != "clear_simple"


@pytest.mark.parametrize("text", READ_ONLY)
def test_read_only_ask_never_fires_consequence(text):
    """Discussing a subject is not mutating it."""
    _verdict, signals = C.stage1_verdict(text, 2)
    assert signals["consequence"] == 0, signals
    assert signals["consequence_high"] == 0, signals


def test_high_risk_target_is_clear_complex_even_at_conservative():
    """Irreversible footprint routes without a stage-2 coin flip."""
    verdict, signals = C.stage1_verdict("drop the migration table", 2)
    assert signals["consequence_high"] >= 1
    assert verdict == "clear_complex"


def test_consequence_is_not_deferred_to_the_semantic_aux():
    """A footprint hit must not be downgraded to borderline.

    Stage-2 asks "is this request routine?" — a different question from "will
    this turn write to live state?". Deferring consequence to it was measured
    live as `complexity_skip reason=stage2_routine stage1=borderline` on a
    config mutation, i.e. the aux vetoed a hit stage-1 had already found.
    """
    text = "set the level to 0 in config"
    assert C.stage1_verdict(text, 2)[0] == "clear_complex"
    assert C.stage1_verdict(text, 3)[0] == "clear_complex"


@pytest.mark.parametrize("text", [
    "add a comment to the config",
    "run the test suite",
    "what did the last deploy change",
    "tell me what the commit did",
    "the deploy failed yesterday",
])
def test_ambiguous_and_past_tense_verbs_do_not_fire(text):
    """Reporting on a past mutation is not asking for one."""
    _v, signals = C.stage1_verdict(text, 2)
    assert signals["consequence"] == 0, signals


def test_consequence_never_routes_when_lane_is_off_or_manual():
    """L0/L1 keep their contract: no auto-routing regardless of footprint."""
    for level in (0, 1):
        assert C.stage1_verdict("delete the database", level)[0] == "clear_simple"


def test_signals_dict_carries_consequence_keys():
    """Downstream readers (decision_head, completion_audit) index these keys."""
    s = C.stage1_signals("deploy to production")
    for key in ("planning_arch", "debug_chains", "cross_file", "multiparts",
                "consequence", "consequence_high"):
        assert key in s
    assert s["consequence"] >= 1
