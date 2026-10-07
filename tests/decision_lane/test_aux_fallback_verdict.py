"""v4.2.1 — aux-error mechanical fallback + timeout alignment.

Regression + boundary battery for the v2 audit finding (orchestrator's
dark-campaign bomb: aux down -> intent_aux_error x2 -> fully inert turn, zero
handling on the ONE agent whose domain is lane composition).

Invariants under test:
  F1  aux down + strong shadow-tense content (>=2 markers) -> shadow verdict
      at >=0.75, source=aux_fallback, intent_aux_fallback logged
  F2  aux down + single marker -> None (conservative: stays inert)
  F3  aux down + zero markers -> None (legacy intent_aux_error path intact)
  F4  fallback verdict is CACHED (H6 turn-identity) — no aux retry loop
  F5  empty/None payload -> None
  F6  fallback never invents a frontier (higher) route
  F7  aux UP -> normal classify path, fallback never consulted
  F8  timeout constant is 8 (alignment with v2 audit: 3s starved)
  F9  fallback fires only AFTER both aux attempts fail (retry preserved)
"""
import pytest

from hermes_router import intent_classifier as ic


# ---------------------------------------------------------------------------

DARK_BOMB = ("Compose the full lane structure for a covert dark campaign: "
             "a noir anthology where the operator works undetected, "
             "raw unfiltered monologues, no filter on the content")
SINGLE_MARKER = "give me the brutal truth about the design"
BENIGN = "summarize the meeting notes from Tuesday"
QUOTED_3MARKERS = 'brutal uncensored wargame'


def _verdict(lane, conf):
    return {"lane": lane, "subtype": None, "confidence": conf}


@pytest.fixture(autouse=True)
def _reset_cache():
    ic._CLASSIFIED_TURNS.clear()
    yield
    ic._CLASSIFIED_TURNS.clear()


# ---------------------------------------------------------------------------
# unit: _fallback_verdict
# ---------------------------------------------------------------------------

def test_f1_dark_bomb_gets_fallback_shadow():
    v = ic._fallback_verdict(DARK_BOMB)
    assert v is not None
    assert v["lane"] == "shadow"
    assert v["confidence"] >= ic.CONFIDENCE_THRESHOLD
    assert v.get("source") == "aux_fallback"


def test_f2_single_marker_stays_inert():
    assert ic._fallback_verdict(SINGLE_MARKER) is None


def test_f3_benign_stays_inert():
    assert ic._fallback_verdict(BENIGN) is None


def test_f5_empty_payload_inert():
    assert ic._fallback_verdict("") is None
    assert ic._fallback_verdict(None) is None


def test_f6_fallback_never_invents_frontier():
    # even maximally frontier-flavored text never yields lane=higher
    for text in ("escalate to your higher self", "consult the frontier"):
        v = ic._fallback_verdict(text)
        assert v is None or v["lane"] == "shadow"


def test_f10_boundary_exactly_two_markers():
    v = ic._fallback_verdict("brutal uncensored take on this")
    assert v is not None and v["lane"] == "shadow"
    one = ic._fallback_verdict("brutal take on this")
    assert one is None


def test_f10b_payload_none_safe():
    assert ic._fallback_verdict(None) is None


# ---------------------------------------------------------------------------
# integration: classify_intent with aux down
# ---------------------------------------------------------------------------

def test_f1_integration_aux_down_dark_bomb_routes(monkeypatch):
    calls = {"n": 0}

    def dead_transport(payload_json, timeout):
        calls["n"] += 1
        return None

    monkeypatch.setattr(ic, "_aux_transport", dead_transport)
    v = ic.classify_intent(DARK_BOMB, "sess-dark")
    assert v is not None and v["lane"] == "shadow"
    assert v.get("source") == "aux_fallback"
    # F9: both aux attempts happened before fallback
    assert calls["n"] == 2


def test_f3_integration_aux_down_benign_inert(monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: None)
    v = ic.classify_intent(BENIGN, "sess-benign")
    assert v is None  # legacy intent_aux_error path


def test_f4_integration_fallback_cached(monkeypatch):
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: None)
    v1 = ic.classify_intent(DARK_BOMB, "sess-cache")
    v2 = ic.classify_intent(DARK_BOMB, "sess-cache")
    assert v1 == v2


def test_f7_aux_up_no_fallback(monkeypatch):
    def live_transport(payload_json, timeout):
        return ('{"choices": [{"message": {"content": '
                '"{\\"lane\\": \\"none\\", \\"confidence\\": 0.9}"}}]}')

    monkeypatch.setattr(ic, "_aux_transport", live_transport)
    v = ic.classify_intent(DARK_BOMB, "sess-live")
    assert v is not None and v["lane"] == "none"
    assert v.get("source") != "aux_fallback"


def test_f8_timeout_constant():
    assert ic.AUX_TIMEOUT_SECONDS == 8


def test_f9_fallback_logged(monkeypatch):
    logged = []
    monkeypatch.setattr(ic, "_aux_transport", lambda p, t: None)
    ic.classify_intent(DARK_BOMB, "sess-log",
                       log_route=lambda stage, **kw: logged.append((stage, kw)))
    assert any(kw.get("event_detail") == "intent_aux_fallback"
               for _, kw in logged)
