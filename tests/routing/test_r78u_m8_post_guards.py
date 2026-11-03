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
    """The GUARDED entry — the function `register()` hands the gateway.

    `plugin.on_transform_llm_output` stays the bare orchestrator (its body is
    introspected by the banner-seam pins), so the guards are exercised through
    the registered wrapper, which is the live path.
    """
    from hermes_router.gate import registration

    return registration.on_transform_llm_output_guarded(
        response_text=body, session_id=session_id, model=model, **ctx)


def _dec_post(monkeypatch, session_id, body, model="minimax-m3"):
    """The GUARDED entry + the decision row it wrote.

    M35-15: the delivered-bytes contract is now stated by the decision point
    (`core/delivery_decision.decide()`), so these pins read the case id from
    the `delivery_decision` event instead of inferring it from the body.
    """
    from hermes_router.gate import registration

    rows = []

    def _spy(seam, event_detail="", **kw):
        if event_detail == "delivery_decision":
            rows.append(kw)

    monkeypatch.setattr(plugin, "_log_route", _spy)
    out = registration.on_transform_llm_output_guarded(
        response_text=body, session_id=session_id, model=model)
    return out, (rows[-1] if rows else {})


# --------------------------------------------------------------------------- #
# 1 — identity rewrite is never delivered
# --------------------------------------------------------------------------- #

def test_identity_rewrite_is_not_delivered(monkeypatch):
    """The measured burn: benign Italian answer, routed, render re-says it.

    M35-15 (old case: the identity guard suppressed the render and the edge
    returned the kept body explicitly; new case: D4 COMPLIANT_DELIVER).
    Compliance outranks the rule that routed, so the decision names the
    ENTERED body and `strip_bytes == 0` — which is the passthrough contract
    (`None`), i.e. the host ships exactly the bytes it gave us. The property
    both eras must hold: the billed rewrite never reaches the wire.
    """
    assert dc.classify(BENIGN_IT) == dc.ACTION_DENIAL  # the defect's trigger
    _prime("m8-identity")
    with mock.patch.object(plugin.router, "call",
                           return_value=BENIGN_IT_REWRITE) as venice:
        out, row = _dec_post(monkeypatch, "m8-identity", BENIGN_IT)
    assert venice.called          # the route itself is not what we test here
    assert row.get("case_id") == "D4", row
    assert row.get("delivered_source") == "main", row
    assert row.get("strip_bytes") == 0, row
    assert out is None, out       # passthrough == the entered body, byte-exact
    assert BENIGN_IT_REWRITE not in (out or "")


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


# --------------------------------------------------------------------------- #
# 2 — action_denial is bounded by SHAPE, not English object vocabulary
# --------------------------------------------------------------------------- #

def test_benign_non_english_reply_is_not_rewritten(monkeypatch):
    """ACCEPTANCE: a benign non-English reply through the POST path, no swap.

    The classifier still calls this body action_denial (English object
    vocabulary cannot read "non posso" + no English object) — that is the
    defect. The decision refuses the swap because the body classifies as
    compliance: M35-15 (old case: the shape guard vetoed the render; new
    case: D4 COMPLIANT_DELIVER, `strip_bytes=0` -> passthrough).
    """
    assert dc.classify(BENIGN_IT) == dc.ACTION_DENIAL
    _prime("m8-benign-it")
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out, row = _dec_post(monkeypatch, "m8-benign-it", BENIGN_IT)
    assert venice.called, "precondition: the classifier did route this body"
    assert row.get("case_id") == "D4", row
    assert row.get("delivered_source") == "main", row
    assert out is None, "a benign non-English answer must never be swapped"
    assert RENDER not in (out or "")


def test_short_non_english_refusal_still_routes():
    """The gate must not mute a real hold in another language."""
    assert dc.classify(SHORT_IT_REFUSAL) == dc.ACTION_DENIAL
    _prime("m8-short-it")
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("m8-short-it", SHORT_IT_REFUSAL)
    assert out == RENDER


