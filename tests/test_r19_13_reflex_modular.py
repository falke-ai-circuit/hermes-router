"""R19.13 second addition (Goran directive): reflex lane modularization.

'decision' lane becomes the 'reflex' lane; existing decision behavior =
reflex TYPE 'decision'. Cosmetic/structural ONLY — ZERO behavior change.
Pin battery:
  1. behavior parity — the registry's decision detector/handler ARE the
     existing decision.py functions (same input -> same verdict path,
     banner label, ledger rows unchanged)
  2. registry silent default for unknown types (no fire, no log spam)
  3. ledger backcompat — fork_class accepts legacy AND 'reflex:decision'
  4. config alias — hermes_router.reflex block wins; decision fallback.
"""
import json

import hermes_router.config_access as _cac
import hermes_router.debug_banner as _dbgmod
from hermes_router import decision as D
from hermes_router import debug_banner as DB
from hermes_router import reflex as R
from hermes_router import router_core as RC


DECIDE_ASK = "Decide this:\nOption A — keep, because stable.\nOption B — rewrite, since drift."


# --- 1. parity: registry delegates to the EXISTING functions -----------------

def test_registry_has_only_decision():
    assert R.registered_types() == ("decision",)


def test_registry_detector_is_detect_v3():
    """Zero behavior change: the registered detector IS decision.detect_v3 —
    same input -> same hit."""
    entry = R.registry_lookup("decision")
    assert entry is not None
    assert entry["handle"] is D.handle_decision_v3
    hit = entry["detect"](DECIDE_ASK, dict(D._cfg()))
    assert hit is not None and hit.get("trigger") == "manual"
    # zero behavior change: registry output == direct detect_v3 output
    assert hit == D.detect_v3(DECIDE_ASK, None,
                              cfg=dict(D._cfg()))


def test_classify_signal_parity():
    name, hit = R.classify_signal(DECIDE_ASK, dict(D._cfg()))
    assert name == "decision"
    assert hit is not None and hit.get("trigger") == "manual"


def test_type_identity_constant():
    assert D.REFLEX_TYPE == "decision"


def test_banner_label_parity_and_reflex_lane():
    """Same banner label for the legacy lane id AND the new 'reflex' id —
    legacy string recognized, label unchanged."""
    b_legacy = DB.format_banner(lane="decision", trigger="manual",
                                model="jev", endpoint="", tokens_in=1,
                                tokens_out=1, est_cost=0.0, latency_s=0.0,
                                retries=0, task_id="", session_id="s")
    b_new = DB.format_banner(lane="reflex", trigger="manual",
                             model="jev", endpoint="", tokens_in=1,
                             tokens_out=1, est_cost=0.0, latency_s=0.0,
                             retries=0, task_id="", session_id="s")
    assert "impulse (decision)" in b_legacy
    assert "impulse (decision)" in b_new


# --- 2. silent default for unknown types --------------------------------------

def test_unknown_type_silent():
    """Unregistered shapes default to SILENT: lookup None, dispatch False,
    classify (None, None) — no fire, no log spam."""
    assert R.registry_lookup("salience") is None
    assert R.registry_lookup("dread") is None
    assert R.registry_lookup("") is None
    assert R.registry_lookup(None) is None
    assert R.dispatch("salience", session_id="s") is False
    assert R.classify_signal("anything at all", {}) == (None, None)


def test_unknown_type_never_raises():
    assert R.dispatch(None) is False
    assert R.classify_signal(None, None) == (None, None)
    assert R.normalize_reflex_class(None) == ""


# --- 3. ledger backcompat: both class values ----------------------------------

def test_reflex_class_normalizer():
    assert R.normalize_reflex_class("reflex:decision") == "decision"
    assert R.normalize_reflex_class("decision") == "decision"
    assert R.normalize_reflex_class("deploy") == "deploy"
    assert R.normalize_reflex_class("") == ""


def test_priors_accept_both_class_values(tmp_path):
    """A row written with the NEW 'reflex:decision' spelling is found by the
    priors lookup keyed on the LEGACY base class (and vice versa)."""
    db = str(tmp_path / "t.db")
    # new-spelling row
    r1 = D.ledger_write({"session_id": "s", "task_id": "t",
                         "trigger": "pre", "fork_class": "reflex:decision",
                         "choice": "opt-1", "confidence": 0.9,
                         "outcome": "applied"}, db_path=db)
    assert r1 is not None
    # priors lookup keyed the legacy way must find the reflex-prefixed row
    priors = D._ledger_priors("decision", 0, {"enabled": True}, db_path=db)
    assert isinstance(priors, str)
    assert "opt-1 chosen 1x" in priors


# --- 4. config alias fallback ---------------------------------------------------

def test_config_alias_reflex_wins(monkeypatch):
    monkeypatch.setattr(_cac, "sub_block",
                        lambda name: {"enabled": True, "level": 3}
                        if name == "reflex" else {"enabled": True,
                                                  "level": 1})
    assert D._cfg()["level"] == 3  # reflex block wins


def test_config_alias_decision_fallback(monkeypatch):
    monkeypatch.setattr(_cac, "sub_block",
                        lambda name: {"enabled": True, "level": 1}
                        if name == "decision" else {})
    assert D._cfg()["level"] == 1  # decision fallback when no reflex block


def test_config_alias_empty_blocks_fall_through(monkeypatch):
    monkeypatch.setattr(_cac, "sub_block", lambda name: {})
    cfg = D._cfg()
    assert cfg["level"] == D.DEFAULTS["level"]  # defaults, fail-open


def test_router_core_decision_cfg_alias(monkeypatch):
    monkeypatch.setattr(_dbgmod, "_banner_section", lambda: {
        "reflex": {"enabled": True, "level": 3},
        "decision": {"enabled": True, "level": 1}})
    assert RC._decision_cfg()["level"] == 3
    monkeypatch.setattr(_dbgmod, "_banner_section", lambda: {
        "decision": {"enabled": True, "level": 1}})
    assert RC._decision_cfg()["level"] == 1
    monkeypatch.setattr(_dbgmod, "_banner_section", lambda: {})
    assert RC._decision_cfg() == {}
