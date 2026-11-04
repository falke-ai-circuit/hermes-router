"""M42-02 — options carry probabilities: the PRODUCER must ask for them.

The parser and the ledger already handle a per-option distribution
(`_measured_probability_map`, `_option_objects`); what was missing is that
nothing ever ASKED for one, so every live row persisted `id`+`label` only and
the board the model sees carried labels with no odds (the mechanical weights
rode a separate `weighting` block instead of the options).

Pins:
  (a) the wire question requests `probabilities` keyed by option id, and the
      `<option id>` placeholder appears in BOTH clauses (choice + distribution)
      so a backend cannot satisfy one and drop the other;
  (b) the second half is a ROUND TRIP: a verdict that does answer with a
      distribution survives validate_verdict and lands on the persisted
      option objects. That half passes before the fix — it is here so the
      failing half is provably the producer, not the consumer;
  (c) the envelope's own option objects carry the mechanical probability the
      weighting block already computed (the board and the ledger agree).
"""
from hermes_router import decision

ASK = "Redis or memcached for the session cache? weigh the tradeoffs"
OPTS = ["redis", "memcached"]


def test_envelope_question_requests_per_option_probabilities():
    env = decision.build_envelope("m42-02", ASK, OPTS, "manual")
    assert env, "envelope must build for an explicit option pair"
    text = env["question"]["text"]
    # (a) the producer asks for the distribution at all
    assert '"probabilities"' in text
    # ... and the id placeholder now appears in the choice clause AND the
    # distribution clause — one placeholder means one clause.
    assert text.count("<option id>") >= 2


def test_a_returned_distribution_round_trips_to_the_persisted_board():
    env = decision.build_envelope("m42-02", ASK, OPTS, "manual")
    ids = [o["id"] for o in env["options"]]
    body = ('{"choice": "%s", "confidence": 0.7, "alternatives": ["%s"], '
            '"probabilities": {"%s": 0.7, "%s": 0.3}}'
            % (ids[0], ids[1], ids[0], ids[1]))
    verdict, reason = decision.validate_verdict(body, env)
    assert verdict is not None, reason
    out = decision.ledger_options_json(env["options"], verdict)
    import json
    rows = json.loads(out)
    assert rows[0]["probability"] == 0.7
    assert rows[1]["probability"] == 0.3


def test_board_options_carry_the_mechanical_probability():
    """(c) the BOARD the model sees must agree with the weighting block — the
    distribution existed one variable away and never rode the options."""
    env = decision.build_envelope("m42-02", ASK, OPTS, "manual")
    weights = (env.get("weighting") or {}).get("weights") or {}
    assert weights, "weighting block must be present for a two-option fork"
    opts = [o for o in env["options"] if isinstance(o, dict)]
    assert len(opts) == 2
    for o in opts:
        assert o.get("probability") == weights[o["id"]]
    assert abs(sum(o["probability"] for o in opts) - 1.0) < 0.02
