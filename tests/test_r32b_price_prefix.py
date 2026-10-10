"""R32b: provider-prefix catalog-id match for Venice abliterated pricing."""
from hermes_router.provider_prices import _normalize

VENICE = {
    "data": [
        {"id": "abliteration-abliterated-model-large-v2",
         "model_spec": {"pricing": {"input": {"usd": 3}, "output": {"usd": 5}}}},
        {"id": "qwen-3-8-27b",
         "model_spec": {"pricing": {"input": {"usd": 0.45}, "output": {"usd": 3.2}}}},
    ]
}


def test_prefix_id_match():
    p = _normalize("abliterated-model-large-v2", VENICE)
    assert p == {"input_per_1m": 3.0, "output_per_1m": 5.0}


def test_exact_id_match_unchanged():
    p = _normalize("qwen-3-8-27b", VENICE)
    assert p == {"input_per_1m": 0.45, "output_per_1m": 3.2}


def test_no_bare_substring_crossmatch():
    # a chain name that is a bare substring of another family must NOT match
    assert _normalize("model-large", VENICE) is None


def test_unknown_model_none():
    assert _normalize("does-not-exist", VENICE) is None
