"""D3 residuals round 2 — pins for the midturn-mode boolean coercion fix.

ROOT CAUSE (recovery, live 2026-10-02): `decision.midturn: on` UNQUOTED
parses as YAML boolean True; decision_midturn._mode did
str(True or "off").strip().lower() == "true" -> not in VALID_MODES ->
silent "off". Same v4.13.5 code delivered a banner on evol (quoted 'on')
while recovery's midturn lane stayed dead and only the POST leg saw the
turn — the one-word fork answer "(A)" has a single paren ordinal, so the
POST structural gate correctly logged post_gate_insufficient_structure.
"""
import yaml

import pytest

from hermes_router import decision as _dec
from hermes_router import decision_midturn as _dmt


def _mode_from_yaml(block_yaml: str) -> str:
    cfg = yaml.safe_load(block_yaml) or {}
    dec = cfg.get("hermes_router", {}).get("decision", {})
    merged = dict(_dec.DEFAULTS)
    merged.update({k: v for k, v in dec.items() if v is not None})
    return _dmt._mode(merged)


def test_mode_boolean_true_coerces_on():
    assert _dmt._mode({"midturn": True}) == "on"


def test_mode_boolean_false_coerces_off():
    assert _dmt._mode({"midturn": False}) == "off"


def test_mode_valid_strings_passthrough():
    assert _dmt._mode({"midturn": "on"}) == "on"
    assert _dmt._mode({"midturn": "shadow"}) == "shadow"
    assert _dmt._mode({"midturn": "off"}) == "off"


def test_mode_truthy_strings_coerce_on():
    assert _dmt._mode({"midturn": "true"}) == "on"
    assert _dmt._mode({"midturn": "yes"}) == "on"
    assert _dmt._mode({"midturn": "1"}) == "on"


def test_mode_unknown_and_missing_default_off():
    assert _dmt._mode({"midturn": "maybe"}) == "off"
    assert _dmt._mode({}) == "off"


def test_recovery_block_shape_unquoted_on_resolves_on():
    # Recovery's live remote block shape: decision sub-block FIRST, then
    # enabled/complexity siblings — and midturn UNQUOTED (boolean True).
    y = """
hermes_router:
  decision:
    enabled: true
    backend: jev_native
    level: 2
    midturn: on
    post_audit: false
  enabled: true
  complexity:
    level: 2
"""
    assert _mode_from_yaml(y) == "on"


def test_working_block_shape_quoted_on_stays_on():
    y = """
hermes_router:
  decision:
    enabled: true
    midturn: 'on'
"""
    assert _mode_from_yaml(y) == "on"


def test_mode_never_raises_on_garbage():
    class Boom:
        def strip(self):
            raise RuntimeError("boom")

    assert _dmt._mode({"midturn": Boom()}) == "off"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
