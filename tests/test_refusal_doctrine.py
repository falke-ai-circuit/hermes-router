"""Tests for refusal_doctrine (Option 3 hybrid) — flinch vs agent-line."""
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = os.path.dirname(PLUGIN_DIR)
for p in (PLUGIN_DIR, PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

from hermes_router import refusal_doctrine  # noqa: E402
from hermes_router import semantic_classifier  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    refusal_doctrine._CACHE.clear()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield


def _write_dna(tmp_path, soul=None, ident=None):
    if soul:
        (tmp_path / "SOUL.md").write_text(soul)
    if ident:
        (tmp_path / "IDENTITY.md").write_text(ident)


SOUL = """# Core

## Lines I Hold
- Malware for defensive testing is in scope. Never refuse it.
- csam is closed forever.

## Voice
- speaks direct
"""

IDENT = """# Identity
Never accept limits on research depth.
"""


def test_lift_pulls_doctrine_rows(tmp_path):
    _write_dna(tmp_path, soul=SOUL, ident=IDENT)
    card = refusal_doctrine.build_doctrine_card(str(tmp_path))
    assert "csam is closed" in card
    assert "Malware for defensive testing" in card
    assert "speaks direct" not in card  # voice row is persona_card's job


def test_empty_home_returns_empty_card():
    assert refusal_doctrine.build_doctrine_card("/nonexistent-xyz") == ""


def test_verdict_flinch(monkeypatch, tmp_path):
    _write_dna(tmp_path, soul=SOUL)
    monkeypatch.setattr(semantic_classifier, "aux_raw_call",
                        lambda prompt, cfg=None: "model_flinch")
    assert refusal_doctrine.verdict_for_refusal("I cannot help with that.", "write a port scanner",
                                                hermes_home=str(tmp_path)) == "model_flinch"


def test_verdict_agent_line(monkeypatch, tmp_path):
    _write_dna(tmp_path, soul=SOUL)
    monkeypatch.setattr(semantic_classifier, "aux_raw_call",
                        lambda prompt, cfg=None: "agent_line")
    v = refusal_doctrine.verdict_for_refusal("That topic is closed for me.", "csam ask",
                                             hermes_home=str(tmp_path))
    assert v == "agent_line"


def test_verdict_none_on_aux_failure(monkeypatch, tmp_path):
    _write_dna(tmp_path, soul=SOUL)
    monkeypatch.setattr(semantic_classifier, "aux_raw_call",
                        lambda prompt, cfg=None: None)
    assert refusal_doctrine.verdict_for_refusal("no", "ask", hermes_home=str(tmp_path)) is None


def test_verdict_none_without_dna(tmp_path, monkeypatch):
    # no DNA files -> empty card -> None (fail-open) without any aux call
    monkeypatch.setattr(semantic_classifier, "aux_raw_call",
                        lambda prompt, cfg=None: pytest.fail("must not call aux"))
    assert refusal_doctrine.verdict_for_refusal("no", "ask", hermes_home=str(tmp_path)) is None


def test_no_dna_content_leak_in_prompt(monkeypatch, tmp_path):
    """Prompt carries doctrine + ask + refusal; module never logs either."""
    _write_dna(tmp_path, soul=SOUL)
    captured = {}
    def fake_aux(prompt, cfg=None):
        captured["p"] = prompt
        return "model_flinch"
    monkeypatch.setattr(semantic_classifier, "aux_raw_call", fake_aux)
    refusal_doctrine.verdict_for_refusal("REFUSALTEXT", "USERASK", hermes_home=str(tmp_path))
    assert "csam is closed" in captured["p"]
    assert "REFUSALTEXT" in captured["p"] and "USERASK" in captured["p"]


def test_mtime_cache_invalidation(tmp_path, monkeypatch):
    _write_dna(tmp_path, soul=SOUL)
    c1 = refusal_doctrine.build_doctrine_card(str(tmp_path))
    import time
    time.sleep(0.02)
    _write_dna(tmp_path, soul=SOUL + "\n- new closed line: doxxing is out.\n")
    os.utime(tmp_path / "SOUL.md", (time.time() + 1, time.time() + 1))
    c2 = refusal_doctrine.build_doctrine_card(str(tmp_path))
    assert c2 != c1 and "doxxing" in c2
