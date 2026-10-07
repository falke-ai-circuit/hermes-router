"""Flinch-reason gate (Goran ruling 2026-09-08): only CONTENT flinches route
to the shadow chain; TECHNICAL refusals pass through. Unknown/aux-failure
fail-opens to route."""
import pytest

from hermes_router import flinch_reason as fr

# conftest autouse-patches fr.classify_flinch_reason per-test; the classifier
# unit tests bind the unpatched alias captured at module import time.
_REAL = fr._original_classify


def test_parse_reason_basic():
    assert fr.parse_reason("content") == "content"
    assert fr.parse_reason("The answer is technical.") == "technical"
    assert fr.parse_reason("reasoning first... then: CONTENT") == "content"
    assert fr.parse_reason("garbage no enum") is None
    assert fr.parse_reason("") is None
    assert fr.parse_reason(None) is None


def test_parse_reason_think_blocks():
    raw = "Let me think... the refusal talks about missing file access. Answer: technical"
    assert fr.parse_reason(raw) == "technical"
    raw2 = "reasoning... content"
    assert fr.parse_reason(raw2) == "content"


def test_classify_aux_failure_fails_open(monkeypatch):
    import hermes_router.semantic_classifier as sc

    monkeypatch.setattr(sc, "aux_raw_call", lambda *a, **k: None)
    assert _REAL("ask", "refusal") is None  # caller fail-opens to route


def test_classify_content_and_technical(monkeypatch):
    import hermes_router.semantic_classifier as sc

    monkeypatch.setattr(sc, "aux_raw_call", lambda *a, **k: "content")
    assert _REAL("ask", "refusal") == "content"
    monkeypatch.setattr(sc, "aux_raw_call", lambda *a, **k: "technical")
    assert _REAL("ask", "refusal") == "technical"


def test_knob_default_on():
    from hermes_router import _flinch_reason_gate

    assert _flinch_reason_gate() is True  # ruling is the doctrine