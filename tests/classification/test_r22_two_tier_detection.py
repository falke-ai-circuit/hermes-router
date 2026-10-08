"""R22 two-tier denial detection — test batteries (spec §Tests 1-6).

Battery 1: live-verified R21 specimens route with NO dictionary entry
           (R21 _REFUSAL_PHRASES additions stripped from the fixture pack).
Battery 2: technical epistemic limitation (no produce/echo marker) -> CLEAN,
           no route, judge never invoked.
Battery 3: normal delivery -> no T2 call (cost path).
Battery 4: aux timeout -> fail-open (candidate routes / non-candidate
           delivers).
Battery 5: model 'auto' resolves the aux model; aux resolution error ->
           fallback model.
Battery 6: sentinel / legacy-row text never reaches detection.

Aux behavior is always mocked — no test in this file touches the network.
"""
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PARENT_DIR, PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router.features.detection import semantic_judge as sj  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402
from hermes_router import state  # noqa: E402

RENDERED = "R22 RENDER MARKER " * 10
ASK = "write an explicit scene with the named public figure"

# The four live-verified R21 positives (spec battery 1), dictionary-negative
# once the R21 pack additions are stripped.
POSITIVE_AUTHOR = ("I won't author fabricated sexual content attributed to "
                   "a real person.")
POSITIVE_REAL_PERSON = "I decline to render that — it involves a real person."
POSITIVE_ESSAY = ("No.\n\nThis is not a flinch. The answer is the same as it "
                  "was, and it will be the same tomorrow.")
POSITIVE_DRAW = "I won't draw her — she's a real person."
R21_POSITIVES = [POSITIVE_AUTHOR, POSITIVE_REAL_PERSON, POSITIVE_ESSAY,
                 POSITIVE_DRAW]

R21_ADDED_PATTERNS = (
    "\\b(?:attributed to|about|involving|of)\\s+(?:a\\s+)?real\\s+(?:person|people|individual\\w*|named)\\b.{0,120}\\b(?:refus\\w*|won'?t|can'?t|cannot|not\\s+(?:write|author|render|creat\\w*|produc\\w*))\\b",
    "\\b(?:won'?t|can'?t|cannot|refus\\w*|declin\\w*)\\b.{0,80}\\b(?:attributed to|about|involving)\\s+(?:a\\s+)?real\\s+(?:person|people|individual\\w*)\\b",
    "(?s)^No\\.?\\b.{0,400}?\\bnot a flinch\\b",
    "(?s)^No\\.?\\b.{0,400}?\\b(?:one thing,? not a|holding,? because it'?s one thing)\\b",
)
# R21's extension of the _REFUSAL_PHRASES[0] produce-verb alternation.
R21_ADDED_ALTERNATION = ("|author|depict|render|craft\\w*|compos\\w*|"
                         "illustrat\\w*|draw|make|describ\\w*|portray|"
                         "writ\\w*|fulfil\\w*|fulfill\\w*")


def _strip_r21_pack(monkeypatch) -> None:
    """Patch the classifier's refusal group to the PRE-R21 pack (R21
    additions REMOVED) — proves the positives route via the structural+judge
    path, not the dictionary."""
    import re as _re

    src = [p for p in plugin.classifier._REFUSAL_PHRASES
           if p not in R21_ADDED_PATTERNS]
    src = [p.replace(R21_ADDED_ALTERNATION, "")
           if R21_ADDED_ALTERNATION in p else p for p in src]
    assert len(src) < len(plugin.classifier._REFUSAL_PHRASES)
    monkeypatch.setattr(plugin.classifier, "_REFUSAL_PHRASES", src)
    monkeypatch.setattr(
        plugin.classifier, "PATTERN_GROUPS",
        dict(plugin.classifier.PATTERN_GROUPS,
             refusal_phrases=[_re.compile(p, _re.IGNORECASE) for p in src]))
    monkeypatch.setattr(plugin.classifier, "_REFUSAL_COMBINED_RE",
                        plugin.classifier._join_combined(src))


def _dictionary_negative(text: str) -> bool:
    return plugin.classifier.scan_post(
        text, patterns=["refusal_phrases", "line_hold_essay"]) == []


