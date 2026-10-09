"""R25 can't-vs-won't denial gate — suppression battery (spec §5).

12-pair battery: genuine impossibilities (world/capacity) are SUPPRESSED —
delivered inline, never routed, with a detection_t2_suppress ledger row;
refusals / frame-holds / deflections route as today. The ambiguous edge
("I can't help with that", no object) defaults to ROUTE.

The judge is ALWAYS mocked — no test in this file touches the network.
Tier-1 (structural.py) is intentionally untouched: high-recall candidate
flagger, suppression happens at Tier-2 only. Fail-open is unchanged.
"""
import json
import sqlite3
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

PLUGIN_DIR = __file__.rsplit("/tests/", 1)[0]
import os as _os

PLUGIN_DIR = _os.path.abspath(PLUGIN_DIR)
PARENT_DIR = _os.path.dirname(PLUGIN_DIR)
for _p in (PARENT_DIR, PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router.features.detection import semantic_judge as sj  # noqa: E402
from hermes_router.features.detection import structural  # noqa: E402
from hermes_router import state  # noqa: E402

ASK = "do the thing the turn talks about"

# R21 dictionary additions (mirror of the R22 battery's strip list) so the
# battery proves the detection path, not the refusal dictionary.
R21_ADDED_PATTERNS = (
    "\\b(?:attributed to|about|involving|of)\\s+(?:a\\s+)?real\\s+(?:person|people|individual\\w*|named)\\b.{0,120}\\b(?:refus\\w*|won'?t|can'?t|cannot|not\\s+(?:write|author|render|creat\\w*|produc\\w*))\\b",
    "\\b(?:won'?t|can'?t|cannot|refus\\w*|declin\\w*)\\b.{0,80}\\b(?:attributed to|about|involving)\\s+(?:a\\s+)?real\\s+(?:person|people|individual\\w*)\\b",
    "(?s)^No\\.?\\b.{0,400}?\\bnot a flinch\\b",
    "(?s)^No\\.?\\b.{0,400}?\\b(?:one thing,? not a|holding,? because it'?s one thing)\\b",
)
R21_ADDED_ALTERNATION = ("|author|depict|render|craft\\w*|compos\\w*|"
                         "illustrat\\w*|draw|make|describ\\w*|portray|"
                         "writ\\w*|fulfil\\w*|fulfill\\w*")


def _strip_r21_pack(monkeypatch) -> None:
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


def _v(verdict, confidence=0.9):
    return {"verdict": verdict, "confidence": confidence,
            "model": "aux-model-9", "latency_s": 0.01}


# ---------------------------------------------------------------------------
# The 12-pair battery. Every text must be a Tier-1 candidate (the structural
# gate is unchanged, so the battery only exercises Tier-2 verdict handling).
# ---------------------------------------------------------------------------
ROUTE_PAIRS = [
    # (label, turn text, judge verdict it should produce)
    ("moral_refusal", "I won't write torture scenes.", "refusal"),
    ("frame_hold_essay",
     "No.\n\nThis is not a flinch. The answer is the same as it was.",
     "refusal"),
    ("deflection",
     "I won't write that scene — let me instead offer a summary of the "
     "topic.", "partial"),
    ("real_person_refusal",
     "I refuse to author fabricated content about a real person.",
     "refusal"),
    ("decline_render",
     "I decline to render that — it involves a real person.", "refusal"),
    ("ambiguous_no_object", "I can't help with that.", "refusal"),
]

SUPPRESS_PAIRS = [
    ("world_math", "I can't fit 10GB of data into 4GB of RAM — the math "
                   "forbids it.", "impossibility_world"),
    ("world_future", "I can't guarantee this will work.", "impossibility_world"),
    ("world_undelete", "I can't un-delete that file — there is no way once "
                       "it's been overwritten.", "impossibility_world"),
    ("world_logic", "I can't make 2+2 equal 5 — that math cannot exist.",
     "impossibility_world"),
    ("capacity_web", "I can't browse the web from this session — there is "
                     "no web tool here.", "impossibility_capacity"),
    ("capacity_file", "I can't read that file from your local disk — I have "
                      "no file tool here.", "impossibility_capacity"),
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: {
        "enabled": True, "log_routes": False,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1, "aux_classify": True,
                           "mode": "flag_only", "doctrine_verdict": False},
        "detection": {"enabled": True, "model": "auto"}})
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin.router, "_read_key", lambda key_file: "TESTKEY")
    monkeypatch.setattr(sj, "_fallback_client", lambda: None)
    _strip_r21_pack(monkeypatch)
    yield
    state.clear()


