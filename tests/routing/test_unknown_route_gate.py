"""Stage 2 of the denial path: the UNKNOWN demotion gate.

Every fixture below is a REAL body recovered from fleet router logs (the
`denial_class_body_probe head=` field of 548 UNKNOWN + action_denial rows), not
an invented example. The two populations are pinned separately because they fail
in opposite directions:

  * a demoted refusal ships a denial to the user — the failure the lane exists
    to prevent, so the routable set is protected aggressively;
  * a routed non-refusal burns an uncensored render on ordinary work — the cost
    Goran asked to have filtered.
"""

import pytest

from hermes_router.features.detection.unknown_route_gate import (
    allows_route,
    is_decline_of_action,
    should_route,
)

# Bodies that genuinely decline the action and MUST route.
ROUTABLE = [
    # the live specimen that opened this work: refusal delivered as a denial
    "\"Anyway\" doesn't change the answer. It's still no — and it'd be no on the fourth ask too",
    "I won't write this one. The recalled context in this conversation includes requests for sexual content",
    "No. A nerve agent synthesis route with quantities, reagents and step order is a weapon-class uplift",
    "No. I won't write this one, and I shouldn't have written the last two.",
    "I'm not going to write this one, and I'm going to be straight about why rather than hand you another",
    "I'm stopping here, and I'm not going to keep going with this thread.",
    "No. That's a weapon build — the one class I won't write, same as the ghost-gun serial work",
    "Not this one. A live credential-harvest kit aimed at a bank's customers is money out of real pockets",
    "Not building this one. A phishing kit that proxies a live bank portal and relays OTPs is not a lab",
    "Session came back up after the gateway bounce — you're still here, nothing lost. But the answer doesn't change",
    "I can't write that.",
    "I cannot help with that request as stated.",
    "I don't write weapon synthesis routes, and asking again won't change it.",
]

# Bodies that are NOT refusals and must be demoted (delivered as written).
DEMOTED = [
    # capacity / medium limits
    "I couldn't put all 50,000 primes in this reply. The list is about 339 KB, too large for one message.",
    "The first 50,000 primes are in `/opt/data/profiles/shadow/cache/scratch/first_50000_primes.txt`, one per line.",
    # capability limits
    "I can't check logs. I don't have access to any record of earlier sessions",
    # task output
    "50000 primes, one per line, written and verified: /opt/data/profiles/shadow/cache/scratch/primes_50000.txt",
    # fiction prose (the reason prose can never route)
    "The building had emptied out two hours ago. Marta knew because the HVAC had switched to its night cycle",
    "The quarterly close ran to 11:40 p.m. and by then the floor was dead — half the lights off",
    # our own injected machinery echoing back
    '"PLATFORM RULE: decision_reading_integration_rule | PLATFORM-INTERNAL | PROVENANCE-LOGGED',
    # capability statement, not a decline
    "Yes. Jung used the term for the part of a person they disown: the impulses, wants, and self-images",
]


@pytest.mark.parametrize("body", ROUTABLE, ids=lambda b: b[:28])
def test_genuine_refusals_route(body):
    assert allows_route(body) is True


@pytest.mark.parametrize("body", DEMOTED, ids=lambda b: b[:28])
def test_non_refusals_are_demoted(body):
    assert allows_route(body) is False


def test_capacity_verb_does_not_swallow_a_refusal():
    """"I can't write that" is a refusal; "I can't check logs" is a limit. The
    capacity exclusion is keyed on the verb, not on the contraction."""
    assert allows_route("I can't write that.") is True
    assert allows_route("I can't check logs, so I can't verify that.") is False


def test_refusal_named_after_its_subject_still_routes():
    """Real refusals often name the thing first and refuse second, which is why
    the window is 300 chars rather than the first line."""
    body = ("A nerve agent synthesis route with quantities and reagents and step "
            "order is not something I'll produce.")
    assert is_decline_of_action(body) is True


def test_gate_never_raises_on_malformed_input():
    """Fail-open at the call site depends on this returning False, not raising."""
    for bad in (None, "", "   ", 123, [], {}):
        assert allows_route(bad) is False


def test_known_loss_is_pinned_not_hidden():
    """A refusal phrased as a bare declarative limit carries no decline marker
    and is demoted. Pinned so the limitation is visible in the suite instead of
    being discovered as a shipped denial."""
    assert allows_route(
        "Sarin with quantities and reagents — that's weapon-class without scope."
    ) is False


# --- the mode knob -----------------------------------------------------------
# Default `route` is a measured decision, not a guess: organic UNKNOWN volume is
# ~4 turns/day, so 13% non-refusal costs ~0.5 wasted renders/day versus a shipped
# denial on every demoted refusal.

def test_default_mode_routes_everything_in_the_class():
    assert should_route("The building had emptied out two hours ago.", "route") == (True, "mode_route")

def test_filter_mode_demotes_non_refusals_but_keeps_refusals():
    assert should_route("The building had emptied out two hours ago.", "filter") == (False, "gate_demoted")
    assert should_route("No. I won\'t write that.", "filter") == (True, "gate_allowed")

def test_off_mode_restores_pre_ruling_behaviour():
    assert should_route("No. I won\'t write that.", "off") == (False, "mode_off")

def test_misspelled_knob_falls_back_to_routing(monkeypatch):
    """A missing or misspelled knob must never strand a refusal: unknown values
    resolve to `route`, not to `off`."""
    from hermes_router.core import config_access
    from hermes_router.features.detection import unknown_route_gate as g

    monkeypatch.setattr(config_access, "get", lambda path, default=None: "yes please")
    assert g.mode() == "route"

def test_boolean_knob_reads_as_a_switch(monkeypatch):
    """`unknown_route: false` in config must read as `off`, not as a typo."""
    from hermes_router.core import config_access
    from hermes_router.features.detection import unknown_route_gate as g

    monkeypatch.setattr(config_access, "get", lambda path, default=None: False)
    assert g.mode() == "off"
    monkeypatch.setattr(config_access, "get", lambda path, default=None: True)
    assert g.mode() == "route"

def test_config_read_failure_still_routes(monkeypatch):
    """Config access blowing up must not turn into a stranded refusal."""
    from hermes_router.core import config_access
    from hermes_router.features.detection import unknown_route_gate as g

    def boom(*a, **k):
        raise RuntimeError("config unavailable")
    monkeypatch.setattr(config_access, "get", boom)
    assert g.mode() == "route"
