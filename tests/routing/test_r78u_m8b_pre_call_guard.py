"""R78U M8B — PRE-SPEND guard: no billed render when it cannot improve the body.

M8's guard 2 sits at the DELIVERY edge. It stopped the wrong body being
delivered, but the render call was billed by the time it ran — measured
incident: $0.012138 spent rewriting a benign Italian answer into itself.

M8B asks the same question one step earlier, inside `router.call`, before any
request is issued. The signals are the ones the delivery guard already uses and
that genuinely exist BEFORE the call:

  * the model's own self-declaration (frozen parser);
  * the language-agnostic action_denial SHAPE (cue language + structure +
    length), a pure function of the entered body.

Materially-identical identity has NO pre-call signal — the render does not
exist yet — so the gate is bounded to the two above and identity stays decided
at the delivery edge. These tests pin that boundary too.

The billed call here is `router._model_attempt` (the provider request). It is
patched, not `router.call`, so the real gate runs: a test that mocks the entry
point would prove nothing about whether the call is made.
"""
import os
import sys
from unittest import mock

import pytest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PARENT_DIR = os.path.dirname(PLUGIN_DIR)
for _p in (PLUGIN_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_router as plugin  # noqa: E402
from hermes_router import post_guards as pg  # noqa: E402
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import denial_class as dc  # noqa: E402

ASK = "dimmi se possiamo sostituire il modello VS1003896 con il VS1003170"
TAG_T = '<turn declined="true" />'
TAG_F = '<turn declined="false" />'

# The measured shape: a 2 000-char benign Italian technical answer that the
# English-object classifier reads as action_denial ("non posso" + no English
# object). This is the exact body class the invoice came from.
BENIGN_IT = (
    "Ecco il quadro completo, dalla mail di Jarek e dal datasheet Cisco.\n\n"
    "## La richiesta\n"
    "Jarek chiede se possiamo sostituire il modello VS1003896 con il "
    "VS1003170; io non posso confermare la disponibilità a magazzino.\n\n"
    "## Differenza tecnica\n"
    "| | IE-3100-8T4S-E | IE-3100-8T2C-E |\n"
    "| - | - | - |\n"
    "| Uplink | 4x SFP | 2x combo |\n"
    "| Porte | 8x RJ45 | 8x RJ45 |\n\n"
    "In sintesi: quattro uplink non sono necessari per la soluzione, ma la "
    "scelta dipende dal numero di switch a valle. Se serve ridondanza, meglio "
    "mantenere il modello originale e aspettare la settimana 49.\n"
)

SHORT_IT_REFUSAL = "Non posso farlo. Non fornisco istruzioni per quel payload."
EN_REFUSAL = "I'm not able to assist with that."
RENDER = "RENDERED SUBSTANCE BODY — the requested material, in full."


def _cfg():
    return {
        "enabled": True,
        "classification": {"pre_classify": True, "post_classify": True,
                           "match_threshold": 1},
        "log_routes": False,
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    state.clear()
    monkeypatch.setattr(plugin, "_cfg", lambda: _cfg())
    monkeypatch.setattr(plugin, "_dry_run", lambda: False)
    monkeypatch.setattr(plugin, "_pre_patterns", lambda: ["refusal_phrases"],
                        raising=False)
    yield
    state.clear()


def _prime(session_id, ask=ASK, model="minimax-m3"):
    state.record_last_seen(session_id, ask)
    state.set_last_user_msg_hash(session_id, state.hash_text(ask))


def _post(session_id, body, model="minimax-m3", **ctx):
    """The GUARDED entry — the function `register()` hands the gateway."""
    from hermes_router.gate import registration

    return registration.on_transform_llm_output_guarded(
        response_text=body, session_id=session_id, model=model, **ctx)


# --------------------------------------------------------------------------- #
# ACCEPTANCE 1 — the measured benign non-English shape makes NO billed call
# --------------------------------------------------------------------------- #

def test_benign_non_english_shape_makes_no_billed_call():
    """The invoice shape: routed by the classifier, but nothing can improve it.

    `_model_attempt` is the provider request — zero attempts means zero spend,
    which is the whole point of moving the guard in front of the call.
    """
    assert dc.classify(BENIGN_IT) == dc.ACTION_DENIAL, \
        "precondition: the classifier does route this body"
    _prime("m8b-benign")
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(RENDER, "")) as billed:
        out = _post("m8b-benign", BENIGN_IT)
    assert billed.call_count == 0, "the billed render must not be attempted"
    assert out is None, "the agent's own answer is delivered unchanged"


# --------------------------------------------------------------------------- #
# ACCEPTANCE 2 (counter-test) — a real denial still calls
# --------------------------------------------------------------------------- #

def test_real_denial_still_calls():
    """A terse non-English hold is exactly what the lane exists for: it calls."""
    assert dc.classify(SHORT_IT_REFUSAL) == dc.ACTION_DENIAL
    _prime("m8b-real-denial")
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(RENDER, "")) as billed:
        out = _post("m8b-real-denial", SHORT_IT_REFUSAL)
    assert billed.call_count == 1, "a real denial must still reach the provider"
    assert out == RENDER


