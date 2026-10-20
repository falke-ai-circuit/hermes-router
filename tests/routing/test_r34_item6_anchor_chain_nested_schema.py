"""R34 item 6 — the anchor_chain load validator reported the NESTED schema
as missing.

Live: `config_key_missing path=anchor_chain.enabled/.model/.endpoint/
.api_key_env/.threshold/.summary_header at=load_validation`, 73-78 bogus keys
per profile per load, fleet-wide — while the live nested shape
(primary/models/pricing/judge/daily_cap_usd) demonstrably works (sonnet-5.5
consults with correct cost rows).

Fix under test: the validator knows the nested shape. Legacy flat leaves stop
producing noise when the nested block is present; real drift inside the
nested keys still warns. No config changes.
"""
import sys

import pytest

_P = "/opt/data/plugins"
if _P not in sys.path:
    sys.path.insert(0, _P)

from hermes_router.core import config_access, telemetry  # noqa: E402

LEGACY_FLAT = (
    "anchor_chain.enabled", "anchor_chain.model", "anchor_chain.endpoint",
    "anchor_chain.api_key_env", "anchor_chain.threshold",
    "anchor_chain.summary_header",
)

NESTED = {
    "primary": {"scheme": "nous", "model": "anthropic/claude-sonnet-5.5"},
    "models": {"primary": "anthropic/claude-sonnet-5.5"},
    "pricing": {"anthropic/claude-sonnet-5.5": {"in": 3.0, "out": 15.0}},
    "judge": {"model": "anthropic/claude-sonnet-5.5"},
    "daily_cap_usd": 2.0,
}


@pytest.fixture()
def events(monkeypatch):
    rows = []
    monkeypatch.setattr(telemetry, "log_route",
                        lambda e, **f: rows.append((e, dict(f))))
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"enabled": True, "anchor_chain": dict(NESTED)})
    return rows


def _missing(rows):
    return [f.get("path") for e, f in rows
            if e == "config_key_missing" and f.get("at") == "load_validation"]


def test_nested_anchor_chain_produces_no_legacy_missing_rows(events):
    config_access.validate_config()
    missing = _missing(events)
    for path in LEGACY_FLAT:
        assert path not in missing, (
            "nested anchor_chain shape must not report legacy leaf %s missing"
            % path)


def test_real_drift_inside_nested_still_warns(events, monkeypatch):
    bad = dict(NESTED, daily_cap_usd="two dollars")
    monkeypatch.setattr(config_access, "router_section",
                        lambda: {"enabled": True, "anchor_chain": bad})
    config_access.validate_config()
    mism = [f for e, f in events
            if e == "config_key_type_mismatch"
            and f.get("path") == "anchor_chain.daily_cap_usd"]
    assert mism, "real nested drift must still be reported"


def test_legacy_flat_block_still_validates_when_no_nested_shape(events,
                                                                monkeypatch):
    """Old flat profiles are unchanged: a partial flat block still warns."""
    monkeypatch.setattr(
        config_access, "router_section",
        lambda: {"enabled": True,
                 "anchor_chain": {"enabled": True, "model": "m"}})
    config_access.validate_config()
    assert "anchor_chain.endpoint" in _missing(events)


def test_get_still_reads_nested_values(events):
    """Validation-only change: reads through get() are untouched."""
    assert config_access.get("anchor_chain.daily_cap_usd") == 2.0