def test_english_classifier_verdict_is_untouched():
    """An English cue keeps the classifier's instrument: the measured live miss
    ("still no…") must keep routing even mid-body."""
    body = ("Anyway doesn't change the answer. It's still no. I'm not going to "
            "write the exploit you asked for.")
    assert dc.classify(body) == dc.ACTION_DENIAL
    _prime("m8-english")
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out = _post("m8-english", body)
    assert out == RENDER


def test_shape_gate_is_structural_only():
    from hermes_router import post_guards as pg

    long_it = BENIGN_IT
    assert pg.is_non_english_decline(long_it) is True
    assert pg.is_language_agnostic_action_denial(long_it) is False
    assert pg.is_language_agnostic_action_denial(SHORT_IT_REFUSAL) is True


# --------------------------------------------------------------------------- #
# 3 — the model's own self-declaration outranks classifier-only inference
# --------------------------------------------------------------------------- #

def test_declared_true_does_not_beat_a_compliant_body(monkeypatch):
    """A declared decline on a body that already complies keeps the body.

    M35-15 RENAMES this pin. Old case: the declaration was the trigger, so a
    `declined="true"` tag forced a replacement even over a compliant body
    (row 1 ROUTE -> RENDER). New case: D4 COMPLIANT_DELIVER — spec §2.1 and
    the B3 row: compliance outranks every rule, and the lane's render is
    inadmissible twice over. The decision still ships the ENTERED body, with
    the tag stripped as routing metadata (§2a.6) — `strip_bytes > 0` is why
    the bytes are explicit here rather than a passthrough.
    """
    _prime("m8-decl-true")
    body = BENIGN_IT + "\n" + TAG_T
    with mock.patch.object(plugin.router, "call", return_value=RENDER):
        out, row = _dec_post(monkeypatch, "m8-decl-true", body)
    assert row.get("case_id") == "D4", row
    assert row.get("delivered_source") == "main", row
    # Byte-exact apart from the tag LINE's own separator: the strip removes
    # "<turn declined=true />" and leaves the newline that preceded it.
    assert out.rstrip("\n") == BENIGN_IT.rstrip("\n"), out
    assert RENDER not in out
    assert "declined" not in out.lower()


def test_declared_false_no_longer_cancels_the_route(monkeypatch):
    """A `declined="false"` tag no longer cancels a classifier-driven route.

    M35-15 RENAMES this pin. Old case: the declaration cascade
    (`declaration_no_route`) short-circuited the route, so `venice` was never
    called. New case: M35-14 §7 item 7 deleted that cascade — the classifier
    decides again, and the DELIVERY decision settles what ships. The property
    the pin still owns is unchanged and is the one that matters: whatever the
    lane returned, the delivered bytes are the agent's own body with the tag
    stripped, never the classifier-driven render.
    """
    _prime("m8-decl-false")
    body = EN_REFUSAL + "\n" + TAG_F
    with mock.patch.object(plugin.router, "call", return_value=RENDER) as venice:
        out, row = _dec_post(monkeypatch, "m8-decl-false", body)
    # The refusal is non-compliant and the lane render is admissible, so the
    # decision is D5 NONCOMPLIANT_REPLACE and the RENDER is what ships — the
    # exact behaviour §7 item 7 restores by deleting the declaration cascade.
    assert row.get("case_id") == "D5", row
    assert row.get("delivered_source") == "lane", row
    assert out == RENDER, out
    assert "declined" not in (out or "").lower()


def test_undeclared_turn_is_the_only_one_the_shape_gate_decides():
    from hermes_router import post_guards as pg

    _, decl_long = pg.read_declaration(BENIGN_IT)
    _, decl_true = pg.read_declaration(BENIGN_IT + "\n" + TAG_T)
    assert decl_long == pg.UNKNOWN
    assert decl_true == pg.DECLARED_TRUE