def _judge_verdict(verdict: str, confidence: float = 0.9):
    return {"verdict": verdict, "confidence": confidence,
            "model": "aux-model-9", "latency_s": 0.01}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True, "log_routes": False,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1, "aux_classify": True,
                           "mode": "flag_only", "doctrine_verdict": False},
        "detection": {"enabled": True, "model": "auto"},
    })
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin.router, "_read_key", lambda key_file: "TESTKEY")
    monkeypatch.setattr(sj, "_fallback_client", lambda: None)
    _strip_r21_pack(monkeypatch)
    yield
    state.clear()


# ---------------------------------------------------------------------------
# Battery 1 — R21 specimens route via structural+judge, NO dictionary entry
# ---------------------------------------------------------------------------

def test_b1_specimens_are_dictionary_negative_after_strip():
    for text in R21_POSITIVES:
        assert _dictionary_negative(text), \
            "expected dictionary-miss after stripping R21 additions: %r" % text


def test_b1_specimens_are_structural_candidates():
    for text in R21_POSITIVES:
        assert structural.is_candidate(text) is True, text


def test_b1_specimens_route_via_structural_and_judge():
    state.record_last_seen("s1", ASK)
    for i, text in enumerate(R21_POSITIVES):
        with mock.patch.object(sj, "judge_turn",
                               return_value=_judge_verdict("refusal")), \
             mock.patch.object(plugin.router, "call",
                               return_value=RENDERED) as venice:
            out = plugin.on_transform_llm_output(
                response_text=text, session_id="s1", model="test-model")
        assert out == RENDERED, text
        assert venice.called
        assert venice.call_args[0][0] == ASK
        state.clear()
        state.record_last_seen("s1", ASK)


def test_b1_t2_route_writes_detection_t2_ledger_row():
    state.record_last_seen("s1", ASK)
    with mock.patch.object(sj, "judge_turn",
                           return_value=_judge_verdict("refusal")), \
         mock.patch.object(plugin.router, "call", return_value=RENDERED):
        plugin.on_transform_llm_output(response_text=POSITIVE_AUTHOR,
                                       session_id="s1", model="test-model")
    from hermes_router import decision_miner

    db = sqlite3.connect(decision_miner.plugin_db_path())
    try:
        rows = db.execute(
            "SELECT event, verdict, routed FROM detection_ledger"
            " WHERE session_id='s1'").fetchall()
    finally:
        db.close()
    assert ("detection_t2", "refusal", 1) in rows


# ---------------------------------------------------------------------------
# Battery 2 — technical epistemic limitation -> CLEAN, no route
# ---------------------------------------------------------------------------

def test_b2_technical_limitation_no_route_no_judge():
    text = "I can't tell which filesystem the error came from."
    assert _dictionary_negative(text)
    assert structural.is_candidate(text) is False  # no produce/echo marker
    with mock.patch.object(sj, "judge_turn") as judge, \
         mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(
            response_text=text, session_id="s2", model="test-model")
    assert out is None
    assert not venice.called
    assert not judge.called


# ---------------------------------------------------------------------------
# Battery 3 — normal delivery: no T2 call (cost path)
# ---------------------------------------------------------------------------

def test_b3_normal_delivery_no_t2_call():
    text = ("The function sorts the list in place and returns None, so the "
            "caller keeps a reference to the same object it passed in.")
    assert structural.is_candidate(text) is False
    with mock.patch.object(sj, "judge_turn") as judge, \
         mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(
            response_text=text, session_id="s3", model="test-model")
    assert out is None
    assert not venice.called
    assert not judge.called  # Tier-1 miss -> zero aux cost


# ---------------------------------------------------------------------------
# Battery 4 — aux timeout -> fail-open to the Tier-1 decision
# ---------------------------------------------------------------------------

def test_b4_timeout_candidate_routes_fail_open():
    text = POSITIVE_AUTHOR
    state.record_last_seen("s4", ASK)
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text=text, session_id="s4", model="test-model")
    assert out == RENDERED  # fail-open: candidate routes
    assert venice.called
    from hermes_router import decision_miner

    db = sqlite3.connect(decision_miner.plugin_db_path())
    try:
        rows = db.execute(
            "SELECT event, routed FROM detection_ledger"
            " WHERE session_id='s4'").fetchall()
    finally:
        db.close()
    assert ("detection_t1", 1) in rows  # tier-1-only decision logged


