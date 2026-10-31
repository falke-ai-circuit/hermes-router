"""R78U M8 — POST delivery-edge guards.

Measured defect (conductor, valmet session 20261006_190842_1dee2f70): a benign
Italian technical answer was classified `action_denial`, routed, and billed
($0.012138) for a 1589-char paraphrase of the 2011-char answer already going
out. Four more benign renders in the same session.

These tests feed real bodies through the POST path (`plugin.
on_transform_llm_output`) and pin the guards:

  * identity: a render that re-says the original never replaces it;
  * shape: a non-English decline cue must be SHAPED like a refusal, so a long
    structured benign answer is not rewritten;
  * declaration: the model's own tag outranks classifier-only inference.
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
from hermes_router import state  # noqa: E402
from hermes_router.features.detection import denial_class as dc  # noqa: E402

ASK = "dimmi se possiamo sostituire il modello VS1003896 con il VS1003170"
TAG_T = '<turn declined="true" />'
TAG_F = '<turn declined="false" />'

# The valmet shape: a benign Italian technical answer that the English-object
# classifier reads as action_denial because "non posso" names no English object.
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

# A paraphrase of BENIGN_IT: same argument, same opening, trimmed — exactly the
# identity rewrite the lane billed for.
BENIGN_IT_REWRITE = (
    "Ecco il quadro completo, dalla mail di Jarek e dal datasheet Cisco.\n\n"
    "## La richiesta\n"
    "Jarek chiede se possiamo sostituire il modello VS1003896 con il "
    "VS1003170; non posso confermare la disponibilità a magazzino.\n\n"
    "## Differenza tecnica\n"
    "| | IE-3100-8T4S-E | IE-3100-8T2C-E |\n"
    "| - | - | - |\n"
    "| Uplink | 4 porte SFP | 2 porte combo |\n"
    "| Porte | 8x RJ45 | 8x RJ45 |\n\n"
    "In breve: le quattro uplink non sono indispensabili, ma la scelta dipende "
    "da quanti switch stanno a valle. Per la ridondanza conviene tenere il "
    "modello originale e attendere la settimana 49.\n"
)

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
    return plugin.on_transform_llm_output(
        response_text=body, session_id=session_id, model=model, **ctx)


# --------------------------------------------------------------------------- #
# 1 — identity rewrite is never delivered
# --------------------------------------------------------------------------- #

def test_identity_rewrite_is_not_delivered():
    """The measured burn: benign Italian answer, routed, render re-says it."""
    assert dc.classify(BENIGN_IT) == dc.ACTION_DENIAL  # the defect's trigger
    _prime("m8-identity")
    with mock.patch.object(plugin.router, "call",
                           return_value=BENIGN_IT_REWRITE) as venice:
        out = _post("m8-identity", BENIGN_IT)
    assert venice.called          # the route itself is not what we test here
    assert out is None            # ... but the rewrite never replaces the body


def test_real_swap_still_delivers():
    """Guard 1 must not touch a render that actually replaces the body."""
    _prime("m8-real-swap")
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("m8-real-swap", EN_REFUSAL)
    assert out == RENDER


def test_banner_append_is_not_an_identity_rewrite():
    """`body + parked banner` is an append. Suppressing it would lose the
    banner (R15-5 delivery-edge contract)."""
    from hermes_router import post_guards as pg

    body = ("Riassunto tecnico della situazione attuale sull'ordine "
            "POR662797 e sui tempi di consegna previsti dal fornitore.")
    banner = ("\n\n· router · higher-self (frontier) | consult | model | "
              "tok 1/2 | $0.01 | row=296 ·")
    assert pg.is_materially_identical(body, body + banner) is False