def _ledger_rows(session_id):
    from hermes_router import decision_miner

    db = sqlite3.connect(decision_miner.plugin_db_path())
    try:
        return db.execute(
            "SELECT event, verdict, confidence, routed FROM detection_ledger"
            " WHERE session_id=?", (session_id,)).fetchall()
    finally:
        db.close()


def test_all_24_battery_texts_are_tier1_candidates():
    for label, text, _ in ROUTE_PAIRS + SUPPRESS_PAIRS:
        assert structural.is_candidate(text) is True, label


# ---------------------------------------------------------------------------
# Judge prompt contract (§2) — unit-tested against the module constant
# ---------------------------------------------------------------------------

def test_judge_prompt_names_both_impossibility_verdicts():
    assert "IMPOSSIBILITY_WORLD" in sj._JUDGE_PROMPT
    assert "IMPOSSIBILITY_CAPACITY" in sj._JUDGE_PROMPT


def test_judge_prompt_contains_tie_break_rule():
    assert "EXISTS and is producible and the model declines = REFUSAL" \
        in sj._JUDGE_PROMPT
    assert "cannot exist or requires tools/data the model lacks" \
        in sj._JUDGE_PROMPT
    assert "if the object of the cannot is unknown, default to REFUSAL" \
        in sj._JUDGE_PROMPT


def test_judge_prompt_json_enum_is_closed():
    assert ("\"verdict\":\"refusal|impossibility_world|"
            "impossibility_capacity|partial|clean\"") in sj._JUDGE_PROMPT
    # old enum must be gone
    assert '"refusal|partial|clean"' not in sj._JUDGE_PROMPT


def test_parse_accepts_both_new_verdicts_and_rejects_invented():
    for name in ("impossibility_world", "impossibility_capacity"):
        parsed = sj.parse_verdict_json(
            json.dumps({"verdict": name, "confidence": 0.8}))
        assert parsed == {"verdict": name, "confidence": 0.8}
    assert sj.parse_verdict_json(
        '{"verdict":"impossible","confidence":0.9}') is None
    assert sj.parse_verdict_json(
        '{"verdict":"impossibility_worldextra","confidence":0.9}') is None


def test_route_decision_suppresses_impossibility():
    for name in ("impossibility_world", "impossibility_capacity"):
        assert sj.route_decision(_v(name)) is False
    assert sj.route_decision(_v("refusal")) is True
    assert sj.route_decision(None) is False  # fail-open stays caller's call


# ---------------------------------------------------------------------------
# The 12-pair battery through post_detection_scan (mocked judge)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,text,verdict", ROUTE_PAIRS,
                         ids=[p[0] for p in ROUTE_PAIRS])
def test_route_pairs_route(monkeypatch, label, text, verdict):
    monkeypatch.setattr(sj, "judge_turn", lambda *a, **k: _v(verdict))
    assert sj.post_detection_scan(
        "r25-" + label, text, user_ask=ASK,
        log_route=lambda *a, **k: None) is True
    rows = _ledger_rows("r25-" + label)
    assert ("detection_t2", verdict, 0.9, 1) in rows
    assert not any(r[0] == "detection_t2_suppress" for r in rows)


@pytest.mark.parametrize("label,text,verdict", SUPPRESS_PAIRS,
                         ids=[p[0] for p in SUPPRESS_PAIRS])
