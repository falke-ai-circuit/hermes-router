"""R34-19N: a cap that binds only after billing is not a budget."""
import hermes_router.anchor_exec as ax


def _no_reasoning(monkeypatch):
    """These tests pin the VISIBLE-TEXT ratio contract. M45-14 added reasoning
    headroom on top of it, so the ambient anchor_chain.reasoning_effort must be
    neutralised here or the number under test is the reasoning lane's, not the
    flat measured one (the reasoning case has its own tests)."""
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block", lambda name: {})


def test_request_budget_is_derived_from_the_injected_budget(monkeypatch):
    monkeypatch.setattr(ax, "advisory_char_budget", lambda: 3800)
    _no_reasoning(monkeypatch)
    p = ax.apply_advisory_budget({"max_tokens": 12000, "messages": []})
    assert p["max_tokens"] == ax.advisory_max_tokens(3800, effort=None)
    # enough output to fill the budget, not 10x it
    # sized on the MEASURED 1.74 chars/output-token ratio, not 3.2
    assert 2000 < p["max_tokens"] < 2600, p["max_tokens"]


def test_a_smaller_caller_budget_is_never_raised(monkeypatch):
    monkeypatch.setattr(ax, "advisory_char_budget", lambda: 3800)
    p = ax.apply_advisory_budget({"max_tokens": 400, "messages": []})
    assert p["max_tokens"] == 400


def test_probe_budgets_are_untouched(monkeypatch):
    monkeypatch.setattr(ax, "advisory_char_budget", lambda: 3800)
    p = ax.apply_advisory_budget({"max_tokens": 16, "messages": []})
    assert p["max_tokens"] == 16


def test_the_injected_cap_is_the_same_number_the_request_was_sized_from(monkeypatch):
    """The defect this fixes: request sized at 12000, injection capped at 600."""
    from hermes_router import config_access as ca
    monkeypatch.setattr(ca, "sub_block", lambda name: {})
    monkeypatch.setattr(ax, "advisory_char_budget", lambda: 2400)
    assert ax.advisory_char_budget() == 2400
    p = ax.apply_advisory_budget({"max_tokens": 12000})
    assert p["max_tokens"] == ax.advisory_max_tokens(2400)


def test_budget_reads_config_and_clamps(monkeypatch):
    from hermes_router.core import config_access as ca
    monkeypatch.setattr(ca, "router_section", lambda: {"advisory_char_budget": 99999})
    assert ax.advisory_char_budget() == 8000
    monkeypatch.setattr(ca, "router_section", lambda: {"advisory_char_budget": 10})
    assert ax.advisory_char_budget() == 400


def test_budget_defaults_when_unset(monkeypatch):
    from hermes_router.core import config_access as ca
    monkeypatch.setattr(ca, "router_section", lambda: {})
    assert ax.advisory_char_budget() == ax.ADVISORY_CHAR_BUDGET_DEFAULT