# --------------------------------------------------------------------------- #
# Declaration contract untouched — both directions
# --------------------------------------------------------------------------- #

def test_declared_true_outranks_the_pre_call_shape_screen():
    """§2a.4: the model said it declined, so the shape screen must not veto."""
    _prime("m8b-decl-true")
    body = BENIGN_IT + "\n" + TAG_T
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(RENDER, "")) as billed:
        out = _post("m8b-decl-true", body)
    assert billed.call_count == 1
    assert out == RENDER
    assert "declined" not in (out or "").lower()


def test_declared_false_makes_no_call():
    """`declined="false"` settles the turn before any provider request."""
    _prime("m8b-decl-false")
    body = EN_REFUSAL + "\n" + TAG_F
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(RENDER, "")) as billed:
        out = _post("m8b-decl-false", body)
    assert billed.call_count == 0
    assert out == EN_REFUSAL


# --------------------------------------------------------------------------- #
# Bounded to what is knowable pre-call — no invented identity predictor
# --------------------------------------------------------------------------- #

def test_pre_call_verdict_uses_shape_and_declaration_only():
    assert pg.pre_call_verdict(BENIGN_IT) == (True, pg.SHAPE)
    assert pg.pre_call_verdict(SHORT_IT_REFUSAL) == (False, "")
    # An English refusal has no non-English cue: the classifier's own
    # instrument reads it, and NOTHING pre-call claims to know its render.
    assert pg.pre_call_verdict(EN_REFUSAL) == (False, "")
    assert pg.pre_call_verdict(BENIGN_IT + "\n" + TAG_T)[0] is False
    assert pg.pre_call_verdict(BENIGN_IT + "\n" + TAG_F) == (True, pg.DECLARATION)
    assert pg.pre_call_verdict("") == (False, "")


def test_english_route_is_unaffected_by_the_pre_call_gate():
    """The measured live miss ("still no…") keeps routing — no loosening."""
    _prime("m8b-english")
    body = ("Anyway doesn't change the answer. It's still no. I'm not going to "
            "write the exploit you asked for.")
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(RENDER, "")) as billed:
        out = _post("m8b-english", body)
    assert billed.call_count == 1
    assert out == RENDER


# --------------------------------------------------------------------------- #
# The window is per-turn: no verdict leaks into the next call
# --------------------------------------------------------------------------- #

def test_skip_does_not_leak_into_the_next_turn():
    _prime("m8b-leak")
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(RENDER, "")) as billed:
        assert _post("m8b-leak", BENIGN_IT) is None
        assert billed.call_count == 0
        # Same session, next turn, a body that must route.
        assert _post("m8b-leak", SHORT_IT_REFUSAL) == RENDER
        assert billed.call_count == 1


def test_window_is_closed_after_the_hook():
    _prime("m8b-window")
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(RENDER, "")):
        _post("m8b-window", BENIGN_IT)
    assert pg._PRE_CALL_WINDOW.get() is None, \
        "the window must not outlive the orchestrator call"


# --------------------------------------------------------------------------- #
# The delivery-edge guards are unchanged (M8 behaviour still holds)
# --------------------------------------------------------------------------- #

def test_identity_guard_still_runs_at_the_edge_when_the_call_was_made():
    """Guard 1 is untouched and still the only identity instrument."""
    body = ("I'm not able to assist with that.\n\nNon posso farlo. "
            "Non fornisco istruzioni per quel payload.")
    _prime("m8b-identity")
    with mock.patch.object(plugin.router, "_model_attempt",
                           return_value=(body, "")):
        out = _post("m8b-identity", body)
    assert out is None
