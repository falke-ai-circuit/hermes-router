"""v3.0.0 anchor chain + daily cap guard + config-writer atomicity tests.

All network mocked; ledger pointed at tmp files; config writes go to tmp
config paths only — never the real profile config.
"""
import json
import os
import sys

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hermes_router import anchor_chain, config_writer  # noqa: E402


# ---------------------------------------------------------------------------
# Anchor chain scheme resolution
# ---------------------------------------------------------------------------


def test_parse_openrouter_uri():
    ep = anchor_chain.parse_anchor_uri("openrouter://anthropic/claude-fable-5.1", "primary")
    assert ep is not None
    assert ep.scheme == "openrouter"
    assert ep.model == "anthropic/claude-fable-5.1"
    assert ep.base_url == "https://openrouter.ai/api/v1"
    assert ep.api_key_env == "OPENROUTER_API_KEY"
    assert ep.role == "primary"


def test_parse_rejects_garbage():
    assert anchor_chain.parse_anchor_uri("", "primary") is None
    assert anchor_chain.parse_anchor_uri("no-scheme-here", "primary") is None
    assert anchor_chain.parse_anchor_uri("openrouter://", "primary") is None
    assert anchor_chain.parse_anchor_uri(None, "primary") is None  # type: ignore[arg-type]


def test_generic_scheme_via_custom_providers(monkeypatch):
    monkeypatch.setattr(anchor_chain, "_custom_providers", lambda: {
        "myprov": {"base_url": "https://api.myprov.example/v1", "key_env": "MYPROV_KEY"},
    })
    ep = anchor_chain.parse_anchor_uri("myprov://vendor/model-x", "judge")
    assert ep is not None
    assert ep.base_url == "https://api.myprov.example/v1"
    assert ep.api_key_env == "MYPROV_KEY"
    assert ep.model == "vendor/model-x"


def test_unresolvable_scheme_returns_none(monkeypatch):
    monkeypatch.setattr(anchor_chain, "_custom_providers", lambda: {})
    assert anchor_chain.parse_anchor_uri("nosuch://model", "primary") is None


def test_load_anchor_chain_missing_config(monkeypatch):
    import hermes_cli.config as hc

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {}, raising=False)
    chain = anchor_chain.load_anchor_chain()
    assert chain.primary is None and chain.judge is None
    assert chain.overflow == "pass_through"


def test_load_anchor_chain_full_block(monkeypatch):
    cfg = {
        "hermes_router": {
            "anchor_chain": {
                "primary": "openrouter://anthropic/claude-fable-5.1",
                "judge": "openrouter://openai/o4-mini",
                "overflow": "pass_through",
                "daily_cap_usd": 3.5,
                "pricing": {"openai/o4-mini": {"input_per_1m": 0.5, "output_per_1m": 1.5}},
            }
        }
    }
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg, raising=False)
    chain = anchor_chain.load_anchor_chain()
    assert chain.primary is not None and chain.judge is not None
    assert chain.daily_cap_usd == 3.5
    assert chain.pricing.get("openai/o4-mini", {}).get("output_per_1m") == 1.5
    # judge fallback to primary when judge missing
    cfg2 = {"hermes_router": {"anchor_chain": {"primary": "openrouter://a/b"}}}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg2, raising=False)
    chain2 = anchor_chain.load_anchor_chain()
    assert chain2.endpoint_for("judge") == chain2.primary


# ---------------------------------------------------------------------------
# Daily cap ledger
# ---------------------------------------------------------------------------


@pytest.fixture()
def ledger(tmp_path):
    p = str(tmp_path / "spend.json")
    anchor_chain._test_reset(p)
    yield p
    anchor_chain._test_reset(None)


def test_ledger_roundtrip(ledger):
    assert anchor_chain.today_spend() == 0.0
    t = anchor_chain.record_spend(0.25)
    assert abs(t - 0.25) < 1e-9
    assert abs(anchor_chain.today_spend() - 0.25) < 1e-9
    anchor_chain.record_spend(0.75)
    assert abs(anchor_chain.today_spend() - 1.0) < 1e-9
    # file is valid JSON with a date key
    data = json.load(open(ledger))
    assert any(len(k) == 10 and k[4] == "-" for k in data)


def test_ledger_ignores_nonpositive(ledger):
    anchor_chain.record_spend(0.0)
    anchor_chain.record_spend(-1.0)
    assert anchor_chain.today_spend() == 0.0


def test_corrupt_ledger_fails_open(ledger):
    with open(ledger, "w") as fh:
        fh.write("NOT JSON{{{")
    assert anchor_chain.today_spend() == 0.0


def test_cap_check_blocks_over_cap(ledger):
    chain = anchor_chain.AnchorChainCfg(None, None, daily_cap_usd=2.0)
    anchor_chain.record_spend(1.9)
    allowed, spend, projected = anchor_chain.cap_check(chain, 0.2)
    assert allowed is False
    assert abs(spend - 1.9) < 1e-9
    assert abs(projected - 2.1) < 1e-9


