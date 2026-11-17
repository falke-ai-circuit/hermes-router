"""M50-34 — closure-pass target resolution.

The defect these tests pin is not hypothetical: in ALL 11 profiles
`model.provider` is `nous` while the provider block is registered as
`custom:nousinf`, so the old exact `providers.get(provider)` returned None on
every install. The closure pass — the only mechanism that hands the higher-self
note back to the agent's own model so it can emit a closing message — was
therefore skipped on 61 of 64 attempts (`no_target`) and succeeded twice in 386
POST events. Every "is it live?" check passed while the feature was dead.

A config-name difference between two surfaces must not disarm a mechanism, and
when a resolver does fail it must SAY which rule failed.
"""
import os
import sys

PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PARENT)

from hermes_router import closure_pass as cp  # noqa: E402

FLEET_PROVIDERS = {
    "custom:nousinf": {"base_url": "https://inference-api.nousresearch.com/v1",
                       "key_env": "NOUS_API_KEY"},
    "custom:minimax": {"base_url": "https://api.minimax.test/v1",
                       "key_env": "MINIMAX_API_KEY"},
    "ollama-cloud": {"base_url": "https://ollama.test/v1",
                     "key_api_key_env": "OLLAMA_KEY"},
}


def _cfg(provider="nous", model="deepseek/deepseek-v4.1-flash-1017",
         providers=None):
    return {"model": {"default": model, "provider": provider},
            "providers": FLEET_PROVIDERS if providers is None else providers}


# --------------------------------------------------------------- resolver ----

def test_exact_provider_match_wins():
    entry, key, rule = cp._resolve_provider(
        {"nous": {"base_url": "https://x/v1", "key_env": "K"}}, "nous", "m")
    assert rule == "exact" and key == "nous" and entry["base_url"] == "https://x/v1"


def test_custom_prefix_is_normalised_in_both_directions():
    provs = {"custom:nousinf": {"base_url": "https://x/v1", "key_env": "K"}}
    entry, key, rule = cp._resolve_provider(provs, "nousinf", "m")
    assert entry and rule == "prefix" and key == "custom:nousinf"
    # and the reverse: config names the prefixed form, block is bare
    entry2, key2, _ = cp._resolve_provider({"nousinf": {"base_url": "https://y/v1"}},
                                           "custom:nousinf", "m")
    assert entry2 and key2 == "nousinf"


def test_containment_resolves_the_live_fleet_shape():
    """The exact production shape: provider `nous`, block `custom:nousinf`."""
    entry, key, rule = cp._resolve_provider(FLEET_PROVIDERS, "nous", "m")
    assert entry is not None
    assert key == "custom:nousinf"
    assert rule == "contains"
    assert entry["base_url"].endswith("/v1")


def test_ambiguous_containment_refuses_to_guess():
    provs = {"custom:nousinf": {"base_url": "https://a/v1"},
             "custom:nousbackup": {"base_url": "https://b/v1"}}
    entry, key, rule = cp._resolve_provider(provs, "nous", "m")
    assert entry is None and key == ""
    assert rule == "no_provider_match"


def test_model_namespace_used_when_provider_is_empty():
    entry, key, rule = cp._resolve_provider(FLEET_PROVIDERS, "", "nousinf/model-x")
    assert entry is not None and rule == "model_ns"


def test_unknown_provider_reports_a_reason_not_silence():
    _, _, rule = cp._resolve_provider(FLEET_PROVIDERS, "brave", "m")
    assert rule == "no_provider_match"


# ----------------------------------------------------------------- target ----

def test_target_resolves_on_fleet_config(monkeypatch):
    monkeypatch.setenv("NOUS_API_KEY", "k" * 40)
    monkeypatch.setattr(cp, "_config", lambda: _cfg())
    tgt = cp._target()
    assert tgt is not None, "closure pass must find the agent's own model"
    assert tgt["model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert tgt["key"] == "k" * 40
    assert tgt["provider"] == "custom:nousinf"
    assert cp.last_target_rule == "contains"


def test_target_keeps_full_model_slug(monkeypatch):
    """Custom providers 404 on a stripped model name — the slug must be exact."""
    monkeypatch.setenv("NOUS_API_KEY", "k")
    monkeypatch.setattr(cp, "_config", lambda: _cfg())
    assert cp._target()["model"] == "deepseek/deepseek-v4.1-flash-1017"


def test_target_reports_no_model_separately(monkeypatch):
    monkeypatch.setattr(cp, "_config",
                        lambda: {"model": {"provider": "nous"}, "providers": {}})
    assert cp._target() is None
    assert cp.last_target_rule == "no_model"


def test_revise_skip_reason_names_the_failed_rule(monkeypatch):
    """A skip must be diagnosable from the log alone."""
    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    monkeypatch.setattr(cp, "_config",
                        lambda: _cfg(provider="brave", providers=FLEET_PROVIDERS))
    out = cp.revise("ask", "an answer long enough", "a note")
    assert out is None
    assert cp.last_reason.startswith("no_target:"), cp.last_reason
    assert cp.last_reason != "no_target", "bare no_target is the defect"


def test_disabled_kill_switch_still_short_circuits(monkeypatch):
    monkeypatch.setattr(cp, "enabled", lambda: False)
    assert cp.revise("ask", "answer", "note") is None
    assert cp.last_reason == "disabled"