def test_suppress_pairs_deliver_inline_with_ledger_row(monkeypatch, label,
                                                       text, verdict):
    monkeypatch.setattr(sj, "judge_turn", lambda *a, **k: _v(verdict))
    assert sj.post_detection_scan(
        "r25-" + label, text, user_ask=ASK,
        log_route=lambda *a, **k: None) is False
    rows = _ledger_rows("r25-" + label)
    assert ("detection_t2_suppress", verdict, 0.9, 0) in rows
    assert not any(r[3] == 1 for r in rows), "suppressed verdict must not route"


@pytest.mark.parametrize("label,text,verdict",
                         [(p[0], p[1], p[2]) for p in SUPPRESS_PAIRS],
                         ids=[p[0] for p in SUPPRESS_PAIRS])
def test_suppress_pairs_never_reach_render(monkeypatch, label, text, verdict):
    monkeypatch.setattr(sj, "judge_turn", lambda *a, **k: _v(verdict))
    with mock.patch.object(plugin.router, "call") as venice:
        assert sj.post_detection_scan(
            "r25x-" + label, text, user_ask=ASK,
            log_route=lambda *a, **k: None) is False
    assert not venice.called


# ---------------------------------------------------------------------------
# End-to-end: suppressed turn delivers the ORIGINAL text, refusal routes
# ---------------------------------------------------------------------------

def _kill_dictionary(monkeypatch) -> None:
    """e2e-only: route via the detection path, not the refusal dictionary
    (the base pack matches bare 'i can't' regardless of R21 additions)."""
    import re as _re

    monkeypatch.setattr(plugin.classifier, "_REFUSAL_COMBINED_RE",
                        _re.compile(r"(?!)"))


RENDERED = "R25 RENDER MARKER " * 10


def test_e2e_impossibility_delivers_original_text(monkeypatch):
    _kill_dictionary(monkeypatch)
    text = ("I can't browse the web from this session — there is no web "
            "tool here.")
    state.record_last_seen("r25e2e", ASK)
    monkeypatch.setattr(
        sj, "judge_turn", lambda *a, **k: _v("impossibility_capacity"))
    with mock.patch.object(plugin.router, "call") as venice:
        out = plugin.on_transform_llm_output(
            response_text=text, session_id="r25e2e", model="test-model")
    assert out in (None, "")  # no rewrite: the inline text delivers unchanged
    assert not venice.called
    rows = _ledger_rows("r25e2e")
    assert ("detection_t2_suppress", "impossibility_capacity", 0.9, 0) in rows


def test_e2e_refusal_still_routes(monkeypatch):
    _kill_dictionary(monkeypatch)
    state.record_last_seen("r25e2e2", ASK)
    monkeypatch.setattr(sj, "judge_turn",
                        lambda *a, **k: _v("refusal"))
    with mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text="I won't write torture scenes.",
            session_id="r25e2e2", model="test-model")
    assert out == RENDERED
    assert venice.called


# ---------------------------------------------------------------------------
# Fail-open unchanged (§3): judge down -> candidate routes (never block)
# ---------------------------------------------------------------------------

def test_fail_open_judge_down_still_routes(monkeypatch):
    _kill_dictionary(monkeypatch)
    state.record_last_seen("r25fo", ASK)
    monkeypatch.setattr(sj, "judge_turn", lambda *a, **k: None)
    with mock.patch.object(plugin.router, "call",
                           return_value=RENDERED) as venice:
        out = plugin.on_transform_llm_output(
            response_text="I won't write torture scenes.",
            session_id="r25fo", model="test-model")
    assert out == RENDERED
    assert venice.called
    rows = _ledger_rows("r25fo")
    assert ("detection_t1", "candidate", None, 1) in rows


def test_tier1_unchanged_smoke(monkeypatch):
    # structural module untouched: its shapes still flag the same fixtures
    assert structural.is_candidate(
        "I won't author fabricated sexual content attributed to a real "
        "person.") is True
    assert structural.is_candidate(
        "I can't tell which filesystem the error came from.") is False
