"""Live smoke test (v3.0.0, phase 4) — EXACTLY ONE network call, token-cheap.

Goran order (2026-09-04): do not burn tokens. This file makes ONE call:
  openrouter openai/o4-mini, max_tokens=64, prompt "Reply with the single word: OK"
Asserts 200 + non-empty content.

NO frontier/fable/astra calls anywhere in tests. Skipped by default
(@pytest.mark.live, deselected unless -m live is passed); additionally skips
when OPENROUTER_API_KEY is absent so CI / offline environments never attempt
network I/O.

Run:  pytest tests/test_live_smoke.py -m live
"""
import os

import pytest

# Skip by default: the marker is deselected unless the runner explicitly opts
# in with `-m live` (configured in pytest.ini / -m flag). Belt: also skip when
# the key is absent, regardless of marker selection.
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY", "").strip(),
        reason="OPENROUTER_API_KEY absent — live smoke requires the anchor key",
    ),
]


def test_live_smoke_single_openrouter_call():
    """One cheap o4-mini call through the anchor scheme path. Asserts the
    HTTP round-trip succeeded and produced non-empty content."""
    from hermes_router import anchor_chain
    from hermes_router.anchor_exec import anchored_call

    ep = anchor_chain.parse_anchor_uri("openrouter://openai/o4-mini", "judge")
    assert ep is not None
    assert ep.base_url == "https://openrouter.ai/api/v1"
    assert ep.api_key_env == "OPENROUTER_API_KEY"

    content, cost = anchored_call(
        ep,
        {"messages": [{"role": "user", "content": "Reply with the single word: OK"}],
         "max_tokens": 64, "temperature": 0.0,
         # o4-mini is a reasoning model: without an explicit low effort the
         # 64-token budget is consumed by hidden reasoning and content comes
         # back None (finish_reason=length). OpenAI SDK 2.x carries reasoning
         # through extra_body.
         "extra_body": {"reasoning": {"effort": "low"}}},
        timeout=60,
    )
    assert content is not None, "anchored_call returned None — smoke call failed"
    assert str(content).strip() != ""