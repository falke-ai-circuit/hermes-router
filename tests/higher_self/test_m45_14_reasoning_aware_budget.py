"""M45-14: the advisory budget must pay for reasoning, or the model gets nothing.

Live shape: anchor consult returned HTTP 200 with an EMPTY body at
finish=length, reasoning_chars=0, max_tokens=2303 — the flat 1.74 chars/token
ratio sized the call for visible text only. With reasoning_effort: max the
reasoning tokens are billed INSIDE the same max_tokens budget, so they ate the
whole 2303 and the content came back empty. Raising the char budget to its
clamp (8000) papered over it; a bigger reasoning budget would bind again.

Fail-before: these tests are red on the flat formula (2303 for 3800 chars with
effort max) and green once the headroom is reasoning-aware.
"""
import hermes_router.anchor_exec as ax


VISIBLE_NEED = 2303        # tokens of actual text for a 3800-char advisory
MEASURED_MAX_REASONING = 8000  # max-effort reasoning tokens seen on the lane


def _budget(payload, char_budget=3800, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(ax, "advisory_char_budget", lambda: char_budget)
    return ax.apply_advisory_budget(payload)


def test_reasoning_max_leaves_room_for_the_answer(monkeypatch):
    """The defect: effort=max spends the whole 2303 on thinking -> empty 200."""
    p = _budget({"max_tokens": 12000, "reasoning_effort": "max"}, monkeypatch=monkeypatch)
    assert p["max_tokens"] > VISIBLE_NEED, p["max_tokens"]
    # after the reasoning bill there must still be enough for the advisory text
    assert p["max_tokens"] - MEASURED_MAX_REASONING >= VISIBLE_NEED - 200, p["max_tokens"]


def test_xhigh_also_gets_headroom_but_less_than_max(monkeypatch):
    hi = _budget({"max_tokens": 12000, "reasoning_effort": "xhigh"}, monkeypatch=monkeypatch)
    mx = _budget({"max_tokens": 12000, "reasoning_effort": "max"}, monkeypatch=monkeypatch)
    base = ax.advisory_max_tokens(3800, effort=None)  # visible-text base
    assert hi["max_tokens"] > base
    assert mx["max_tokens"] > hi["max_tokens"]


def test_low_effort_is_not_promoted_to_max_headroom(monkeypatch):
    lo = _budget({"max_tokens": 12000, "reasoning_effort": "low"}, monkeypatch=monkeypatch)
    mx = _budget({"max_tokens": 12000, "reasoning_effort": "max"}, monkeypatch=monkeypatch)
    assert lo["max_tokens"] < mx["max_tokens"]


def test_effort_from_config_when_the_payload_carries_none(monkeypatch):
    """anchor_exec injects anchor_chain.reasoning_effort AFTER the budget is
    applied, so the payload is silent at budget time. Reading only the payload
    would leave the live bug unfixed."""
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block",
                        lambda name: {"reasoning_effort": "max"} if name == "anchor_chain" else {})
    p = _budget({"max_tokens": 12000}, monkeypatch=monkeypatch)
    assert p["max_tokens"] > VISIBLE_NEED
    assert p["max_tokens"] - MEASURED_MAX_REASONING >= VISIBLE_NEED - 200


def test_no_reasoning_keeps_the_measured_flat_ratio(monkeypatch):
    """No effort knob -> byte-identical to the shipped formula (no drift)."""
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block", lambda name: {})
    assert ax.advisory_max_tokens(3800) == 2303
    p = _budget({"max_tokens": 12000}, monkeypatch=monkeypatch)
    assert p["max_tokens"] == 2303


def test_ceiling_is_never_impossible(monkeypatch):
    """A big char budget at max effort must not exceed what the model accepts."""
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block", lambda name: {})
    p = _budget({"max_tokens": 12000, "reasoning_effort": "max",
                 "model": "anthropic/claude-sonnet-5.5"}, char_budget=8000,
                monkeypatch=monkeypatch)
    limit = ax.model_output_limit("anthropic/claude-sonnet-5.5")
    assert limit > 0
    assert p["max_tokens"] <= limit, (p["max_tokens"], limit)


def test_unknown_model_clamps_to_the_conservative_default(monkeypatch):
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block", lambda name: {})
    assert ax.model_output_limit("nobody/unknown-model-9") == ax.DEFAULT_MODEL_OUTPUT_LIMIT
    assert ax.model_output_limit("") == ax.DEFAULT_MODEL_OUTPUT_LIMIT
    p = _budget({"max_tokens": 12000, "reasoning_effort": "max",
                 "model": "nobody/unknown-model-9"}, char_budget=8000,
                monkeypatch=monkeypatch)
    assert p["max_tokens"] <= ax.DEFAULT_MODEL_OUTPUT_LIMIT


def test_ceiling_is_configurable(monkeypatch):
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "router_section",
                        lambda: {"advisory_max_tokens_ceiling": 6000})
    assert ax.model_output_limit("anthropic/claude-sonnet-5.5") == 6000


def test_tiny_probe_exemption_is_untouched(monkeypatch):
    """max_tokens < 500 probe calls must never be resized — with or without
    a reasoning effort on the payload."""
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block", lambda name: {})
    monkeypatch.setattr(ax, "advisory_char_budget", lambda: 3800)
    for mt in (16, 256, 400, 499):
        p = ax.apply_advisory_budget({"max_tokens": mt, "reasoning_effort": "max"})
        assert p["max_tokens"] == mt, (mt, p["max_tokens"])


def test_a_starved_caller_budget_is_raised_on_a_reasoning_lane(monkeypatch):
    """A caller budget below base+headroom IS the empty-200 bug on this lane."""
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block", lambda name: {})
    monkeypatch.setattr(ax, "advisory_char_budget", lambda: 3800)
    p = ax.apply_advisory_budget({"max_tokens": 600, "reasoning_effort": "max"})
    assert p["max_tokens"] > 600


def test_apply_never_raises_on_a_junk_effort(monkeypatch):
    for junk in ({"effort": "max"}, "bogus", 7, None, ["max"], True):
        p = ax.apply_advisory_budget({"max_tokens": 12000, "reasoning_effort": junk})
        assert isinstance(p.get("max_tokens"), int)
