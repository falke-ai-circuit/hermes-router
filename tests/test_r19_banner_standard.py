"""R19.9: decision banner standardization — provider name + persona label."""
from hermes_router import decision, decision_midturn, debug_banner


def test_banner_shows_provider_not_url():
    meta = {"model": "typesafe/jev-router", "endpoint":
            "https://openrouter.ai/api/v1/chat/completions",
            "tokens_in": 100, "tokens_out": 20, "latency_s": 0.5}
    b = decision.render_decision_banner("post", "typesafe/jev-router", meta)
    assert "@ openrouter" in b, b
    assert "impulse (decision)" in b, b
    assert "https://" not in b, b


def test_banner_l2_explanation_line():
    meta = {"model": "typesafe/jev-router", "endpoint": "", "tokens_in": 1,
            "tokens_out": 1}
    b = debug_banner.format_banner(
        lane="decision", trigger="midturn", model="typesafe/jev-router",
        endpoint="", tokens_in=1, tokens_out=1, est_cost=0.0,
        latency_s=0.1, initiator="agent")
    assert "impulse (decision)" in b, b