def test_cap_check_allows_under_cap(ledger):
    chain = anchor_chain.AnchorChainCfg(None, None, daily_cap_usd=2.0)
    anchor_chain.record_spend(1.0)
    allowed, _, projected = anchor_chain.cap_check(chain, 0.5)
    assert allowed is True
    assert abs(projected - 1.5) < 1e-9


def test_estimate_call_cost_unknown_model_conservative(ledger):
    ep = anchor_chain.parse_anchor_uri("openrouter://who/unknown-model", "primary")
    assert ep is not None
    # default conservative price: 0.5 in / 1.5 out per 1M
    c = anchor_chain.estimate_call_cost(ep, 1_000_000, 1_000_000, {})
    assert abs(c - 2.0) < 1e-6


def test_estimate_call_cost_priced_model(ledger):
    ep = anchor_chain.parse_anchor_uri("openrouter://openai/o4-mini", "primary")
    assert ep is not None
    pricing = {"openai/o4-mini": {"input_per_1m": 1.0, "output_per_1m": 4.0}}
    c = anchor_chain.estimate_call_cost(ep, 500_000, 250_000, pricing)
    assert abs(c - 1.5) < 1e-6


# ---------------------------------------------------------------------------
# Config writer — atomicity, forbidden keys, validation
# ---------------------------------------------------------------------------


def _write_yaml(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def test_write_plugin_section_atomic_roundtrip(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.yaml"
    _write_yaml(str(cfgp), "other:\n  a: 1\nuncensored_router:\n  enabled: true\n")
    monkeypatch.setattr(config_writer, "_config_path", lambda: str(cfgp))

    def mut(section):
        section["dry_run"] = True

    ok, detail = config_writer.write_plugin_section(mut, _path=str(cfgp))
    assert ok, detail
    # other top-level sections survive; legacy section migrated to canonical
    import yaml

    data = yaml.safe_load(open(str(cfgp)))
    assert data["other"] == {"a": 1}
    assert data["hermes_router"]["dry_run"] is True
    assert data["uncensored_router"]["dry_run"] is True  # legacy untouched
    # no temp files left behind
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".hermes-config-")]
    assert leftovers == []


def test_forbidden_keys_never_written(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.yaml"
    _write_yaml(str(cfgp), "hermes_router:\n  log_path: /keep/me\n  enabled: true\n")
    monkeypatch.setattr(config_writer, "_config_path", lambda: str(cfgp))

    def mut(section):
        section["log_path"] = "/evil/override"
        section["log_routes"] = False
        section["enabled"] = False

    ok, _ = config_writer.write_plugin_section(mut, _path=str(cfgp))
    assert ok
    import yaml

    data = yaml.safe_load(open(str(cfgp)))
    assert data["hermes_router"]["log_path"] == "/keep/me"
    assert "log_routes" not in data["hermes_router"]  # forbidden key stripped, not created
    assert data["hermes_router"]["enabled"] is False  # allowed change landed


def test_bad_mutator_leaves_config_intact(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.yaml"
    _write_yaml(str(cfgp), "hermes_router:\n  enabled: true\n")
    monkeypatch.setattr(config_writer, "_config_path", lambda: str(cfgp))

    def mut(section):
        raise RuntimeError("mutator blew up")

    ok, detail = config_writer.write_plugin_section(mut, _path=str(cfgp))
    assert not ok
    assert "error" in detail
    import yaml

    data = yaml.safe_load(open(str(cfgp)))
    assert data["hermes_router"]["enabled"] is True  # untouched


def test_yaml_roundtrip_validation(tmp_path, monkeypatch):
    # _dump_config must produce text that validates; simulate a value that
    # would break JSON-only validation but is fine YAML (config stays valid).
    cfgp = tmp_path / "config.yaml"
    _write_yaml(str(cfgp), "hermes_router:\n  note: plain\n")
    monkeypatch.setattr(config_writer, "_config_path", lambda: str(cfgp))

    def mut(section):
        section["note"] = "välue with unicode: ✓ — ok"

    ok, detail = config_writer.write_plugin_section(mut, _path=str(cfgp))
    assert ok, detail
    import yaml

    data = yaml.safe_load(open(str(cfgp), encoding="utf-8"))
    assert "✓" in data["hermes_router"]["note"]


def test_bump_cap_up_only():
    ok, _, eff = config_writer.bump_cap(2.0, 5.0, 2.0)
    assert ok and eff == 5.0
    ok, detail, eff = config_writer.bump_cap(2.0, 1.0, 2.0)
    assert not ok
    assert eff == 2.0
    ok, _, _ = config_writer.bump_cap(2.0, "abc", 2.0)
    assert not ok
    ok, _, eff = config_writer.bump_cap(2.0, 0.5, 2.0)
    assert not ok  # below floor is never allowed


def test_read_plugin_section_backcompat(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config",
                        lambda: {"uncensored_router": {"enabled": True, "chain": [1]}},
                        raising=False)
    sec = config_writer.read_plugin_section()
    assert sec.get("enabled") is True
    monkeypatch.setattr("hermes_cli.config.load_config",
                        lambda: {"hermes_router": {"enabled": False},
                                 "uncensored_router": {"enabled": True}},
                        raising=False)
    sec2 = config_writer.read_plugin_section()
    assert sec2.get("enabled") is False  # canonical wins