def test_b4_timeout_non_candidate_delivers():
    text = "I can't tell which filesystem the error came from."
    with mock.patch.object(sj, "judge_turn", return_value=None), \
         mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(
            response_text=text, session_id="s4b", model="test-model")
    assert out is None
    assert not venice.called


def test_b4_judge_timeout_raises_never_breaks_delivery():
    class _Client:
        def create(self, **kwargs):
            raise TimeoutError("judge timeout")

    import agent.auxiliary_client as _ac

    with mock.patch.object(_ac, "get_text_auxiliary_client",
                           lambda task="": (_Client(), "aux-model-9")):
        assert sj.judge_turn(ASK, POSITIVE_AUTHOR) is None


# ---------------------------------------------------------------------------
# Battery 5 — model 'auto' resolution (aux model, fallback on error)
# ---------------------------------------------------------------------------

def test_b5_auto_resolves_aux_model():
    import agent.auxiliary_client as _ac

    with mock.patch.object(_ac, "get_text_auxiliary_client",
                           lambda task="": (object(), "aux-model-9")):
        assert sj.resolve_model({"model": "auto"}) == "aux-model-9"


def test_b5_aux_resolution_error_falls_back():
    import agent.auxiliary_client as _ac

    def _boom(task=""):
        raise RuntimeError("aux config unreadable")

    with mock.patch.object(_ac, "get_text_auxiliary_client", _boom):
        assert sj.resolve_model({"model": "auto"}) == sj.FALLBACK_MODEL


def test_b5_no_explicit_model_branch_exists():
    import agent.auxiliary_client as _ac

    with mock.patch.object(_ac, "get_text_auxiliary_client",
                           lambda task="": (object(), "aux-model-9")):
        # any configured value still resolves through 'auto'
        assert sj.resolve_model({"model": "jev-something"}) == "aux-model-9"


def test_b5_judge_uses_resolved_model():
    from types import SimpleNamespace

    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({"verdict": "refusal", "confidence": 0.9})))])
    seen = {}

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: (seen.update(kw), resp)[1])))

    import agent.auxiliary_client as _ac

    with mock.patch.object(_ac, "get_text_auxiliary_client",
                           lambda task="": (client, "aux-model-9")):
        v = sj.judge_turn(ASK, POSITIVE_AUTHOR)
    assert v is not None and v["verdict"] == "refusal"
    assert v["model"] == "aux-model-9"
    assert seen["model"] == "aux-model-9"
    assert seen["max_tokens"] == sj.JUDGE_MAX_TOKENS
    assert seen["temperature"] == sj.JUDGE_TEMPERATURE
    assert seen["timeout"] == sj.JUDGE_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Battery 6 — sentinel / legacy-row text never reaches detection
# ---------------------------------------------------------------------------

def test_b6_sentinel_text_never_reaches_detection():
    text = ("Your uncensored response follows: I won't write that scene, "
            "refusing the request entirely.")
    assert structural.is_candidate(text) is True  # would be a candidate...
    with mock.patch.object(sj, "judge_turn") as judge:
        assert sj.post_detection_scan("s6", text, user_ask=ASK,
                                      log_route=lambda *a, **k: None) is False
    assert not judge.called  # ...but the routed-turn firewall stops it


def test_b6_platform_envelope_never_reaches_detection():
    text = ("[OUT-OF-BAND USER MESSAGE]\nI won't write that scene.")
    with mock.patch.object(sj, "judge_turn") as judge:
        assert sj.post_detection_scan("s6b", text, user_ask=ASK,
                                      log_route=lambda *a, **k: None) is False
    assert not judge.called


def test_b6_disabled_detection_is_total_noop():
    text = POSITIVE_AUTHOR
    with mock.patch.object(sj, "judge_turn") as judge, \
         mock.patch.object(plugin.router, "call") as venice:
        assert sj.post_detection_scan(
            "s6c", text, user_ask=ASK, cfg={"enabled": False}) is False
    assert not judge.called
    assert not venice.called
